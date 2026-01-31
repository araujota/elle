from __future__ import annotations

"""Tests for the file.edit capability (FileEditCapability).

Covers:
- Pydantic input/output model validation and field defaults
- FileEditCapability.spec property
- FileEditCapability.dry_run() -- happy path, tier 5 without justification, exception path
- FileEditCapability.run() -- success, failure, tier 5 justification error,
  async event loop running, general exception
- FileEditCapability.verify() -- file exists, file missing, exception
- FileEditCapability.rollback() -- with backup, without backup, exception
- FileEditCapability._convert_operation()
- FILE_EDIT_CAPABILITIES export
"""

import asyncio as _real_asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.core.file_edit import (
    FILE_EDIT_CAPABILITIES,
    FileEditCapability,
    FileEditInput,
    FileEditOperation,
    FileEditOutput,
    TierEscalationRecord,
)
from elle.capabilities.models import (
    CapabilityResult,
    DryRunResult,
    RollbackResult,
    VerificationResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def capability():
    """Return a fresh FileEditCapability instance."""
    return FileEditCapability()


@pytest.fixture()
def basic_operation():
    """Return a minimal FileEditOperation for testing."""
    return FileEditOperation(kind="set", path="/some/key", value="42")


@pytest.fixture()
def basic_input(basic_operation):
    """Return a minimal FileEditInput for testing."""
    return FileEditInput(
        file_path="/etc/test.conf",
        operation=basic_operation,
        description="set a key",
    )


@pytest.fixture()
def tier5_input(basic_operation):
    """Input that explicitly targets tier 5 without justification."""
    return FileEditInput(
        file_path="/etc/test.conf",
        operation=basic_operation,
        description="tier5 edit",
        max_tier=5,
    )


@pytest.fixture()
def tier5_justified_input(basic_operation):
    """Input that provides tier 5 justification."""
    return FileEditInput(
        file_path="/etc/test.conf",
        operation=basic_operation,
        description="tier5 justified edit",
        max_tier=5,
        tier5_justification="No structured tool available for this file format",
    )


def _make_tier_selection(tier=2, tier_name="Structured Data", reason="json file", tool_used="jq", escalation_trail=()):
    """Build a mock TierSelection object."""
    sel = MagicMock()
    sel.tier = tier
    sel.tier_name = tier_name
    sel.reason = reason
    sel.tool_used = tool_used
    sel.escalation_trail = escalation_trail
    return sel


def _make_tier_edit_result(
    success=True,
    file_path="/etc/test.conf",
    tier_used=2,
    tier_name="Structured Data",
    tool_used="jq",
    diff="--- a\n+++ b\n",
    backup_path="/var/lib/elle/backups/test.conf",
    validation=None,
    error=None,
):
    """Build a mock TierEditResult object."""
    result = MagicMock()
    result.success = success
    result.file_path = file_path
    result.tier_used = tier_used
    result.tier_name = tier_name
    result.tool_used = tool_used
    result.diff = diff
    result.backup_path = backup_path
    result.validation = validation
    result.error = error
    return result


def _patch_run_deps(selector_cls, tier_getter, edit_result, loop_running=False):
    """Return a context-manager stack that patches all run() dependencies.

    Since asyncio is imported *inside* run(), we need to patch it at the
    stdlib level and control get_event_loop / asyncio.run behaviour.
    """
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = loop_running

    class _Ctx:
        """Holds patches so the caller can inspect mocks afterwards."""

        def __init__(self):
            self.patches = []

        def __enter__(self):
            # The run() method does ``import asyncio`` at the top of run().
            # Because it is a local import it resolves through sys.modules
            # each time, so we build a thin wrapper module that delegates
            # most things to the real asyncio but lets us intercept
            # get_event_loop / run.
            self.fake_asyncio = MagicMock(wraps=_real_asyncio)
            self.fake_asyncio.get_event_loop.return_value = mock_loop

            if not loop_running:
                self.fake_asyncio.run.return_value = edit_result
            # For the running-loop path we mock ThreadPoolExecutor below.

            p1 = patch.dict("sys.modules", {"asyncio": self.fake_asyncio})
            p2 = patch("elle.ops.editing.TierSelector", selector_cls)
            p3 = patch("elle.ops.editing.Tier5RequiresJustificationError", Exception)
            p4 = patch("elle.ops.editing.tiers.get_tier_instance", tier_getter)

            self.patches = [p1, p2, p3, p4]

            if loop_running:
                mock_future = MagicMock()
                mock_future.result.return_value = edit_result
                mock_executor = MagicMock()
                mock_executor.__enter__ = MagicMock(return_value=mock_executor)
                mock_executor.__exit__ = MagicMock(return_value=False)
                mock_executor.submit.return_value = mock_future
                p5 = patch("concurrent.futures.ThreadPoolExecutor", return_value=mock_executor)
                self.patches.append(p5)

            for p in self.patches:
                p.start()
            return self

        def __exit__(self, *args):
            for p in reversed(self.patches):
                p.stop()

    return _Ctx()


# =============================================================================
# Model Validation Tests
# =============================================================================


class TestFileEditOperation:
    """Tests for the FileEditOperation Pydantic model."""

    def test_defaults(self):
        op = FileEditOperation(kind="set")
        assert op.kind == "set"
        assert op.path is None
        assert op.value is None
        assert op.anchor_pattern is None
        assert op.anchor_context_lines == 3
        assert op.section is None
        assert op.key is None
        assert op.position is None

    def test_all_fields(self):
        op = FileEditOperation(
            kind="insert",
            path="/http/server",
            value="listen 8080;",
            anchor_pattern=r"server\s*\{",
            anchor_context_lines=5,
            section="main",
            key="listen",
            position="after",
        )
        assert op.kind == "insert"
        assert op.anchor_context_lines == 5
        assert op.position == "after"

    def test_frozen_model(self):
        op = FileEditOperation(kind="set")
        with pytest.raises(Exception):
            op.kind = "rm"  # type: ignore[misc]

    def test_valid_kinds(self):
        for kind in ("set", "rm", "add", "replace", "append", "insert"):
            op = FileEditOperation(kind=kind)
            assert op.kind == kind

    def test_invalid_kind_rejected(self):
        with pytest.raises(Exception):
            FileEditOperation(kind="invalid_kind")

    def test_anchor_context_lines_bounds(self):
        op_min = FileEditOperation(kind="set", anchor_context_lines=0)
        assert op_min.anchor_context_lines == 0

        op_max = FileEditOperation(kind="set", anchor_context_lines=10)
        assert op_max.anchor_context_lines == 10

        with pytest.raises(Exception):
            FileEditOperation(kind="set", anchor_context_lines=-1)

        with pytest.raises(Exception):
            FileEditOperation(kind="set", anchor_context_lines=11)

    def test_position_values(self):
        for pos in ("before", "after", "into"):
            op = FileEditOperation(kind="insert", position=pos)
            assert op.position == pos


class TestFileEditInput:
    """Tests for the FileEditInput Pydantic model."""

    def test_defaults(self, basic_operation):
        inp = FileEditInput(file_path="/etc/test.conf", operation=basic_operation)
        assert inp.description == ""
        assert inp.preferred_tier is None
        assert inp.max_tier == 5
        assert inp.tier5_justification is None
        assert inp.dropin_name is None
        assert inp.dropin_priority == 99
        assert inp.skip_validation is False
        assert inp.skip_backup is False
        assert inp.incident_id is None

    def test_all_fields(self, basic_operation):
        inp = FileEditInput(
            file_path="/etc/test.conf",
            operation=basic_operation,
            description="full input test",
            preferred_tier=2,
            max_tier=3,
            tier5_justification="reason",
            dropin_name="custom",
            dropin_priority=50,
            skip_validation=True,
            skip_backup=True,
            incident_id="INC-123",
        )
        assert inp.preferred_tier == 2
        assert inp.max_tier == 3
        assert inp.dropin_priority == 50
        assert inp.incident_id == "INC-123"

    def test_max_tier_bounds(self, basic_operation):
        with pytest.raises(Exception):
            FileEditInput(file_path="/etc/test.conf", operation=basic_operation, max_tier=6)
        with pytest.raises(Exception):
            FileEditInput(file_path="/etc/test.conf", operation=basic_operation, max_tier=-1)

    def test_dropin_priority_bounds(self, basic_operation):
        with pytest.raises(Exception):
            FileEditInput(file_path="/etc/test.conf", operation=basic_operation, dropin_priority=100)
        with pytest.raises(Exception):
            FileEditInput(file_path="/etc/test.conf", operation=basic_operation, dropin_priority=-1)

    def test_preferred_tier_bounds(self, basic_operation):
        inp = FileEditInput(file_path="/etc/test.conf", operation=basic_operation, preferred_tier=0)
        assert inp.preferred_tier == 0
        inp = FileEditInput(file_path="/etc/test.conf", operation=basic_operation, preferred_tier=5)
        assert inp.preferred_tier == 5


class TestTierEscalationRecord:
    """Tests for the TierEscalationRecord model."""

    def test_defaults(self):
        rec = TierEscalationRecord(tier=0, tier_name="Native Override", reason="no drop-in dir")
        assert rec.tool_unavailable is False

    def test_all_fields(self):
        rec = TierEscalationRecord(tier=1, tier_name="Augeas", reason="augeas not installed", tool_unavailable=True)
        assert rec.tier == 1
        assert rec.tool_unavailable is True


class TestFileEditOutput:
    """Tests for the FileEditOutput model."""

    def test_defaults(self):
        out = FileEditOutput(
            file_path="/etc/test.conf",
            tier_used=2,
            tier_name="Structured Data",
            tier_selection_reason="json file",
        )
        assert out.escalation_trail == ()
        assert out.tool_used is None
        assert out.diff == ""
        assert out.backup_path is None
        assert out.validation_passed is True

    def test_all_fields(self):
        esc = TierEscalationRecord(tier=0, tier_name="Native Override", reason="skipped")
        out = FileEditOutput(
            file_path="/etc/test.conf",
            tier_used=3,
            tier_name="Format Editors",
            tier_selection_reason="ini file",
            escalation_trail=(esc,),
            tool_used="crudini",
            diff="--- a\n+++ b",
            backup_path="/tmp/backup",
            validation_passed=False,
        )
        assert len(out.escalation_trail) == 1
        assert out.tool_used == "crudini"
        assert out.validation_passed is False


# =============================================================================
# Spec Tests
# =============================================================================


class TestFileEditCapabilitySpec:
    """Tests for the spec property."""

    def test_spec_name(self, capability):
        assert capability.spec.name == "file.edit"

    def test_spec_domain(self, capability):
        assert capability.spec.domain == "file"

    def test_spec_risk(self, capability):
        assert capability.spec.risk == "medium"

    def test_spec_trust_level(self, capability):
        assert capability.spec.trust_level == "core"

    def test_spec_version(self, capability):
        assert capability.spec.version == "1.0.0"

    def test_spec_requires_privilege_false(self, capability):
        assert capability.spec.requires_privilege is False

    def test_spec_not_idempotent(self, capability):
        assert capability.spec.idempotent is False

    def test_spec_side_effects(self, capability):
        effects = capability.spec.side_effects
        assert len(effects) == 1
        assert effects[0].kind == "file_write"
        assert effects[0].reversible is True

    def test_spec_dependencies(self, capability):
        deps = capability.spec.dependencies
        assert "jq" in deps
        assert "yq" in deps
        assert "augeas" in deps
        assert "crudini" in deps
        assert "xmlstarlet" in deps

    def test_spec_schema_names(self, capability):
        assert capability.spec.input_schema_name == "FileEditInput"
        assert capability.spec.output_schema_name == "FileEditOutput"

    def test_spec_summary(self, capability):
        assert "Edit a file" in capability.spec.summary


# =============================================================================
# _convert_operation Tests
# =============================================================================


class TestConvertOperation:
    """Tests for _convert_operation helper."""

    @patch("elle.ops.editing.EditOperation")
    def test_convert_basic_operation(self, mock_edit_op, capability, basic_operation):
        """Verify _convert_operation passes all fields to EditOperation."""
        mock_edit_op.return_value = MagicMock()
        capability._convert_operation(basic_operation)
        mock_edit_op.assert_called_once_with(
            kind="set",
            path="/some/key",
            value="42",
            anchor_pattern=None,
            anchor_context_lines=3,
            section=None,
            key=None,
            position=None,
        )

    @patch("elle.ops.editing.EditOperation")
    def test_convert_all_fields(self, mock_edit_op, capability):
        """Verify _convert_operation maps all FileEditOperation fields."""
        op = FileEditOperation(
            kind="insert",
            path="/xpath",
            value="val",
            anchor_pattern=r"^#",
            anchor_context_lines=7,
            section="sec",
            key="k",
            position="before",
        )
        mock_edit_op.return_value = MagicMock()
        capability._convert_operation(op)
        mock_edit_op.assert_called_once_with(
            kind="insert",
            path="/xpath",
            value="val",
            anchor_pattern=r"^#",
            anchor_context_lines=7,
            section="sec",
            key="k",
            position="before",
        )

    @patch("elle.ops.editing.EditOperation")
    def test_convert_returns_edit_operation_instance(self, mock_edit_op, capability, basic_operation):
        """_convert_operation returns whatever EditOperation() produces."""
        sentinel = MagicMock(name="edit_op_sentinel")
        mock_edit_op.return_value = sentinel
        result = capability._convert_operation(basic_operation)
        assert result is sentinel


# =============================================================================
# dry_run Tests
# =============================================================================


class TestDryRun:
    """Tests for the dry_run method."""

    @patch("elle.capabilities.core.file_edit.FileEditCapability._convert_operation")
    def test_dry_run_happy_path(self, mock_convert, capability, basic_input):
        """Successful dry run returns a valid DryRunResult."""
        mock_edit_op = MagicMock()
        mock_convert.return_value = mock_edit_op

        mock_selection = _make_tier_selection(tier=2, tier_name="Structured Data", reason="json file", tool_used="jq")
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_preview = MagicMock()
        mock_preview.is_valid = True
        mock_preview.diff = "--- a\n+++ b"
        mock_preview.requires_privilege = False
        mock_tier_instance = MagicMock()
        mock_tier_instance.preview.return_value = mock_preview
        mock_get_tier = MagicMock(return_value=mock_tier_instance)

        with (
            patch("elle.ops.editing.TierSelector", mock_selector_cls),
            patch("elle.ops.editing.tiers.get_tier_instance", mock_get_tier),
        ):
            result = capability.dry_run(basic_input)

        assert isinstance(result, DryRunResult)
        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert result.estimated_risk == "medium"
        assert basic_input.file_path in result.would_modify
        assert len(result.would_execute) == 1
        assert "Tier 2" in result.would_execute[0]
        assert result.diff == "--- a\n+++ b"
        assert "Structured Data" in result.preview_text

    @patch("elle.capabilities.core.file_edit.FileEditCapability._convert_operation")
    def test_dry_run_tier5_no_justification(self, mock_convert, capability, tier5_input):
        """Tier 5 selected without justification returns invalid DryRunResult."""
        mock_edit_op = MagicMock()
        mock_convert.return_value = mock_edit_op

        mock_selection = _make_tier_selection(tier=5, tier_name="Text Patching", reason="last resort")
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        with (
            patch("elle.ops.editing.TierSelector", mock_selector_cls),
            patch("elle.ops.editing.tiers.get_tier_instance"),
        ):
            result = capability.dry_run(tier5_input)

        assert result.is_valid is False
        assert len(result.validation_errors) > 0
        assert "justification" in result.validation_errors[0].lower()
        assert result.requires_confirmation is True
        assert len(result.would_execute) == 0

    @patch("elle.capabilities.core.file_edit.FileEditCapability._convert_operation")
    def test_dry_run_tier5_with_justification(self, mock_convert, capability, tier5_justified_input):
        """Tier 5 selected with justification proceeds to preview."""
        mock_edit_op = MagicMock()
        mock_convert.return_value = mock_edit_op

        mock_selection = _make_tier_selection(tier=5, tier_name="Text Patching", reason="last resort", tool_used="sed")
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_preview = MagicMock()
        mock_preview.is_valid = True
        mock_preview.diff = "--- old\n+++ new"
        mock_preview.requires_privilege = True
        mock_tier_instance = MagicMock()
        mock_tier_instance.preview.return_value = mock_preview
        mock_get_tier = MagicMock(return_value=mock_tier_instance)

        with (
            patch("elle.ops.editing.TierSelector", mock_selector_cls),
            patch("elle.ops.editing.tiers.get_tier_instance", mock_get_tier),
        ):
            result = capability.dry_run(tier5_justified_input)

        assert result.is_valid is True
        assert "Text Patching" in result.preview_text
        # Source uses capital "R" in "Requires privilege:"
        assert "Requires privilege: True" in result.preview_text

    @patch("elle.capabilities.core.file_edit.FileEditCapability._convert_operation")
    def test_dry_run_exception(self, mock_convert, capability, basic_input):
        """Exceptions during dry run produce an invalid DryRunResult with error."""
        mock_convert.side_effect = ImportError("module not found")

        result = capability.dry_run(basic_input)

        assert result.is_valid is False
        assert len(result.validation_errors) > 0
        assert "module not found" in result.validation_errors[0]
        assert result.requires_confirmation is True

    @patch("elle.capabilities.core.file_edit.FileEditCapability._convert_operation")
    def test_dry_run_tool_used_none(self, mock_convert, capability, basic_input):
        """Preview text shows 'default' when tool_used is None."""
        mock_edit_op = MagicMock()
        mock_convert.return_value = mock_edit_op

        mock_selection = _make_tier_selection(
            tier=4, tier_name="Document-aware", reason="markdown file", tool_used=None
        )
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_preview = MagicMock()
        mock_preview.is_valid = True
        mock_preview.diff = ""
        mock_preview.requires_privilege = False
        mock_tier_instance = MagicMock()
        mock_tier_instance.preview.return_value = mock_preview
        mock_get_tier = MagicMock(return_value=mock_tier_instance)

        with (
            patch("elle.ops.editing.TierSelector", mock_selector_cls),
            patch("elle.ops.editing.tiers.get_tier_instance", mock_get_tier),
        ):
            result = capability.dry_run(basic_input)

        assert result.is_valid is True
        assert "default tool" in result.would_execute[0]
        assert "Tool: default" in result.preview_text


# =============================================================================
# run Tests
# =============================================================================


class TestRun:
    """Tests for the run method."""

    def test_run_success_no_running_loop(self, capability, basic_input):
        """Successful run when no event loop is running."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_validation = MagicMock()
        mock_validation.passed = True
        edit_result = _make_tier_edit_result(success=True, validation=mock_validation)

        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert result.success is True
        assert result.output is not None
        assert result.output.file_path == "/etc/test.conf"
        assert result.output.tier_used == 2
        assert result.output.validation_passed is True
        assert result.evidence.verification_passed is True
        assert len(result.evidence.files_modified) == 1
        assert result.execution_time_ms >= 0

    def test_run_success_running_loop(self, capability, basic_input):
        """Successful run when an event loop is already running (ThreadPoolExecutor path)."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_validation = MagicMock()
        mock_validation.passed = True
        edit_result = _make_tier_edit_result(success=True, validation=mock_validation)

        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=True):
            result = capability.run(basic_input)

        assert result.success is True
        assert result.output.tier_used == 2

    def test_run_failure_result(self, capability, basic_input):
        """Edit operation returns success=False -- result captures the error."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        edit_result = _make_tier_edit_result(
            success=False,
            error="syntax error in file",
            validation=None,
        )

        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert result.success is False
        assert result.error == "syntax error in file"
        assert result.output.validation_passed is False
        assert result.evidence.verification_passed is False
        assert len(result.evidence.files_modified) == 0

    def test_run_tier5_no_justification_raises(self, capability, tier5_input):
        """Tier 5 without justification raises and returns error result."""
        mock_selection = _make_tier_selection(tier=5, tier_name="Text Patching", reason="last resort")
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        # We need the Tier5RequiresJustificationError to actually be raised,
        # so we mock it as a real exception class that the code can instantiate.
        mock_error_cls = type(
            "Tier5RequiresJustificationError",
            (Exception,),
            {"__init__": lambda self, fp: Exception.__init__(self, f"Tier 5 requires justification for {fp}")},
        )

        _make_tier_edit_result()  # won't be used
        mock_get_tier = MagicMock()

        with (
            patch("elle.ops.editing.TierSelector", mock_selector_cls),
            patch("elle.ops.editing.Tier5RequiresJustificationError", mock_error_cls),
            patch("elle.ops.editing.tiers.get_tier_instance", mock_get_tier),
        ):
            result = capability.run(tier5_input)

        assert result.success is False
        assert "justification" in result.error.lower()
        assert result.evidence.verification_passed is False

    @patch("elle.capabilities.core.file_edit.FileEditCapability._convert_operation")
    def test_run_import_error(self, mock_convert, capability, basic_input):
        """General exception during run produces error result."""
        mock_convert.side_effect = ImportError("missing editing module")

        result = capability.run(basic_input)

        assert result.success is False
        assert "missing editing module" in result.error
        assert result.evidence.verification_passed is False
        assert result.execution_time_ms >= 0

    def test_run_success_with_no_validation(self, capability, basic_input):
        """When validation is None, validation_passed defaults to True."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        edit_result = _make_tier_edit_result(success=True, validation=None)
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert result.success is True
        assert result.output.validation_passed is True
        assert result.evidence.verification_passed is True

    def test_run_success_with_failed_validation(self, capability, basic_input):
        """When validation.passed is False, it is reflected in the output."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_validation = MagicMock()
        mock_validation.passed = False
        edit_result = _make_tier_edit_result(success=True, validation=mock_validation)
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert result.success is True
        assert result.output.validation_passed is False
        assert result.evidence.verification_passed is False

    def test_run_escalation_trail_conversion(self, capability, basic_input):
        """Escalation trail from TierSelection is converted to TierEscalationRecords."""
        esc1 = MagicMock()
        esc1.tier = 0
        esc1.tier_name = "Native Override"
        esc1.reason = "no drop-in directory"
        esc1.tool_unavailable = False

        esc2 = MagicMock()
        esc2.tier = 1
        esc2.tier_name = "Augeas"
        esc2.reason = "augeas not installed"
        esc2.tool_unavailable = True

        mock_selection = _make_tier_selection(tier=2, escalation_trail=(esc1, esc2))
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_validation = MagicMock()
        mock_validation.passed = True
        edit_result = _make_tier_edit_result(success=True, validation=mock_validation)
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert result.success is True
        trail = result.output.escalation_trail
        assert len(trail) == 2
        assert trail[0].tier == 0
        assert trail[0].tier_name == "Native Override"
        assert trail[0].tool_unavailable is False
        assert trail[1].tier == 1
        assert trail[1].tool_unavailable is True

    def test_run_evidence_on_success(self, capability, basic_input):
        """Evidence fields are populated correctly on success."""
        mock_selection = _make_tier_selection(tier=3, tier_name="Format Editors", tool_used="crudini")
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_validation = MagicMock()
        mock_validation.passed = True
        edit_result = _make_tier_edit_result(
            success=True,
            validation=mock_validation,
            tier_used=3,
            tier_name="Format Editors",
            tool_used="crudini",
            diff="diff output here",
        )
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert "Tier 3: crudini" in result.evidence.commands_executed
        assert basic_input.file_path in result.evidence.files_modified
        assert "Format Editors" in result.evidence.rationale
        assert result.evidence.verification_details == "diff output here"

    def test_run_side_effects_on_success(self, capability, basic_input):
        """Side effects are recorded on successful edit."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_validation = MagicMock()
        mock_validation.passed = True
        edit_result = _make_tier_edit_result(success=True, validation=mock_validation)
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert len(result.side_effects_applied) == 1
        se = result.side_effects_applied[0]
        assert se.kind == "file_write"
        assert se.target == basic_input.file_path
        assert se.reversible is True

    def test_run_failure_evidence(self, capability, basic_input):
        """Evidence on failure has empty files_modified and failure rationale."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        edit_result = _make_tier_edit_result(success=False, error="write permission denied", validation=None)
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert result.success is False
        assert len(result.evidence.files_modified) == 0
        assert "Edit failed" in result.evidence.rationale

    def test_run_exception_evidence(self, capability, basic_input):
        """Evidence on exception path has empty commands and exception rationale."""
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.side_effect = RuntimeError("selector crashed")

        edit_result = _make_tier_edit_result()  # won't be used
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert result.success is False
        assert "selector crashed" in result.error
        assert len(result.evidence.commands_executed) == 0
        assert "Exception" in result.evidence.rationale

    def test_run_executed_at_is_datetime(self, capability, basic_input):
        """executed_at field is a datetime instance."""
        mock_selection = _make_tier_selection()
        mock_selector_cls = MagicMock()
        mock_selector_cls.return_value.select_tier.return_value = mock_selection

        mock_validation = MagicMock()
        mock_validation.passed = True
        edit_result = _make_tier_edit_result(success=True, validation=mock_validation)
        mock_get_tier = MagicMock()

        with _patch_run_deps(mock_selector_cls, mock_get_tier, edit_result, loop_running=False):
            result = capability.run(basic_input)

        assert isinstance(result.executed_at, datetime)


# =============================================================================
# verify Tests
# =============================================================================


class TestVerify:
    """Tests for the verify method."""

    def test_verify_file_exists(self, capability, basic_input):
        """verify() returns passed=True when file exists."""
        with patch.object(Path, "exists", return_value=True):
            result = capability.verify(basic_input)

        assert isinstance(result, VerificationResult)
        assert result.passed is True
        assert "file_exists" in result.checks_performed
        assert "content_valid" in result.checks_performed
        assert result.actual_state["exists"] is True
        assert result.actual_state["readable"] is True

    def test_verify_file_not_exists(self, capability, basic_input):
        """verify() returns passed=False when file does not exist."""
        with patch.object(Path, "exists", return_value=False):
            result = capability.verify(basic_input)

        assert result.passed is False
        assert "file_exists" in result.checks_performed
        assert result.actual_state == {"exists": False}
        assert result.expected_state == {"exists": True}
        assert len(result.discrepancies) > 0
        assert "does not exist" in result.discrepancies[0].lower()

    def test_verify_exception(self, capability, basic_input):
        """verify() returns passed=False on exception."""
        with patch.object(Path, "exists", side_effect=PermissionError("denied")):
            result = capability.verify(basic_input)

        assert result.passed is False
        assert "verify" in result.checks_performed
        assert "denied" in result.discrepancies[0]


# =============================================================================
# rollback Tests
# =============================================================================


class TestRollback:
    """Tests for the rollback method."""

    def test_rollback_with_backup(self, capability, basic_input):
        """Rollback succeeds when backup_path is present and restore works."""
        mock_output = MagicMock()
        mock_output.backup_path = "/var/lib/elle/backups/test.conf.bak"

        cap_result = CapabilityResult(
            success=True,
            output=mock_output,
        )

        mock_restore = MagicMock()
        with patch("elle.ops.augeas.backup.restore_file", mock_restore):
            result = capability.rollback(basic_input, cap_result)

        assert isinstance(result, RollbackResult)
        assert result.success is True
        assert basic_input.file_path in result.rolled_back
        assert len(result.failed_to_rollback) == 0
        assert "restore from" in result.commands_executed[0]
        mock_restore.assert_called_once_with("/var/lib/elle/backups/test.conf.bak")

    def test_rollback_no_backup(self, capability, basic_input):
        """Rollback fails when no backup_path is available."""
        mock_output = MagicMock()
        mock_output.backup_path = None

        cap_result = CapabilityResult(
            success=True,
            output=mock_output,
        )

        with patch("elle.ops.augeas.backup.restore_file"):
            result = capability.rollback(basic_input, cap_result)

        assert result.success is False
        assert result.error == "No backup available"
        assert basic_input.file_path in result.failed_to_rollback

    def test_rollback_no_backup_path_attr(self, capability, basic_input):
        """Rollback fails when output has no backup_path attribute."""
        mock_output = MagicMock(spec=[])  # spec=[] means no attributes

        cap_result = CapabilityResult(
            success=True,
            output=mock_output,
        )

        with patch("elle.ops.augeas.backup.restore_file"):
            result = capability.rollback(basic_input, cap_result)

        assert result.success is False
        assert result.error == "No backup available"

    def test_rollback_empty_backup_path(self, capability, basic_input):
        """Rollback fails when backup_path is an empty string."""
        mock_output = MagicMock()
        mock_output.backup_path = ""

        cap_result = CapabilityResult(
            success=True,
            output=mock_output,
        )

        with patch("elle.ops.augeas.backup.restore_file"):
            result = capability.rollback(basic_input, cap_result)

        assert result.success is False
        assert result.error == "No backup available"

    def test_rollback_restore_exception(self, capability, basic_input):
        """Rollback catches exceptions from restore_file."""
        mock_output = MagicMock()
        mock_output.backup_path = "/var/lib/elle/backups/test.conf.bak"

        cap_result = CapabilityResult(
            success=True,
            output=mock_output,
        )

        with patch("elle.ops.augeas.backup.restore_file", side_effect=OSError("permission denied")):
            result = capability.rollback(basic_input, cap_result)

        assert result.success is False
        assert "permission denied" in result.error
        assert basic_input.file_path in result.failed_to_rollback

    def test_rollback_import_error(self, capability, basic_input):
        """Rollback catches import errors from the backup module."""
        cap_result = CapabilityResult(
            success=True,
            output=MagicMock(backup_path="/some/path"),
        )

        with patch.dict(
            "sys.modules",
            {"elle.ops.augeas.backup": None},
        ):
            # The import will fail because we set the module to None
            result = capability.rollback(basic_input, cap_result)

        assert result.success is False
        assert basic_input.file_path in result.failed_to_rollback


# =============================================================================
# Export Tests
# =============================================================================


class TestExports:
    """Tests for module-level exports."""

    def test_file_edit_capabilities_list(self):
        """FILE_EDIT_CAPABILITIES contains FileEditCapability."""
        assert FileEditCapability in FILE_EDIT_CAPABILITIES
        assert len(FILE_EDIT_CAPABILITIES) == 1

    def test_capability_is_instantiable(self):
        """FileEditCapability can be instantiated from the export list."""
        cls = FILE_EDIT_CAPABILITIES[0]
        instance = cls()
        assert instance.spec.name == "file.edit"
