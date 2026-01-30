"""Tests for jq operation models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from elle.ops.jq.models import (
    JQChange,
    JQEditPreview,
    JQEditRequest,
    JQEditResult,
    JQError,
    JQFilterError,
    JQOperation,
    JQParseError,
    JQRequest,
    JQResult,
    JQStatus,
    JQUnavailableError,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_jq_operation() -> JQOperation:
    """Create a basic JQOperation for reuse."""
    return JQOperation(kind="query", filter=".name")


@pytest.fixture
def sample_jq_change() -> JQChange:
    """Create a basic JQChange for reuse."""
    return JQChange(
        path=".config.timeout",
        old_value=30,
        new_value=60,
        operation="transform",
    )


@pytest.fixture
def sample_jq_request() -> JQRequest:
    """Create a basic JQRequest for reuse."""
    return JQRequest(
        file_path="/etc/app/config.json",
        filter=".settings.timeout",
        description="Query timeout setting",
    )


@pytest.fixture
def sample_jq_result() -> JQResult:
    """Create a basic successful JQResult for reuse."""
    return JQResult(
        success=True,
        output='"hello"',
        parsed_output="hello",
        filter_used=".greeting",
    )


@pytest.fixture
def sample_jq_edit_request() -> JQEditRequest:
    """Create a basic JQEditRequest for reuse."""
    return JQEditRequest(
        file_path="/etc/app/config.json",
        filter='.timeout = 60',
        description="Set timeout to 60 seconds",
    )


@pytest.fixture
def sample_jq_edit_result(sample_jq_change: JQChange) -> JQEditResult:
    """Create a basic JQEditResult for reuse."""
    return JQEditResult(
        success=True,
        file_path="/etc/app/config.json",
        diff="--- a\n+++ b\n@@ -1 +1 @@\n-30\n+60",
        changes=(sample_jq_change,),
    )


# =============================================================================
# JQOperation
# =============================================================================


class TestJQOperation:
    """Tests for JQOperation model."""

    def test_basic_query_creation(self) -> None:
        """Test creating a query operation."""
        op = JQOperation(kind="query", filter=".name")
        assert op.kind == "query"
        assert op.filter == ".name"

    def test_default_flags_are_false(self) -> None:
        """Test all boolean flags default to False."""
        op = JQOperation(kind="query", filter=".")
        assert op.raw_output is False
        assert op.slurp is False
        assert op.compact is False
        assert op.sort_keys is False

    def test_transform_kind(self) -> None:
        """Test creating a transform operation."""
        op = JQOperation(kind="transform", filter='.name = "new"')
        assert op.kind == "transform"

    def test_validate_kind(self) -> None:
        """Test creating a validate operation."""
        op = JQOperation(kind="validate", filter=".")
        assert op.kind == "validate"

    def test_invalid_kind_rejected(self) -> None:
        """Test that invalid kind values are rejected."""
        with pytest.raises(ValidationError):
            JQOperation(kind="delete", filter=".")

    def test_all_flags_enabled(self) -> None:
        """Test enabling all boolean flags."""
        op = JQOperation(
            kind="query",
            filter=".[] | select(.active)",
            raw_output=True,
            slurp=True,
            compact=True,
            sort_keys=True,
        )
        assert op.raw_output is True
        assert op.slurp is True
        assert op.compact is True
        assert op.sort_keys is True

    def test_frozen_model(self) -> None:
        """Test that JQOperation is immutable."""
        op = JQOperation(kind="query", filter=".")
        with pytest.raises(ValidationError):
            op.kind = "transform"

    def test_missing_required_filter(self) -> None:
        """Test that filter is required."""
        with pytest.raises(ValidationError):
            JQOperation(kind="query")

    def test_missing_required_kind(self) -> None:
        """Test that kind is required."""
        with pytest.raises(ValidationError):
            JQOperation(filter=".")

    def test_serialization_round_trip(self, sample_jq_operation: JQOperation) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_jq_operation.model_dump()
        restored = JQOperation.model_validate(data)
        assert restored == sample_jq_operation
        assert restored.kind == "query"
        assert restored.filter == ".name"

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        op = JQOperation(kind="transform", filter='.x = 1', raw_output=True)
        json_str = op.model_dump_json()
        restored = JQOperation.model_validate_json(json_str)
        assert restored == op

    def test_complex_filter_expression(self) -> None:
        """Test a complex jq filter expression is stored correctly."""
        expr = '.[] | select(.age > 21) | {name, age}'
        op = JQOperation(kind="query", filter=expr)
        assert op.filter == expr


# =============================================================================
# JQChange
# =============================================================================


class TestJQChange:
    """Tests for JQChange model."""

    def test_basic_creation(self) -> None:
        """Test creating a change record."""
        change = JQChange(
            path=".config.timeout",
            old_value=30,
            new_value=60,
        )
        assert change.path == ".config.timeout"
        assert change.old_value == 30
        assert change.new_value == 60

    def test_default_values(self) -> None:
        """Test default values for optional fields."""
        change = JQChange(path=".key")
        assert change.old_value is None
        assert change.new_value is None
        assert change.operation == "transform"

    def test_creation_change(self) -> None:
        """Test a change representing new value creation."""
        change = JQChange(
            path=".new_key",
            old_value=None,
            new_value="created",
            operation="create",
        )
        assert change.old_value is None
        assert change.new_value == "created"

    def test_deletion_change(self) -> None:
        """Test a change representing value deletion."""
        change = JQChange(
            path=".removed_key",
            old_value="was_here",
            new_value=None,
            operation="delete",
        )
        assert change.old_value == "was_here"
        assert change.new_value is None

    def test_complex_value_types(self) -> None:
        """Test storing complex value types (dict, list)."""
        change = JQChange(
            path=".config",
            old_value={"timeout": 30},
            new_value={"timeout": 60, "retries": 3},
        )
        assert change.old_value == {"timeout": 30}
        assert change.new_value["retries"] == 3

    def test_frozen_model(self) -> None:
        """Test that JQChange is immutable."""
        change = JQChange(path=".key")
        with pytest.raises(ValidationError):
            change.path = ".other"

    def test_serialization_round_trip(self, sample_jq_change: JQChange) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_jq_change.model_dump()
        restored = JQChange.model_validate(data)
        assert restored == sample_jq_change

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        change = JQChange(path=".x", old_value=1, new_value=2, operation="update")
        json_str = change.model_dump_json()
        restored = JQChange.model_validate_json(json_str)
        assert restored == change


# =============================================================================
# JQRequest
# =============================================================================


class TestJQRequest:
    """Tests for JQRequest model."""

    def test_file_path_source(self) -> None:
        """Test request with file_path input source."""
        req = JQRequest(file_path="/tmp/data.json", filter=".name")
        assert req.file_path == "/tmp/data.json"
        assert req.json_input is None

    def test_json_input_source(self) -> None:
        """Test request with json_input source."""
        req = JQRequest(json_input='{"name": "test"}', filter=".name")
        assert req.json_input == '{"name": "test"}'
        assert req.file_path is None

    def test_both_sources_allowed(self) -> None:
        """Test that providing both sources does not raise (validation is business logic)."""
        req = JQRequest(
            file_path="/tmp/data.json",
            json_input='{"name": "test"}',
            filter=".name",
        )
        assert req.file_path is not None
        assert req.json_input is not None

    def test_default_options(self) -> None:
        """Test default option values."""
        req = JQRequest(filter=".")
        assert req.raw_output is False
        assert req.slurp is False
        assert req.compact is False
        assert req.sort_keys is False
        assert req.null_input is False
        assert req.description == ""

    def test_all_options_enabled(self) -> None:
        """Test enabling all optional flags."""
        req = JQRequest(
            filter=".",
            raw_output=True,
            slurp=True,
            compact=True,
            sort_keys=True,
            null_input=True,
            description="Generate empty JSON",
        )
        assert req.raw_output is True
        assert req.slurp is True
        assert req.compact is True
        assert req.sort_keys is True
        assert req.null_input is True
        assert req.description == "Generate empty JSON"

    def test_missing_required_filter(self) -> None:
        """Test that filter is required."""
        with pytest.raises(ValidationError):
            JQRequest(file_path="/tmp/data.json")

    def test_frozen_model(self) -> None:
        """Test that JQRequest is immutable."""
        req = JQRequest(filter=".")
        with pytest.raises(ValidationError):
            req.filter = ".modified"

    def test_serialization_round_trip(self, sample_jq_request: JQRequest) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_jq_request.model_dump()
        restored = JQRequest.model_validate(data)
        assert restored == sample_jq_request

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        req = JQRequest(
            file_path="/tmp/test.json",
            filter=".items[]",
            raw_output=True,
            description="Extract items",
        )
        json_str = req.model_dump_json()
        restored = JQRequest.model_validate_json(json_str)
        assert restored == req


# =============================================================================
# JQResult
# =============================================================================


class TestJQResult:
    """Tests for JQResult model."""

    def test_success_result(self) -> None:
        """Test a successful result."""
        result = JQResult(
            success=True,
            output='"hello"',
            parsed_output="hello",
            filter_used=".greeting",
        )
        assert result.success is True
        assert result.output == '"hello"'
        assert result.parsed_output == "hello"
        assert result.error is None
        assert result.exit_code == 0

    def test_failure_result(self) -> None:
        """Test a failed result."""
        result = JQResult(
            success=False,
            error="jq: error: syntax error",
            exit_code=2,
            filter_used=".bad[",
        )
        assert result.success is False
        assert result.error == "jq: error: syntax error"
        assert result.exit_code == 2

    def test_default_values(self) -> None:
        """Test default values on a minimal result."""
        result = JQResult(success=True)
        assert result.output == ""
        assert result.parsed_output is None
        assert result.error is None
        assert result.exit_code == 0
        assert result.filter_used == ""

    def test_completed_at_auto_set(self) -> None:
        """Test that completed_at is automatically set."""
        before = datetime.utcnow()
        result = JQResult(success=True)
        after = datetime.utcnow()
        assert before <= result.completed_at <= after

    def test_parsed_output_complex_type(self) -> None:
        """Test parsed_output with complex types (list, dict)."""
        result = JQResult(
            success=True,
            output='[1,2,3]',
            parsed_output=[1, 2, 3],
        )
        assert result.parsed_output == [1, 2, 3]

    def test_frozen_model(self) -> None:
        """Test that JQResult is immutable."""
        result = JQResult(success=True)
        with pytest.raises(ValidationError):
            result.success = False

    def test_serialization_round_trip(self, sample_jq_result: JQResult) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_jq_result.model_dump()
        restored = JQResult.model_validate(data)
        assert restored.success == sample_jq_result.success
        assert restored.output == sample_jq_result.output
        assert restored.parsed_output == sample_jq_result.parsed_output
        assert restored.filter_used == sample_jq_result.filter_used

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        result = JQResult(
            success=True,
            output="42",
            parsed_output=42,
            filter_used=".count",
            exit_code=0,
        )
        json_str = result.model_dump_json()
        restored = JQResult.model_validate_json(json_str)
        assert restored.success is True
        assert restored.parsed_output == 42


# =============================================================================
# JQEditRequest
# =============================================================================


class TestJQEditRequest:
    """Tests for JQEditRequest model."""

    def test_basic_creation(self) -> None:
        """Test creating a basic edit request."""
        req = JQEditRequest(
            file_path="/etc/app/config.json",
            filter='.timeout = 60',
            description="Set timeout to 60 seconds",
        )
        assert req.file_path == "/etc/app/config.json"
        assert req.filter == '.timeout = 60'
        assert req.description == "Set timeout to 60 seconds"

    def test_default_values(self) -> None:
        """Test default values."""
        req = JQEditRequest(
            file_path="/tmp/test.json",
            filter=".",
            description="no-op",
        )
        assert req.incident_id is None
        assert req.dry_run is False
        assert req.skip_backup is False
        assert req.skip_validation is False
        assert req.sort_keys is False
        assert req.indent == 2

    def test_dry_run_mode(self) -> None:
        """Test dry_run flag."""
        req = JQEditRequest(
            file_path="/tmp/test.json",
            filter='.x = 1',
            description="test",
            dry_run=True,
        )
        assert req.dry_run is True

    def test_incident_id_linked(self) -> None:
        """Test linking to an incident."""
        req = JQEditRequest(
            file_path="/tmp/test.json",
            filter='.x = 1',
            description="test",
            incident_id="inc-abc-123",
        )
        assert req.incident_id == "inc-abc-123"

    def test_indent_valid_range(self) -> None:
        """Test valid indent values (0 to 8)."""
        for indent in (0, 1, 2, 4, 8):
            req = JQEditRequest(
                file_path="/tmp/test.json",
                filter=".",
                description="test",
                indent=indent,
            )
            assert req.indent == indent

    def test_indent_below_minimum_rejected(self) -> None:
        """Test that indent below 0 is rejected."""
        with pytest.raises(ValidationError):
            JQEditRequest(
                file_path="/tmp/test.json",
                filter=".",
                description="test",
                indent=-1,
            )

    def test_indent_above_maximum_rejected(self) -> None:
        """Test that indent above 8 is rejected."""
        with pytest.raises(ValidationError):
            JQEditRequest(
                file_path="/tmp/test.json",
                filter=".",
                description="test",
                indent=9,
            )

    def test_missing_required_fields(self) -> None:
        """Test that file_path, filter, and description are required."""
        with pytest.raises(ValidationError):
            JQEditRequest(filter=".", description="test")
        with pytest.raises(ValidationError):
            JQEditRequest(file_path="/tmp/test.json", description="test")
        with pytest.raises(ValidationError):
            JQEditRequest(file_path="/tmp/test.json", filter=".")

    def test_frozen_model(self) -> None:
        """Test that JQEditRequest is immutable."""
        req = JQEditRequest(
            file_path="/tmp/test.json",
            filter=".",
            description="test",
        )
        with pytest.raises(ValidationError):
            req.file_path = "/other/path.json"

    def test_all_skip_flags(self) -> None:
        """Test setting all skip/override flags."""
        req = JQEditRequest(
            file_path="/tmp/test.json",
            filter=".",
            description="test",
            skip_backup=True,
            skip_validation=True,
            sort_keys=True,
        )
        assert req.skip_backup is True
        assert req.skip_validation is True
        assert req.sort_keys is True

    def test_serialization_round_trip(self, sample_jq_edit_request: JQEditRequest) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_jq_edit_request.model_dump()
        restored = JQEditRequest.model_validate(data)
        assert restored == sample_jq_edit_request

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        req = JQEditRequest(
            file_path="/tmp/test.json",
            filter='.x = 1',
            description="set x",
            indent=4,
            dry_run=True,
        )
        json_str = req.model_dump_json()
        restored = JQEditRequest.model_validate_json(json_str)
        assert restored == req


# =============================================================================
# JQEditResult
# =============================================================================


class TestJQEditResult:
    """Tests for JQEditResult model."""

    def test_success_result(self) -> None:
        """Test a successful edit result."""
        result = JQEditResult(
            success=True,
            file_path="/etc/app/config.json",
            diff="--- a\n+++ b",
        )
        assert result.success is True
        assert result.file_path == "/etc/app/config.json"
        assert result.diff == "--- a\n+++ b"

    def test_default_values(self) -> None:
        """Test default values."""
        result = JQEditResult(success=True, file_path="/tmp/test.json")
        assert result.diff == ""
        assert result.diff_colored == ""
        assert result.backup_path is None
        assert result.validation_passed is True
        assert result.error is None
        assert result.rollback_applied is False
        assert result.changes == ()
        assert result.requires_privilege is False

    def test_failure_with_rollback(self) -> None:
        """Test a failed result with rollback."""
        result = JQEditResult(
            success=False,
            file_path="/tmp/test.json",
            error="Invalid JSON produced",
            rollback_applied=True,
            validation_passed=False,
        )
        assert result.success is False
        assert result.rollback_applied is True
        assert result.validation_passed is False

    def test_with_backup_path(self) -> None:
        """Test result with backup path recorded."""
        result = JQEditResult(
            success=True,
            file_path="/etc/app/config.json",
            backup_path="/etc/app/config.json.bak.20240101",
        )
        assert result.backup_path == "/etc/app/config.json.bak.20240101"

    def test_with_changes(self, sample_jq_change: JQChange) -> None:
        """Test result with change records."""
        result = JQEditResult(
            success=True,
            file_path="/tmp/test.json",
            changes=(sample_jq_change,),
        )
        assert len(result.changes) == 1
        assert result.changes[0].path == ".config.timeout"

    def test_completed_at_auto_set(self) -> None:
        """Test that completed_at is automatically set."""
        before = datetime.utcnow()
        result = JQEditResult(success=True, file_path="/tmp/test.json")
        after = datetime.utcnow()
        assert before <= result.completed_at <= after

    def test_requires_privilege_flag(self) -> None:
        """Test the requires_privilege flag."""
        result = JQEditResult(
            success=True,
            file_path="/etc/shadow",
            requires_privilege=True,
        )
        assert result.requires_privilege is True

    def test_frozen_model(self) -> None:
        """Test that JQEditResult is immutable."""
        result = JQEditResult(success=True, file_path="/tmp/test.json")
        with pytest.raises(ValidationError):
            result.success = False

    def test_serialization_round_trip(self, sample_jq_edit_result: JQEditResult) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_jq_edit_result.model_dump()
        restored = JQEditResult.model_validate(data)
        assert restored.success == sample_jq_edit_result.success
        assert restored.file_path == sample_jq_edit_result.file_path
        assert len(restored.changes) == len(sample_jq_edit_result.changes)

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        result = JQEditResult(
            success=True,
            file_path="/tmp/test.json",
            diff="some diff",
            backup_path="/tmp/test.json.bak",
        )
        json_str = result.model_dump_json()
        restored = JQEditResult.model_validate_json(json_str)
        assert restored.file_path == "/tmp/test.json"
        assert restored.backup_path == "/tmp/test.json.bak"


# =============================================================================
# JQEditPreview
# =============================================================================


class TestJQEditPreview:
    """Tests for JQEditPreview model."""

    def test_basic_creation(self) -> None:
        """Test creating a basic preview."""
        preview = JQEditPreview(
            file_path="/tmp/test.json",
            original_content='{"x": 1}',
            proposed_content='{"x": 2}',
            diff="--- a\n+++ b\n@@ -1 +1 @@\n-1\n+2",
            diff_colored="\x1b[31m-1\x1b[0m\n\x1b[32m+2\x1b[0m",
        )
        assert preview.file_path == "/tmp/test.json"
        assert preview.original_content == '{"x": 1}'
        assert preview.proposed_content == '{"x": 2}'

    def test_default_values(self) -> None:
        """Test default values for optional fields."""
        preview = JQEditPreview(
            file_path="/tmp/test.json",
            original_content="{}",
            proposed_content="{}",
            diff="",
            diff_colored="",
        )
        assert preview.requires_privilege is False
        assert preview.is_valid_json is True

    def test_invalid_json_preview(self) -> None:
        """Test preview with invalid JSON output."""
        preview = JQEditPreview(
            file_path="/tmp/test.json",
            original_content='{"x": 1}',
            proposed_content="not valid json",
            diff="some diff",
            diff_colored="colored diff",
            is_valid_json=False,
        )
        assert preview.is_valid_json is False

    def test_privileged_file(self) -> None:
        """Test preview for a privileged file."""
        preview = JQEditPreview(
            file_path="/etc/sensitive.json",
            original_content="{}",
            proposed_content="{}",
            diff="",
            diff_colored="",
            requires_privilege=True,
        )
        assert preview.requires_privilege is True

    def test_missing_required_fields(self) -> None:
        """Test that all required fields must be provided."""
        with pytest.raises(ValidationError):
            JQEditPreview(
                file_path="/tmp/test.json",
                original_content="{}",
                # missing proposed_content, diff, diff_colored
            )

    def test_frozen_model(self) -> None:
        """Test that JQEditPreview is immutable."""
        preview = JQEditPreview(
            file_path="/tmp/test.json",
            original_content="{}",
            proposed_content="{}",
            diff="",
            diff_colored="",
        )
        with pytest.raises(ValidationError):
            preview.file_path = "/other.json"

    def test_serialization_round_trip(self) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        preview = JQEditPreview(
            file_path="/tmp/test.json",
            original_content='{"a": 1}',
            proposed_content='{"a": 2}',
            diff="diff text",
            diff_colored="colored diff",
            requires_privilege=True,
            is_valid_json=True,
        )
        data = preview.model_dump()
        restored = JQEditPreview.model_validate(data)
        assert restored == preview

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        preview = JQEditPreview(
            file_path="/tmp/test.json",
            original_content="{}",
            proposed_content='{"new": true}',
            diff="+ new",
            diff_colored="colored",
        )
        json_str = preview.model_dump_json()
        restored = JQEditPreview.model_validate_json(json_str)
        assert restored == preview


# =============================================================================
# JQStatus
# =============================================================================


class TestJQStatus:
    """Tests for JQStatus model."""

    def test_available(self) -> None:
        """Test status when jq is available."""
        status = JQStatus(
            available=True,
            version="jq-1.7.1",
            path="/usr/bin/jq",
        )
        assert status.available is True
        assert status.version == "jq-1.7.1"
        assert status.path == "/usr/bin/jq"

    def test_unavailable(self) -> None:
        """Test status when jq is not available."""
        status = JQStatus(available=False)
        assert status.available is False
        assert status.version is None
        assert status.path is None

    def test_default_optional_fields(self) -> None:
        """Test default values for optional fields."""
        status = JQStatus(available=True)
        assert status.version is None
        assert status.path is None

    def test_missing_required_available(self) -> None:
        """Test that available is required."""
        with pytest.raises(ValidationError):
            JQStatus()

    def test_frozen_model(self) -> None:
        """Test that JQStatus is immutable."""
        status = JQStatus(available=True)
        with pytest.raises(ValidationError):
            status.available = False

    def test_serialization_round_trip(self) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        status = JQStatus(available=True, version="1.7", path="/usr/bin/jq")
        data = status.model_dump()
        restored = JQStatus.model_validate(data)
        assert restored == status

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        status = JQStatus(available=False)
        json_str = status.model_dump_json()
        restored = JQStatus.model_validate_json(json_str)
        assert restored == status


# =============================================================================
# Error Types
# =============================================================================


class TestJQError:
    """Tests for JQError base exception."""

    def test_basic_exception(self) -> None:
        """Test creating a basic JQError."""
        err = JQError("something went wrong")
        assert str(err) == "something went wrong"

    def test_is_exception(self) -> None:
        """Test that JQError is an Exception subclass."""
        assert issubclass(JQError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """Test raising and catching JQError."""
        with pytest.raises(JQError):
            raise JQError("test")


class TestJQUnavailableError:
    """Tests for JQUnavailableError exception."""

    def test_basic_exception(self) -> None:
        """Test creating a JQUnavailableError."""
        err = JQUnavailableError("jq not found")
        assert str(err) == "jq not found"

    def test_inherits_from_jq_error(self) -> None:
        """Test inheritance from JQError."""
        assert issubclass(JQUnavailableError, JQError)

    def test_caught_by_parent(self) -> None:
        """Test that it can be caught as JQError."""
        with pytest.raises(JQError):
            raise JQUnavailableError("jq not installed")


class TestJQFilterError:
    """Tests for JQFilterError exception."""

    def test_without_details(self) -> None:
        """Test filter error without details."""
        err = JQFilterError(filter_expr=".bad[")
        assert err.filter_expr == ".bad["
        assert err.details is None
        assert "Invalid jq filter: .bad[" in str(err)

    def test_with_details(self) -> None:
        """Test filter error with details."""
        err = JQFilterError(filter_expr=".x | foo", details="unknown function foo")
        assert err.filter_expr == ".x | foo"
        assert err.details == "unknown function foo"
        assert "Invalid jq filter: .x | foo" in str(err)
        assert "unknown function foo" in str(err)

    def test_inherits_from_jq_error(self) -> None:
        """Test inheritance from JQError."""
        assert issubclass(JQFilterError, JQError)

    def test_caught_by_parent(self) -> None:
        """Test that it can be caught as JQError."""
        with pytest.raises(JQError):
            raise JQFilterError(filter_expr="bad")


class TestJQParseError:
    """Tests for JQParseError exception."""

    def test_without_details(self) -> None:
        """Test parse error without details."""
        err = JQParseError(source="/tmp/data.json")
        assert err.source == "/tmp/data.json"
        assert err.details is None
        assert "Failed to parse JSON from: /tmp/data.json" in str(err)

    def test_with_details(self) -> None:
        """Test parse error with details."""
        err = JQParseError(source="stdin", details="unexpected EOF")
        assert err.source == "stdin"
        assert err.details == "unexpected EOF"
        assert "Failed to parse JSON from: stdin" in str(err)
        assert "unexpected EOF" in str(err)

    def test_inherits_from_jq_error(self) -> None:
        """Test inheritance from JQError."""
        assert issubclass(JQParseError, JQError)

    def test_caught_by_parent(self) -> None:
        """Test that it can be caught as JQError."""
        with pytest.raises(JQError):
            raise JQParseError(source="test")
