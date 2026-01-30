"""Hardware profile detection and dynamic threshold scaling.

Detects system hardware configuration and adjusts monitoring
thresholds based on available resources.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class HardwareProfile(BaseModel):
    """Detected hardware configuration."""

    cpu_count: int = Field(ge=0, default=1)
    ram_total_mb: int = Field(ge=0, default=0)
    disk_total_gb: dict[str, float] = Field(default_factory=dict)
    gpu_count: int = Field(ge=0, default=0)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    profile_hash: str = ""

    def compute_hash(self) -> str:
        """Compute a stable hash of the hardware profile."""
        data = json.dumps(
            {
                "cpu_count": self.cpu_count,
                "ram_total_mb": self.ram_total_mb,
                "disk_total_gb": dict(sorted(self.disk_total_gb.items())),
                "gpu_count": self.gpu_count,
            },
            sort_keys=True,
        )
        return hashlib.sha256(data.encode()).hexdigest()[:16]


class DynamicThresholds(BaseModel):
    """Thresholds scaled to hardware configuration."""

    cpu_load_warning: float = 3.0
    cpu_load_critical: float = 4.0
    mem_used_pct_warning: float = 80.0
    mem_used_pct_critical: float = 92.0
    disk_used_pct_warning: float = 80.0
    disk_used_pct_critical: float = 92.0
    swap_used_pct_warning: float = 70.0
    swap_used_pct_critical: float = 90.0


def detect_hardware() -> HardwareProfile:
    """Detect current hardware configuration from /proc and /sys."""
    cpu_count = os.cpu_count() or 1

    # Read total RAM from /proc/meminfo
    ram_total_mb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        ram_total_mb = int(parts[1]) // 1024  # kB to MB
                    break
    except (OSError, ValueError):
        pass

    # Read disk sizes via statvfs
    disk_total_gb: dict[str, float] = {}
    mounts = ["/", "/home", "/var"]
    for mount in mounts:
        try:
            st = os.statvfs(mount)
            total_gb = (st.f_blocks * st.f_frsize) / (1024**3)
            if total_gb > 0:
                disk_total_gb[mount] = round(total_gb, 1)
        except OSError:
            pass

    # Detect GPUs (nvidia-smi presence)
    gpu_count = 0
    try:
        result = os.popen("nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null")
        output = result.read().strip()
        result.close()
        if output:
            gpu_count = int(output.split("\n")[0].strip())
    except (OSError, ValueError):
        pass

    profile = HardwareProfile(
        cpu_count=cpu_count,
        ram_total_mb=ram_total_mb,
        disk_total_gb=disk_total_gb,
        gpu_count=gpu_count,
    )
    profile.profile_hash = profile.compute_hash()
    return profile


def compute_dynamic_thresholds(profile: HardwareProfile) -> DynamicThresholds:
    """Scale thresholds based on hardware profile.

    Rules:
        cpu.load warning = cpu_count * 0.75
        cpu.load critical = cpu_count * 1.0
        mem: if RAM > 64GB, warning 85%, critical 95%
        disk: if disk > 1TB, warning 85%, critical 95%
        swap: if swap < 1GB, warning 50%, critical 70%
    """
    thresholds = DynamicThresholds()

    # CPU load scales with core count
    thresholds.cpu_load_warning = profile.cpu_count * 0.75
    thresholds.cpu_load_critical = float(profile.cpu_count)

    # Memory: larger systems have more headroom
    if profile.ram_total_mb > 65536:  # > 64GB
        thresholds.mem_used_pct_warning = 85.0
        thresholds.mem_used_pct_critical = 95.0

    # Disk: larger disks have more headroom
    max_disk_gb = max(profile.disk_total_gb.values()) if profile.disk_total_gb else 0
    if max_disk_gb > 1000:  # > 1TB
        thresholds.disk_used_pct_warning = 85.0
        thresholds.disk_used_pct_critical = 95.0

    # Swap: small swap means less tolerance
    # We'd need to read swap info, but use conservative defaults
    # This will be refined when swap size is available from snapshot

    return thresholds
