"""Tests for confgen validator -- path safety, shell injection, domain and rollback checks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.rag.confgen.models import (
    ConfigGenResult,
    ConfigGenValidation,
    ConfigOp,
    ValidationIssue,
)
from elle.rag.confgen.validator import (
    CRITICAL_PREFIXES,
    FORBIDDEN_PATHS,
    ROLLBACK_REQUIRED_PATHS,
    ConfigValidator,
    get_validator,
    validate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(
    target_path: str = "/etc/app/config.yaml",
    operations: tuple[ConfigOp, ...] = (),
    generated_content: str | None = None,
    file_type: str = "text",
    domain: str = "general",
    rollback_hint: str | None = None,
) -> ConfigGenResult:
    """Shortcut to build a ConfigGenResult."""
    return ConfigGenResult(
        title="Test",
        explanation="Test change",
        target_path=target_path,
        operations=operations,
        generated_content=generated_content,
        file_type=file_type,
        domain=domain,
        rollback_hint=rollback_hint,
    )


def _op(kind: str = "set", path: str = "/test", value: str | None = None) -> ConfigOp:
    """Shortcut to build a ConfigOp."""
    return ConfigOp(kind=kind, path=path, value=value)


# ============================================================================
# _validate_path
# ============================================================================


class TestValidatePath:
    """Tests for path validation."""

    def test_forbidden_path_error(self) -> None:
        v = ConfigValidator()
        for fp in FORBIDDEN_PATHS:
            r = _result(target_path=fp)
            val = v.validate(r)
            assert val.valid is False, f"Expected forbidden path error for {fp}"
            assert val.path_valid is False

    def test_critical_prefix_warning(self) -> None:
        v = ConfigValidator()
        for prefix in CRITICAL_PREFIXES:
            r = _result(target_path=f"{prefix}/something")
            val = v.validate(r)
            # Should have a warning but not necessarily invalid
            warnings = [i for i in val.issues if i.severity == "warning"]
            assert any("critical" in w.message.lower() for w in warnings), (
                f"Expected critical path warning for {prefix}/something"
            )

    def test_path_traversal_error(self) -> None:
        v = ConfigValidator()
        r = _result(target_path="/etc/../etc/shadow")
        val = v.validate(r)
        assert val.valid is False
        assert any("traversal" in i.message.lower() for i in val.issues)

    def test_relative_path_warning(self) -> None:
        v = ConfigValidator()
        r = _result(target_path="config/app.yaml")
        val = v.validate(r)
        warnings = [i for i in val.issues if i.severity == "warning"]
        assert any("not absolute" in w.message.lower() for w in warnings)

    def test_valid_absolute_path(self) -> None:
        v = ConfigValidator()
        r = _result(target_path="/etc/myapp/config.yaml")
        val = v.validate(r)
        path_errors = [
            i for i in val.issues
            if i.severity == "error" and "path" in i.message.lower()
        ]
        assert len(path_errors) == 0


# ============================================================================
# _validate_operations
# ============================================================================


class TestValidateOperations:
    """Tests for operation validation."""

    def test_empty_path_error(self) -> None:
        v = ConfigValidator()
        op = ConfigOp(kind="set", path="", value="val")
        r = _result(operations=(op,))
        val = v.validate(r)
        assert any("empty path" in i.message.lower() for i in val.issues)

    def test_shell_injection_in_value(self) -> None:
        v = ConfigValidator()
        op = _op(value="$(rm -rf /)")
        r = _result(operations=(op,))
        val = v.validate(r)
        assert val.valid is False
        assert any("injection" in i.message.lower() for i in val.issues)

    def test_augeas_path_warning(self) -> None:
        v = ConfigValidator()
        op = _op(path="no_leading_slash")
        r = _result(operations=(op,), file_type="augeas")
        val = v.validate(r)
        warnings = [i for i in val.issues if i.severity == "warning"]
        assert any("augeas" in w.message.lower() for w in warnings)

    def test_yaml_path_info(self) -> None:
        v = ConfigValidator()
        op = _op(path="/files/etc/netplan/config")
        r = _result(operations=(op,), file_type="yaml")
        val = v.validate(r)
        infos = [i for i in val.issues if i.severity == "info"]
        assert any("yaml" in info.message.lower() for info in infos)

    def test_safe_operation_passes(self) -> None:
        v = ConfigValidator()
        op = _op(value="normal_value")
        r = _result(operations=(op,))
        val = v.validate(r)
        errors = [i for i in val.issues if i.severity == "error"]
        # No errors from the operation itself
        assert not any("injection" in e.message.lower() for e in errors)


# ============================================================================
# _validate_content
# ============================================================================


class TestValidateContent:
    """Tests for content validation."""

    def test_shell_injection_in_content(self) -> None:
        v = ConfigValidator()
        r = _result(generated_content="`whoami`", file_type="text")
        val = v.validate(r)
        assert val.valid is False
        assert val.values_valid is False

    def test_valid_yaml_content(self) -> None:
        v = ConfigValidator()
        r = _result(
            generated_content="network:\n  version: 2\n",
            file_type="yaml",
        )
        val = v.validate(r)
        # Should not have yaml syntax errors
        syntax_errors = [
            i for i in val.issues
            if i.severity == "error" and "syntax" in i.message.lower()
        ]
        assert len(syntax_errors) == 0

    def test_invalid_json_content(self) -> None:
        v = ConfigValidator()
        r = _result(generated_content="{bad json", file_type="json")
        val = v.validate(r)
        assert val.syntax_valid is False

    def test_valid_json_content(self) -> None:
        v = ConfigValidator()
        r = _result(generated_content='{"key": "value"}', file_type="json")
        val = v.validate(r)
        syntax_errors = [
            i for i in val.issues
            if i.severity == "error" and "syntax" in i.message.lower()
        ]
        assert len(syntax_errors) == 0

    def test_invalid_toml_content(self) -> None:
        v = ConfigValidator()
        r = _result(generated_content="[[[[invalid", file_type="toml")
        val = v.validate(r)
        assert val.syntax_valid is False

    def test_valid_toml_content(self) -> None:
        v = ConfigValidator()
        r = _result(
            generated_content='[section]\nkey = "value"\n',
            file_type="toml",
        )
        val = v.validate(r)
        syntax_errors = [
            i for i in val.issues
            if i.severity == "error" and "syntax" in i.message.lower()
        ]
        assert len(syntax_errors) == 0


# ============================================================================
# _validate_rollback
# ============================================================================


class TestValidateRollback:
    """Tests for rollback requirement checks."""

    def test_rollback_required_without_hint(self) -> None:
        v = ConfigValidator()
        for prefix in ROLLBACK_REQUIRED_PATHS:
            r = _result(target_path=f"{prefix}/some_file", rollback_hint=None)
            val = v.validate(r)
            warnings = [i for i in val.issues if i.severity == "warning"]
            assert any("rollback" in w.message.lower() for w in warnings), (
                f"Expected rollback warning for {prefix}/some_file"
            )

    def test_rollback_required_with_hint(self) -> None:
        v = ConfigValidator()
        r = _result(
            target_path="/etc/fstab",
            rollback_hint="cp /etc/fstab.bak /etc/fstab",
        )
        val = v.validate(r)
        warnings = [i for i in val.issues if i.severity == "warning"]
        rollback_warnings = [w for w in warnings if "rollback" in w.message.lower()]
        assert len(rollback_warnings) == 0

    def test_non_critical_path_no_rollback_warning(self) -> None:
        v = ConfigValidator()
        r = _result(target_path="/etc/myapp/config.yaml", rollback_hint=None)
        val = v.validate(r)
        warnings = [i for i in val.issues if i.severity == "warning"]
        rollback_warnings = [w for w in warnings if "rollback" in w.message.lower()]
        assert len(rollback_warnings) == 0


# ============================================================================
# _validate_domain
# ============================================================================


class TestValidateDomain:
    """Tests for domain-specific validation."""

    def test_domain_handler_called_for_operations(self) -> None:
        v = ConfigValidator()
        mock_handler = MagicMock()
        mock_handler.validate_operations.return_value = ConfigGenValidation(
            valid=True,
            issues=(
                ValidationIssue(severity="info", message="Domain check passed"),
            ),
        )
        op = _op(value="safe_value")
        r = _result(operations=(op,), domain="general")

        with patch("elle.rag.confgen.validator.get_handler", return_value=mock_handler):
            val = v.validate(r)
        mock_handler.validate_operations.assert_called_once()
        assert any("domain check" in i.message.lower() for i in val.issues)

    def test_domain_handler_called_for_content(self) -> None:
        v = ConfigValidator()
        mock_handler = MagicMock()
        mock_handler.validate_content.return_value = ConfigGenValidation(
            valid=True,
            issues=(),
        )
        r = _result(generated_content="content", domain="general")

        with patch("elle.rag.confgen.validator.get_handler", return_value=mock_handler):
            v.validate(r)
        mock_handler.validate_content.assert_called_once()


# ============================================================================
# _check_shell_injection
# ============================================================================


class TestShellInjection:
    """Tests for shell injection detection."""

    @pytest.mark.parametrize(
        "value,description",
        [
            ("$(rm -rf /)", "command substitution $()"),
            ("`whoami`", "backtick substitution"),
            ("value; rm -rf /", "command chaining with ;"),
            ("data | bash", "pipe to command"),
            ("ok && rm file", "command chaining with &&"),
            ("ok || rm file", "command chaining with ||"),
            ("> /dev/null", "redirect to device"),
            ("< /dev/urandom", "read from device"),
            ("eval dangerous", "eval command"),
            ("exec /bin/sh", "exec command"),
        ],
    )
    def test_injection_detected(self, value: str, description: str) -> None:
        v = ConfigValidator()
        issues = v._check_shell_injection(value, "test")
        assert len(issues) > 0, f"Expected injection detection for: {description}"
        assert issues[0].severity == "error"

    def test_safe_values(self) -> None:
        v = ConfigValidator()
        safe = [
            "normal_value",
            "192.168.1.1",
            "/usr/bin/python3",
            "true",
            "eth0",
            "my-service.timer",
        ]
        for val in safe:
            issues = v._check_shell_injection(val, "test")
            assert len(issues) == 0, f"False positive for safe value: {val}"


# ============================================================================
# Overall validation flags
# ============================================================================


class TestValidationFlags:
    """Tests for the overall validation result flags."""

    def test_valid_result(self) -> None:
        v = ConfigValidator()
        r = _result(target_path="/etc/myapp/config.yaml")
        val = v.validate(r)
        assert val.valid is True
        assert val.syntax_valid is True
        assert val.path_valid is True
        assert val.values_valid is True

    def test_syntax_invalid_flag(self) -> None:
        v = ConfigValidator()
        r = _result(
            target_path="/etc/myapp/config.json",
            generated_content="{bad",
            file_type="json",
        )
        val = v.validate(r)
        assert val.valid is False
        assert val.syntax_valid is False

    def test_path_invalid_flag(self) -> None:
        v = ConfigValidator()
        r = _result(target_path="/etc/shadow")
        val = v.validate(r)
        assert val.valid is False
        assert val.path_valid is False

    def test_values_invalid_flag(self) -> None:
        v = ConfigValidator()
        op = _op(value="$(bad)")
        r = _result(operations=(op,))
        val = v.validate(r)
        assert val.valid is False
        assert val.values_valid is False


# ============================================================================
# Module-level convenience functions
# ============================================================================


class TestModuleLevelFunctions:
    """Tests for get_validator() and validate()."""

    def test_get_validator_singleton(self) -> None:
        import elle.rag.confgen.validator as mod

        original = mod._validator
        try:
            mod._validator = None
            v1 = get_validator()
            v2 = get_validator()
            assert v1 is v2
            assert isinstance(v1, ConfigValidator)
        finally:
            mod._validator = original

    def test_validate_convenience(self) -> None:
        r = _result(target_path="/etc/myapp/config.yaml")
        val = validate(r)
        assert isinstance(val, ConfigGenValidation)
        assert val.valid is True

    def test_validate_with_current_content(self) -> None:
        r = _result(target_path="/etc/myapp/config.yaml")
        val = validate(r, current_content="existing content")
        assert isinstance(val, ConfigGenValidation)
