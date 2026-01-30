"""Tests for yq operation models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from elle.ops.yq.models import (
    YQChange,
    YQEditPreview,
    YQEditRequest,
    YQEditResult,
    YQError,
    YQFilterError,
    YQOperation,
    YQParseError,
    YQRequest,
    YQResult,
    YQStatus,
    YQUnavailableError,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_yq_operation() -> YQOperation:
    """Create a basic YQOperation for reuse."""
    return YQOperation(kind="query", filter=".services")


@pytest.fixture
def sample_yq_change() -> YQChange:
    """Create a basic YQChange for reuse."""
    return YQChange(
        path=".services.web.image",
        old_value="nginx:1.24",
        new_value="nginx:1.25",
        operation="transform",
    )


@pytest.fixture
def sample_yq_request() -> YQRequest:
    """Create a basic YQRequest for reuse."""
    return YQRequest(
        file_path="/etc/app/docker-compose.yml",
        filter=".services.web.image",
        description="Query web service image",
    )


@pytest.fixture
def sample_yq_result() -> YQResult:
    """Create a basic successful YQResult for reuse."""
    return YQResult(
        success=True,
        output="nginx:1.25",
        parsed_output="nginx:1.25",
        filter_used=".services.web.image",
    )


@pytest.fixture
def sample_yq_edit_request() -> YQEditRequest:
    """Create a basic YQEditRequest for reuse."""
    return YQEditRequest(
        file_path="/etc/app/docker-compose.yml",
        filter='.services.web.image = "nginx:1.25"',
        description="Update web service image to nginx 1.25",
    )


@pytest.fixture
def sample_yq_edit_result(sample_yq_change: YQChange) -> YQEditResult:
    """Create a basic YQEditResult for reuse."""
    return YQEditResult(
        success=True,
        file_path="/etc/app/docker-compose.yml",
        diff="--- a\n+++ b\n@@ -1 +1 @@\n-nginx:1.24\n+nginx:1.25",
        changes=(sample_yq_change,),
    )


# =============================================================================
# YQOperation
# =============================================================================


class TestYQOperation:
    """Tests for YQOperation model."""

    def test_basic_query_creation(self) -> None:
        """Test creating a query operation."""
        op = YQOperation(kind="query", filter=".services")
        assert op.kind == "query"
        assert op.filter == ".services"

    def test_default_values(self) -> None:
        """Test default values for optional fields."""
        op = YQOperation(kind="query", filter=".")
        assert op.in_place is False
        assert op.yaml_output is True
        assert op.preserve_comments is True

    def test_transform_kind(self) -> None:
        """Test creating a transform operation."""
        op = YQOperation(kind="transform", filter='.services.web.image = "nginx:latest"')
        assert op.kind == "transform"

    def test_validate_kind(self) -> None:
        """Test creating a validate operation."""
        op = YQOperation(kind="validate", filter=".")
        assert op.kind == "validate"

    def test_invalid_kind_rejected(self) -> None:
        """Test that invalid kind values are rejected."""
        with pytest.raises(ValidationError):
            YQOperation(kind="delete", filter=".")

    def test_in_place_enabled(self) -> None:
        """Test enabling in-place modification."""
        op = YQOperation(kind="transform", filter='.x = 1', in_place=True)
        assert op.in_place is True

    def test_json_output_mode(self) -> None:
        """Test JSON output mode (yaml_output=False)."""
        op = YQOperation(kind="query", filter=".", yaml_output=False)
        assert op.yaml_output is False

    def test_disable_comment_preservation(self) -> None:
        """Test disabling comment preservation."""
        op = YQOperation(kind="transform", filter=".", preserve_comments=False)
        assert op.preserve_comments is False

    def test_all_options_set(self) -> None:
        """Test setting all optional flags."""
        op = YQOperation(
            kind="transform",
            filter='.x = 1',
            in_place=True,
            yaml_output=False,
            preserve_comments=False,
        )
        assert op.in_place is True
        assert op.yaml_output is False
        assert op.preserve_comments is False

    def test_frozen_model(self) -> None:
        """Test that YQOperation is immutable."""
        op = YQOperation(kind="query", filter=".")
        with pytest.raises(ValidationError):
            op.kind = "transform"

    def test_missing_required_filter(self) -> None:
        """Test that filter is required."""
        with pytest.raises(ValidationError):
            YQOperation(kind="query")

    def test_missing_required_kind(self) -> None:
        """Test that kind is required."""
        with pytest.raises(ValidationError):
            YQOperation(filter=".")

    def test_serialization_round_trip(self, sample_yq_operation: YQOperation) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_yq_operation.model_dump()
        restored = YQOperation.model_validate(data)
        assert restored == sample_yq_operation
        assert restored.kind == "query"
        assert restored.filter == ".services"

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        op = YQOperation(
            kind="transform",
            filter='.x = 1',
            in_place=True,
            yaml_output=False,
        )
        json_str = op.model_dump_json()
        restored = YQOperation.model_validate_json(json_str)
        assert restored == op

    def test_complex_filter_expression(self) -> None:
        """Test a complex yq filter expression is stored correctly."""
        expr = '.services[] | select(.image | test("nginx")) | .image'
        op = YQOperation(kind="query", filter=expr)
        assert op.filter == expr


# =============================================================================
# YQChange
# =============================================================================


class TestYQChange:
    """Tests for YQChange model."""

    def test_basic_creation(self) -> None:
        """Test creating a change record."""
        change = YQChange(
            path=".services.web.image",
            old_value="nginx:1.24",
            new_value="nginx:1.25",
        )
        assert change.path == ".services.web.image"
        assert change.old_value == "nginx:1.24"
        assert change.new_value == "nginx:1.25"

    def test_default_values(self) -> None:
        """Test default values for optional fields."""
        change = YQChange(path=".key")
        assert change.old_value is None
        assert change.new_value is None
        assert change.operation == "transform"

    def test_creation_change(self) -> None:
        """Test a change representing a new value creation."""
        change = YQChange(
            path=".services.db",
            old_value=None,
            new_value={"image": "postgres:16"},
            operation="create",
        )
        assert change.old_value is None
        assert change.new_value == {"image": "postgres:16"}

    def test_deletion_change(self) -> None:
        """Test a change representing value deletion."""
        change = YQChange(
            path=".services.old_service",
            old_value={"image": "legacy:1.0"},
            new_value=None,
            operation="delete",
        )
        assert change.old_value == {"image": "legacy:1.0"}
        assert change.new_value is None

    def test_complex_value_types(self) -> None:
        """Test storing complex value types (dict, list)."""
        change = YQChange(
            path=".services.web.ports",
            old_value=["8080:80"],
            new_value=["8080:80", "8443:443"],
        )
        assert len(change.new_value) == 2

    def test_frozen_model(self) -> None:
        """Test that YQChange is immutable."""
        change = YQChange(path=".key")
        with pytest.raises(ValidationError):
            change.path = ".other"

    def test_serialization_round_trip(self, sample_yq_change: YQChange) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_yq_change.model_dump()
        restored = YQChange.model_validate(data)
        assert restored == sample_yq_change

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        change = YQChange(
            path=".version",
            old_value="3.8",
            new_value="3.9",
            operation="update",
        )
        json_str = change.model_dump_json()
        restored = YQChange.model_validate_json(json_str)
        assert restored == change

    def test_numeric_values(self) -> None:
        """Test change with numeric values."""
        change = YQChange(
            path=".services.web.replicas",
            old_value=1,
            new_value=3,
        )
        assert change.old_value == 1
        assert change.new_value == 3

    def test_boolean_values(self) -> None:
        """Test change with boolean values."""
        change = YQChange(
            path=".services.web.privileged",
            old_value=False,
            new_value=True,
        )
        assert change.old_value is False
        assert change.new_value is True


# =============================================================================
# YQRequest
# =============================================================================


class TestYQRequest:
    """Tests for YQRequest model."""

    def test_file_path_source(self) -> None:
        """Test request with file_path input source."""
        req = YQRequest(file_path="/tmp/compose.yml", filter=".services")
        assert req.file_path == "/tmp/compose.yml"
        assert req.yaml_input is None

    def test_yaml_input_source(self) -> None:
        """Test request with yaml_input source."""
        req = YQRequest(yaml_input="key: value\n", filter=".key")
        assert req.yaml_input == "key: value\n"
        assert req.file_path is None

    def test_both_sources_allowed(self) -> None:
        """Test that providing both sources does not raise (validation is business logic)."""
        req = YQRequest(
            file_path="/tmp/compose.yml",
            yaml_input="key: value\n",
            filter=".key",
        )
        assert req.file_path is not None
        assert req.yaml_input is not None

    def test_default_values(self) -> None:
        """Test default option values."""
        req = YQRequest(filter=".")
        assert req.yaml_output is True
        assert req.raw_output is False
        assert req.description == ""
        assert req.file_path is None
        assert req.yaml_input is None

    def test_json_output_mode(self) -> None:
        """Test JSON output mode."""
        req = YQRequest(filter=".", yaml_output=False)
        assert req.yaml_output is False

    def test_raw_output_mode(self) -> None:
        """Test raw output mode."""
        req = YQRequest(filter=".key", raw_output=True)
        assert req.raw_output is True

    def test_with_description(self) -> None:
        """Test request with description."""
        req = YQRequest(
            file_path="/tmp/compose.yml",
            filter=".services.web.image",
            description="Get web service image tag",
        )
        assert req.description == "Get web service image tag"

    def test_missing_required_filter(self) -> None:
        """Test that filter is required."""
        with pytest.raises(ValidationError):
            YQRequest(file_path="/tmp/compose.yml")

    def test_frozen_model(self) -> None:
        """Test that YQRequest is immutable."""
        req = YQRequest(filter=".")
        with pytest.raises(ValidationError):
            req.filter = ".modified"

    def test_serialization_round_trip(self, sample_yq_request: YQRequest) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_yq_request.model_dump()
        restored = YQRequest.model_validate(data)
        assert restored == sample_yq_request

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        req = YQRequest(
            file_path="/tmp/compose.yml",
            filter=".services",
            yaml_output=False,
            raw_output=True,
            description="Extract services as JSON",
        )
        json_str = req.model_dump_json()
        restored = YQRequest.model_validate_json(json_str)
        assert restored == req

    def test_all_options_set(self) -> None:
        """Test setting all optional fields."""
        req = YQRequest(
            file_path="/tmp/test.yml",
            yaml_input="x: 1",
            filter=".x",
            yaml_output=False,
            raw_output=True,
            description="full opts",
        )
        assert req.file_path == "/tmp/test.yml"
        assert req.yaml_input == "x: 1"
        assert req.yaml_output is False
        assert req.raw_output is True
        assert req.description == "full opts"


# =============================================================================
# YQResult
# =============================================================================


class TestYQResult:
    """Tests for YQResult model."""

    def test_success_result(self) -> None:
        """Test a successful result."""
        result = YQResult(
            success=True,
            output="nginx:1.25\n",
            parsed_output="nginx:1.25",
            filter_used=".services.web.image",
        )
        assert result.success is True
        assert result.output == "nginx:1.25\n"
        assert result.parsed_output == "nginx:1.25"
        assert result.error is None
        assert result.exit_code == 0

    def test_failure_result(self) -> None:
        """Test a failed result."""
        result = YQResult(
            success=False,
            error="yq: error: syntax error",
            exit_code=2,
            filter_used=".bad[",
        )
        assert result.success is False
        assert result.error == "yq: error: syntax error"
        assert result.exit_code == 2

    def test_default_values(self) -> None:
        """Test default values on a minimal result."""
        result = YQResult(success=True)
        assert result.output == ""
        assert result.parsed_output is None
        assert result.error is None
        assert result.exit_code == 0
        assert result.filter_used == ""

    def test_completed_at_auto_set(self) -> None:
        """Test that completed_at is automatically set."""
        before = datetime.utcnow()
        result = YQResult(success=True)
        after = datetime.utcnow()
        assert before <= result.completed_at <= after

    def test_parsed_output_complex_type(self) -> None:
        """Test parsed_output with complex types (dict with YAML structure)."""
        result = YQResult(
            success=True,
            output="web:\n  image: nginx\n",
            parsed_output={"web": {"image": "nginx"}},
        )
        assert result.parsed_output == {"web": {"image": "nginx"}}

    def test_parsed_output_list(self) -> None:
        """Test parsed_output with a list."""
        result = YQResult(
            success=True,
            output="- 8080:80\n- 8443:443\n",
            parsed_output=["8080:80", "8443:443"],
        )
        assert len(result.parsed_output) == 2

    def test_frozen_model(self) -> None:
        """Test that YQResult is immutable."""
        result = YQResult(success=True)
        with pytest.raises(ValidationError):
            result.success = False

    def test_serialization_round_trip(self, sample_yq_result: YQResult) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_yq_result.model_dump()
        restored = YQResult.model_validate(data)
        assert restored.success == sample_yq_result.success
        assert restored.output == sample_yq_result.output
        assert restored.parsed_output == sample_yq_result.parsed_output
        assert restored.filter_used == sample_yq_result.filter_used

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        result = YQResult(
            success=True,
            output="name: test\n",
            parsed_output={"name": "test"},
            filter_used=".name",
            exit_code=0,
        )
        json_str = result.model_dump_json()
        restored = YQResult.model_validate_json(json_str)
        assert restored.success is True
        assert restored.parsed_output == {"name": "test"}


# =============================================================================
# YQEditRequest
# =============================================================================


class TestYQEditRequest:
    """Tests for YQEditRequest model."""

    def test_basic_creation(self) -> None:
        """Test creating a basic edit request."""
        req = YQEditRequest(
            file_path="/etc/app/docker-compose.yml",
            filter='.services.web.image = "nginx:1.25"',
            description="Update web image",
        )
        assert req.file_path == "/etc/app/docker-compose.yml"
        assert "nginx:1.25" in req.filter
        assert req.description == "Update web image"

    def test_default_values(self) -> None:
        """Test default values."""
        req = YQEditRequest(
            file_path="/tmp/test.yml",
            filter=".",
            description="no-op",
        )
        assert req.incident_id is None
        assert req.dry_run is False
        assert req.skip_backup is False
        assert req.skip_validation is False
        assert req.preserve_comments is True

    def test_dry_run_mode(self) -> None:
        """Test dry_run flag."""
        req = YQEditRequest(
            file_path="/tmp/test.yml",
            filter='.x = 1',
            description="test",
            dry_run=True,
        )
        assert req.dry_run is True

    def test_incident_id_linked(self) -> None:
        """Test linking to an incident."""
        req = YQEditRequest(
            file_path="/tmp/test.yml",
            filter='.x = 1',
            description="test",
            incident_id="inc-abc-123",
        )
        assert req.incident_id == "inc-abc-123"

    def test_disable_comment_preservation(self) -> None:
        """Test disabling comment preservation."""
        req = YQEditRequest(
            file_path="/tmp/test.yml",
            filter='.x = 1',
            description="test",
            preserve_comments=False,
        )
        assert req.preserve_comments is False

    def test_all_skip_flags(self) -> None:
        """Test setting all skip/override flags."""
        req = YQEditRequest(
            file_path="/tmp/test.yml",
            filter=".",
            description="test",
            skip_backup=True,
            skip_validation=True,
            preserve_comments=False,
        )
        assert req.skip_backup is True
        assert req.skip_validation is True
        assert req.preserve_comments is False

    def test_missing_required_fields(self) -> None:
        """Test that file_path, filter, and description are required."""
        with pytest.raises(ValidationError):
            YQEditRequest(filter=".", description="test")
        with pytest.raises(ValidationError):
            YQEditRequest(file_path="/tmp/test.yml", description="test")
        with pytest.raises(ValidationError):
            YQEditRequest(file_path="/tmp/test.yml", filter=".")

    def test_frozen_model(self) -> None:
        """Test that YQEditRequest is immutable."""
        req = YQEditRequest(
            file_path="/tmp/test.yml",
            filter=".",
            description="test",
        )
        with pytest.raises(ValidationError):
            req.file_path = "/other/path.yml"

    def test_serialization_round_trip(self, sample_yq_edit_request: YQEditRequest) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_yq_edit_request.model_dump()
        restored = YQEditRequest.model_validate(data)
        assert restored == sample_yq_edit_request

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        req = YQEditRequest(
            file_path="/tmp/test.yml",
            filter='.x = 1',
            description="set x",
            dry_run=True,
            preserve_comments=False,
        )
        json_str = req.model_dump_json()
        restored = YQEditRequest.model_validate_json(json_str)
        assert restored == req


# =============================================================================
# YQEditResult
# =============================================================================


class TestYQEditResult:
    """Tests for YQEditResult model."""

    def test_success_result(self) -> None:
        """Test a successful edit result."""
        result = YQEditResult(
            success=True,
            file_path="/etc/app/docker-compose.yml",
            diff="--- a\n+++ b",
        )
        assert result.success is True
        assert result.file_path == "/etc/app/docker-compose.yml"
        assert result.diff == "--- a\n+++ b"

    def test_default_values(self) -> None:
        """Test default values."""
        result = YQEditResult(success=True, file_path="/tmp/test.yml")
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
        result = YQEditResult(
            success=False,
            file_path="/tmp/test.yml",
            error="Invalid YAML produced",
            rollback_applied=True,
            validation_passed=False,
        )
        assert result.success is False
        assert result.rollback_applied is True
        assert result.validation_passed is False

    def test_with_backup_path(self) -> None:
        """Test result with backup path recorded."""
        result = YQEditResult(
            success=True,
            file_path="/etc/app/compose.yml",
            backup_path="/etc/app/compose.yml.bak.20240101",
        )
        assert result.backup_path == "/etc/app/compose.yml.bak.20240101"

    def test_with_changes(self, sample_yq_change: YQChange) -> None:
        """Test result with change records."""
        result = YQEditResult(
            success=True,
            file_path="/tmp/test.yml",
            changes=(sample_yq_change,),
        )
        assert len(result.changes) == 1
        assert result.changes[0].path == ".services.web.image"

    def test_multiple_changes(self) -> None:
        """Test result with multiple change records."""
        changes = (
            YQChange(path=".services.web.image", old_value="nginx:1.24", new_value="nginx:1.25"),
            YQChange(path=".services.db.image", old_value="postgres:15", new_value="postgres:16"),
        )
        result = YQEditResult(
            success=True,
            file_path="/tmp/test.yml",
            changes=changes,
        )
        assert len(result.changes) == 2

    def test_completed_at_auto_set(self) -> None:
        """Test that completed_at is automatically set."""
        before = datetime.utcnow()
        result = YQEditResult(success=True, file_path="/tmp/test.yml")
        after = datetime.utcnow()
        assert before <= result.completed_at <= after

    def test_requires_privilege_flag(self) -> None:
        """Test the requires_privilege flag."""
        result = YQEditResult(
            success=True,
            file_path="/etc/nginx/nginx.conf",
            requires_privilege=True,
        )
        assert result.requires_privilege is True

    def test_frozen_model(self) -> None:
        """Test that YQEditResult is immutable."""
        result = YQEditResult(success=True, file_path="/tmp/test.yml")
        with pytest.raises(ValidationError):
            result.success = False

    def test_serialization_round_trip(self, sample_yq_edit_result: YQEditResult) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        data = sample_yq_edit_result.model_dump()
        restored = YQEditResult.model_validate(data)
        assert restored.success == sample_yq_edit_result.success
        assert restored.file_path == sample_yq_edit_result.file_path
        assert len(restored.changes) == len(sample_yq_edit_result.changes)

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        result = YQEditResult(
            success=True,
            file_path="/tmp/test.yml",
            diff="some diff",
            backup_path="/tmp/test.yml.bak",
        )
        json_str = result.model_dump_json()
        restored = YQEditResult.model_validate_json(json_str)
        assert restored.file_path == "/tmp/test.yml"
        assert restored.backup_path == "/tmp/test.yml.bak"

    def test_error_with_details(self) -> None:
        """Test a failure result with detailed error message."""
        result = YQEditResult(
            success=False,
            file_path="/tmp/test.yml",
            error="yq: error: could not parse YAML at line 5",
        )
        assert result.success is False
        assert "line 5" in result.error


# =============================================================================
# YQEditPreview
# =============================================================================


class TestYQEditPreview:
    """Tests for YQEditPreview model."""

    def test_basic_creation(self) -> None:
        """Test creating a basic preview."""
        preview = YQEditPreview(
            file_path="/tmp/test.yml",
            original_content="key: old_value\n",
            proposed_content="key: new_value\n",
            diff="--- a\n+++ b\n@@ -1 +1 @@\n-key: old_value\n+key: new_value",
            diff_colored="\x1b[31m-key: old_value\x1b[0m\n\x1b[32m+key: new_value\x1b[0m",
        )
        assert preview.file_path == "/tmp/test.yml"
        assert preview.original_content == "key: old_value\n"
        assert preview.proposed_content == "key: new_value\n"

    def test_default_values(self) -> None:
        """Test default values for optional fields."""
        preview = YQEditPreview(
            file_path="/tmp/test.yml",
            original_content="x: 1\n",
            proposed_content="x: 1\n",
            diff="",
            diff_colored="",
        )
        assert preview.requires_privilege is False
        assert preview.is_valid_yaml is True

    def test_invalid_yaml_preview(self) -> None:
        """Test preview with invalid YAML output."""
        preview = YQEditPreview(
            file_path="/tmp/test.yml",
            original_content="key: value\n",
            proposed_content="not: valid: yaml: {{{\n",
            diff="some diff",
            diff_colored="colored diff",
            is_valid_yaml=False,
        )
        assert preview.is_valid_yaml is False

    def test_privileged_file(self) -> None:
        """Test preview for a privileged file."""
        preview = YQEditPreview(
            file_path="/etc/nginx/nginx.conf",
            original_content="worker_processes: auto\n",
            proposed_content="worker_processes: 4\n",
            diff="diff",
            diff_colored="colored",
            requires_privilege=True,
        )
        assert preview.requires_privilege is True

    def test_missing_required_fields(self) -> None:
        """Test that all required fields must be provided."""
        with pytest.raises(ValidationError):
            YQEditPreview(
                file_path="/tmp/test.yml",
                original_content="x: 1\n",
                # missing proposed_content, diff, diff_colored
            )

    def test_frozen_model(self) -> None:
        """Test that YQEditPreview is immutable."""
        preview = YQEditPreview(
            file_path="/tmp/test.yml",
            original_content="x: 1\n",
            proposed_content="x: 2\n",
            diff="",
            diff_colored="",
        )
        with pytest.raises(ValidationError):
            preview.file_path = "/other.yml"

    def test_serialization_round_trip(self) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        preview = YQEditPreview(
            file_path="/tmp/test.yml",
            original_content="a: 1\n",
            proposed_content="a: 2\n",
            diff="diff text",
            diff_colored="colored diff",
            requires_privilege=True,
            is_valid_yaml=True,
        )
        data = preview.model_dump()
        restored = YQEditPreview.model_validate(data)
        assert restored == preview

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        preview = YQEditPreview(
            file_path="/tmp/test.yml",
            original_content="x: 1\n",
            proposed_content="x: 2\n",
            diff="+ x: 2",
            diff_colored="colored",
        )
        json_str = preview.model_dump_json()
        restored = YQEditPreview.model_validate_json(json_str)
        assert restored == preview


# =============================================================================
# YQStatus
# =============================================================================


class TestYQStatus:
    """Tests for YQStatus model."""

    def test_available_kislyuk(self) -> None:
        """Test status when yq (kislyuk/Python variant) is available."""
        status = YQStatus(
            available=True,
            version="3.2.3",
            path="/usr/bin/yq",
            variant="kislyuk",
        )
        assert status.available is True
        assert status.version == "3.2.3"
        assert status.path == "/usr/bin/yq"
        assert status.variant == "kislyuk"

    def test_available_mikefarah(self) -> None:
        """Test status when yq (mikefarah/Go variant) is available."""
        status = YQStatus(
            available=True,
            version="4.40.5",
            path="/usr/local/bin/yq",
            variant="mikefarah",
        )
        assert status.variant == "mikefarah"

    def test_unavailable(self) -> None:
        """Test status when yq is not available."""
        status = YQStatus(available=False)
        assert status.available is False
        assert status.version is None
        assert status.path is None
        assert status.variant is None

    def test_default_optional_fields(self) -> None:
        """Test default values for optional fields."""
        status = YQStatus(available=True)
        assert status.version is None
        assert status.path is None
        assert status.variant is None

    def test_missing_required_available(self) -> None:
        """Test that available is required."""
        with pytest.raises(ValidationError):
            YQStatus()

    def test_frozen_model(self) -> None:
        """Test that YQStatus is immutable."""
        status = YQStatus(available=True)
        with pytest.raises(ValidationError):
            status.available = False

    def test_serialization_round_trip(self) -> None:
        """Test model_dump and model_validate produce equivalent models."""
        status = YQStatus(
            available=True,
            version="3.2.3",
            path="/usr/bin/yq",
            variant="kislyuk",
        )
        data = status.model_dump()
        restored = YQStatus.model_validate(data)
        assert restored == status

    def test_json_round_trip(self) -> None:
        """Test JSON serialization and deserialization."""
        status = YQStatus(available=False)
        json_str = status.model_dump_json()
        restored = YQStatus.model_validate_json(json_str)
        assert restored == status

    def test_variant_field_accepts_arbitrary_string(self) -> None:
        """Test that variant accepts any string value (not just known variants)."""
        status = YQStatus(available=True, variant="unknown_variant")
        assert status.variant == "unknown_variant"


# =============================================================================
# Error Types
# =============================================================================


class TestYQError:
    """Tests for YQError base exception."""

    def test_basic_exception(self) -> None:
        """Test creating a basic YQError."""
        err = YQError("something went wrong")
        assert str(err) == "something went wrong"

    def test_is_exception(self) -> None:
        """Test that YQError is an Exception subclass."""
        assert issubclass(YQError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """Test raising and catching YQError."""
        with pytest.raises(YQError):
            raise YQError("test")


class TestYQUnavailableError:
    """Tests for YQUnavailableError exception."""

    def test_basic_exception(self) -> None:
        """Test creating a YQUnavailableError."""
        err = YQUnavailableError("yq not found")
        assert str(err) == "yq not found"

    def test_inherits_from_yq_error(self) -> None:
        """Test inheritance from YQError."""
        assert issubclass(YQUnavailableError, YQError)

    def test_caught_by_parent(self) -> None:
        """Test that it can be caught as YQError."""
        with pytest.raises(YQError):
            raise YQUnavailableError("yq not installed")


class TestYQFilterError:
    """Tests for YQFilterError exception."""

    def test_without_details(self) -> None:
        """Test filter error without details."""
        err = YQFilterError(filter_expr=".bad[")
        assert err.filter_expr == ".bad["
        assert err.details is None
        assert "Invalid yq filter: .bad[" in str(err)

    def test_with_details(self) -> None:
        """Test filter error with details."""
        err = YQFilterError(filter_expr=".x | foo", details="unknown function foo")
        assert err.filter_expr == ".x | foo"
        assert err.details == "unknown function foo"
        assert "Invalid yq filter: .x | foo" in str(err)
        assert "unknown function foo" in str(err)

    def test_inherits_from_yq_error(self) -> None:
        """Test inheritance from YQError."""
        assert issubclass(YQFilterError, YQError)

    def test_caught_by_parent(self) -> None:
        """Test that it can be caught as YQError."""
        with pytest.raises(YQError):
            raise YQFilterError(filter_expr="bad")


class TestYQParseError:
    """Tests for YQParseError exception."""

    def test_without_details(self) -> None:
        """Test parse error without details."""
        err = YQParseError(source="/tmp/data.yml")
        assert err.source == "/tmp/data.yml"
        assert err.details is None
        assert "Failed to parse YAML from: /tmp/data.yml" in str(err)

    def test_with_details(self) -> None:
        """Test parse error with details."""
        err = YQParseError(source="stdin", details="unexpected token at line 3")
        assert err.source == "stdin"
        assert err.details == "unexpected token at line 3"
        assert "Failed to parse YAML from: stdin" in str(err)
        assert "unexpected token at line 3" in str(err)

    def test_inherits_from_yq_error(self) -> None:
        """Test inheritance from YQError."""
        assert issubclass(YQParseError, YQError)

    def test_caught_by_parent(self) -> None:
        """Test that it can be caught as YQError."""
        with pytest.raises(YQError):
            raise YQParseError(source="test")
