"""Tests for configuration validators."""

from __future__ import annotations

import pytest

from elle.ops.augeas.validators import (
    CronValidator,
    FstabValidator,
    HostsValidator,
    NoOpValidator,
    SshdValidator,
    SysctlValidator,
    ValidationResult,
    get_validation_command,
    get_validator,
    list_validatable_domains,
    list_validators,
)


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_valid_result(self) -> None:
        """Valid result should have valid=True."""
        result = ValidationResult(valid=True, validator="test")

        assert result.valid is True
        assert result.exit_code == 0

    def test_invalid_result(self) -> None:
        """Invalid result should have valid=False and error."""
        result = ValidationResult(
            valid=False,
            validator="test",
            error="Syntax error",
            exit_code=1,
        )

        assert result.valid is False
        assert result.error == "Syntax error"
        assert result.exit_code == 1

    def test_warnings(self) -> None:
        """Warnings should be included in result."""
        result = ValidationResult(
            valid=True,
            validator="test",
            warnings=("Warning 1", "Warning 2"),
        )

        assert len(result.warnings) == 2
        assert "Warning 1" in result.warnings

    def test_frozen(self) -> None:
        """ValidationResult should be immutable."""
        result = ValidationResult(valid=True, validator="test")

        with pytest.raises(Exception):
            result.valid = False  # type: ignore[misc]


class TestValidatorProperties:
    """Tests for validator properties."""

    def test_fstab_validator_properties(self) -> None:
        """FstabValidator should have correct properties."""
        validator = FstabValidator()

        assert validator.name == "fstab"
        assert "fstab" in validator.domains

    def test_sshd_validator_properties(self) -> None:
        """SshdValidator should have correct properties."""
        validator = SshdValidator()

        assert validator.name == "sshd"
        assert "ssh" in validator.domains

    def test_sysctl_validator_properties(self) -> None:
        """SysctlValidator should have correct properties."""
        validator = SysctlValidator()

        assert validator.name == "sysctl"
        assert "sysctl" in validator.domains

    def test_hosts_validator_properties(self) -> None:
        """HostsValidator should have correct properties."""
        validator = HostsValidator()

        assert validator.name == "hosts"
        assert "network" in validator.domains

    def test_cron_validator_properties(self) -> None:
        """CronValidator should have correct properties."""
        validator = CronValidator()

        assert validator.name == "cron"
        assert "cron" in validator.domains

    def test_noop_validator_properties(self) -> None:
        """NoOpValidator should pass through."""
        validator = NoOpValidator("custom")

        assert validator.name == "noop"
        assert "custom" in validator.domains


class TestGetValidator:
    """Tests for get_validator function."""

    def test_get_fstab_validator(self) -> None:
        """Should return FstabValidator for /etc/fstab."""
        validator = get_validator("/etc/fstab")

        assert isinstance(validator, FstabValidator)

    def test_get_sshd_validator(self) -> None:
        """Should return SshdValidator for sshd_config."""
        validator = get_validator("/etc/ssh/sshd_config")

        assert isinstance(validator, SshdValidator)

    def test_get_sysctl_validator(self) -> None:
        """Should return SysctlValidator for sysctl.conf."""
        validator = get_validator("/etc/sysctl.conf")

        assert isinstance(validator, SysctlValidator)

    def test_get_hosts_validator(self) -> None:
        """Should return HostsValidator for /etc/hosts."""
        validator = get_validator("/etc/hosts")

        assert isinstance(validator, HostsValidator)

    def test_get_unknown_returns_noop(self) -> None:
        """Unknown files should get NoOpValidator."""
        validator = get_validator("/some/unknown/file.conf")

        assert isinstance(validator, NoOpValidator)


class TestGetValidationCommand:
    """Tests for get_validation_command function."""

    def test_fstab_command(self) -> None:
        """Should return mount command for fstab."""
        cmd = get_validation_command("/etc/fstab")

        assert cmd is not None
        assert "mount" in cmd

    def test_sshd_command(self) -> None:
        """Should return sshd command for sshd_config."""
        cmd = get_validation_command("/etc/ssh/sshd_config")

        assert cmd is not None
        assert "sshd" in cmd

    def test_unknown_returns_none(self) -> None:
        """Unknown files should return None."""
        cmd = get_validation_command("/some/unknown/file")

        assert cmd is None


class TestListFunctions:
    """Tests for list functions."""

    def test_list_validators(self) -> None:
        """Should return list of validator names."""
        validators = list_validators()

        assert isinstance(validators, list)
        assert len(validators) > 0
        assert "fstab" in validators
        assert "sshd" in validators

    def test_list_validatable_domains(self) -> None:
        """Should return list of validatable domains."""
        domains = list_validatable_domains()

        assert isinstance(domains, list)
        assert len(domains) > 0
        assert "fstab" in domains
        assert "ssh" in domains


class TestCronFieldValidation:
    """Tests for cron field validation."""

    def test_validate_cron_field_asterisk(self) -> None:
        """Asterisk should be valid for any field."""
        validator = CronValidator()

        assert validator._validate_cron_field("*", 0) is True
        assert validator._validate_cron_field("*", 4) is True

    def test_validate_cron_field_valid_numbers(self) -> None:
        """Valid numbers should pass."""
        validator = CronValidator()

        # Minutes (0-59)
        assert validator._validate_cron_field("0", 0) is True
        assert validator._validate_cron_field("59", 0) is True

        # Hours (0-23)
        assert validator._validate_cron_field("0", 1) is True
        assert validator._validate_cron_field("23", 1) is True

        # Days (1-31)
        assert validator._validate_cron_field("1", 2) is True
        assert validator._validate_cron_field("31", 2) is True

    def test_validate_cron_field_ranges(self) -> None:
        """Ranges should be validated."""
        validator = CronValidator()

        assert validator._validate_cron_field("0-59", 0) is True
        assert validator._validate_cron_field("1,15,30", 0) is True


@pytest.mark.asyncio
class TestNoOpValidator:
    """Tests for NoOpValidator async behavior."""

    async def test_noop_always_valid(self) -> None:
        """NoOpValidator should always return valid."""
        validator = NoOpValidator()
        result = await validator.validate("/any/file")

        assert result.valid is True
        assert "No specific validator" in result.output

    async def test_noop_has_warning(self) -> None:
        """NoOpValidator should include a warning."""
        validator = NoOpValidator()
        result = await validator.validate("/any/file")

        assert len(result.warnings) > 0
        assert "validator" in result.warnings[0].lower()
