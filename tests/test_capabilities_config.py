"""Tests for configuration editing capabilities."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pydantic
import pytest

from elle.capabilities.core.config import (
    CONFIG_CAPABILITIES,
    ConfigEditCapability,
    ConfigEditInput,
    ConfigEditOutput,
    ConfigOperation,
    ConfigPreviewCapability,
    ConfigPreviewInput,
    ConfigRollbackCapability,
    ConfigRollbackInput,
)
from elle.capabilities.models import CapabilityResult

# =============================================================================
# Mock helpers
# =============================================================================


def _make_operations(*ops: tuple[str, str, str | None]) -> tuple[ConfigOperation, ...]:
    """Build a tuple of ConfigOperation from (kind, path, value) triples."""
    return tuple(ConfigOperation(kind=kind, path=path, value=value) for kind, path, value in ops)


def _make_edit_input(
    file_path: str = "/etc/ssh/sshd_config",
    operations: tuple[ConfigOperation, ...] | None = None,
    lens: str | None = None,
    description: str = "test edit",
    skip_validation: bool = False,
    incident_id: str | None = None,
) -> ConfigEditInput:
    """Build a ConfigEditInput with sensible defaults."""
    if operations is None:
        operations = _make_operations(("set", "/PermitRootLogin", "no"))
    return ConfigEditInput(
        file_path=file_path,
        operations=operations,
        lens=lens,
        description=description,
        skip_validation=skip_validation,
        incident_id=incident_id,
    )


def _make_preview_input(
    file_path: str = "/etc/ssh/sshd_config",
    operations: tuple[ConfigOperation, ...] | None = None,
    lens: str | None = None,
) -> ConfigPreviewInput:
    """Build a ConfigPreviewInput with sensible defaults."""
    if operations is None:
        operations = _make_operations(("set", "/PermitRootLogin", "no"))
    return ConfigPreviewInput(
        file_path=file_path,
        operations=operations,
        lens=lens,
    )


def _make_rollback_input(
    incident_id: str = "inc-001",
    path: str = "/etc/ssh/sshd_config",
    verify: bool = True,
) -> ConfigRollbackInput:
    """Build a ConfigRollbackInput with sensible defaults."""
    return ConfigRollbackInput(
        incident_id=incident_id,
        path=path,
        verify=verify,
    )


def _mock_augeas_change(
    path: str = "/PermitRootLogin",
    old_value: str = "yes",
    new_value: str = "no",
    operation: str = "set",
) -> MagicMock:
    """Create a mock AugeasChange object."""
    change = MagicMock()
    change.path = path
    change.old_value = old_value
    change.new_value = new_value
    change.operation = operation
    return change


def _mock_augeas_result(
    success: bool = True,
    error: str | None = None,
    changes: list | None = None,
    diff: str | None = "--- a/sshd_config\n+++ b/sshd_config",
    backup_path: str | None = "/var/lib/elle/backups/sshd_config.bak",
    validation_passed: bool = True,
    validation_output: str | None = "OK",
    rollback_applied: bool = False,
) -> MagicMock:
    """Create a mock AugeasEditResult from controller.execute()."""
    result = MagicMock()
    result.success = success
    result.error = error
    result.changes = changes if changes is not None else [_mock_augeas_change()]
    result.diff = diff
    result.backup_path = backup_path
    result.validation_passed = validation_passed
    result.validation_output = validation_output
    result.rollback_applied = rollback_applied
    return result


def _mock_augeas_preview(
    diff: str = "--- a/sshd_config\n+++ b/sshd_config",
    diff_colored: str = "\x1b[31m- old\x1b[0m\n\x1b[32m+ new\x1b[0m",
    changes: list | None = None,
    requires_privilege: bool = False,
) -> MagicMock:
    """Create a mock AugeasPreviewResult from controller.preview()."""
    preview = MagicMock()
    preview.diff = diff
    preview.diff_colored = diff_colored
    preview.changes = changes if changes is not None else [_mock_augeas_change()]
    preview.requires_privilege = requires_privilege
    return preview


def _make_async_execute(return_value):
    """Create a callable that returns a coroutine resolving to return_value.

    The source code calls ``controller.execute(request)`` and then passes
    the return value to ``asyncio.run()`` or ``loop.run_until_complete()``,
    both of which expect a coroutine/awaitable.
    """

    async def _coro(*args, **kwargs):
        return return_value

    return _coro


# =============================================================================
# TestConfigOperation (Pydantic model validation)
# =============================================================================


class TestConfigOperation:
    """Tests for ConfigOperation Pydantic model."""

    def test_set_operation_valid(self):
        """A well-formed 'set' operation is accepted."""
        op = ConfigOperation(kind="set", path="/Port", value="2222")
        assert op.kind == "set"
        assert op.path == "/Port"
        assert op.value == "2222"

    def test_rm_operation_no_value(self):
        """An 'rm' operation with value=None is accepted."""
        op = ConfigOperation(kind="rm", path="/X11Forwarding")
        assert op.kind == "rm"
        assert op.value is None

    def test_frozen_model_rejects_mutation(self):
        """ConfigOperation is frozen; attribute assignment raises."""
        op = ConfigOperation(kind="set", path="/Port", value="22")
        with pytest.raises(pydantic.ValidationError):
            op.kind = "rm"


# =============================================================================
# TestConfigEditInput (Pydantic model validation)
# =============================================================================


class TestConfigEditInput:
    """Tests for ConfigEditInput Pydantic model."""

    def test_minimal_valid_input(self):
        """Minimal required fields produce a valid model."""
        inp = ConfigEditInput(
            file_path="/etc/foo.conf",
            operations=_make_operations(("set", "/key", "val")),
        )
        assert inp.file_path == "/etc/foo.conf"
        assert len(inp.operations) == 1
        assert inp.lens is None
        assert inp.description == ""
        assert inp.skip_validation is False
        assert inp.incident_id is None

    def test_missing_file_path_raises(self):
        """Omitting file_path raises ValidationError."""
        with pytest.raises(pydantic.ValidationError):
            ConfigEditInput(
                operations=_make_operations(("set", "/key", "val")),
            )

    def test_missing_operations_raises(self):
        """Omitting operations raises ValidationError."""
        with pytest.raises(pydantic.ValidationError):
            ConfigEditInput(file_path="/etc/foo.conf")


# =============================================================================
# TestConfigEditCapability - spec
# =============================================================================


class TestConfigEditSpec:
    """Tests for ConfigEditCapability spec property."""

    def setup_method(self):
        self.cap = ConfigEditCapability()

    def test_spec_name(self):
        """Spec name is 'config.edit'."""
        assert self.cap.spec.name == "config.edit"

    def test_spec_domain(self):
        """Spec domain is 'config'."""
        assert self.cap.spec.domain == "config"

    def test_spec_risk(self):
        """Spec risk is 'medium'."""
        assert self.cap.spec.risk == "medium"

    def test_spec_side_effects(self):
        """Spec declares a file_write side effect."""
        assert len(self.cap.spec.side_effects) == 1
        assert self.cap.spec.side_effects[0].kind == "file_write"
        assert self.cap.spec.side_effects[0].reversible is True

    def test_spec_trust_level(self):
        """Spec trust level is 'core'."""
        assert self.cap.spec.trust_level == "core"


# =============================================================================
# TestConfigEditCapability - dry_run
# =============================================================================


class TestConfigEditDryRun:
    """Tests for ConfigEditCapability.dry_run()."""

    def setup_method(self):
        self.cap = ConfigEditCapability()

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_dry_run_success_no_privilege(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Successful dry_run returns valid preview with low risk when no privilege needed."""
        preview = _mock_augeas_preview(requires_privilege=False)
        controller = MagicMock()
        controller.preview.return_value = preview
        mock_get_ctrl.return_value = controller

        inp = _make_edit_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "low"
        assert inp.file_path in result.would_modify
        assert result.requires_confirmation is True
        assert result.diff == preview.diff

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_dry_run_success_privilege_required(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """When preview shows privilege required, risk is medium."""
        preview = _mock_augeas_preview(requires_privilege=True)
        controller = MagicMock()
        controller.preview.return_value = preview
        mock_get_ctrl.return_value = controller

        inp = _make_edit_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "medium"

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_dry_run_exception(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """General exception during dry_run yields invalid result."""
        mock_get_ctrl.side_effect = RuntimeError("controller broken")

        inp = _make_edit_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "controller broken" in result.validation_errors[0]
        assert "Preview failed" in result.preview_text

    def test_dry_run_import_error(self):
        """ImportError when Augeas is unavailable yields invalid result."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.ops.augeas" in name:
                raise ImportError("No augeas")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigEditCapability()
            inp = _make_edit_input()
            result = cap.dry_run(inp)

            assert result.is_valid is False
            assert "Augeas" in result.validation_errors[0] or "augeas" in result.validation_errors[0].lower()


# =============================================================================
# TestConfigEditCapability - run
# =============================================================================


class TestConfigEditRun:
    """Tests for ConfigEditCapability.run()."""

    def setup_method(self):
        self.cap = ConfigEditCapability()

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_success(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Successful run returns output with changes, diff, and evidence."""
        augeas_result = _mock_augeas_result(success=True)
        controller = MagicMock()
        controller.execute = _make_async_execute(augeas_result)
        mock_get_ctrl.return_value = controller

        inp = _make_edit_input(description="Harden SSH")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.file_path == inp.file_path
        assert len(result.output.changes) == 1
        assert result.output.backup_path == "/var/lib/elle/backups/sshd_config.bak"
        assert result.output.validation_passed is True
        assert result.execution_time_ms >= 0
        assert result.evidence.files_modified == (inp.file_path,)
        assert "Harden SSH" in result.evidence.rationale
        assert len(result.side_effects_applied) == 1

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_failure_from_controller(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Failed execution returns success=False with error message."""
        augeas_result = _mock_augeas_result(
            success=False,
            error="Syntax error in config",
            rollback_applied=False,
        )
        controller = MagicMock()
        controller.execute = _make_async_execute(augeas_result)
        mock_get_ctrl.return_value = controller

        inp = _make_edit_input()
        result = self.cap.run(inp)

        assert result.success is False
        assert result.error == "Syntax error in config"
        assert result.evidence.files_modified == ()

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_failure_with_rollback_applied(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """When controller fails but rollback was applied, file is recorded as modified."""
        augeas_result = _mock_augeas_result(
            success=False,
            error="Validation failed",
            rollback_applied=True,
        )
        controller = MagicMock()
        controller.execute = _make_async_execute(augeas_result)
        mock_get_ctrl.return_value = controller

        inp = _make_edit_input()
        result = self.cap.run(inp)

        assert result.success is False
        assert result.evidence.files_modified == (inp.file_path,)

    def test_run_import_error(self):
        """ImportError when Augeas is unavailable yields failure result."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.ops.augeas" in name:
                raise ImportError("No augeas")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigEditCapability()
            inp = _make_edit_input()
            result = cap.run(inp)

            assert result.success is False
            assert "Augeas" in result.error or "augeas" in result.error.lower()

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_general_exception(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Unexpected exception during run is caught and reported."""
        mock_get_ctrl.side_effect = OSError("Permission denied")

        inp = _make_edit_input()
        result = self.cap.run(inp)

        assert result.success is False
        assert "Permission denied" in result.error
        assert "Config edit failed" in result.evidence.rationale

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_multiple_operations(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Multiple operations are all converted and passed through."""
        changes = [
            _mock_augeas_change("/Port", "22", "2222", "set"),
            _mock_augeas_change("/PermitRootLogin", "yes", "no", "set"),
        ]
        augeas_result = _mock_augeas_result(success=True, changes=changes)
        controller = MagicMock()
        controller.execute = _make_async_execute(augeas_result)
        mock_get_ctrl.return_value = controller

        ops = _make_operations(
            ("set", "/Port", "2222"),
            ("set", "/PermitRootLogin", "no"),
        )
        inp = _make_edit_input(operations=ops)
        result = self.cap.run(inp)

        assert result.success is True
        assert len(result.output.changes) == 2

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_success_with_running_loop(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Successful run via ThreadPoolExecutor when an event loop is already running."""
        augeas_result = _mock_augeas_result(success=True)
        controller = MagicMock()
        controller.execute = _make_async_execute(augeas_result)
        mock_get_ctrl.return_value = controller

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        inp = _make_edit_input(description="Running loop test")
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = self.cap.run(inp)

        assert result.success is True
        assert result.output.file_path == inp.file_path

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_validation_failed_still_success(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """When edit succeeds but validation fails, result is still success=True with flag."""
        augeas_result = _mock_augeas_result(
            success=True,
            validation_passed=False,
            validation_output="syntax error line 42",
        )
        controller = MagicMock()
        controller.execute = _make_async_execute(augeas_result)
        mock_get_ctrl.return_value = controller

        inp = _make_edit_input()
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.validation_passed is False
        assert result.output.validation_output == "syntax error line 42"
        assert result.evidence.verification_passed is False


# =============================================================================
# TestConfigEditCapability - verify
# =============================================================================


class TestConfigEditVerify:
    """Tests for ConfigEditCapability.verify()."""

    def setup_method(self):
        self.cap = ConfigEditCapability()

    def test_verify_file_missing(self, tmp_path):
        """Verify returns failure when the edited file does not exist."""
        missing = str(tmp_path / "nonexistent.conf")
        inp = _make_edit_input(file_path=missing)
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "does not exist" in result.discrepancies[0]

    def test_verify_file_exists_no_validators(self, tmp_path):
        """Verify returns passed when file exists but validators are unavailable."""
        conf = tmp_path / "test.conf"
        conf.write_text("key = value\n")

        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.ops.augeas.validators" in name:
                raise ImportError("No validators")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigEditCapability()
            inp = _make_edit_input(file_path=str(conf))
            result = cap.verify(inp)

            assert result.passed is True
            assert "file exists" in result.checks_performed

    @patch("elle.ops.augeas.validators.validate_config")
    def test_verify_validation_passes(self, mock_validate, tmp_path):
        """Verify returns passed when validation succeeds."""
        conf = tmp_path / "test.conf"
        conf.write_text("key = value\n")

        async def _async_validate(*args, **kwargs):
            val_result = MagicMock()
            val_result.valid = True
            val_result.command = "sshd -t"
            return val_result

        mock_validate.side_effect = _async_validate

        inp = _make_edit_input(file_path=str(conf))
        result = self.cap.verify(inp)

        assert result.passed is True
        assert result.actual_state == {"valid": True}
        assert result.expected_state == {"valid": True}

    @patch("elle.ops.augeas.validators.validate_config")
    def test_verify_validation_fails(self, mock_validate, tmp_path):
        """Verify returns failure when validation reports invalid."""
        conf = tmp_path / "test.conf"
        conf.write_text("broken\n")

        async def _async_validate(*args, **kwargs):
            val_result = MagicMock()
            val_result.valid = False
            val_result.command = "sshd -t"
            val_result.error = "bad directive"
            return val_result

        mock_validate.side_effect = _async_validate

        inp = _make_edit_input(file_path=str(conf))
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "bad directive" in result.discrepancies[0]

    @patch("elle.ops.augeas.validators.validate_config")
    def test_verify_general_exception(self, mock_validate, tmp_path):
        """Verify returns failure when an unexpected error occurs."""
        conf = tmp_path / "test.conf"
        conf.write_text("content\n")

        mock_validate.side_effect = RuntimeError("unexpected")

        inp = _make_edit_input(file_path=str(conf))
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "unexpected" in result.discrepancies[0]

    @patch("elle.ops.augeas.validators.validate_config")
    def test_verify_with_running_loop(self, mock_validate, tmp_path):
        """Verify works via ThreadPoolExecutor when event loop is already running."""
        conf = tmp_path / "test.conf"
        conf.write_text("key = value\n")

        async def _async_validate(*args, **kwargs):
            val_result = MagicMock()
            val_result.valid = True
            val_result.command = "sshd -t"
            return val_result

        mock_validate.side_effect = _async_validate

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        inp = _make_edit_input(file_path=str(conf))
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = self.cap.verify(inp)

        assert result.passed is True


# =============================================================================
# TestConfigEditCapability - rollback
# =============================================================================


class TestConfigEditRollback:
    """Tests for ConfigEditCapability.rollback()."""

    def setup_method(self):
        self.cap = ConfigEditCapability()

    def test_rollback_no_output(self):
        """Rollback fails when result has no output."""
        cap_result = CapabilityResult(success=True, output=None)
        inp = _make_edit_input()
        rb = self.cap.rollback(inp, cap_result)

        assert rb.success is False
        assert "No output" in rb.error

    def test_rollback_no_backup_path(self):
        """Rollback fails when output has no backup_path."""
        output = ConfigEditOutput(
            file_path="/etc/ssh/sshd_config",
            backup_path=None,
        )
        cap_result = CapabilityResult(success=True, output=output)
        inp = _make_edit_input()
        rb = self.cap.rollback(inp, cap_result)

        assert rb.success is False
        assert "No backup available" in rb.error

    @patch("elle.ops.augeas.controller.rollback_edit")
    def test_rollback_success(self, mock_rb_edit):
        """Successful rollback restores the file."""
        mock_rb_edit.return_value = True

        output = ConfigEditOutput(
            file_path="/etc/ssh/sshd_config",
            backup_path="/var/lib/elle/backups/sshd_config.bak",
        )
        cap_result = CapabilityResult(success=True, output=output)
        inp = _make_edit_input()
        rb = self.cap.rollback(inp, cap_result)

        assert rb.success is True
        assert rb.error is None
        assert inp.file_path in rb.rolled_back
        assert len(rb.failed_to_rollback) == 0
        mock_rb_edit.assert_called_once_with(inp.file_path, "/var/lib/elle/backups/sshd_config.bak")

    @patch("elle.ops.augeas.controller.rollback_edit")
    def test_rollback_failure(self, mock_rb_edit):
        """When rollback_edit returns False, result indicates failure."""
        mock_rb_edit.return_value = False

        output = ConfigEditOutput(
            file_path="/etc/ssh/sshd_config",
            backup_path="/var/lib/elle/backups/sshd_config.bak",
        )
        cap_result = CapabilityResult(success=True, output=output)
        inp = _make_edit_input()
        rb = self.cap.rollback(inp, cap_result)

        assert rb.success is False
        assert "Rollback failed" in rb.error
        assert inp.file_path in rb.failed_to_rollback

    def test_rollback_import_error(self):
        """ImportError from Augeas controller yields failure."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.ops.augeas" in name:
                raise ImportError("No augeas")
            return real_import(name, *args, **kwargs)

        output = ConfigEditOutput(
            file_path="/etc/ssh/sshd_config",
            backup_path="/var/lib/elle/backups/sshd_config.bak",
        )
        cap_result = CapabilityResult(success=True, output=output)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigEditCapability()
            inp = _make_edit_input()
            rb = cap.rollback(inp, cap_result)

            assert rb.success is False
            assert "Augeas" in rb.error or "augeas" in rb.error.lower()

    @patch("elle.ops.augeas.controller.rollback_edit")
    def test_rollback_general_exception(self, mock_rb_edit):
        """Unexpected exception during rollback is caught and reported."""
        mock_rb_edit.side_effect = OSError("disk full")

        output = ConfigEditOutput(
            file_path="/etc/ssh/sshd_config",
            backup_path="/var/lib/elle/backups/sshd_config.bak",
        )
        cap_result = CapabilityResult(success=True, output=output)
        inp = _make_edit_input()
        rb = self.cap.rollback(inp, cap_result)

        assert rb.success is False
        assert "disk full" in rb.error
        assert inp.file_path in rb.failed_to_rollback


# =============================================================================
# TestConfigPreviewCapability - spec
# =============================================================================


class TestConfigPreviewSpec:
    """Tests for ConfigPreviewCapability spec property."""

    def setup_method(self):
        self.cap = ConfigPreviewCapability()

    def test_spec_name(self):
        """Spec name is 'config.preview'."""
        assert self.cap.spec.name == "config.preview"

    def test_spec_risk_none(self):
        """Preview is read-only and has risk 'none'."""
        assert self.cap.spec.risk == "none"

    def test_spec_no_side_effects(self):
        """Preview declares no side effects."""
        assert len(self.cap.spec.side_effects) == 0

    def test_spec_idempotent(self):
        """Preview is idempotent."""
        assert self.cap.spec.idempotent is True


# =============================================================================
# TestConfigPreviewCapability - dry_run
# =============================================================================


class TestConfigPreviewDryRun:
    """Tests for ConfigPreviewCapability.dry_run()."""

    def setup_method(self):
        self.cap = ConfigPreviewCapability()

    def test_dry_run_always_valid(self):
        """Preview dry_run is always valid since it is itself a read-only operation."""
        inp = _make_preview_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "none"
        assert result.requires_confirmation is False
        assert len(result.would_modify) == 0
        assert inp.file_path in result.preview_text


# =============================================================================
# TestConfigPreviewCapability - run
# =============================================================================


class TestConfigPreviewRun:
    """Tests for ConfigPreviewCapability.run()."""

    def setup_method(self):
        self.cap = ConfigPreviewCapability()

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_success(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Successful preview returns diff and changes."""
        preview = _mock_augeas_preview()
        controller = MagicMock()
        controller.preview.return_value = preview
        mock_get_ctrl.return_value = controller

        inp = _make_preview_input()
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.file_path == inp.file_path
        assert result.output.diff == preview.diff
        assert result.output.diff_colored == preview.diff_colored
        assert len(result.output.changes) == 1
        assert result.output.requires_privilege is False
        assert "preview" in result.evidence.rationale.lower()

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_privilege_required(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Preview for privileged file reports requires_privilege=True."""
        preview = _mock_augeas_preview(requires_privilege=True)
        controller = MagicMock()
        controller.preview.return_value = preview
        mock_get_ctrl.return_value = controller

        inp = _make_preview_input()
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.requires_privilege is True

    def test_run_import_error(self):
        """ImportError when Augeas is unavailable yields failure."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.ops.augeas" in name:
                raise ImportError("No augeas")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigPreviewCapability()
            inp = _make_preview_input()
            result = cap.run(inp)

            assert result.success is False
            assert "Augeas" in result.error or "augeas" in result.error.lower()

    @patch("elle.ops.augeas.controller.get_controller")
    @patch("elle.ops.augeas.models.AugeasOp")
    @patch("elle.ops.augeas.models.AugeasEditRequest")
    def test_run_general_exception(self, mock_req_cls, mock_op_cls, mock_get_ctrl):
        """Unexpected exception during preview is caught."""
        mock_get_ctrl.side_effect = ValueError("bad lens")

        inp = _make_preview_input()
        result = self.cap.run(inp)

        assert result.success is False
        assert "bad lens" in result.error


# =============================================================================
# TestConfigRollbackInput (Pydantic model validation)
# =============================================================================


class TestConfigRollbackInputModel:
    """Tests for ConfigRollbackInput Pydantic model."""

    def test_valid_input(self):
        """Well-formed input is accepted."""
        inp = ConfigRollbackInput(
            incident_id="inc-42",
            path="/etc/nginx/nginx.conf",
            verify=False,
        )
        assert inp.incident_id == "inc-42"
        assert inp.path == "/etc/nginx/nginx.conf"
        assert inp.verify is False

    def test_verify_defaults_true(self):
        """verify field defaults to True."""
        inp = ConfigRollbackInput(
            incident_id="inc-1",
            path="/etc/foo.conf",
        )
        assert inp.verify is True

    def test_missing_incident_id_raises(self):
        """Omitting incident_id raises ValidationError."""
        with pytest.raises(pydantic.ValidationError):
            ConfigRollbackInput(path="/etc/foo.conf")

    def test_missing_path_raises(self):
        """Omitting path raises ValidationError."""
        with pytest.raises(pydantic.ValidationError):
            ConfigRollbackInput(incident_id="inc-1")


# =============================================================================
# TestConfigRollbackCapability - spec
# =============================================================================


class TestConfigRollbackSpec:
    """Tests for ConfigRollbackCapability spec property."""

    def setup_method(self):
        self.cap = ConfigRollbackCapability()

    def test_spec_name(self):
        """Spec name is 'config.rollback'."""
        assert self.cap.spec.name == "config.rollback"

    def test_spec_risk_high(self):
        """Rollback is a high-risk operation."""
        assert self.cap.spec.risk == "high"

    def test_spec_requires_privilege(self):
        """Rollback requires privilege for /etc files."""
        assert self.cap.spec.requires_privilege is True

    def test_spec_idempotent(self):
        """Rollback is idempotent."""
        assert self.cap.spec.idempotent is True

    def test_spec_side_effect_not_reversible(self):
        """Rollback side effect is not reversible."""
        assert len(self.cap.spec.side_effects) == 1
        assert self.cap.spec.side_effects[0].reversible is False


# =============================================================================
# TestConfigRollbackCapability - dry_run
# =============================================================================


class TestConfigRollbackDryRun:
    """Tests for ConfigRollbackCapability.dry_run()."""

    def setup_method(self):
        self.cap = ConfigRollbackCapability()

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_dry_run_success(self, mock_get_backup, tmp_path):
        """Successful dry_run shows what would be restored."""
        backup_file = tmp_path / "nginx.conf.bak"
        backup_file.write_text("backup content")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "high"
        assert result.requires_confirmation is True
        assert inp.path in result.would_modify

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_dry_run_no_backup_found(self, mock_get_backup):
        """Dry_run returns invalid when no backup exists for the incident."""

        async def _get_backup(*args, **kwargs):
            return None

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "No backup found" in result.validation_errors[0]

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_dry_run_backup_file_missing(self, mock_get_backup):
        """Dry_run returns invalid when backup file no longer exists on disk."""

        async def _get_backup(*args, **kwargs):
            return {"backup_path": "/nonexistent/path/gone.bak"}

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "Backup file not found" in result.validation_errors[0]

    def test_dry_run_import_error(self):
        """ImportError from config tracker yields invalid result."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.daemon.incidents.config_tracker" in name:
                raise ImportError("No config tracker")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigRollbackCapability()
            inp = _make_rollback_input()
            result = cap.dry_run(inp)

            assert result.is_valid is False
            assert "Config tracker not available" in result.validation_errors[0]

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_dry_run_general_exception(self, mock_get_backup):
        """General exception during dry_run yields invalid result."""

        async def _get_backup(*args, **kwargs):
            raise RuntimeError("db locked")

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input()
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "db locked" in result.validation_errors[0]

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_dry_run_with_running_loop(self, mock_get_backup, tmp_path):
        """Dry run works via ThreadPoolExecutor when event loop is already running."""
        backup_file = tmp_path / "test.bak"
        backup_file.write_text("backup")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        inp = _make_rollback_input()
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = self.cap.dry_run(inp)

        assert result.is_valid is True


# =============================================================================
# TestConfigRollbackCapability - run
# =============================================================================


class TestConfigRollbackRun:
    """Tests for ConfigRollbackCapability.run()."""

    def setup_method(self):
        self.cap = ConfigRollbackCapability()

    @patch("elle.ops.augeas.validators.validate_config")
    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_success_with_verify(self, mock_get_backup, mock_validate, tmp_path):
        """Successful rollback copies backup, hashes, and validates."""
        # Set up real files for hash computation
        backup_file = tmp_path / "sshd.bak"
        backup_file.write_text("original content")
        target_file = tmp_path / "sshd_config"
        target_file.write_text("corrupted content")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        async def _async_validate(*args, **kwargs):
            val_result = MagicMock()
            val_result.valid = True
            return val_result

        mock_validate.side_effect = _async_validate

        inp = _make_rollback_input(path=str(target_file), verify=True)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.success is True
        assert result.output.path == str(target_file)
        assert result.output.validation_passed is True
        assert result.output.sha256_before is not None
        assert result.output.sha256_after is not None
        assert result.evidence.files_modified == (str(target_file),)

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_success_skip_verify(self, mock_get_backup, tmp_path):
        """Rollback with verify=False skips validation."""
        backup_file = tmp_path / "foo.bak"
        backup_file.write_text("backup")
        target_file = tmp_path / "foo.conf"
        target_file.write_text("current")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input(path=str(target_file), verify=False)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.validation_passed is True

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_no_backup(self, mock_get_backup):
        """Run returns failure when no backup exists for the incident."""

        async def _get_backup(*args, **kwargs):
            return None

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input()
        result = self.cap.run(inp)

        assert result.success is False
        assert "No backup found" in result.error

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_backup_file_missing(self, mock_get_backup):
        """Run returns failure when backup file no longer exists on disk."""

        async def _get_backup(*args, **kwargs):
            return {"backup_path": "/nonexistent/gone.bak"}

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input()
        result = self.cap.run(inp)

        assert result.success is False
        assert "Backup file not found" in result.error

    @patch("elle.ops.augeas.validators.validate_config")
    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_validation_failed_keeps_restored(self, mock_get_backup, mock_validate, tmp_path):
        """Rollback keeps restored state even when validation fails."""
        backup_file = tmp_path / "sshd.bak"
        backup_file.write_text("original")
        target_file = tmp_path / "sshd_config"
        target_file.write_text("corrupted")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        async def _async_validate(*args, **kwargs):
            val_result = MagicMock()
            val_result.valid = False
            return val_result

        mock_validate.side_effect = _async_validate

        inp = _make_rollback_input(path=str(target_file), verify=True)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.validation_passed is False

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_target_file_missing_no_sha_before(self, mock_get_backup, tmp_path):
        """When target file does not exist, sha256_before is None."""
        backup_file = tmp_path / "new.bak"
        backup_file.write_text("backup content")
        target_file = tmp_path / "new.conf"
        # Do NOT create the target file; it should not exist

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input(path=str(target_file), verify=False)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.sha256_before is None

    def test_run_import_error(self):
        """ImportError from config tracker yields failure."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.daemon.incidents.config_tracker" in name:
                raise ImportError("No config tracker")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigRollbackCapability()
            inp = _make_rollback_input()
            result = cap.run(inp)

            assert result.success is False
            assert "Config tracker not available" in result.error

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_general_exception(self, mock_get_backup):
        """Unexpected exception during run is caught and reported."""

        async def _get_backup(*args, **kwargs):
            raise RuntimeError("db crashed")

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input()
        result = self.cap.run(inp)

        assert result.success is False
        assert "db crashed" in result.error
        assert "Config rollback failed" in result.evidence.rationale

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_restores_backup_content(self, mock_get_backup, tmp_path):
        """After rollback, the target file contains the backup content."""
        backup_file = tmp_path / "nginx.bak"
        backup_file.write_text("good config")
        target_file = tmp_path / "nginx.conf"
        target_file.write_text("bad config")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        inp = _make_rollback_input(path=str(target_file), verify=False)
        result = self.cap.run(inp)

        assert result.success is True
        # Verify the file content was actually restored
        assert target_file.read_text() == "good config"

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_with_running_loop(self, mock_get_backup, tmp_path):
        """Run works via ThreadPoolExecutor when event loop is already running."""
        backup_file = tmp_path / "run_loop.bak"
        backup_file.write_text("backup content")
        target_file = tmp_path / "run_loop.conf"
        target_file.write_text("current content")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        inp = _make_rollback_input(path=str(target_file), verify=False)
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = self.cap.run(inp)

        assert result.success is True

    @patch("elle.ops.augeas.validators.validate_config")
    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_verify_with_running_loop(self, mock_get_backup, mock_validate, tmp_path):
        """Run with verify=True works via ThreadPoolExecutor when loop is running."""
        backup_file = tmp_path / "verify_loop.bak"
        backup_file.write_text("backup")
        target_file = tmp_path / "verify_loop.conf"
        target_file.write_text("corrupted")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        async def _async_validate(*args, **kwargs):
            val_result = MagicMock()
            val_result.valid = True
            return val_result

        mock_validate.side_effect = _async_validate

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        inp = _make_rollback_input(path=str(target_file), verify=True)
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = self.cap.run(inp)

        assert result.success is True
        assert result.output.validation_passed is True

    @patch("elle.daemon.incidents.config_tracker.get_config_backup")
    def test_run_verify_validators_import_error(self, mock_get_backup, tmp_path):
        """When validators are unavailable during verify step, assume OK."""
        backup_file = tmp_path / "foo.bak"
        backup_file.write_text("backup")
        target_file = tmp_path / "foo.conf"
        target_file.write_text("current")

        async def _get_backup(*args, **kwargs):
            return {"backup_path": str(backup_file)}

        mock_get_backup.side_effect = _get_backup

        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if "elle.ops.augeas.validators" in name:
                raise ImportError("No validators")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            cap = ConfigRollbackCapability()
            inp = _make_rollback_input(path=str(target_file), verify=True)
            result = cap.run(inp)

            assert result.success is True
            # Validators not available, so validation_passed defaults to True
            assert result.output.validation_passed is True


# =============================================================================
# TestConfigRollbackCapability - verify
# =============================================================================


class TestConfigRollbackVerify:
    """Tests for ConfigRollbackCapability.verify()."""

    def setup_method(self):
        self.cap = ConfigRollbackCapability()

    def test_verify_file_exists(self, tmp_path):
        """Verify passes when the rolled-back file exists."""
        conf = tmp_path / "test.conf"
        conf.write_text("content")

        inp = _make_rollback_input(path=str(conf))
        result = self.cap.verify(inp)

        assert result.passed is True
        assert "file exists" in result.checks_performed

    def test_verify_file_missing(self, tmp_path):
        """Verify fails when the rolled-back file does not exist."""
        missing = str(tmp_path / "missing.conf")
        inp = _make_rollback_input(path=missing)
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "does not exist" in result.discrepancies[0]


# =============================================================================
# TestConfigCapabilitiesExport
# =============================================================================


class TestConfigCapabilitiesExport:
    """Tests for the CONFIG_CAPABILITIES export list."""

    def test_exports_all_three_capabilities(self):
        """CONFIG_CAPABILITIES contains all three capability classes."""
        assert len(CONFIG_CAPABILITIES) == 3
        assert ConfigEditCapability in CONFIG_CAPABILITIES
        assert ConfigPreviewCapability in CONFIG_CAPABILITIES
        assert ConfigRollbackCapability in CONFIG_CAPABILITIES

    def test_all_capabilities_instantiable(self):
        """All exported capability classes can be instantiated."""
        for cls in CONFIG_CAPABILITIES:
            instance = cls()
            assert instance.spec is not None
            assert instance.spec.name.startswith("config.")
