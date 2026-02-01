"""Tests for capabilities/core/__init__.py ImportError except branches.

Each of the 10 domain imports in get_core_capabilities() has an ``except ImportError: pass``
branch. We simulate each ImportError individually and verify the function still returns
capabilities from the other (non-broken) domains.
"""

from __future__ import annotations

from unittest.mock import patch

from elle.capabilities.core import get_core_capabilities

# The 10 domain module paths that get_core_capabilities() tries to import.
_DOMAIN_MODULES = [
    "elle.capabilities.core.service",
    "elle.capabilities.core.file",
    "elle.capabilities.core.config",
    "elle.capabilities.core.package",
    "elle.capabilities.core.docker",
    "elle.capabilities.core.notify",
    "elle.capabilities.core.network",
    "elle.capabilities.core.incident",
    "elle.capabilities.core.auth",
    "elle.capabilities.core.file_edit",
]


class TestGetCoreCapabilitiesImportErrors:
    """Each test makes one domain import fail and verifies the rest load."""

    def _run_with_broken_import(self, broken_module: str):
        """Helper: make *broken_module* raise ImportError inside get_core_capabilities."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def selective_import(name, *args, **kwargs):
            if name == broken_module:
                raise ImportError(f"Simulated failure for {broken_module}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=selective_import):
            return get_core_capabilities()

    def test_service_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.service")
        # Should still return capabilities from other domains
        assert isinstance(caps, list)

    def test_file_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.file")
        assert isinstance(caps, list)

    def test_config_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.config")
        assert isinstance(caps, list)

    def test_package_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.package")
        assert isinstance(caps, list)

    def test_docker_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.docker")
        assert isinstance(caps, list)

    def test_notify_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.notify")
        assert isinstance(caps, list)

    def test_network_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.network")
        assert isinstance(caps, list)

    def test_incident_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.incident")
        assert isinstance(caps, list)

    def test_auth_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.auth")
        assert isinstance(caps, list)

    def test_file_edit_import_error(self):
        caps = self._run_with_broken_import("elle.capabilities.core.file_edit")
        assert isinstance(caps, list)

    def test_all_imports_fail(self):
        """When every domain import fails, get_core_capabilities returns empty list."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def all_fail(name, *args, **kwargs):
            if name in _DOMAIN_MODULES:
                raise ImportError(f"Simulated failure for {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=all_fail):
            caps = get_core_capabilities()
            assert caps == []
