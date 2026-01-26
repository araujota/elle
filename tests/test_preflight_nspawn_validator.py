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
