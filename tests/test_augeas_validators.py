"""Tests for configuration validators."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.ops.augeas.validators import (
    ApacheValidator,
    CronValidator,
    FstabValidator,
    HostsValidator,
    NetplanValidator,
    NginxValidator,
    NoOpValidator,
    SshdValidator,
    SudoersValidator,
    SysctlValidator,
    ValidationResult,
    get_validation_command,
    get_validator,
    list_validatable_domains,
    list_validators,
    validate_config,
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


# ---------------------------------------------------------------------------
# Extended: _run_command on ConfigValidator base class
# ---------------------------------------------------------------------------


class TestRunCommand:
    """Tests for ConfigValidator._run_command."""

    def test_run_command_timeout(self) -> None:
        """_run_command should handle timeout."""
        validator = FstabValidator()
        with patch("elle.ops.augeas.validators.subprocess.run") as mock_run:
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["test"], timeout=30)
            exit_code, stdout, stderr = validator._run_command(["test"])
            assert exit_code == -1
            assert "timed out" in stderr

    def test_run_command_not_found(self) -> None:
        """_run_command should handle FileNotFoundError."""
        validator = FstabValidator()
        with patch("elle.ops.augeas.validators.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            exit_code, stdout, stderr = validator._run_command(["nonexistent"])
            assert exit_code == -1
            assert "not found" in stderr

    def test_run_command_generic_exception(self) -> None:
        """_run_command should handle generic exceptions."""
        validator = FstabValidator()
        with patch("elle.ops.augeas.validators.subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("unexpected")
            exit_code, stdout, stderr = validator._run_command(["test"])
            assert exit_code == -1
            assert "unexpected" in stderr

    def test_run_command_success(self) -> None:
        """_run_command should return proper values on success."""
        validator = FstabValidator()
        with patch("elle.ops.augeas.validators.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            exit_code, stdout, stderr = validator._run_command(["test"])
            assert exit_code == 0
            assert stdout == "ok"


# ---------------------------------------------------------------------------
# Extended: FstabValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFstabValidatorValidate:
    async def test_validate_default_fstab(self) -> None:
        """Should use mount -a -f --fake for /etc/fstab."""
        validator = FstabValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = await validator.validate("/etc/fstab")
            assert result.valid is True
            assert "mount" in result.command
            # Should NOT have --fstab flag for default location
            assert "--fstab" not in result.command

    async def test_validate_custom_fstab_path(self) -> None:
        """Should use --fstab flag for non-standard locations."""
        validator = FstabValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = await validator.validate("/tmp/test_fstab")
            assert result.valid is True
            assert "--fstab" in result.command

    async def test_validate_fstab_failure(self) -> None:
        """Should return invalid on failure."""
        validator = FstabValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (1, "", "mount: bad line")
            result = await validator.validate("/etc/fstab")
            assert result.valid is False

    async def test_validate_fstab_unknown_fs_warning(self) -> None:
        """Should add warning for unknown filesystem type."""
        validator = FstabValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "Unknown filesystem type")
            result = await validator.validate("/etc/fstab")
            assert result.valid is True
            assert len(result.warnings) > 0


# ---------------------------------------------------------------------------
# Extended: SshdValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSshdValidatorValidate:
    async def test_validate_sshd_success(self) -> None:
        """Should return valid on sshd -t success."""
        validator = SshdValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = await validator.validate("/etc/ssh/sshd_config")
            assert result.valid is True
            assert "sshd" in result.command

    async def test_validate_sshd_failure(self) -> None:
        """Should return invalid on sshd -t failure."""
        validator = SshdValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (1, "", "/etc/ssh/sshd_config: line 5: Bad configuration")
            result = await validator.validate("/etc/ssh/sshd_config")
            assert result.valid is False
            assert result.error != ""


# ---------------------------------------------------------------------------
# Extended: SysctlValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSysctlValidatorValidate:
    async def test_validate_sysctl_success(self) -> None:
        """Should return valid on sysctl success."""
        validator = SysctlValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "vm.swappiness = 60", "")
            result = await validator.validate("/etc/sysctl.conf")
            assert result.valid is True

    async def test_validate_sysctl_dry_run_unsupported(self) -> None:
        """Should fallback when --dry-run is not supported."""
        validator = SysctlValidator()
        with patch.object(validator, "_run_command") as mock_run:
            # First call: --dry-run fails with unrecognized option
            # Second call: fallback succeeds
            mock_run.side_effect = [
                (1, "", "sysctl: unrecognized option '--dry-run'"),
                (0, "vm.swappiness = 60", ""),
            ]
            result = await validator.validate("/etc/sysctl.conf")
            assert result.valid is True

    async def test_validate_sysctl_cannot_stat_warning(self) -> None:
        """Should add warning for 'cannot stat' in stderr."""
        validator = SysctlValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "cannot stat /proc/sys/test")
            result = await validator.validate("/etc/sysctl.conf")
            assert result.valid is True
            assert len(result.warnings) > 0


# ---------------------------------------------------------------------------
# Extended: NetplanValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNetplanValidatorValidate:
    async def test_validate_netplan_success(self) -> None:
        """Should return valid on success."""
        validator = NetplanValidator()
        assert validator.name == "netplan"
        assert "netplan" in validator.domains
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "DEBUG: generated config")
            result = await validator.validate("/etc/netplan/01-config.yaml")
            assert result.valid is True

    async def test_validate_netplan_failure(self) -> None:
        """Should return invalid on failure."""
        validator = NetplanValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (1, "", "Error in network definition")
            result = await validator.validate("/etc/netplan/01-config.yaml")
            assert result.valid is False


# ---------------------------------------------------------------------------
# Extended: NginxValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNginxValidatorValidate:
    async def test_validate_nginx_success(self) -> None:
        """Should return valid when syntax is ok."""
        validator = NginxValidator()
        assert validator.name == "nginx"
        assert "nginx" in validator.domains
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (
                0,
                "",
                "nginx: the configuration file syntax is ok\nnginx: configuration file test is successful",
            )
            result = await validator.validate("/etc/nginx/nginx.conf")
            assert result.valid is True

    async def test_validate_nginx_failure(self) -> None:
        """Should return invalid when syntax fails."""
        validator = NginxValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (1, "", "nginx: emerg: unexpected end of file")
            result = await validator.validate("/etc/nginx/nginx.conf")
            assert result.valid is False

    async def test_validate_nginx_no_syntax_ok(self) -> None:
        """Should return invalid even with exit 0 if 'syntax is ok' missing."""
        validator = NginxValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "something else")
            result = await validator.validate("/etc/nginx/nginx.conf")
            assert result.valid is False


# ---------------------------------------------------------------------------
# Extended: ApacheValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApacheValidatorValidate:
    async def test_validate_apache_success(self) -> None:
        """Should return valid on apachectl success."""
        validator = ApacheValidator()
        assert validator.name == "apache"
        assert "apache" in validator.domains
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "Syntax OK", "")
            result = await validator.validate("/etc/apache2/apache2.conf")
            assert result.valid is True

    async def test_validate_apache_fallback_httpd(self) -> None:
        """Should fallback to httpd -t when apachectl not found."""
        validator = ApacheValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.side_effect = [
                (-1, "", "Validator command not found: apachectl"),
                (0, "Syntax OK", ""),
            ]
            result = await validator.validate("/etc/httpd/httpd.conf")
            assert result.valid is True

    async def test_validate_apache_both_fail(self) -> None:
        """Should return invalid when both commands fail."""
        validator = ApacheValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.side_effect = [
                (-1, "", "Validator command not found: apachectl"),
                (1, "", "httpd: Syntax error on line 5"),
            ]
            result = await validator.validate("/etc/httpd/httpd.conf")
            assert result.valid is False


# ---------------------------------------------------------------------------
# Extended: HostsValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHostsValidatorValidate:
    async def test_validate_hosts_success(self, tmp_path) -> None:
        """Should return valid for well-formed hosts file."""
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n::1 localhost\n")
        validator = HostsValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "127.0.0.1 localhost", "")
            result = await validator.validate(str(hosts))
            assert result.valid is True

    async def test_validate_hosts_invalid_line(self, tmp_path) -> None:
        """Should warn about invalid lines."""
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\nbadline\n")
        validator = HostsValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "127.0.0.1 localhost", "")
            result = await validator.validate(str(hosts))
            assert result.valid is False  # warnings make it invalid
            assert len(result.warnings) > 0

    async def test_validate_hosts_comment_and_empty(self, tmp_path) -> None:
        """Should skip comments and empty lines."""
        hosts = tmp_path / "hosts"
        hosts.write_text("# comment\n\n127.0.0.1 localhost\n")
        validator = HostsValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "127.0.0.1 localhost", "")
            result = await validator.validate(str(hosts))
            assert result.valid is True

    async def test_validate_hosts_unreadable_file(self) -> None:
        """Should handle file read errors."""
        validator = HostsValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = await validator.validate("/nonexistent/hosts")
            assert result.valid is False
            assert "Failed to read" in result.error


# ---------------------------------------------------------------------------
# Extended: CronValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCronValidatorValidate:
    async def test_validate_cron_valid(self, tmp_path) -> None:
        """Should return valid for well-formed crontab."""
        crontab = tmp_path / "crontab"
        crontab.write_text("# comment\n0 5 * * * /usr/bin/backup\n")
        validator = CronValidator()
        result = await validator.validate(str(crontab))
        assert result.valid is True
        assert "passed" in result.output.lower()

    async def test_validate_cron_env_vars(self, tmp_path) -> None:
        """Should handle environment variable lines."""
        crontab = tmp_path / "crontab"
        crontab.write_text("SHELL=/bin/bash\n0 5 * * * /usr/bin/backup\n")
        validator = CronValidator()
        result = await validator.validate(str(crontab))
        assert result.valid is True

    async def test_validate_cron_not_enough_fields(self, tmp_path) -> None:
        """Should error on lines with too few fields."""
        crontab = tmp_path / "crontab"
        crontab.write_text("0 5 *\n")
        validator = CronValidator()
        result = await validator.validate(str(crontab))
        assert result.valid is False
        assert "Not enough fields" in result.error

    async def test_validate_cron_unreadable_file(self) -> None:
        """Should handle file read errors."""
        validator = CronValidator()
        result = await validator.validate("/nonexistent/crontab")
        assert result.valid is False
        assert "Failed to read" in result.error


# ---------------------------------------------------------------------------
# Extended: CronValidator._validate_cron_field edge cases
# ---------------------------------------------------------------------------


class TestCronFieldValidationExtended:
    def test_invalid_minute_too_high(self) -> None:
        """Minute > 59 should fail."""
        validator = CronValidator()
        assert validator._validate_cron_field("60", 0) is False

    def test_invalid_hour_too_high(self) -> None:
        """Hour > 23 should fail."""
        validator = CronValidator()
        assert validator._validate_cron_field("24", 1) is False

    def test_negative_treated_as_range(self) -> None:
        """Negative values are split by '-' so treated as ranges (returns True)."""
        validator = CronValidator()
        # "-1" splits to ["", "1"] -- "" is non-numeric, returns True
        assert validator._validate_cron_field("-1", 0) is True

    def test_valid_step_values(self) -> None:
        """Step values like */5 should be valid."""
        validator = CronValidator()
        assert validator._validate_cron_field("*/5", 0) is True

    def test_month_name(self) -> None:
        """Month names should be treated as valid."""
        validator = CronValidator()
        # Non-numeric values return True (could be month/day name)
        assert validator._validate_cron_field("jan", 3) is True

    def test_weekday_field_7(self) -> None:
        """Weekday 7 should be valid (same as 0)."""
        validator = CronValidator()
        assert validator._validate_cron_field("7", 4) is True


# ---------------------------------------------------------------------------
# Extended: SudoersValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSudoersValidatorValidate:
    async def test_validate_sudoers_success(self) -> None:
        """Should return valid on visudo success."""
        validator = SudoersValidator()
        assert validator.name == "sudoers"
        assert "security" in validator.domains
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (0, "/etc/sudoers: parsed OK", "")
            result = await validator.validate("/etc/sudoers")
            assert result.valid is True

    async def test_validate_sudoers_failure(self) -> None:
        """Should return invalid on visudo failure."""
        validator = SudoersValidator()
        with patch.object(validator, "_run_command") as mock_run:
            mock_run.return_value = (1, "", ">>> /etc/sudoers: syntax error near line 5")
            result = await validator.validate("/etc/sudoers")
            assert result.valid is False
            assert result.error != ""


# ---------------------------------------------------------------------------
# Extended: validate_config top-level function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestValidateConfig:
    async def test_validate_config_dispatches_correctly(self) -> None:
        """validate_config should dispatch to the correct validator."""
        with patch("elle.ops.augeas.validators.get_validator") as mock_get:
            mock_validator = MagicMock()
            mock_validator.validate = MagicMock(return_value=ValidationResult(valid=True, validator="mock"))
            # Make validate a coroutine

            async def mock_validate(fp):
                return ValidationResult(valid=True, validator="mock")

            mock_validator.validate = mock_validate
            mock_get.return_value = mock_validator
            result = await validate_config("/etc/fstab")
            assert result.valid is True


# ---------------------------------------------------------------------------
# Extended: get_validation_command more domains
# ---------------------------------------------------------------------------


class TestGetValidationCommandExtended:
    def test_sysctl_command(self) -> None:
        """Should return sysctl command."""
        cmd = get_validation_command("/etc/sysctl.conf")
        assert cmd is not None
        assert "sysctl" in cmd

    def test_nginx_command(self) -> None:
        """Should return nginx -t command."""
        with patch("elle.ops.augeas.validators.detect_domain", return_value="nginx"):
            cmd = get_validation_command("/etc/nginx/nginx.conf")
            assert cmd is not None
            assert "nginx" in cmd

    def test_apache_command(self) -> None:
        """Should return apachectl command."""
        with patch("elle.ops.augeas.validators.detect_domain", return_value="apache"):
            cmd = get_validation_command("/etc/apache2/apache2.conf")
            assert cmd is not None
            assert "apachectl" in cmd

    def test_security_command(self) -> None:
        """Should return visudo command."""
        with patch("elle.ops.augeas.validators.detect_domain", return_value="security"):
            cmd = get_validation_command("/etc/sudoers")
            assert cmd is not None
            assert "visudo" in cmd

    def test_netplan_command(self) -> None:
        """Should return netplan generate command."""
        with patch("elle.ops.augeas.validators.detect_domain", return_value="netplan"):
            cmd = get_validation_command("/etc/netplan/config.yaml")
            assert cmd is not None
            assert "netplan" in cmd


# ---------------------------------------------------------------------------
# Extended: NoOpValidator domain customization
# ---------------------------------------------------------------------------


class TestNoOpValidatorExtended:
    def test_custom_domain(self) -> None:
        """NoOpValidator should use the provided domain."""
        validator = NoOpValidator("custom_domain")
        assert "custom_domain" in validator.domains

    def test_default_domain(self) -> None:
        """NoOpValidator should default to 'other'."""
        validator = NoOpValidator()
        assert "other" in validator.domains


# ---------------------------------------------------------------------------
# Extended: get_validator additional paths
# ---------------------------------------------------------------------------


class TestGetValidatorExtended:
    def test_get_cron_validator(self) -> None:
        """Should return CronValidator for crontab."""
        with patch("elle.ops.augeas.validators.detect_domain", return_value="cron"):
            validator = get_validator("/etc/crontab")
            assert isinstance(validator, CronValidator)

    def test_get_nginx_validator(self) -> None:
        """Should return NginxValidator for nginx config."""
        with patch("elle.ops.augeas.validators.detect_domain", return_value="nginx"):
            validator = get_validator("/etc/nginx/nginx.conf")
            assert isinstance(validator, NginxValidator)

    def test_get_netplan_validator(self) -> None:
        """Should return NetplanValidator for netplan config."""
        with patch("elle.ops.augeas.validators.detect_domain", return_value="netplan"):
            validator = get_validator("/etc/netplan/config.yaml")
            assert isinstance(validator, NetplanValidator)
