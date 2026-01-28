"""Surface cache for control surface snapshots.

Provides warm caching for expensive-to-compute control surface
data that changes infrequently. Enables fast snapshot collection.

Cache TTLs:
- Hardware identity: Until reboot (boot_id change)
- Kernel policy: 1 hour
- Services: 30 seconds
- Configs: 30 seconds
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from elle.daemon.incidents.control_surface import (
    ConfigFileSurface,
    HardwareIdentitySurface,
    KernelPolicySurface,
    SystemdServiceSurface,
    _collect_config_surface,
    _collect_hardware_identity_surface,
    _collect_kernel_policy_surface,
    _collect_service_surface,
)

# Cache TTLs in seconds
HARDWARE_TTL = float("inf")  # Until reboot
KERNEL_POLICY_TTL = 3600  # 1 hour
SERVICE_TTL = 30  # 30 seconds
CONFIG_TTL = 30  # 30 seconds


@dataclass
class CacheEntry:
    """A cached value with expiration."""

    value: Any
    expires_at: float
    boot_id: str = ""

    def is_expired(self, current_boot_id: str = "") -> bool:
        """Check if this entry has expired."""
        # Hardware cache expires on reboot
        if self.boot_id and current_boot_id and self.boot_id != current_boot_id:
            return True
        return time.time() > self.expires_at


@dataclass
class SurfaceCache:
    """Cache for control surface data.

    Thread-safe cache for expensive surface data.
    Uses TTL-based expiration with boot_id tracking for hardware.
    """

    _hardware: CacheEntry | None = None
    _kernel_policy: CacheEntry | None = None
    _services: dict[str, CacheEntry] = field(default_factory=dict)
    _configs: dict[str, CacheEntry] = field(default_factory=dict)
    _boot_id: str = ""

    def __post_init__(self) -> None:
        """Initialize boot_id."""
        self._boot_id = self._get_boot_id()

    @staticmethod
    def _get_boot_id() -> str:
        """Get current boot ID."""
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except Exception:
            return ""

    def _current_boot_id(self) -> str:
        """Get current boot ID, refreshing if needed."""
        boot_id = self._get_boot_id()
        if boot_id != self._boot_id:
            # Boot ID changed - clear all caches
            self.clear()
            self._boot_id = boot_id
        return boot_id

    def clear(self) -> None:
        """Clear all cached data."""
        self._hardware = None
        self._kernel_policy = None
        self._services.clear()
        self._configs.clear()

    def get_hardware(self) -> HardwareIdentitySurface:
        """Get hardware identity surface (cached until reboot)."""
        boot_id = self._current_boot_id()

        if self._hardware and not self._hardware.is_expired(boot_id):
            cached: HardwareIdentitySurface = self._hardware.value
            return cached

        # Collect fresh data
        surface = _collect_hardware_identity_surface()
        self._hardware = CacheEntry(
            value=surface,
            expires_at=time.time() + HARDWARE_TTL,
            boot_id=boot_id,
        )
        return surface

    def get_kernel_policy(self) -> KernelPolicySurface:
        """Get kernel policy surface (cached for 1 hour)."""
        boot_id = self._current_boot_id()

        if self._kernel_policy and not self._kernel_policy.is_expired(boot_id):
            cached: KernelPolicySurface = self._kernel_policy.value
            return cached

        # Collect fresh data
        surface = _collect_kernel_policy_surface()
        self._kernel_policy = CacheEntry(
            value=surface,
            expires_at=time.time() + KERNEL_POLICY_TTL,
            boot_id=boot_id,
        )
        return surface

    def get_service(self, name: str, force_refresh: bool = False) -> SystemdServiceSurface | None:
        """Get service surface (cached for 30 seconds).

        Args:
            name: Service unit name.
            force_refresh: If True, bypass cache.

        Returns:
            Service surface if service exists, None otherwise.
        """
        boot_id = self._current_boot_id()

        if not force_refresh and name in self._services:
            entry = self._services[name]
            if not entry.is_expired(boot_id):
                cached: SystemdServiceSurface | None = entry.value
                return cached

        # Collect fresh data
        surface = _collect_service_surface(name)
        if surface:
            self._services[name] = CacheEntry(
                value=surface,
                expires_at=time.time() + SERVICE_TTL,
                boot_id=boot_id,
            )
        else:
            # Cache the negative result too
            self._services[name] = CacheEntry(
                value=None,
                expires_at=time.time() + SERVICE_TTL,
                boot_id=boot_id,
            )
        return surface

    def get_config(self, path: str, force_refresh: bool = False) -> ConfigFileSurface | None:
        """Get config surface (cached for 30 seconds).

        Args:
            path: Config file or directory path.
            force_refresh: If True, bypass cache.

        Returns:
            Config surface if path exists, None otherwise.
        """
        boot_id = self._current_boot_id()

        if not force_refresh and path in self._configs:
            entry = self._configs[path]
            if not entry.is_expired(boot_id):
                cached: ConfigFileSurface | None = entry.value
                return cached

        # Collect fresh data
        surface = _collect_config_surface(path)
        if surface:
            self._configs[path] = CacheEntry(
                value=surface,
                expires_at=time.time() + CONFIG_TTL,
                boot_id=boot_id,
            )
        else:
            # Cache the negative result too
            self._configs[path] = CacheEntry(
                value=None,
                expires_at=time.time() + CONFIG_TTL,
                boot_id=boot_id,
            )
        return surface

    def invalidate_service(self, name: str) -> None:
        """Invalidate cached service data.

        Call this when you know a service has been modified.
        """
        self._services.pop(name, None)

    def invalidate_config(self, path: str) -> None:
        """Invalidate cached config data.

        Call this when you know a config has been modified.
        """
        self._configs.pop(path, None)

    def invalidate_kernel_policy(self) -> None:
        """Invalidate kernel policy cache.

        Call this after sysctl changes or similar.
        """
        self._kernel_policy = None

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        boot_id = self._current_boot_id()
        now = time.time()

        return {
            "hardware_cached": self._hardware is not None and not self._hardware.is_expired(boot_id),
            "kernel_policy_cached": self._kernel_policy is not None and not self._kernel_policy.is_expired(boot_id),
            "kernel_policy_expires_in": (max(0, self._kernel_policy.expires_at - now) if self._kernel_policy else 0),
            "services_cached": len([s for s, e in self._services.items() if not e.is_expired(boot_id)]),
            "configs_cached": len([c for c, e in self._configs.items() if not e.is_expired(boot_id)]),
            "boot_id": boot_id[:8] if boot_id else "unknown",
        }


# Global cache instance
_cache: SurfaceCache | None = None


def get_surface_cache() -> SurfaceCache:
    """Get the global surface cache instance."""
    global _cache
    if _cache is None:
        _cache = SurfaceCache()
    return _cache


def clear_surface_cache() -> None:
    """Clear the global surface cache."""
    global _cache
    if _cache is not None:
        _cache.clear()
