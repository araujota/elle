"""Tests for the 6-Tier Deterministic Editing Stack Pydantic models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from elle.ops.editing.models import (
    TIER_NAMES,
    EditingStackError,
    EditingStackStatus,
    EditOperation,
    FileEditPreview,
    FileEditRequest,
    NoApplicableTierError,
    Tier5RequiresJustificationError,
    TierApplicability,
    TierEditResult,
    TierEscalation,
    TierInfo,
    TierSelection,
    TierUnavailableError,
    ValidationResult,
)


# =============================================================================
# TierInfo
# =============================================================================


class TestTierInfo:
    """Tests for TierInfo model."""

    def test_create_full(self) -> None:
        info = TierInfo(
            number=0,
            name="Native Override",
            tools=("systemd-dropin",),
            use_case="systemd unit overrides",
        )
        assert info.number == 0
        assert info.name == "Native Override"
        assert info.tools == ("systemd-dropin",)
        assert info.use_case == "systemd unit overrides"
        assert info.available is True
        assert info.missing_dependencies == ()

    def test_defaults(self) -> None:
        info = TierInfo(
            number=3,
            name="Format Editors",
            tools=("yq", "jq"),
            use_case="YAML/JSON editing",
        )
        assert info.available is True
        assert info.missing_dependencies == ()

    def test_unavailable_tier(self) -> None:
        info = TierInfo(
            number=1,
            name="Augeas",
            tools=("augeas",),
            use_case="Config lenses",
            available=False,
            missing_dependencies=("augeas-tools",),
        )
        assert info.available is False
        assert info.missing_dependencies == ("augeas-tools",)

    def test_frozen(self) -> None:
        info = TierInfo(
            number=2,
            name="Structured Data",
            tools=("crudini",),
            use_case="INI files",
        )
        with pytest.raises(ValidationError):
            info.name = "Changed"

    def test_invalid_tier_number(self) -> None:
        with pytest.raises(ValidationError):
            TierInfo(
                number=9,
                name="Invalid",
                tools=(),
                use_case="nothing",
            )

    def test_serialization_roundtrip(self) -> None:
        info = TierInfo(
            number=4,
            name="Document-aware",
            tools=("xmlstarlet",),
            use_case="XML editing",
            available=False,
            missing_dependencies=("xmlstarlet",),
        )
        data = info.model_dump()
        restored = TierInfo.model_validate(data)
        assert restored == info
        assert restored.number == 4
        assert restored.missing_dependencies == ("xmlstarlet",)

    def test_all_tier_numbers(self) -> None:
        for num in (0, 1, 2, 3, 4, 5):
            info = TierInfo(
                number=num,
                name=TIER_NAMES[num],
                tools=("tool",),
                use_case="test",
            )
            assert info.number == num


# =============================================================================
# EditOperation
# =============================================================================


class TestEditOperation:
    """Tests for EditOperation model."""

    def test_set_operation(self) -> None:
        op = EditOperation(kind="set", path="/etc/sshd", value="no")
        assert op.kind == "set"
        assert op.path == "/etc/sshd"
        assert op.value == "no"

    def test_defaults(self) -> None:
        op = EditOperation(kind="rm")
        assert op.path is None
        assert op.value is None
        assert op.anchor_pattern is None
        assert op.anchor_context_lines == 3
        assert op.section is None
        assert op.key is None
        assert op.position is None

    def test_ini_operation(self) -> None:
        op = EditOperation(
            kind="set",
            section="database",
            key="host",
            value="localhost",
        )
        assert op.section == "database"
        assert op.key == "host"

    def test_tier5_anchor(self) -> None:
        op = EditOperation(
            kind="replace",
            anchor_pattern=r"^ListenAddress\s+",
            anchor_context_lines=5,
            value="ListenAddress 0.0.0.0",
        )
        assert op.anchor_pattern == r"^ListenAddress\s+"
        assert op.anchor_context_lines == 5

    def test_insert_with_position(self) -> None:
        op = EditOperation(
            kind="insert",
            path="/root/child",
            value="<new/>",
            position="after",
        )
        assert op.kind == "insert"
        assert op.position == "after"

    def test_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            EditOperation(kind="invalid_op")

    def test_invalid_position(self) -> None:
        with pytest.raises(ValidationError):
            EditOperation(kind="insert", position="middle")

    def test_frozen(self) -> None:
        op = EditOperation(kind="set", value="test")
        with pytest.raises(ValidationError):
            op.value = "changed"

    def test_all_kinds(self) -> None:
        for kind in ("set", "rm", "add", "replace", "append", "insert"):
            op = EditOperation(kind=kind)
            assert op.kind == kind

    def test_serialization_roundtrip(self) -> None:
        op = EditOperation(
            kind="set",
            path="/files/etc/ssh/sshd_config",
            value="no",
            section="sshd",
            key="PermitRootLogin",
        )
        data = op.model_dump()
        restored = EditOperation.model_validate(data)
        assert restored == op


# =============================================================================
# TierApplicability
# =============================================================================


class TestTierApplicability:
    """Tests for TierApplicability model."""

    def test_applicable(self) -> None:
        ta = TierApplicability(
            tier=1,
            applicable=True,
            reason="Augeas has a lens for sshd_config",
        )
        assert ta.tier == 1
        assert ta.applicable is True
        assert ta.confidence == 1.0
        assert ta.tool_available is True
        assert ta.missing_tool is None

    def test_not_applicable(self) -> None:
        ta = TierApplicability(
            tier=2,
            applicable=False,
            reason="File is not INI format",
            confidence=0.0,
            tool_available=False,
            missing_tool="crudini",
        )
        assert ta.applicable is False
        assert ta.confidence == 0.0
        assert ta.missing_tool == "crudini"

    def test_confidence_bounds(self) -> None:
        ta = TierApplicability(tier=3, applicable=True, reason="ok", confidence=0.5)
        assert ta.confidence == 0.5

    def test_confidence_too_high(self) -> None:
        with pytest.raises(ValidationError):
            TierApplicability(tier=3, applicable=True, reason="ok", confidence=1.5)

    def test_confidence_too_low(self) -> None:
        with pytest.raises(ValidationError):
            TierApplicability(tier=3, applicable=True, reason="ok", confidence=-0.1)

    def test_serialization_roundtrip(self) -> None:
        ta = TierApplicability(
            tier=0,
            applicable=True,
            reason="drop-in supported",
            confidence=0.95,
            tool_available=True,
        )
        data = ta.model_dump()
        restored = TierApplicability.model_validate(data)
        assert restored == ta


# =============================================================================
# TierEscalation
# =============================================================================


class TestTierEscalation:
    """Tests for TierEscalation model."""

    def test_create(self) -> None:
        esc = TierEscalation(
            tier=0,
            tier_name="Native Override",
            reason="Not a systemd unit file",
        )
        assert esc.tier == 0
        assert esc.tier_name == "Native Override"
        assert esc.tool_unavailable is False

    def test_tool_unavailable(self) -> None:
        esc = TierEscalation(
            tier=1,
            tier_name="Augeas",
            reason="augeas-tools not installed",
            tool_unavailable=True,
        )
        assert esc.tool_unavailable is True

    def test_frozen(self) -> None:
        esc = TierEscalation(tier=2, tier_name="Structured Data", reason="skip")
        with pytest.raises(ValidationError):
            esc.reason = "changed"

    def test_serialization_roundtrip(self) -> None:
        esc = TierEscalation(
            tier=3,
            tier_name="Format Editors",
            reason="yq not available",
            tool_unavailable=True,
        )
        data = esc.model_dump()
        restored = TierEscalation.model_validate(data)
        assert restored == esc


# =============================================================================
# TierSelection
# =============================================================================


class TestTierSelection:
    """Tests for TierSelection model."""

    def test_create_without_escalation(self) -> None:
        sel = TierSelection(
            tier=0,
            tier_name="Native Override",
            reason="systemd unit detected",
        )
        assert sel.tier == 0
        assert sel.escalation_trail == ()
        assert sel.tool_used is None

    def test_with_escalation_trail(self) -> None:
        trail = (
            TierEscalation(tier=0, tier_name="Native Override", reason="not a unit file"),
            TierEscalation(tier=1, tier_name="Augeas", reason="no lens", tool_unavailable=True),
        )
        sel = TierSelection(
            tier=2,
            tier_name="Structured Data",
            reason="File is INI format",
            escalation_trail=trail,
            tool_used="crudini",
        )
        assert sel.tier == 2
        assert len(sel.escalation_trail) == 2
        assert sel.tool_used == "crudini"

    def test_serialization_roundtrip(self) -> None:
        sel = TierSelection(
            tier=5,
            tier_name="Text Patching",
            reason="No structured tool available",
            tool_used="sed",
        )
        data = sel.model_dump()
        restored = TierSelection.model_validate(data)
        assert restored == sel


# =============================================================================
# ValidationResult
# =============================================================================


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_passed(self) -> None:
        vr = ValidationResult(passed=True, method="nginx -t")
        assert vr.passed is True
        assert vr.method == "nginx -t"
        assert vr.output == ""
        assert vr.errors == ()

    def test_failed_with_errors(self) -> None:
        vr = ValidationResult(
            passed=False,
            method="python -c 'import json; json.load(...)'",
            output="JSONDecodeError: ...",
            errors=("Invalid JSON at line 5",),
        )
        assert vr.passed is False
        assert len(vr.errors) == 1

    def test_serialization_roundtrip(self) -> None:
        vr = ValidationResult(
            passed=True,
            method="yamllint",
            output="ok",
            errors=(),
        )
        data = vr.model_dump()
        restored = ValidationResult.model_validate(data)
        assert restored == vr


# =============================================================================
# TierEditResult
# =============================================================================


class TestTierEditResult:
    """Tests for TierEditResult model."""

    def test_successful_result(self) -> None:
        result = TierEditResult(
            success=True,
            file_path="/etc/ssh/sshd_config",
            tier_used=1,
            tier_name="Augeas",
            tier_selection_reason="Augeas lens available",
            tool_used="augeas",
            diff="--- a\n+++ b\n@@ ...",
        )
        assert result.success is True
        assert result.file_path == "/etc/ssh/sshd_config"
        assert result.tier_used == 1
        assert result.backup_path is None
        assert result.rollback_applied is False
        assert result.validation is None
        assert result.error is None
        assert result.requires_privilege is False
        assert isinstance(result.completed_at, datetime)

    def test_failed_result_with_rollback(self) -> None:
        result = TierEditResult(
            success=False,
            file_path="/etc/nginx/nginx.conf",
            tier_used=3,
            tier_name="Format Editors",
            tier_selection_reason="YAML file",
            error="Validation failed after edit",
            rollback_applied=True,
            backup_path="/tmp/nginx.conf.bak",
            validation=ValidationResult(
                passed=False,
                method="nginx -t",
                errors=("syntax error",),
            ),
        )
        assert result.success is False
        assert result.rollback_applied is True
        assert result.validation is not None
        assert result.validation.passed is False

    def test_with_escalation_trail(self) -> None:
        trail = (
            TierEscalation(tier=0, tier_name="Native Override", reason="not applicable"),
        )
        result = TierEditResult(
            success=True,
            file_path="/etc/config",
            tier_used=2,
            tier_name="Structured Data",
            tier_selection_reason="INI format",
            escalation_trail=trail,
        )
        assert len(result.escalation_trail) == 1

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            TierEditResult(success=True)

    def test_serialization_roundtrip(self) -> None:
        result = TierEditResult(
            success=True,
            file_path="/etc/test.conf",
            tier_used=5,
            tier_name="Text Patching",
            tier_selection_reason="fallback",
            tool_used="sed",
            diff="--- a\n+++ b",
            requires_privilege=True,
        )
        data = result.model_dump()
        restored = TierEditResult.model_validate(data)
        assert restored.file_path == result.file_path
        assert restored.tier_used == result.tier_used
        assert restored.requires_privilege is True


# =============================================================================
# FileEditRequest
# =============================================================================


class TestFileEditRequest:
    """Tests for FileEditRequest model."""

    @pytest.fixture()
    def basic_operation(self) -> EditOperation:
        return EditOperation(kind="set", path="/key", value="val")

    def test_minimal(self, basic_operation: EditOperation) -> None:
        req = FileEditRequest(
            file_path="/etc/test.conf",
            operation=basic_operation,
            description="Set key to val",
        )
        assert req.file_path == "/etc/test.conf"
        assert req.preferred_tier is None
        assert req.max_tier == 5
        assert req.tier5_justification is None
        assert req.dropin_name is None
        assert req.dropin_priority == 99
        assert req.dry_run is False
        assert req.skip_backup is False
        assert req.skip_validation is False
        assert req.incident_id is None

    def test_full_request(self, basic_operation: EditOperation) -> None:
        req = FileEditRequest(
            file_path="/etc/ssh/sshd_config",
            operation=basic_operation,
            description="Disable root login",
            preferred_tier=1,
            max_tier=3,
            tier5_justification="No structured tool",
            dry_run=True,
            incident_id="INC-001",
        )
        assert req.preferred_tier == 1
        assert req.max_tier == 3
        assert req.dry_run is True
        assert req.incident_id == "INC-001"

    def test_dropin_priority_bounds(self, basic_operation: EditOperation) -> None:
        req = FileEditRequest(
            file_path="/etc/systemd/system/foo.service",
            operation=basic_operation,
            description="Systemd override",
            dropin_name="override",
            dropin_priority=0,
        )
        assert req.dropin_priority == 0

    def test_dropin_priority_too_high(self, basic_operation: EditOperation) -> None:
        with pytest.raises(ValidationError):
            FileEditRequest(
                file_path="/etc/systemd/system/foo.service",
                operation=basic_operation,
                description="test",
                dropin_priority=100,
            )

    def test_dropin_priority_negative(self, basic_operation: EditOperation) -> None:
        with pytest.raises(ValidationError):
            FileEditRequest(
                file_path="/etc/systemd/system/foo.service",
                operation=basic_operation,
                description="test",
                dropin_priority=-1,
            )

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            FileEditRequest(file_path="/etc/test.conf")

    def test_serialization_roundtrip(self, basic_operation: EditOperation) -> None:
        req = FileEditRequest(
            file_path="/etc/test.conf",
            operation=basic_operation,
            description="Test edit",
            preferred_tier=2,
            dry_run=True,
        )
        data = req.model_dump()
        restored = FileEditRequest.model_validate(data)
        assert restored == req


# =============================================================================
# FileEditPreview
# =============================================================================


class TestFileEditPreview:
    """Tests for FileEditPreview model."""

    def test_create(self) -> None:
        preview = FileEditPreview(
            file_path="/etc/test.conf",
            tier_selected=2,
            tier_name="Structured Data",
            original_content="key=old",
            proposed_content="key=new",
            diff="--- a\n+++ b\n-key=old\n+key=new",
            diff_colored="\x1b[31m-key=old\x1b[0m\n\x1b[32m+key=new\x1b[0m",
        )
        assert preview.file_path == "/etc/test.conf"
        assert preview.tier_selected == 2
        assert preview.is_valid is True
        assert preview.requires_privilege is False
        assert preview.tool_to_use is None

    def test_with_escalation_trail(self) -> None:
        trail = (
            TierEscalation(tier=0, tier_name="Native Override", reason="not a unit"),
            TierEscalation(tier=1, tier_name="Augeas", reason="no lens"),
        )
        preview = FileEditPreview(
            file_path="/etc/test.conf",
            tier_selected=2,
            tier_name="Structured Data",
            escalation_trail=trail,
            original_content="a",
            proposed_content="b",
            diff="diff",
            diff_colored="diff",
            tool_to_use="crudini",
        )
        assert len(preview.escalation_trail) == 2
        assert preview.tool_to_use == "crudini"


# =============================================================================
# EditingStackStatus
# =============================================================================


class TestEditingStackStatus:
    """Tests for EditingStackStatus model."""

    def test_create(self) -> None:
        tier0 = TierInfo(number=0, name="Native Override", tools=("systemd",), use_case="units")
        tier1 = TierInfo(
            number=1, name="Augeas", tools=("augeas",), use_case="lenses",
            available=False, missing_dependencies=("augeas-tools",),
        )
        status = EditingStackStatus(
            tiers=(tier0, tier1),
            available_tiers=(0,),
            unavailable_tiers=(1,),
            missing_dependencies={1: ("augeas-tools",)},
        )
        assert len(status.tiers) == 2
        assert status.available_tiers == (0,)
        assert status.unavailable_tiers == (1,)
        assert 1 in status.missing_dependencies

    def test_serialization_roundtrip(self) -> None:
        tier = TierInfo(number=0, name="Native Override", tools=("systemd",), use_case="units")
        status = EditingStackStatus(
            tiers=(tier,),
            available_tiers=(0,),
            unavailable_tiers=(),
        )
        data = status.model_dump()
        restored = EditingStackStatus.model_validate(data)
        assert restored.available_tiers == (0,)


# =============================================================================
# Error Types
# =============================================================================


class TestEditingStackErrors:
    """Tests for editing stack error types."""

    def test_base_error(self) -> None:
        err = EditingStackError("something went wrong")
        assert str(err) == "something went wrong"
        assert isinstance(err, Exception)

    def test_tier_unavailable_error(self) -> None:
        err = TierUnavailableError(tier=1, missing_tool="augeas")
        assert err.tier == 1
        assert err.missing_tool == "augeas"
        assert "Tier 1" in str(err)
        assert "augeas" in str(err)
        assert isinstance(err, EditingStackError)

    def test_no_applicable_tier_error(self) -> None:
        trail = [
            TierEscalation(tier=0, tier_name="T0", reason="not applicable"),
            TierEscalation(tier=1, tier_name="T1", reason="no lens"),
        ]
        err = NoApplicableTierError(file_path="/etc/complex.conf", escalation_trail=trail)
        assert err.file_path == "/etc/complex.conf"
        assert len(err.escalation_trail) == 2
        assert "Tier 0" in str(err)
        assert "Tier 1" in str(err)
        assert isinstance(err, EditingStackError)

    def test_tier5_justification_error(self) -> None:
        err = Tier5RequiresJustificationError(file_path="/etc/custom.conf")
        assert err.file_path == "/etc/custom.conf"
        assert "Tier 5" in str(err)
        assert "justification" in str(err).lower()
        assert isinstance(err, EditingStackError)


# =============================================================================
# TIER_NAMES constant
# =============================================================================


class TestTierNames:
    """Tests for TIER_NAMES constant."""

    def test_all_tiers_present(self) -> None:
        assert set(TIER_NAMES.keys()) == {0, 1, 2, 3, 4, 5}

    def test_tier_names_are_strings(self) -> None:
        for name in TIER_NAMES.values():
            assert isinstance(name, str)
            assert len(name) > 0
