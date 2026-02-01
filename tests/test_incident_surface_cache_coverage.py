"""Tests for surface_cache.py to boost coverage.

Tests CacheEntry expiration, SurfaceCache get/invalidate/stats methods,
and the global cache functions, all with mocked system calls.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.surface_cache import (
    CacheEntry,
    SurfaceCache,
    clear_surface_cache,
    get_surface_cache,
)


# ---------------------------------------------------------------------------
# CacheEntry tests
# ---------------------------------------------------------------------------


class TestCacheEntry:
    @pytest.mark.timeout(10)
    def test_not_expired(self):
        entry = CacheEntry(value="data", expires_at=time.time() + 9999)
        assert entry.is_expired() is False

    @pytest.mark.timeout(10)
    def test_expired_by_time(self):
        entry = CacheEntry(value="data", expires_at=time.time() - 1)
        assert entry.is_expired() is True

    @pytest.mark.timeout(10)
    def test_expired_by_boot_id_change(self):
        entry = CacheEntry(value="data", expires_at=time.time() + 9999, boot_id="old-boot")
        assert entry.is_expired(current_boot_id="new-boot") is True

    @pytest.mark.timeout(10)
    def test_not_expired_same_boot_id(self):
        entry = CacheEntry(value="data", expires_at=time.time() + 9999, boot_id="same")
        assert entry.is_expired(current_boot_id="same") is False

    @pytest.mark.timeout(10)
    def test_not_expired_empty_boot_id(self):
        """Empty boot_id on entry or current should not trigger boot_id expiry."""
        entry = CacheEntry(value="data", expires_at=time.time() + 9999, boot_id="")
        assert entry.is_expired(current_boot_id="anything") is False

    @pytest.mark.timeout(10)
    def test_not_expired_empty_current_boot_id(self):
        entry = CacheEntry(value="data", expires_at=time.time() + 9999, boot_id="abc")
        assert entry.is_expired(current_boot_id="") is False


# ---------------------------------------------------------------------------
# SurfaceCache tests
# ---------------------------------------------------------------------------


PATCH_BOOT_ID = "elle.daemon.incidents.surface_cache.SurfaceCache._get_boot_id"
PATCH_HW = "elle.daemon.incidents.surface_cache._collect_hardware_identity_surface"
PATCH_KERNEL = "elle.daemon.incidents.surface_cache._collect_kernel_policy_surface"
PATCH_SVC = "elle.daemon.incidents.surface_cache._collect_service_surface"
PATCH_CFG = "elle.daemon.incidents.surface_cache._collect_config_surface"


class TestSurfaceCache:
    @pytest.mark.timeout(10)
    def test_clear(self):
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            cache = SurfaceCache()
            cache._hardware = CacheEntry(value="hw", expires_at=time.time() + 9999, boot_id="boot1")
            cache._kernel_policy = CacheEntry(value="kp", expires_at=time.time() + 9999, boot_id="boot1")
            cache._services["nginx"] = CacheEntry(value="svc", expires_at=time.time() + 9999, boot_id="boot1")
            cache._configs["/etc/hosts"] = CacheEntry(value="cfg", expires_at=time.time() + 9999, boot_id="boot1")

            cache.clear()
            assert cache._hardware is None
            assert cache._kernel_policy is None
            assert len(cache._services) == 0
            assert len(cache._configs) == 0

    @pytest.mark.timeout(10)
    def test_get_hardware_cached(self):
        hw_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_HW, return_value=hw_mock) as collect:
                cache = SurfaceCache()
                # First call collects
                result1 = cache.get_hardware()
                assert result1 is hw_mock
                assert collect.call_count == 1

                # Second call should use cache
                result2 = cache.get_hardware()
                assert result2 is hw_mock
                assert collect.call_count == 1  # Not called again

    @pytest.mark.timeout(10)
    def test_get_hardware_refreshes_on_expiry(self):
        hw_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_HW, return_value=hw_mock) as collect:
                cache = SurfaceCache()
                # Manually set expired entry
                cache._hardware = CacheEntry(value="old", expires_at=time.time() - 1, boot_id="boot1")

                result = cache.get_hardware()
                assert result is hw_mock
                assert collect.call_count == 1

    @pytest.mark.timeout(10)
    def test_get_kernel_policy_cached(self):
        kp_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_KERNEL, return_value=kp_mock) as collect:
                cache = SurfaceCache()
                result1 = cache.get_kernel_policy()
                assert result1 is kp_mock
                assert collect.call_count == 1

                result2 = cache.get_kernel_policy()
                assert result2 is kp_mock
                assert collect.call_count == 1

    @pytest.mark.timeout(10)
    def test_get_kernel_policy_refreshes_on_expiry(self):
        kp_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_KERNEL, return_value=kp_mock):
                cache = SurfaceCache()
                cache._kernel_policy = CacheEntry(value="old", expires_at=time.time() - 1, boot_id="boot1")

                result = cache.get_kernel_policy()
                assert result is kp_mock

    @pytest.mark.timeout(10)
    def test_get_service_cached(self):
        svc_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_SVC, return_value=svc_mock) as collect:
                cache = SurfaceCache()
                result1 = cache.get_service("nginx")
                assert result1 is svc_mock
                assert collect.call_count == 1

                result2 = cache.get_service("nginx")
                assert result2 is svc_mock
                assert collect.call_count == 1

    @pytest.mark.timeout(10)
    def test_get_service_force_refresh(self):
        svc_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_SVC, return_value=svc_mock) as collect:
                cache = SurfaceCache()
                cache.get_service("nginx")
                assert collect.call_count == 1

                cache.get_service("nginx", force_refresh=True)
                assert collect.call_count == 2

    @pytest.mark.timeout(10)
    def test_get_service_none_cached(self):
        """When service does not exist, None should be cached."""
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_SVC, return_value=None) as collect:
                cache = SurfaceCache()
                result1 = cache.get_service("missing")
                assert result1 is None
                assert collect.call_count == 1

                result2 = cache.get_service("missing")
                assert result2 is None
                assert collect.call_count == 1  # Negative cached

    @pytest.mark.timeout(10)
    def test_get_config_cached(self):
        cfg_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_CFG, return_value=cfg_mock) as collect:
                cache = SurfaceCache()
                result1 = cache.get_config("/etc/hosts")
                assert result1 is cfg_mock
                assert collect.call_count == 1

                result2 = cache.get_config("/etc/hosts")
                assert result2 is cfg_mock
                assert collect.call_count == 1

    @pytest.mark.timeout(10)
    def test_get_config_force_refresh(self):
        cfg_mock = MagicMock()
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_CFG, return_value=cfg_mock) as collect:
                cache = SurfaceCache()
                cache.get_config("/etc/hosts")
                cache.get_config("/etc/hosts", force_refresh=True)
                assert collect.call_count == 2

    @pytest.mark.timeout(10)
    def test_get_config_none_cached(self):
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            with patch(PATCH_CFG, return_value=None) as collect:
                cache = SurfaceCache()
                result1 = cache.get_config("/nonexistent")
                assert result1 is None
                result2 = cache.get_config("/nonexistent")
                assert result2 is None
                assert collect.call_count == 1

    @pytest.mark.timeout(10)
    def test_invalidate_service(self):
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            cache = SurfaceCache()
            cache._services["nginx"] = CacheEntry(value="x", expires_at=time.time() + 9999, boot_id="boot1")
            cache.invalidate_service("nginx")
            assert "nginx" not in cache._services

    @pytest.mark.timeout(10)
    def test_invalidate_service_missing(self):
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            cache = SurfaceCache()
            cache.invalidate_service("nonexistent")  # Should not raise

    @pytest.mark.timeout(10)
    def test_invalidate_config(self):
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            cache = SurfaceCache()
            cache._configs["/etc/hosts"] = CacheEntry(value="x", expires_at=time.time() + 9999, boot_id="boot1")
            cache.invalidate_config("/etc/hosts")
            assert "/etc/hosts" not in cache._configs

    @pytest.mark.timeout(10)
    def test_invalidate_kernel_policy(self):
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            cache = SurfaceCache()
            cache._kernel_policy = CacheEntry(value="x", expires_at=time.time() + 9999, boot_id="boot1")
            cache.invalidate_kernel_policy()
            assert cache._kernel_policy is None

    @pytest.mark.timeout(10)
    def test_stats(self):
        with patch(PATCH_BOOT_ID, return_value="boot12345"):
            cache = SurfaceCache()
            cache._hardware = CacheEntry(value="hw", expires_at=time.time() + 9999, boot_id="boot12345")
            cache._kernel_policy = CacheEntry(value="kp", expires_at=time.time() + 9999, boot_id="boot12345")
            cache._services["nginx"] = CacheEntry(value="svc", expires_at=time.time() + 9999, boot_id="boot12345")
            cache._configs["/etc/hosts"] = CacheEntry(value="cfg", expires_at=time.time() + 9999, boot_id="boot12345")

            stats = cache.stats()
            assert stats["hardware_cached"] is True
            assert stats["kernel_policy_cached"] is True
            assert stats["services_cached"] == 1
            assert stats["configs_cached"] == 1
            assert stats["boot_id"] == "boot1234"  # Truncated to 8 chars
            assert stats["kernel_policy_expires_in"] > 0

    @pytest.mark.timeout(10)
    def test_stats_empty(self):
        with patch(PATCH_BOOT_ID, return_value=""):
            cache = SurfaceCache()
            stats = cache.stats()
            assert stats["hardware_cached"] is False
            assert stats["kernel_policy_cached"] is False
            assert stats["services_cached"] == 0
            assert stats["configs_cached"] == 0
            assert stats["boot_id"] == "unknown"
            assert stats["kernel_policy_expires_in"] == 0

    @pytest.mark.timeout(10)
    def test_boot_id_change_clears_caches(self):
        """When boot_id changes, all caches should be cleared."""
        with patch(PATCH_BOOT_ID, return_value="boot1"):
            cache = SurfaceCache()
            cache._hardware = CacheEntry(value="hw", expires_at=time.time() + 9999, boot_id="boot1")
            cache._kernel_policy = CacheEntry(value="kp", expires_at=time.time() + 9999, boot_id="boot1")
            cache._services["nginx"] = CacheEntry(value="svc", expires_at=time.time() + 9999, boot_id="boot1")

        # Now simulate a boot_id change
        with patch(PATCH_BOOT_ID, return_value="boot2"):
            current = cache._current_boot_id()
            assert current == "boot2"
            assert cache._hardware is None
            assert cache._kernel_policy is None
            assert len(cache._services) == 0


# ---------------------------------------------------------------------------
# Global cache functions
# ---------------------------------------------------------------------------


class TestGlobalCache:
    @pytest.mark.timeout(10)
    def test_get_surface_cache_singleton(self):
        import elle.daemon.incidents.surface_cache as mod

        old = mod._cache
        mod._cache = None
        try:
            with patch(PATCH_BOOT_ID, return_value=""):
                c1 = get_surface_cache()
                c2 = get_surface_cache()
                assert c1 is c2
        finally:
            mod._cache = old

    @pytest.mark.timeout(10)
    def test_clear_surface_cache(self):
        import elle.daemon.incidents.surface_cache as mod

        with patch(PATCH_BOOT_ID, return_value=""):
            mod._cache = SurfaceCache()
            mod._cache._hardware = CacheEntry(value="x", expires_at=time.time() + 9999)
            clear_surface_cache()
            assert mod._cache._hardware is None

    @pytest.mark.timeout(10)
    def test_clear_surface_cache_when_none(self):
        import elle.daemon.incidents.surface_cache as mod

        mod._cache = None
        clear_surface_cache()  # Should not raise
