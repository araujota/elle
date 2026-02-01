"""Tests for preflight nspawn validator."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.ops.preflight.models import (
    IssueSeverity,
    NspawnConfig,
    PreflightStatus,
    PreflightTest,
)
from elle.ops.preflight.nspawn_validator import (
    PACKAGE_SERVICE_MAP,
    NspawnValidator,
    _get_package_services,
    create_nspawn_validator,
)


class TestGetPackageServices:
    """Tests for _get_package_services helper."""

    def test_known_package(self):
        """Test getting services for known package."""
        services = _get_package_services("nginx")
        assert "nginx" in services

    def test_postgres(self):
        """Test getting services for postgresql."""
        services = _get_package_services("postgresql")
        assert "postgresql" in services

    def test_mysql(self):
        """Test getting services for mysql-server."""
        services = _get_package_services("mysql-server")
        assert "mysql" in services

    def test_unknown_package(self):
        """Test getting services for unknown package."""
        services = _get_package_services("some-unknown-package")
        # Should return package name as service name
        assert "some-unknown-package" in services

    def test_package_with_version(self):
        """Test getting services for package with version."""
        services = _get_package_services("nginx=1.24.0")
        assert "nginx" in services

    def test_package_with_suite(self):
        """Test getting services for package with suite."""
        services = _get_package_services("nginx/noble")
        assert "nginx" in services


class TestPackageServiceMap:
    """Tests for PACKAGE_SERVICE_MAP constant."""

    def test_map_not_empty(self):
        """Test that service map is populated."""
        assert len(PACKAGE_SERVICE_MAP) > 0

    def test_common_services_mapped(self):
        """Test that common services are mapped."""
        assert "nginx" in PACKAGE_SERVICE_MAP
        assert "postgresql" in PACKAGE_SERVICE_MAP
        assert "mysql-server" in PACKAGE_SERVICE_MAP
        assert "redis-server" in PACKAGE_SERVICE_MAP


class TestNspawnValidator:
    """Tests for NspawnValidator class."""

    def test_creation_default(self):
        """Test validator creation with defaults."""
        validator = NspawnValidator()
        assert validator._config is not None

    def test_creation_custom_config(self):
        """Test validator creation with custom config."""
        config = NspawnConfig(timeout_sec=300)
        validator = NspawnValidator(config=config)
        assert validator._config.timeout_sec == 300

    @patch("elle.ops.preflight.nspawn_validator.subprocess.run")
    def test_is_available_true(self, mock_run):
        """Test is_available when nspawn is available."""
        mock_run.return_value = MagicMock(returncode=0)
        validator = NspawnValidator()
        assert validator.is_available is True

    @patch("elle.ops.preflight.nspawn_validator.subprocess.run")
    def test_is_available_false_not_found(self, mock_run):
        """Test is_available when nspawn is not found."""
        mock_run.side_effect = FileNotFoundError()
        validator = NspawnValidator()
        assert validator.is_available is False

    @patch("elle.ops.preflight.nspawn_validator.subprocess.run")
    def test_is_available_false_error(self, mock_run):
        """Test is_available when nspawn returns error."""
        mock_run.return_value = MagicMock(returncode=1)
        validator = NspawnValidator()
        assert validator.is_available is False

    @patch("elle.ops.preflight.nspawn_validator.Path.exists")
    def test_has_base_image_true(self, mock_exists):
        """Test has_base_image when image exists."""
        mock_exists.return_value = True
        validator = NspawnValidator()
        # Need to also mock the os-release check
        with patch.object(Path, "exists", return_value=True):
            assert validator.has_base_image is True

    @patch("elle.ops.preflight.nspawn_validator.Path.exists")
    def test_has_base_image_false(self, mock_exists):
        """Test has_base_image when image doesn't exist."""
        mock_exists.return_value = False
        validator = NspawnValidator()
        assert validator.has_base_image is False

    @patch("elle.ops.preflight.nspawn_validator.subprocess.run")
    def test_validate_nspawn_not_available(self, mock_run):
        """Test validation when nspawn is not available."""
        mock_run.side_effect = FileNotFoundError()
        validator = NspawnValidator()

        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False
        assert any("not available" in i.message.lower() for i in result.issues)

    def test_validate_base_image_preparation_failure(self):
        """Test validation when base image preparation fails."""
        validator = NspawnValidator()

        # Mock properties and methods
        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: False)):
                with patch.object(validator, "prepare_base_image", return_value=False):
                    test = PreflightTest(
                        packages=("nginx",),
                        operation="install",
                    )
                    result = validator.validate(test)

                    assert result.status == PreflightStatus.BLOCKED
                    assert result.can_proceed is False
                    assert any("base image" in i.message.lower() for i in result.issues)

    def test_validate_install_success(self):
        """Test successful package validation."""
        validator = NspawnValidator()

        # Create a mock context manager for the container
        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    with patch.object(validator, "_run_in_container", return_value=(0, "Success", "")):
                        test = PreflightTest(
                            packages=("nginx",),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.SAFE
                        assert result.can_proceed is True
                        assert result.tier_used == 2

    def test_validate_install_failure(self):
        """Test validation when package install fails."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    # apt update succeeds, apt install fails
                    with patch.object(
                        validator,
                        "_run_in_container",
                        side_effect=[
                            (0, "Updated", ""),  # apt update
                            (1, "", "Package not found"),  # apt install
                        ],
                    ):
                        test = PreflightTest(
                            packages=("nonexistent-package",),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.BLOCKED
                        assert result.can_proceed is False
                        assert any(i.severity == IssueSeverity.ERROR for i in result.issues)

    def test_validate_service_start_failure(self):
        """Test validation when service fails to start."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    # apt update succeeds, apt install succeeds, service start fails
                    with patch.object(
                        validator,
                        "_run_in_container",
                        side_effect=[
                            (0, "Updated", ""),  # apt update
                            (0, "Installed", ""),  # apt install
                            (1, "", "Service failed to start"),  # systemctl start
                        ],
                    ):
                        test = PreflightTest(
                            packages=("nginx",),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.BLOCKED
                        assert result.can_proceed is False
                        assert any("failed to start" in i.message.lower() for i in result.issues)

    def test_validate_container_exception(self):
        """Test validation when container creation fails."""
        validator = NspawnValidator()

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(
                    validator, "_create_ephemeral_container", side_effect=RuntimeError("Container creation failed")
                ):
                    test = PreflightTest(
                        packages=("nginx",),
                        operation="install",
                    )
                    result = validator.validate(test)

                    assert result.status == PreflightStatus.BLOCKED
                    assert result.can_proceed is False
                    assert any("error" in i.message.lower() for i in result.issues)

    def test_prepare_base_image_debootstrap_not_found(self):
        """Test base image preparation when debootstrap not found."""
        validator = NspawnValidator()

        with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: False)):
            with patch("elle.ops.preflight.nspawn_validator.subprocess.run", side_effect=FileNotFoundError()):
                result = validator.prepare_base_image()
                assert result is False


class TestCreateNspawnValidator:
    """Tests for create_nspawn_validator factory function."""

    def test_create_default(self):
        """Test creating validator with defaults."""
        validator = create_nspawn_validator()
        assert isinstance(validator, NspawnValidator)

    def test_create_with_config(self):
        """Test creating validator with custom config."""
        config = NspawnConfig(timeout_sec=60)
        validator = create_nspawn_validator(config=config)
        assert validator._config.timeout_sec == 60


class TestNspawnConfigValidation:
    """Tests for NspawnConfig constraints."""

    def test_timeout_minimum(self):
        """Test that timeout has minimum value."""
        with pytest.raises(Exception):
            NspawnConfig(timeout_sec=5)  # Below minimum of 10

    def test_timeout_maximum(self):
        """Test that timeout has maximum value."""
        with pytest.raises(Exception):
            NspawnConfig(timeout_sec=700)  # Above maximum of 600


class TestIsAvailableTimeout:
    """Tests for is_available timeout branch."""

    @patch("elle.ops.preflight.nspawn_validator.subprocess.run")
    def test_is_available_timeout(self, mock_run):
        """Test is_available when nspawn check times out."""
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd="systemd-nspawn", timeout=10)
        validator = NspawnValidator()
        assert validator.is_available is False


class TestHasBaseImagePaths:
    """Tests for has_base_image property edge cases."""

    def test_has_base_image_base_exists_osrelease_missing(self, tmp_path):
        """Test has_base_image when base dir exists but os-release does not."""
        config = NspawnConfig(base_image_path=str(tmp_path))
        validator = NspawnValidator(config=config)
        # tmp_path exists but tmp_path/etc/os-release does not
        assert validator.has_base_image is False

    def test_has_base_image_both_exist(self, tmp_path):
        """Test has_base_image when both base dir and os-release exist."""
        etc_dir = tmp_path / "etc"
        etc_dir.mkdir()
        (etc_dir / "os-release").write_text("ID=ubuntu\n")
        config = NspawnConfig(base_image_path=str(tmp_path))
        validator = NspawnValidator(config=config)
        assert validator.has_base_image is True


class TestPrepareBaseImage:
    """Tests for prepare_base_image paths."""

    def test_prepare_base_image_already_exists(self, tmp_path):
        """Test prepare_base_image returns True when image already exists."""
        etc_dir = tmp_path / "etc"
        etc_dir.mkdir()
        (etc_dir / "os-release").write_text("ID=ubuntu\n")
        config = NspawnConfig(base_image_path=str(tmp_path))
        validator = NspawnValidator(config=config)
        assert validator.prepare_base_image() is True

    def test_prepare_base_image_success(self, tmp_path):
        """Test prepare_base_image succeeds with debootstrap."""
        base_path = tmp_path / "base-image"
        config = NspawnConfig(base_image_path=str(base_path))
        validator = NspawnValidator(config=config)

        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=mock_result):
            result = validator.prepare_base_image()
        assert result is True

    def test_prepare_base_image_debootstrap_failure(self, tmp_path):
        """Test prepare_base_image when debootstrap returns non-zero."""
        base_path = tmp_path / "base-image"
        config = NspawnConfig(base_image_path=str(base_path))
        validator = NspawnValidator(config=config)

        mock_result = MagicMock(returncode=1, stdout="", stderr="E: Some error")
        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=mock_result):
            result = validator.prepare_base_image()
        assert result is False

    def test_prepare_base_image_timeout(self, tmp_path):
        """Test prepare_base_image when debootstrap times out."""
        import subprocess as sp

        base_path = tmp_path / "base-image"
        config = NspawnConfig(base_image_path=str(base_path))
        validator = NspawnValidator(config=config)

        with patch(
            "elle.ops.preflight.nspawn_validator.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="debootstrap", timeout=600),
        ):
            result = validator.prepare_base_image()
        assert result is False

    def test_prepare_base_image_generic_exception(self, tmp_path):
        """Test prepare_base_image when an unexpected exception occurs."""
        base_path = tmp_path / "base-image"
        config = NspawnConfig(base_image_path=str(base_path))
        validator = NspawnValidator(config=config)

        with patch(
            "elle.ops.preflight.nspawn_validator.subprocess.run",
            side_effect=OSError("Disk full"),
        ):
            result = validator.prepare_base_image()
        assert result is False


class TestCreateEphemeralContainer:
    """Tests for _create_ephemeral_container context manager."""

    def test_container_no_base_image(self, tmp_path):
        """Test container creation raises when no base image exists."""
        config = NspawnConfig(base_image_path=str(tmp_path / "missing"))
        validator = NspawnValidator(config=config)

        with pytest.raises(RuntimeError, match="Base image not prepared"):
            with validator._create_ephemeral_container():
                pass

    def test_container_mount_success_and_cleanup(self, tmp_path):
        """Test container creation with overlay mount and cleanup."""
        # Set up base image
        base_path = tmp_path / "base"
        etc_dir = base_path / "etc"
        etc_dir.mkdir(parents=True)
        (etc_dir / "os-release").write_text("ID=ubuntu\n")

        overlay_path = tmp_path / "overlays"
        overlay_path.mkdir()

        config = NspawnConfig(
            base_image_path=str(base_path),
            overlay_path=str(overlay_path),
        )
        validator = NspawnValidator(config=config)

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with validator._create_ephemeral_container() as container_path:
                assert container_path is not None
                assert "merged" in str(container_path)

            # mount and umount should both have been called
            assert mock_run.call_count >= 2

    def test_container_mount_failure(self, tmp_path):
        """Test container creation when mount command fails."""
        import subprocess as sp

        base_path = tmp_path / "base"
        etc_dir = base_path / "etc"
        etc_dir.mkdir(parents=True)
        (etc_dir / "os-release").write_text("ID=ubuntu\n")

        config = NspawnConfig(base_image_path=str(base_path))
        validator = NspawnValidator(config=config)

        with patch(
            "elle.ops.preflight.nspawn_validator.subprocess.run",
            side_effect=sp.CalledProcessError(1, "mount"),
        ):
            with pytest.raises(sp.CalledProcessError):
                with validator._create_ephemeral_container():
                    pass

    def test_container_cleanup_umount_exception(self, tmp_path):
        """Test that umount exception in cleanup is handled gracefully."""
        base_path = tmp_path / "base"
        etc_dir = base_path / "etc"
        etc_dir.mkdir(parents=True)
        (etc_dir / "os-release").write_text("ID=ubuntu\n")

        config = NspawnConfig(base_image_path=str(base_path))
        validator = NspawnValidator(config=config)

        call_count = 0

        def run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # mount succeeds
                return MagicMock(returncode=0)
            else:
                # umount fails
                raise OSError("umount failed")

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", side_effect=run_side_effect):
            with validator._create_ephemeral_container() as container_path:
                assert container_path is not None
            # Should not raise; cleanup exceptions are logged

    def test_container_cleanup_rmtree_exception(self, tmp_path):
        """Test that rmtree exception in cleanup is handled gracefully."""
        base_path = tmp_path / "base"
        etc_dir = base_path / "etc"
        etc_dir.mkdir(parents=True)
        (etc_dir / "os-release").write_text("ID=ubuntu\n")

        config = NspawnConfig(base_image_path=str(base_path))
        validator = NspawnValidator(config=config)

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=MagicMock(returncode=0)):
            with patch("elle.ops.preflight.nspawn_validator.shutil.rmtree", side_effect=OSError("Permission denied")):
                with validator._create_ephemeral_container() as container_path:
                    assert container_path is not None
                # Should not raise; cleanup exceptions are logged

    def test_container_overlay_path_not_exists(self, tmp_path):
        """Test container creation when overlay_path does not exist (falls back to None)."""
        base_path = tmp_path / "base"
        etc_dir = base_path / "etc"
        etc_dir.mkdir(parents=True)
        (etc_dir / "os-release").write_text("ID=ubuntu\n")

        config = NspawnConfig(
            base_image_path=str(base_path),
            overlay_path="/nonexistent/path/for/overlays",
        )
        validator = NspawnValidator(config=config)

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=MagicMock(returncode=0)):
            with validator._create_ephemeral_container() as container_path:
                assert container_path is not None


class TestRunInContainer:
    """Tests for _run_in_container method."""

    def test_run_success(self):
        """Test successful command execution in container."""
        validator = NspawnValidator()
        mock_result = MagicMock(returncode=0, stdout="output", stderr="")

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=mock_result):
            code, stdout, stderr = validator._run_in_container(Path("/tmp/container"), "echo hello")
        assert code == 0
        assert stdout == "output"
        assert stderr == ""

    def test_run_failure(self):
        """Test failed command execution in container."""
        validator = NspawnValidator()
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=mock_result):
            code, stdout, stderr = validator._run_in_container(Path("/tmp/container"), "bad command")
        assert code == 1
        assert stderr == "error"

    def test_run_timeout(self):
        """Test command timeout in container."""
        import subprocess as sp

        validator = NspawnValidator()

        with patch(
            "elle.ops.preflight.nspawn_validator.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="nspawn", timeout=60),
        ):
            code, stdout, stderr = validator._run_in_container(Path("/tmp/container"), "slow cmd", timeout_sec=60)
        assert code == -1
        assert "timed out" in stderr

    def test_run_generic_exception(self):
        """Test generic exception during command execution in container."""
        validator = NspawnValidator()

        with patch(
            "elle.ops.preflight.nspawn_validator.subprocess.run",
            side_effect=OSError("nspawn not found"),
        ):
            code, stdout, stderr = validator._run_in_container(Path("/tmp/container"), "echo hello")
        assert code == -1
        assert "nspawn not found" in stderr

    def test_run_network_disabled(self):
        """Test command execution with network disabled (no --network-veth)."""
        config = NspawnConfig(network_enabled=False)
        validator = NspawnValidator(config=config)
        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=mock_result) as mock_run:
            code, stdout, stderr = validator._run_in_container(Path("/tmp/container"), "echo hello")
        assert code == 0
        # Verify --network-veth was not in the command
        call_args = mock_run.call_args[0][0]
        assert "--network-veth" not in call_args

    def test_run_network_enabled(self):
        """Test command execution with network enabled (includes --network-veth)."""
        config = NspawnConfig(network_enabled=True)
        validator = NspawnValidator(config=config)
        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("elle.ops.preflight.nspawn_validator.subprocess.run", return_value=mock_result) as mock_run:
            code, stdout, stderr = validator._run_in_container(Path("/tmp/container"), "echo hello")
        assert code == 0
        call_args = mock_run.call_args[0][0]
        assert "--network-veth" in call_args


class TestValidateWarningPath:
    """Tests for validate method warning-only path (no errors, but warnings present)."""

    def test_validate_warning_status(self):
        """Test validate returns WARNING when apt-update fails but install succeeds."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    # apt-get update fails (WARNING), apt-get install succeeds, service starts OK
                    with patch.object(
                        validator,
                        "_run_in_container",
                        side_effect=[
                            (1, "", "Could not resolve repo"),  # apt update fails -> WARNING
                            (0, "Installed", ""),  # apt install succeeds
                            (0, "", ""),  # service start succeeds
                        ],
                    ):
                        test = PreflightTest(
                            packages=("nginx",),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.WARNING
                        assert result.can_proceed is True
                        assert any(i.severity == IssueSeverity.WARNING for i in result.issues)


class TestValidateMultiplePackages:
    """Tests for validate with multiple packages and service validation."""

    def test_validate_multiple_packages_all_services_start(self):
        """Test validate with multiple packages where all services start."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    with patch.object(
                        validator,
                        "_run_in_container",
                        side_effect=[
                            (0, "Updated", ""),  # apt update
                            (0, "Installed", ""),  # apt install
                            (0, "", ""),  # service nginx start
                            (0, "", ""),  # service redis-server start
                        ],
                    ):
                        test = PreflightTest(
                            packages=("nginx", "redis-server"),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.SAFE
                        assert result.can_proceed is True
                        assert result.nspawn_output is not None

    def test_validate_service_start_with_empty_stderr(self):
        """Test service start failure with empty stderr produces None technical_detail."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    with patch.object(
                        validator,
                        "_run_in_container",
                        side_effect=[
                            (0, "Updated", ""),  # apt update
                            (0, "Installed", ""),  # apt install
                            (1, "", ""),  # service start fails with empty stderr
                        ],
                    ):
                        test = PreflightTest(
                            packages=("nginx",),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.BLOCKED
                        # The issue for service failure should have None technical_detail
                        service_issue = [i for i in result.issues if "failed to start" in i.message.lower()][0]
                        assert service_issue.technical_detail is None

    def test_validate_install_failure_empty_stderr(self):
        """Test install failure with empty stderr produces 'Unknown error' technical_detail."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    with patch.object(
                        validator,
                        "_run_in_container",
                        side_effect=[
                            (0, "Updated", ""),  # apt update
                            (1, "", ""),  # apt install fails with empty stderr
                        ],
                    ):
                        test = PreflightTest(
                            packages=("bad-pkg",),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.BLOCKED
                        install_issue = [i for i in result.issues if "installation failed" in i.message.lower()][0]
                        assert install_issue.technical_detail == "Unknown error"

    def test_validate_install_failure_long_stderr(self):
        """Test install failure with long stderr is truncated to 1000 chars."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        long_error = "x" * 2000

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(type(validator), "has_base_image", new_callable=lambda: property(lambda self: True)):
                with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                    with patch.object(
                        validator,
                        "_run_in_container",
                        side_effect=[
                            (0, "Updated", ""),  # apt update
                            (1, "", long_error),  # apt install fails with long stderr
                        ],
                    ):
                        test = PreflightTest(
                            packages=("bad-pkg",),
                            operation="install",
                        )
                        result = validator.validate(test)

                        assert result.status == PreflightStatus.BLOCKED
                        install_issue = [i for i in result.issues if "installation failed" in i.message.lower()][0]
                        assert len(install_issue.technical_detail) == 1000


class TestValidateBaseImageAutoPrep:
    """Tests for validate auto-preparing base image when missing."""

    def test_validate_prepares_base_image_on_demand(self):
        """Test validate calls prepare_base_image and succeeds."""
        validator = NspawnValidator()

        mock_container_cm = MagicMock()
        mock_container_cm.__enter__ = MagicMock(return_value=Path("/tmp/container"))
        mock_container_cm.__exit__ = MagicMock(return_value=False)

        has_base_calls = [False, True]  # First call False, after prepare True

        with patch.object(type(validator), "is_available", new_callable=lambda: property(lambda self: True)):
            with patch.object(
                type(validator),
                "has_base_image",
                new_callable=lambda: property(lambda self: has_base_calls.pop(0) if has_base_calls else True),
            ):
                with patch.object(validator, "prepare_base_image", return_value=True):
                    with patch.object(validator, "_create_ephemeral_container", return_value=mock_container_cm):
                        with patch.object(validator, "_run_in_container", return_value=(0, "ok", "")):
                            test = PreflightTest(
                                packages=("curl",),
                                operation="install",
                            )
                            result = validator.validate(test)
                            assert result.can_proceed is True


class TestGetPackageServicesUnmapped:
    """Tests for _get_package_services with unmapped packages."""

    def test_unmapped_package_returns_base_name(self):
        """Test unmapped package with version returns base name as service."""
        services = _get_package_services("my-custom-app=1.0.0")
        assert services == ["my-custom-app"]

    def test_unmapped_package_with_suite_returns_base_name(self):
        """Test unmapped package with suite returns base name as service."""
        services = _get_package_services("my-custom-app/noble")
        assert services == ["my-custom-app"]

    def test_openssh_server_services(self):
        """Test openssh-server returns multiple services."""
        services = _get_package_services("openssh-server")
        assert "ssh" in services
        assert "sshd" in services
