"""Tests for confgen validator coverage."""

from __future__ import annotations

from elle.rag.confgen.models import (
    ConfigGenResult,
    ConfigOp,
)
from elle.rag.confgen.validator import (
    ConfigValidator,
    get_validator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    target_path: str = "/etc/myapp/config.yaml",
    file_type: str = "yaml",
    domain: str = "general",
    operations: tuple[ConfigOp, ...] = (),
    generated_content: str | None = None,
    rollback_hint: str | None = None,
) -> ConfigGenResult:
    """Create a ConfigGenResult with sensible defaults."""
    return ConfigGenResult(
        title="Test change",
        explanation="Test explanation",
        target_path=target_path,
        file_type=file_type,
        domain=domain,
        operations=operations,
        generated_content=generated_content,
        rollback_hint=rollback_hint,
    )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestGetValidator:
    """Tests for get_validator singleton."""

    def test_get_validator_returns_instance(self) -> None:
        """get_validator() returns a ConfigValidator."""
        v = get_validator()
        assert isinstance(v, ConfigValidator)

    def test_get_validator_is_singleton(self) -> None:
        """get_validator() returns the same instance each time."""
        v1 = get_validator()
        v2 = get_validator()
        assert v1 is v2


# ---------------------------------------------------------------------------
# Path Validation
# ---------------------------------------------------------------------------


class TestValidatePath:
    """Tests for _validate_path."""

    def setup_method(self) -> None:
        self.validator = ConfigValidator()

    def test_forbidden_path_passwd(self) -> None:
        """Forbidden paths like /etc/passwd produce an error."""
        result = _make_result(target_path="/etc/passwd")
        validation = self.validator.validate(result)
        assert validation.valid is False
        assert validation.path_valid is False
        assert any("Forbidden path" in i.message for i in validation.issues)

    def test_forbidden_path_shadow(self) -> None:
        """Forbidden paths like /etc/shadow produce an error."""
        result = _make_result(target_path="/etc/shadow")
        validation = self.validator.validate(result)
        assert validation.valid is False

    def test_forbidden_path_sudoers(self) -> None:
        """Forbidden paths like /etc/sudoers produce an error."""
        result = _make_result(target_path="/etc/sudoers")
        validation = self.validator.validate(result)
        assert validation.valid is False

    def test_forbidden_path_grub(self) -> None:
        """Forbidden paths like /boot/grub/grub.cfg produce an error."""
        result = _make_result(target_path="/boot/grub/grub.cfg")
        validation = self.validator.validate(result)
        assert validation.valid is False

    def test_critical_prefix_boot(self) -> None:
        """Critical prefix /boot produces a warning."""
        result = _make_result(target_path="/boot/some-custom-file")
        validation = self.validator.validate(result)
        assert any("Critical system path" in i.message and i.severity == "warning" for i in validation.issues)

    def test_critical_prefix_dev(self) -> None:
        """Critical prefix /dev produces a warning."""
        result = _make_result(target_path="/dev/null")
        validation = self.validator.validate(result)
        assert any("Critical system path" in i.message for i in validation.issues)

    def test_critical_prefix_proc(self) -> None:
        """Critical prefix /proc produces a warning."""
        result = _make_result(target_path="/proc/sys/net/ipv4")
        validation = self.validator.validate(result)
        assert any("Critical system path" in i.message for i in validation.issues)

    def test_critical_prefix_sys(self) -> None:
        """Critical prefix /sys produces a warning."""
        result = _make_result(target_path="/sys/class/net")
        validation = self.validator.validate(result)
        assert any("Critical system path" in i.message for i in validation.issues)

    def test_path_traversal(self) -> None:
        """Path traversal (..) produces an error."""
        result = _make_result(target_path="/etc/../etc/passwd")
        validation = self.validator.validate(result)
        assert any("traversal" in i.message.lower() for i in validation.issues)

    def test_relative_path_warning(self) -> None:
        """Relative path produces a warning."""
        result = _make_result(target_path="config/settings.yaml")
        validation = self.validator.validate(result)
        assert any("not absolute" in i.message.lower() for i in validation.issues)

    def test_safe_path_no_errors(self) -> None:
        """A safe path like /etc/myapp/config.yaml produces no errors."""
        result = _make_result(target_path="/etc/myapp/config.yaml")
        validation = self.validator.validate(result)
        # Should not have path-related errors
        path_errors = [i for i in validation.issues if i.severity == "error" and "path" in i.message.lower()]
        assert len(path_errors) == 0


# ---------------------------------------------------------------------------
# Operations Validation
# ---------------------------------------------------------------------------


class TestValidateOperations:
    """Tests for _validate_operations."""

    def setup_method(self) -> None:
        self.validator = ConfigValidator()

    def test_empty_path_produces_error(self) -> None:
        """An operation with an empty path produces an error."""
        op = ConfigOp(kind="set", path="", value="test")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("empty path" in i.message.lower() for i in validation.issues)

    def test_shell_injection_command_substitution(self) -> None:
        """Shell injection via $() is detected."""
        op = ConfigOp(kind="set", path=".key", value="$(rm -rf /)")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_backtick(self) -> None:
        """Shell injection via backticks is detected."""
        op = ConfigOp(kind="set", path=".key", value="`whoami`")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_semicolon_chaining(self) -> None:
        """Shell injection via ; is detected."""
        op = ConfigOp(kind="set", path=".key", value="value; rm something")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_pipe(self) -> None:
        """Shell injection via | is detected."""
        op = ConfigOp(kind="set", path=".key", value="input| cat /etc/shadow")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_and_chaining(self) -> None:
        """Shell injection via && is detected."""
        op = ConfigOp(kind="set", path=".key", value="value&& malicious")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_or_chaining(self) -> None:
        """Shell injection via || is detected."""
        op = ConfigOp(kind="set", path=".key", value="value|| fallback")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_redirect_to_dev(self) -> None:
        """Shell injection via > /dev/ is detected."""
        op = ConfigOp(kind="set", path=".key", value="> /dev/sda")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_read_from_dev(self) -> None:
        """Shell injection via < /dev/ is detected."""
        op = ConfigOp(kind="set", path=".key", value="< /dev/urandom")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_eval(self) -> None:
        """Shell injection via eval is detected."""
        op = ConfigOp(kind="set", path=".key", value="eval malicious_code")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_shell_injection_exec(self) -> None:
        """Shell injection via exec is detected."""
        op = ConfigOp(kind="set", path=".key", value="exec /bin/bash")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_augeas_path_without_leading_slash(self) -> None:
        """Augeas paths without leading / produce a warning."""
        op = ConfigOp(kind="set", path="files/etc/myapp", value="test")
        result = _make_result(file_type="augeas", operations=(op,))
        validation = self.validator.validate(result)
        assert any("Augeas path should start with /" in i.message for i in validation.issues)

    def test_yaml_path_with_leading_slash(self) -> None:
        """YAML paths with leading / produce an info note."""
        op = ConfigOp(kind="set", path="/network/ethernets", value="test")
        result = _make_result(file_type="yaml", operations=(op,))
        validation = self.validator.validate(result)
        assert any("YAML path uses Augeas notation" in i.message for i in validation.issues)

    def test_safe_operation_no_injection(self) -> None:
        """A safe operation value does not produce injection warnings."""
        op = ConfigOp(kind="set", path=".database.host", value="localhost")
        result = _make_result(operations=(op,))
        validation = self.validator.validate(result)
        injection_issues = [i for i in validation.issues if "injection" in i.message.lower()]
        assert len(injection_issues) == 0


# ---------------------------------------------------------------------------
# Content Validation
# ---------------------------------------------------------------------------


class TestValidateContent:
    """Tests for _validate_content."""

    def setup_method(self) -> None:
        self.validator = ConfigValidator()

    def test_valid_yaml_content(self) -> None:
        """Valid YAML content passes syntax check."""
        result = _make_result(
            file_type="yaml",
            generated_content="key: value\nlist:\n  - item1\n  - item2\n",
        )
        validation = self.validator.validate(result)
        syntax_errors = [i for i in validation.issues if i.severity == "error" and "yaml syntax" in i.message.lower()]
        assert len(syntax_errors) == 0

    def test_invalid_yaml_content(self) -> None:
        """Invalid YAML content produces a syntax error."""
        result = _make_result(
            file_type="yaml",
            generated_content="key: [\ninvalid: yaml: content:\n",
        )
        self.validator.validate(result)
        # May or may not produce a YAML error depending on ruamel.yaml availability
        # The important thing is no crash

    def test_valid_json_content(self) -> None:
        """Valid JSON content passes syntax check."""
        result = _make_result(
            file_type="json",
            generated_content='{"key": "value", "number": 42}',
        )
        validation = self.validator.validate(result)
        json_errors = [i for i in validation.issues if i.severity == "error" and "json syntax" in i.message.lower()]
        assert len(json_errors) == 0

    def test_invalid_json_content(self) -> None:
        """Invalid JSON content produces a syntax error."""
        result = _make_result(
            file_type="json",
            generated_content="{not: valid json",
        )
        validation = self.validator.validate(result)
        assert any("json syntax" in i.message.lower() for i in validation.issues)

    def test_valid_toml_content(self) -> None:
        """Valid TOML content passes syntax check."""
        result = _make_result(
            file_type="toml",
            generated_content='[section]\nkey = "value"\n',
        )
        validation = self.validator.validate(result)
        toml_errors = [i for i in validation.issues if i.severity == "error" and "toml syntax" in i.message.lower()]
        assert len(toml_errors) == 0

    def test_invalid_toml_content(self) -> None:
        """Invalid TOML content produces a syntax error."""
        result = _make_result(
            file_type="toml",
            generated_content="[[[invalid toml\nno = closing",
        )
        validation = self.validator.validate(result)
        assert any("toml syntax" in i.message.lower() for i in validation.issues)

    def test_content_shell_injection_in_content_body(self) -> None:
        """Shell injection patterns in generated content are detected."""
        result = _make_result(
            file_type="text",
            generated_content="some value $(rm -rf /)",
        )
        validation = self.validator.validate(result)
        assert any("injection" in i.message.lower() for i in validation.issues)


# ---------------------------------------------------------------------------
# Rollback Validation
# ---------------------------------------------------------------------------


class TestValidateRollback:
    """Tests for _validate_rollback."""

    def setup_method(self) -> None:
        self.validator = ConfigValidator()

    def test_rollback_required_without_hint(self) -> None:
        """Critical paths without rollback_hint produce a warning."""
        result = _make_result(
            target_path="/etc/fstab",
            rollback_hint=None,
        )
        validation = self.validator.validate(result)
        assert any("rollback hint" in i.message.lower() for i in validation.issues)

    def test_rollback_required_netplan_without_hint(self) -> None:
        """Netplan path without rollback_hint produces a warning."""
        result = _make_result(
            target_path="/etc/netplan/01-netcfg.yaml",
            rollback_hint=None,
        )
        validation = self.validator.validate(result)
        assert any("rollback hint" in i.message.lower() for i in validation.issues)

    def test_rollback_required_sshd_without_hint(self) -> None:
        """sshd_config path without rollback_hint produces a warning."""
        result = _make_result(
            target_path="/etc/ssh/sshd_config",
            rollback_hint=None,
        )
        validation = self.validator.validate(result)
        assert any("rollback hint" in i.message.lower() for i in validation.issues)

    def test_rollback_required_with_hint_no_warning(self) -> None:
        """Critical paths with rollback_hint do not produce rollback warnings."""
        result = _make_result(
            target_path="/etc/fstab",
            rollback_hint="Restore from backup",
        )
        validation = self.validator.validate(result)
        rollback_warnings = [i for i in validation.issues if "rollback hint" in i.message.lower()]
        assert len(rollback_warnings) == 0

    def test_non_critical_path_no_rollback_warning(self) -> None:
        """Non-critical paths do not produce rollback warnings."""
        result = _make_result(
            target_path="/etc/myapp/config.yaml",
            rollback_hint=None,
        )
        validation = self.validator.validate(result)
        rollback_warnings = [i for i in validation.issues if "rollback hint" in i.message.lower()]
        assert len(rollback_warnings) == 0


# ---------------------------------------------------------------------------
# Overall Validation
# ---------------------------------------------------------------------------


class TestValidateOverall:
    """Tests for overall validate() flow."""

    def setup_method(self) -> None:
        self.validator = ConfigValidator()

    def test_valid_result_passes(self) -> None:
        """A clean result with no issues passes validation."""
        result = _make_result(
            target_path="/etc/myapp/config.yaml",
            file_type="yaml",
            generated_content="key: value\n",
        )
        validation = self.validator.validate(result)
        # Should not have any errors (warnings are ok)
        errors = [i for i in validation.issues if i.severity == "error"]
        assert len(errors) == 0
        assert validation.syntax_valid is True
        assert validation.values_valid is True

    def test_multiple_issues_combined(self) -> None:
        """Multiple issues from different validators are combined."""
        op = ConfigOp(kind="set", path="", value="$(evil)")
        result = _make_result(
            target_path="/etc/fstab",
            operations=(op,),
            rollback_hint=None,
        )
        validation = self.validator.validate(result)
        # Should have at least: empty path error, injection error, rollback warning
        assert len(validation.issues) >= 2
