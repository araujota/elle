from __future__ import annotations

"""Comprehensive tests for elle.capabilities.dependencies.installer module.

Covers DependencySecurityError, DependencyInstaller (install, _validate_packages,
_run_apt_install, can_install, get_disallowed_packages), and module-level
convenience functions (get_installer, reset_installer, install_dependency).

All external dependencies are mocked -- no PostgreSQL, no subprocess, no
real elle modules.
"""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
from elle.capabilities.dependencies.installer import (
    DependencyInstaller,
    DependencySecurityError,
    get_installer,
    install_dependency,
    reset_installer,
)

# ---------------------------------------------------------------------------
# We import the lightweight models directly because they are pure Pydantic
# models with no I/O side-effects.  Everything else is mocked.
# ---------------------------------------------------------------------------
from elle.capabilities.dependencies.models import (
    InstallationRequest,
    InstallationResult,
)

# ============================================================================
# DependencySecurityError
# ============================================================================


class TestDependencySecurityError:
    """Tests for the DependencySecurityError exception class."""

    def test_basic_message(self):
        err = DependencySecurityError("blocked")
        assert str(err) == "blocked"
        assert err.packages == ()

    def test_with_packages(self):
        err = DependencySecurityError("not allowed", packages=("evil-pkg", "bad-pkg"))
        assert err.packages == ("evil-pkg", "bad-pkg")
        assert "not allowed" in str(err)

    def test_inherits_from_exception(self):
        err = DependencySecurityError("boom")
        assert isinstance(err, Exception)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def mock_registry():
    return MagicMock()


@pytest.fixture()
def mock_checker():
    return MagicMock()


@pytest.fixture()
def mock_store():
    return MagicMock()


@pytest.fixture()
def installer(mock_registry, mock_checker, mock_store):
    return DependencyInstaller(mock_registry, mock_checker, mock_store)


def _make_request(
    dependency: str = "augeas",
    apt_packages: tuple[str, ...] = ("augeas-tools",),
    pip_packages: tuple[str, ...] = (),
    reason: str = "unit test",
) -> InstallationRequest:
    """Helper to build an InstallationRequest."""
    return InstallationRequest(
        dependency=dependency,
        apt_packages=apt_packages,
        pip_packages=pip_packages,
        reason=reason,
    )


# ============================================================================
# DependencyInstaller.__init__
# ============================================================================


class TestInstallerInit:
    def test_stores_dependencies(self, mock_registry, mock_checker, mock_store):
        inst = DependencyInstaller(mock_registry, mock_checker, mock_store)
        assert inst._registry is mock_registry
        assert inst._checker is mock_checker
        assert inst._store is mock_store


# ============================================================================
# _validate_packages
# ============================================================================


class TestValidatePackages:
    """Tests for the internal _validate_packages method."""

    def test_allowed_packages_pass(self, installer):
        """Packages present in ALLOWED_PACKAGES should not raise."""
        # "augeas-tools" is in the real ALLOWED_PACKAGES frozenset
        installer._validate_packages(("augeas-tools", "docker.io"))

    def test_disallowed_packages_raise(self, installer):
        with pytest.raises(DependencySecurityError) as exc_info:
            installer._validate_packages(("totally-evil-pkg",))
        assert "totally-evil-pkg" in str(exc_info.value)
        assert exc_info.value.packages == ("totally-evil-pkg",)

    def test_mixed_allowed_and_disallowed_raises(self, installer):
        with pytest.raises(DependencySecurityError) as exc_info:
            installer._validate_packages(("augeas-tools", "evil-pkg", "bad-pkg"))
        assert "evil-pkg" in str(exc_info.value)
        assert "bad-pkg" in str(exc_info.value)
        assert "augeas-tools" not in exc_info.value.packages

    def test_empty_tuple_passes(self, installer):
        """An empty package tuple should not raise."""
        installer._validate_packages(())


# ============================================================================
# can_install
# ============================================================================


class TestCanInstall:
    def test_all_allowed(self, installer):
        assert installer.can_install(("augeas-tools", "docker.io")) is True

    def test_some_disallowed(self, installer):
        assert installer.can_install(("augeas-tools", "nope")) is False

    def test_all_disallowed(self, installer):
        assert installer.can_install(("nope", "bad")) is False

    def test_empty_tuple(self, installer):
        # all() on empty iterable is True
        assert installer.can_install(()) is True


# ============================================================================
# get_disallowed_packages
# ============================================================================


class TestGetDisallowedPackages:
    def test_returns_only_disallowed(self, installer):
        result = installer.get_disallowed_packages(("augeas-tools", "evil", "docker.io", "bad"))
        assert set(result) == {"evil", "bad"}

    def test_all_allowed_returns_empty(self, installer):
        result = installer.get_disallowed_packages(("augeas-tools",))
        assert result == ()

    def test_empty_input(self, installer):
        result = installer.get_disallowed_packages(())
        assert result == ()


# ============================================================================
# _run_apt_install
# ============================================================================


class TestRunAptInstall:
    """Tests for the async _run_apt_install helper."""

    async def test_success(self, installer):
        """Successful apt-get returns (True, None)."""
        fake_result = MagicMock()
        fake_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_result)
            success, error = await installer._run_apt_install(("augeas-tools",))

        assert success is True
        assert error is None

    async def test_nonzero_returncode_stderr(self, installer):
        """Non-zero exit code with stderr returns (False, stderr)."""
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = "  E: Unable to locate package  "
        fake_result.stdout = ""

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_result)
            success, error = await installer._run_apt_install(("bad-pkg",))

        assert success is False
        assert error == "E: Unable to locate package"

    async def test_nonzero_returncode_stdout_fallback(self, installer):
        """When stderr is empty, stdout is used as the error message."""
        fake_result = MagicMock()
        fake_result.returncode = 100
        fake_result.stderr = ""
        fake_result.stdout = "Some stdout error info"

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_result)
            success, error = await installer._run_apt_install(("x",))

        assert success is False
        assert error == "Some stdout error info"

    async def test_nonzero_returncode_unknown_error(self, installer):
        """When both stderr and stdout are empty, 'Unknown error' is returned."""
        fake_result = MagicMock()
        fake_result.returncode = 2
        fake_result.stderr = ""
        fake_result.stdout = ""

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_result)
            success, error = await installer._run_apt_install(("x",))

        assert success is False
        assert error == "Unknown error"

    async def test_timeout_expired(self, installer):
        """subprocess.TimeoutExpired is caught and returns a timeout message."""
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=subprocess.TimeoutExpired(cmd="apt-get", timeout=300)
            )
            success, error = await installer._run_apt_install(("pkg",))

        assert success is False
        assert "timed out" in error.lower()

    async def test_generic_exception(self, installer):
        """An unexpected exception is caught and its str() is returned."""
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(side_effect=OSError("Permission denied"))
            success, error = await installer._run_apt_install(("pkg",))

        assert success is False
        assert "Permission denied" in error


# ============================================================================
# install (the main public coroutine)
# ============================================================================


class TestInstall:
    """Tests for DependencyInstaller.install -- the full pipeline."""

    async def test_successful_install(self, installer, mock_store, mock_checker):
        """Happy-path: allowed packages, apt succeeds."""
        request = _make_request(
            dependency="augeas",
            apt_packages=("augeas-tools",),
        )

        with patch.object(
            installer,
            "_run_apt_install",
            new_callable=AsyncMock,
            return_value=(True, None),
        ):
            result = await installer.install(request)

        assert result.success is True
        assert result.dependency == "augeas"
        assert result.installed_packages == ("augeas-tools",)
        assert result.error_message is None
        assert result.duration_sec >= 0

        mock_store.record_installation.assert_called_once()
        mock_checker.invalidate_cache.assert_called_once_with("augeas")

    async def test_failed_install(self, installer, mock_store, mock_checker):
        """apt fails: installed_packages should be empty, error propagated."""
        request = _make_request(
            dependency="augeas",
            apt_packages=("augeas-tools",),
        )

        with patch.object(
            installer,
            "_run_apt_install",
            new_callable=AsyncMock,
            return_value=(False, "E: Unable to locate package"),
        ):
            result = await installer.install(request)

        assert result.success is False
        assert result.installed_packages == ()
        assert result.error_message == "E: Unable to locate package"
        mock_store.record_installation.assert_called_once()
        mock_checker.invalidate_cache.assert_called_once_with("augeas")

    async def test_disallowed_package_raises(self, installer):
        """Installing a disallowed package raises DependencySecurityError."""
        request = _make_request(
            dependency="malicious",
            apt_packages=("evil-pkg",),
        )

        with pytest.raises(DependencySecurityError):
            await installer.install(request)

    async def test_pip_packages_warning(self, installer, mock_store, mock_checker):
        """pip_packages are logged as a warning but do not block install."""
        request = _make_request(
            dependency="augeas",
            apt_packages=("augeas-tools",),
            pip_packages=("some-pip-pkg",),
        )

        with (
            patch.object(
                installer,
                "_run_apt_install",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch("elle.capabilities.dependencies.installer.logger") as mock_logger,
        ):
            result = await installer.install(request)

        assert result.success is True
        mock_logger.warning.assert_called_once()
        assert "pip" in mock_logger.warning.call_args[0][0].lower()

    async def test_no_pip_packages_no_warning(self, installer, mock_store, mock_checker):
        """When pip_packages is empty, no pip warning is logged."""
        request = _make_request(
            dependency="augeas",
            apt_packages=("augeas-tools",),
            pip_packages=(),
        )

        with (
            patch.object(
                installer,
                "_run_apt_install",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch("elle.capabilities.dependencies.installer.logger") as mock_logger,
        ):
            await installer.install(request)

        # warning should NOT have been called for pip
        for call in mock_logger.warning.call_args_list:
            assert "pip" not in call[0][0].lower()

    async def test_install_records_duration(self, installer, mock_store, mock_checker):
        """The result should record a non-negative duration."""
        request = _make_request(apt_packages=("augeas-tools",))

        with patch.object(
            installer,
            "_run_apt_install",
            new_callable=AsyncMock,
            return_value=(True, None),
        ):
            result = await installer.install(request)

        assert isinstance(result.duration_sec, float)
        assert result.duration_sec >= 0


# ============================================================================
# Module-level convenience functions
# ============================================================================


class TestGetInstaller:
    """Tests for get_installer() singleton and reset_installer()."""

    def setup_method(self):
        reset_installer()

    def teardown_method(self):
        reset_installer()

    @patch("elle.capabilities.dependencies.store.get_store", create=True)
    @patch("elle.capabilities.dependencies.checker.get_checker")
    @patch("elle.capabilities.dependencies.registry.get_registry")
    def test_creates_singleton(self, mock_get_registry, mock_get_checker, mock_get_store):
        """First call creates the installer; lazy imports are called."""
        inst = get_installer()
        assert isinstance(inst, DependencyInstaller)
        mock_get_registry.assert_called_once()
        mock_get_checker.assert_called_once()
        mock_get_store.assert_called_once()

    @patch("elle.capabilities.dependencies.store.get_store", create=True)
    @patch("elle.capabilities.dependencies.checker.get_checker")
    @patch("elle.capabilities.dependencies.registry.get_registry")
    def test_returns_same_instance(self, mock_get_registry, mock_get_checker, mock_get_store):
        """Subsequent calls return the same object without re-importing."""
        inst1 = get_installer()
        inst2 = get_installer()
        assert inst1 is inst2
        # Factory functions called only once
        assert mock_get_registry.call_count == 1

    def test_reset_clears_singleton(self):
        """reset_installer sets the module-level _installer to None."""
        import elle.capabilities.dependencies.installer as mod

        mod._installer = MagicMock()
        reset_installer()
        assert mod._installer is None


class TestInstallDependency:
    """Tests for the module-level install_dependency() convenience coroutine."""

    def setup_method(self):
        reset_installer()

    def teardown_method(self):
        reset_installer()

    @patch("elle.capabilities.dependencies.installer.get_installer")
    async def test_delegates_to_global_installer(self, mock_get_installer):
        """install_dependency forwards to the global installer.install()."""
        mock_inst = MagicMock()
        mock_inst.install = AsyncMock(
            return_value=InstallationResult(
                success=True,
                dependency="augeas",
                installed_packages=("augeas-tools",),
            )
        )
        mock_get_installer.return_value = mock_inst

        request = _make_request(apt_packages=("augeas-tools",))
        result = await install_dependency(request)

        assert result.success is True
        mock_inst.install.assert_awaited_once_with(request)


# ============================================================================
# Integration-style tests (still fully mocked, but test full flow)
# ============================================================================


class TestInstallerIntegration:
    """End-to-end flow through install() with _run_apt_install mocked at
    the subprocess level."""

    async def test_full_success_flow(self):
        """Install allowed packages, apt succeeds, result recorded."""
        registry = MagicMock()
        checker = MagicMock()
        store = MagicMock()
        inst = DependencyInstaller(registry, checker, store)

        request = _make_request(
            dependency="docker",
            apt_packages=("docker.io",),
        )

        fake_proc = MagicMock()
        fake_proc.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_proc)
            result = await inst.install(request)

        assert result.success is True
        assert result.installed_packages == ("docker.io",)
        store.record_installation.assert_called_once()
        checker.invalidate_cache.assert_called_once_with("docker")

    async def test_full_failure_flow(self):
        """Install allowed packages, apt fails, error recorded."""
        registry = MagicMock()
        checker = MagicMock()
        store = MagicMock()
        inst = DependencyInstaller(registry, checker, store)

        request = _make_request(
            dependency="wireguard",
            apt_packages=("wireguard",),
        )

        fake_proc = MagicMock()
        fake_proc.returncode = 100
        fake_proc.stderr = "dpkg error"
        fake_proc.stdout = ""

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_proc)
            result = await inst.install(request)

        assert result.success is False
        assert result.error_message == "dpkg error"
        assert result.installed_packages == ()
        store.record_installation.assert_called_once()

    async def test_security_error_stops_before_apt(self):
        """Disallowed packages cause a security error before apt is called."""
        registry = MagicMock()
        checker = MagicMock()
        store = MagicMock()
        inst = DependencyInstaller(registry, checker, store)

        request = _make_request(
            dependency="evil",
            apt_packages=("evil-pkg",),
        )

        with pytest.raises(DependencySecurityError) as exc_info:
            await inst.install(request)

        assert "evil-pkg" in str(exc_info.value)
        # apt should never have been called
        store.record_installation.assert_not_called()
        checker.invalidate_cache.assert_not_called()

    async def test_multiple_allowed_packages(self):
        """Multiple allowed packages are all passed to apt."""
        registry = MagicMock()
        checker = MagicMock()
        store = MagicMock()
        inst = DependencyInstaller(registry, checker, store)

        request = _make_request(
            dependency="augeas",
            apt_packages=("augeas-tools", "python3-augeas"),
        )

        fake_proc = MagicMock()
        fake_proc.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_proc)
            result = await inst.install(request)

        assert result.success is True
        assert result.installed_packages == ("augeas-tools", "python3-augeas")

    async def test_timeout_during_install(self):
        """A timeout during apt results in a failure result (not an exception)."""
        registry = MagicMock()
        checker = MagicMock()
        store = MagicMock()
        inst = DependencyInstaller(registry, checker, store)

        request = _make_request(
            dependency="augeas",
            apt_packages=("augeas-tools",),
        )

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=subprocess.TimeoutExpired(cmd="apt-get", timeout=300)
            )
            result = await inst.install(request)

        assert result.success is False
        assert "timed out" in result.error_message.lower()
        store.record_installation.assert_called_once()
