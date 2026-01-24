"""Capability checking for eBPF.

Verifies that the system has the required capabilities and kernel
support for eBPF telemetry.

Required capabilities (kernel 5.8+):
- CAP_BPF: Load/unload BPF programs, create maps
- CAP_PERFMON: Attach to tracepoints and perf events

Optional:
- CAP_NET_ADMIN: Network namespace access for net probes
"""

import ctypes
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Linux capability numbers (from /usr/include/linux/capability.h)
CAP_NET_ADMIN = 12
CAP_SYS_ADMIN = 21
CAP_BPF = 39
CAP_PERFMON = 38

# Minimum kernel version for unprivileged eBPF with CAP_BPF
MIN_KERNEL_VERSION = (5, 8)


@dataclass(frozen=True)
class CapabilityCheck:
    """Result of capability checking."""

    available: bool
    kernel_version: tuple[int, int, int]
    has_cap_bpf: bool
    has_cap_perfmon: bool
    has_cap_net_admin: bool
    has_cap_sys_admin: bool
    bpf_fs_available: bool
    reason: str


def _get_kernel_version() -> tuple[int, int, int]:
    """Get the running kernel version.

    Returns:
        Tuple of (major, minor, patch).
    """
    try:
        with open("/proc/version") as f:
            version_string = f.read()

        # Parse version like "Linux version 6.8.0-40-generic ..."
        match = re.search(r"Linux version (\d+)\.(\d+)\.(\d+)", version_string)
        if match:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except Exception as e:
        logger.debug(f"Failed to read kernel version: {e}")

    # Fallback to uname
    try:
        uname = os.uname()
        parts = uname.release.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        # Patch might have suffix like "0-40-generic"
        patch_str = parts[2].split("-")[0] if len(parts) > 2 else "0"
        patch = int(patch_str)
        return (major, minor, patch)
    except Exception as e:
        logger.warning(f"Failed to parse kernel version: {e}")
        return (0, 0, 0)


def _check_capability(cap: int) -> bool:
    """Check if current process has a specific capability.

    Uses prctl(PR_CAPBSET_READ) to check capability bounding set.

    Args:
        cap: Capability number.

    Returns:
        True if capability is available.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_CAPBSET_READ = 23
        result = libc.prctl(PR_CAPBSET_READ, cap, 0, 0, 0)
        return result == 1
    except Exception as e:
        logger.debug(f"Failed to check capability {cap}: {e}")
        return False


def _check_effective_capability(cap: int) -> bool:
    """Check if capability is in effective set.

    Reads /proc/self/status to check effective capabilities.

    Args:
        cap: Capability number.

    Returns:
        True if capability is effective.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    # Parse hex capability mask
                    cap_hex = line.split(":")[1].strip()
                    cap_mask = int(cap_hex, 16)
                    return bool(cap_mask & (1 << cap))
    except Exception as e:
        logger.debug(f"Failed to read effective capabilities: {e}")
    return False


def _is_root() -> bool:
    """Check if running as root."""
    return os.geteuid() == 0


def _bpf_fs_available() -> bool:
    """Check if BPF filesystem is mounted.

    The BPF filesystem at /sys/fs/bpf is required for
    persistent maps and pinning.
    """
    bpf_path = Path("/sys/fs/bpf")
    if not bpf_path.exists():
        return False

    # Check if it's actually a bpffs mount
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/sys/fs/bpf":
                    return parts[2] == "bpf"
    except Exception:
        pass

    return bpf_path.is_dir()


def _bcc_available() -> bool:
    """Check if BCC Python library is available."""
    try:
        from bcc import BPF  # noqa: F401
        return True
    except ImportError:
        return False


def check_capabilities() -> CapabilityCheck:
    """Check all capabilities required for eBPF.

    Returns:
        CapabilityCheck with detailed status.
    """
    kernel_version = _get_kernel_version()
    is_root = _is_root()

    # Check individual capabilities
    has_cap_bpf = is_root or _check_effective_capability(CAP_BPF)
    has_cap_perfmon = is_root or _check_effective_capability(CAP_PERFMON)
    has_cap_net_admin = is_root or _check_effective_capability(CAP_NET_ADMIN)
    has_cap_sys_admin = is_root or _check_effective_capability(CAP_SYS_ADMIN)

    bpf_fs = _bpf_fs_available()

    # Determine if eBPF is usable
    reason = ""
    available = False

    # Check kernel version
    if kernel_version[:2] < MIN_KERNEL_VERSION:
        reason = f"Kernel {kernel_version[0]}.{kernel_version[1]} too old (need 5.8+)"
    elif not bpf_fs:
        reason = "BPF filesystem not available at /sys/fs/bpf"
    elif not _bcc_available():
        reason = "BCC Python library not installed"
    elif is_root:
        available = True
        reason = "Running as root"
    elif has_cap_bpf and has_cap_perfmon:
        available = True
        reason = "Has CAP_BPF and CAP_PERFMON"
    elif has_cap_sys_admin:
        # Legacy fallback for older configs
        available = True
        reason = "Has CAP_SYS_ADMIN (legacy)"
    else:
        missing = []
        if not has_cap_bpf:
            missing.append("CAP_BPF")
        if not has_cap_perfmon:
            missing.append("CAP_PERFMON")
        reason = f"Missing capabilities: {', '.join(missing)}"

    return CapabilityCheck(
        available=available,
        kernel_version=kernel_version,
        has_cap_bpf=has_cap_bpf,
        has_cap_perfmon=has_cap_perfmon,
        has_cap_net_admin=has_cap_net_admin,
        has_cap_sys_admin=has_cap_sys_admin,
        bpf_fs_available=bpf_fs,
        reason=reason,
    )


def can_use_ebpf() -> tuple[bool, str]:
    """Check if eBPF telemetry can be used.

    Convenience function that returns a simple bool and reason.

    Returns:
        Tuple of (available, reason).
    """
    check = check_capabilities()
    return (check.available, check.reason)


def get_missing_capabilities() -> list[str]:
    """Get list of missing capabilities.

    Returns:
        List of capability names that are missing.
    """
    check = check_capabilities()
    missing = []

    if not check.has_cap_bpf:
        missing.append("CAP_BPF")
    if not check.has_cap_perfmon:
        missing.append("CAP_PERFMON")

    return missing


def get_kernel_version_string() -> str:
    """Get kernel version as string.

    Returns:
        Version string like "6.8.0".
    """
    v = _get_kernel_version()
    return f"{v[0]}.{v[1]}.{v[2]}"
