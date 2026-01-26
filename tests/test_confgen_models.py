"""Tests for confgen models."""

from __future__ import annotations

from pathlib import Path

import pytest

from elle.rag.confgen.models import (
    ConfigGenContext,
    ConfigGenError,
    ConfigGenLLMError,
    ConfigGenOutcome,
    ConfigGenRequest,
    ConfigGenResult,
    ConfigGenValidation,
    ConfigGenValidationError,
    ConfigOp,
    ValidationIssue,
)


class TestConfigOp:
    """Tests for ConfigOp model."""

    def test_basic_set_operation(self) -> None:
        """Test creating a set operation."""
        op = ConfigOp(
            kind="set",
            path="/files/etc/ssh/sshd_config/PermitRootLogin",
            value="no",
            explanation="Disable root login",
        )

        assert op.kind == "set"
        assert op.path == "/files/etc/ssh/sshd_config/PermitRootLogin"
        assert op.value == "no"
        assert op.explanation == "Disable root login"

    def test_remove_operation(self) -> None:
        """Test creating a remove operation."""
        op = ConfigOp(
            kind="rm",
            path="network.ethernets.eth0",
        )

        assert op.kind == "rm"
        assert op.value is None

    def test_insert_operation_with_position(self) -> None:
        """Test creating an insert operation with position."""
        op = ConfigOp(
            kind="ins",
            path="/files/etc/fstab/entry",
            value="/dev/sdb1 /mnt ext4 defaults 0 0",
            position="after",
        )

        assert op.kind == "ins"
        assert op.position == "after"

    def test_operation_immutability(self) -> None:
        """Test that ConfigOp is frozen/immutable."""
        op = ConfigOp(kind="set", path="/test", value="value")

        with pytest.raises(Exception):  # pydantic ValidationError
            op.value = "new value"


class TestConfigGenRequest:
    """Tests for ConfigGenRequest model."""

    def test_basic_request(self) -> None:
        """Test creating a basic request."""
        request = ConfigGenRequest(
            request="Set static IP 192.168.1.10 for eth0",
            target_path=Path("/etc/netplan/01-netcfg.yaml"),
        )

        assert request.request == "Set static IP 192.168.1.10 for eth0"
        assert request.mode == "edit"  # default
        assert request.domain is None

    def test_request_with_all_fields(self) -> None:
        """Test creating request with all fields."""
        request = ConfigGenRequest(
            request="Create new netplan config",
            mode="create",
            target_path=Path("/etc/netplan/99-elle.yaml"),
            domain="network",
            file_type="yaml",
            entities=("eth0", "192.168.1.10"),
        )

        assert request.mode == "create"
        assert request.domain == "network"
        assert request.file_type == "yaml"
        assert request.entities == ("eth0", "192.168.1.10")

    def test_request_immutability(self) -> None:
        """Test that request is frozen."""
        request = ConfigGenRequest(request="Test")

        with pytest.raises(Exception):
            request.mode = "create"


class TestConfigGenResult:
    """Tests for ConfigGenResult model."""

    def test_result_with_operations(self) -> None:
        """Test creating result with operations."""
        ops = (
            ConfigOp(kind="set", path="network.version", value="2"),
            ConfigOp(kind="set", path="network.renderer", value="networkd"),
        )

        result = ConfigGenResult(
            title="Configure network",
            explanation="Set up netplan configuration",
            operations=ops,
            target_path="/etc/netplan/01-netcfg.yaml",
            file_type="yaml",
            domain="network",
            requires_privilege=True,
        )

        assert len(result.operations) == 2
        assert result.requires_privilege is True
        assert result.generated_content is None

    def test_result_with_generated_content(self) -> None:
        """Test creating result with generated content."""
        result = ConfigGenResult(
            title="New netplan config",
            explanation="Created new configuration",
            generated_content="network:\n  version: 2\n",
            target_path="/etc/netplan/99-elle.yaml",
            file_type="yaml",
        )

        assert result.generated_content == "network:\n  version: 2\n"
        assert len(result.operations) == 0

    def test_result_with_risks(self) -> None:
        """Test result with risks."""
        result = ConfigGenResult(
            title="Modify SSH config",
            explanation="Change SSH settings",
            target_path="/etc/ssh/sshd_config",
            risks=("May lock out users", "Requires SSH restart"),
        )

        assert len(result.risks) == 2
        assert "lock out" in result.risks[0]


class TestConfigGenValidation:
    """Tests for ConfigGenValidation model."""

    def test_valid_result(self) -> None:
        """Test validation with no issues."""
        validation = ConfigGenValidation(valid=True)

        assert validation.valid is True
        assert len(validation.issues) == 0
        assert validation.error_count == 0
        assert validation.warning_count == 0

    def test_validation_with_issues(self) -> None:
        """Test validation with issues."""
        issues = (
            ValidationIssue(
                severity="error",
                message="Invalid syntax",
            ),
            ValidationIssue(
                severity="warning",
                message="Deprecated option",
            ),
            ValidationIssue(
                severity="info",
                message="Consider using alternative",
            ),
        )

        validation = ConfigGenValidation(
            valid=False,
            issues=issues,
        )

        assert validation.valid is False
        assert validation.error_count == 1
        assert validation.warning_count == 1

    def test_validation_issue_with_path(self) -> None:
        """Test validation issue with path."""
        issue = ValidationIssue(
            severity="error",
            message="Invalid value",
            path="/etc/fstab",
            suggestion="Use correct mount options",
        )

        assert issue.path == "/etc/fstab"
        assert issue.suggestion is not None


class TestConfigGenContext:
    """Tests for ConfigGenContext model."""

    def test_context_creation(self) -> None:
        """Test creating context."""
        request = ConfigGenRequest(request="Test request")

        context = ConfigGenContext(
            request=request,
            current_content="existing content",
            man_docs=("man page 1", "man page 2"),
            detected_domain="network",
            detected_file_type="yaml",
        )

        assert context.current_content == "existing content"
        assert len(context.man_docs) == 2
        assert context.detected_domain == "network"


class TestConfigGenOutcome:
    """Tests for ConfigGenOutcome model."""

    def test_successful_outcome(self) -> None:
        """Test successful outcome."""
        request = ConfigGenRequest(request="Test")
        result = ConfigGenResult(
            title="Test",
            explanation="Test result",
            target_path="/tmp/test",
        )
        validation = ConfigGenValidation(valid=True)

        outcome = ConfigGenOutcome(
            request=request,
            result=result,
            validation=validation,
        )

        assert outcome.success is True
        assert outcome.generation_error is None

    def test_failed_generation_outcome(self) -> None:
        """Test outcome with generation error."""
        request = ConfigGenRequest(request="Test")

        outcome = ConfigGenOutcome(
            request=request,
            generation_error="LLM not available",
        )

        assert outcome.success is False
        assert outcome.result is None

    def test_failed_validation_outcome(self) -> None:
        """Test outcome with validation failure."""
        request = ConfigGenRequest(request="Test")
        result = ConfigGenResult(
            title="Test",
            explanation="Test result",
            target_path="/tmp/test",
        )
        validation = ConfigGenValidation(
            valid=False,
            issues=(ValidationIssue(severity="error", message="Invalid"),),
        )

        outcome = ConfigGenOutcome(
            request=request,
            result=result,
            validation=validation,
        )

        assert outcome.success is False
        assert outcome.result is not None

    def test_applied_outcome(self) -> None:
        """Test outcome after apply."""
        request = ConfigGenRequest(request="Test")
        result = ConfigGenResult(
            title="Test",
            explanation="Test result",
            target_path="/tmp/test",
        )
        validation = ConfigGenValidation(valid=True)

        outcome = ConfigGenOutcome(
            request=request,
            result=result,
            validation=validation,
            applied=True,
            backup_path="/var/lib/elle/backups/test.bak",
        )

        assert outcome.applied is True
        assert outcome.backup_path is not None


class TestConfigGenExceptions:
    """Tests for confgen exceptions."""

    def test_config_gen_error(self) -> None:
        """Test ConfigGenError."""
        error = ConfigGenError("Something went wrong")
        assert str(error) == "Something went wrong"

    def test_config_gen_llm_error(self) -> None:
        """Test ConfigGenLLMError with raw response."""
        error = ConfigGenLLMError(
            "Failed to parse JSON",
            raw_response='{"invalid": json}',
        )

        assert "JSON" in str(error)
        assert error.raw_response == '{"invalid": json}'

    def test_config_gen_validation_error(self) -> None:
        """Test ConfigGenValidationError."""
        validation = ConfigGenValidation(
            valid=False,
            issues=(ValidationIssue(severity="error", message="Test error"),),
        )

        error = ConfigGenValidationError("Validation failed", validation)

        assert error.validation is validation
        assert error.validation.error_count == 1


class TestTypeAliases:
    """Tests for type aliases."""

    def test_config_op_mode_values(self) -> None:
        """Test ConfigOpMode accepts valid values."""
        for mode in ("edit", "create", "patch"):
            request = ConfigGenRequest(request="Test", mode=mode)
            assert request.mode == mode

    def test_config_file_type_values(self) -> None:
        """Test ConfigFileType accepts valid values."""
        for file_type in ("augeas", "yaml", "json", "toml", "text", "markdown", "systemd"):
            result = ConfigGenResult(
                title="Test",
                explanation="Test",
                target_path="/test",
                file_type=file_type,
            )
            assert result.file_type == file_type

    def test_config_domain_values(self) -> None:
        """Test ConfigDomain accepts valid values."""
        domains = (
            "network",
            "filesystem",
            "ssh",
            "firewall",
            "service",
            "cron",
            "package",
            "hosts",
            "general",
        )
        for domain in domains:
            result = ConfigGenResult(
                title="Test",
                explanation="Test",
                target_path="/test",
                domain=domain,
            )
            assert result.domain == domain
