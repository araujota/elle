"""Tests for surface cache functionality."""

import time

import pytest

from elle.daemon.incidents.surface_cache import (
    CacheEntry,
    SurfaceCache,
    clear_surface_cache,
    get_surface_cache,
    HARDWARE_TTL,
    KERNEL_POLICY_TTL,
    SERVICE_TTL,
    CONFIG_TTL,
)


class TestCacheEntry:
    """Tests for CacheEntry class."""

    def test_cache_entry_not_expired(self) -> None:
        """Entry should not be expired when created."""
        entry = CacheEntry(value="test", expires_at=time.time() + 100)
        assert not entry.is_expired()

    def test_cache_entry_expired(self) -> None:
        """Entry should be expired after expiration time."""
        entry = CacheEntry(value="test", expires_at=time.time() - 1)
        assert entry.is_expired()

    def test_cache_entry_boot_id_change(self) -> None:
        """Entry should expire on boot_id change."""
        entry = CacheEntry(
            value="test",
            expires_at=time.time() + 1000,
            boot_id="boot1",
        )
        assert entry.is_expired(current_boot_id="boot2")

    def test_cache_entry_same_boot_id(self) -> None:
        """Entry should not expire with same boot_id."""
        entry = CacheEntry(
            value="test",
            expires_at=time.time() + 1000,
            boot_id="boot1",
        )
        assert not entry.is_expired(current_boot_id="boot1")


class TestSurfaceCache:
    """Tests for SurfaceCache class."""

    def test_cache_initialization(self) -> None:
        """Cache should initialize empty."""
        cache = SurfaceCache()
        stats = cache.stats()
        assert stats["hardware_cached"] is False
        assert stats["kernel_policy_cached"] is False
        assert stats["services_cached"] == 0
        assert stats["configs_cached"] == 0

    def test_get_hardware_caches(self) -> None:
        """Hardware should be cached after first call."""
        cache = SurfaceCache()
        hw1 = cache.get_hardware()
        stats = cache.stats()
        assert stats["hardware_cached"] is True
        # Second call should return same object
        hw2 = cache.get_hardware()
        assert hw1.cpu_cores == hw2.cpu_cores

    def test_get_kernel_policy_caches(self) -> None:
        """Kernel policy should be cached after first call."""
        cache = SurfaceCache()
        kp1 = cache.get_kernel_policy()
        stats = cache.stats()
        assert stats["kernel_policy_cached"] is True
        # Second call should return same data
        kp2 = cache.get_kernel_policy()
        assert kp1.kernel_version == kp2.kernel_version

    def test_get_service_caches(self) -> None:
        """Service should be cached after first call."""
        cache = SurfaceCache()
        # Use a service that likely doesn't exist
        svc1 = cache.get_service("nonexistent.service")
        stats = cache.stats()
        # Should cache the negative result
        assert stats["services_cached"] == 1

    def test_get_service_force_refresh(self) -> None:
        """force_refresh should bypass cache."""
        cache = SurfaceCache()
        cache.get_service("test.service")
        # Force refresh should still work
        cache.get_service("test.service", force_refresh=True)
        stats = cache.stats()
        assert stats["services_cached"] == 1

    def test_get_config_caches(self) -> None:
        """Config should be cached after first call."""
        cache = SurfaceCache()
        cfg1 = cache.get_config("/etc/hosts")
        stats = cache.stats()
        # Should cache (even if None on some systems)
        assert stats["configs_cached"] == 1

    def test_invalidate_service(self) -> None:
        """invalidate_service should remove cached entry."""
        cache = SurfaceCache()
        cache.get_service("test.service")
        assert cache.stats()["services_cached"] == 1
        cache.invalidate_service("test.service")
        assert cache.stats()["services_cached"] == 0

    def test_invalidate_config(self) -> None:
        """invalidate_config should remove cached entry."""
        cache = SurfaceCache()
        cache.get_config("/etc/hosts")
        assert cache.stats()["configs_cached"] == 1
        cache.invalidate_config("/etc/hosts")
        assert cache.stats()["configs_cached"] == 0

    def test_invalidate_kernel_policy(self) -> None:
        """invalidate_kernel_policy should clear kernel cache."""
        cache = SurfaceCache()
        cache.get_kernel_policy()
        assert cache.stats()["kernel_policy_cached"] is True
        cache.invalidate_kernel_policy()
        assert cache.stats()["kernel_policy_cached"] is False

    def test_clear_cache(self) -> None:
        """clear should remove all cached data."""
        cache = SurfaceCache()
        cache.get_hardware()
        cache.get_kernel_policy()
        cache.get_service("test.service")
        cache.clear()
        stats = cache.stats()
        assert stats["hardware_cached"] is False
        assert stats["kernel_policy_cached"] is False
        assert stats["services_cached"] == 0


class TestGlobalCache:
    """Tests for global cache accessors."""

    def test_get_surface_cache_returns_singleton(self) -> None:
        """get_surface_cache should return same instance."""
        cache1 = get_surface_cache()
        cache2 = get_surface_cache()
        assert cache1 is cache2

    def test_clear_surface_cache(self) -> None:
        """clear_surface_cache should clear global cache."""
        cache = get_surface_cache()
        cache.get_hardware()
        clear_surface_cache()
        stats = get_surface_cache().stats()
        assert stats["hardware_cached"] is False


class TestCacheTTLConstants:
    """Tests for TTL constants."""

    def test_hardware_ttl_is_infinite(self) -> None:
        """HARDWARE_TTL should be infinite."""
        assert HARDWARE_TTL == float("inf")

    def test_kernel_policy_ttl(self) -> None:
        """KERNEL_POLICY_TTL should be 1 hour."""
        assert KERNEL_POLICY_TTL == 3600

    def test_service_ttl(self) -> None:
        """SERVICE_TTL should be 30 seconds."""
        assert SERVICE_TTL == 30

    def test_config_ttl(self) -> None:
        """CONFIG_TTL should be 30 seconds."""
        assert CONFIG_TTL == 30
