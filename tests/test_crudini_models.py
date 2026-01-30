"""Tests for crudini INI file editing Pydantic models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from elle.ops.crudini.models import (
    CrudiniChange,
    CrudiniEditPreview,
    CrudiniEditRequest,
    CrudiniEditResult,
    CrudiniError,
    CrudiniOperation,
    CrudiniParameterError,
    CrudiniParseError,
    CrudiniRequest,
    CrudiniResult,
    CrudiniSectionError,
    CrudiniStatus,
    CrudiniUnavailableError,
    INIFileContent,
    INISection,
)


# =============================================================================
# CrudiniOperation
# =============================================================================


class TestCrudiniOperation:
    """Tests for CrudiniOperation model."""

    def test_set_operation(self) -> None:
        op = CrudiniOperation(
            kind="set",
            section="database",
            parameter="host",
            value="localhost",
        )
        assert op.kind == "set"
        assert op.section == "database"
        assert op.parameter == "host"
        assert op.value == "localhost"
        assert op.existing == "set"

    def test_get_operation(self) -> None:
        op = CrudiniOperation(
            kind="get",
            section="server",
            parameter="port",
        )
        assert op.kind == "get"
        assert op.value is None

    def test_del_operation(self) -> None:
        op = CrudiniOperation(
            kind="del",
            section="old_section",
        )
        assert op.kind == "del"
        assert op.parameter is None

    def test_merge_operation(self) -> None:
        op = CrudiniOperation(
            kind="merge",
            section="defaults",
            parameter="timeout",
            value="30",
        )
        assert op.kind == "merge"

    def test_defaults(self) -> None:
        op = CrudiniOperation(kind="get", section="main")
        assert op.parameter is None
        assert op.value is None
        assert op.existing == "set"

    def test_existing_keep(self) -> None:
        op = CrudiniOperation(
            kind="set",
            section="db",
            parameter="host",
            value="remote",
            existing="keep",
        )
        assert op.existing == "keep"

    def test_existing_error(self) -> None:
        op = CrudiniOperation(
            kind="set",
            section="db",
            parameter="host",
            value="remote",
            existing="error",
        )
        assert op.existing == "error"

    def test_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            CrudiniOperation(kind="update", section="test")

    def test_invalid_existing(self) -> None:
        with pytest.raises(ValidationError):
            CrudiniOperation(kind="set", section="test", existing="overwrite")

    def test_frozen(self) -> None:
        op = CrudiniOperation(kind="set", section="test", value="val")
        with pytest.raises(ValidationError):
            op.value = "new"

    def test_all_kinds(self) -> None:
        for kind in ("get", "set", "del", "merge"):
            op = CrudiniOperation(kind=kind, section="test")
            assert op.kind == kind

    def test_serialization_roundtrip(self) -> None:
        op = CrudiniOperation(
            kind="set",
            section="database",
            parameter="port",
            value="5432",
            existing="keep",
        )
        data = op.model_dump()
        restored = CrudiniOperation.model_validate(data)
        assert restored == op


# =============================================================================
# CrudiniChange
# =============================================================================


class TestCrudiniChange:
    """Tests for CrudiniChange model."""

    def test_set_change(self) -> None:
        change = CrudiniChange(
            section="database",
            parameter="host",
            old_value="old-host",
            new_value="new-host",
            operation="set",
        )
        assert change.section == "database"
        assert change.parameter == "host"
        assert change.old_value == "old-host"
        assert change.new_value == "new-host"

    def test_add_change(self) -> None:
        change = CrudiniChange(
            section="server",
            parameter="timeout",
            new_value="30",
            operation="add",
        )
        assert change.old_value is None
        assert change.new_value == "30"

    def test_del_change(self) -> None:
        change = CrudiniChange(
            section="removed_section",
            old_value="was_here",
            operation="del",
        )
        assert change.new_value is None
        assert change.operation == "del"

    def test_defaults(self) -> None:
        change = CrudiniChange(section="test")
        assert change.parameter is None
        assert change.old_value is None
        assert change.new_value is None
        assert change.operation == "set"

    def test_frozen(self) -> None:
        change = CrudiniChange(section="test", new_value="val")
        with pytest.raises(ValidationError):
            change.section = "other"

    def test_serialization_roundtrip(self) -> None:
        change = CrudiniChange(
            section="db",
            parameter="port",
            old_value="3306",
            new_value="5432",
            operation="set",
        )
        data = change.model_dump()
        restored = CrudiniChange.model_validate(data)
        assert restored == change


# =============================================================================
# CrudiniRequest
# =============================================================================


class TestCrudiniRequest:
    """Tests for CrudiniRequest model."""

    @pytest.fixture()
    def sample_operation(self) -> CrudiniOperation:
        return CrudiniOperation(kind="get", section="server", parameter="port")

    def test_minimal(self, sample_operation: CrudiniOperation) -> None:
        req = CrudiniRequest(
            file_path="/etc/my.cnf",
            operation=sample_operation,
        )
        assert req.file_path == "/etc/my.cnf"
        assert req.description == ""

    def test_with_description(self, sample_operation: CrudiniOperation) -> None:
        req = CrudiniRequest(
            file_path="/etc/my.cnf",
            operation=sample_operation,
            description="Read server port",
        )
        assert req.description == "Read server port"

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            CrudiniRequest(file_path="/etc/my.cnf")

    def test_serialization_roundtrip(self, sample_operation: CrudiniOperation) -> None:
        req = CrudiniRequest(
            file_path="/etc/my.cnf",
            operation=sample_operation,
            description="test",
        )
        data = req.model_dump()
        restored = CrudiniRequest.model_validate(data)
        assert restored == req


# =============================================================================
# CrudiniResult
# =============================================================================


class TestCrudiniResult:
    """Tests for CrudiniResult model."""

    def test_success(self) -> None:
        result = CrudiniResult(
            success=True,
            output="3306",
            exit_code=0,
        )
        assert result.success is True
        assert result.output == "3306"
        assert result.error is None
        assert result.exit_code == 0
        assert isinstance(result.completed_at, datetime)

    def test_failure(self) -> None:
        result = CrudiniResult(
            success=False,
            error="Section not found",
            exit_code=1,
        )
        assert result.success is False
        assert result.output == ""
        assert result.error == "Section not found"

    def test_defaults(self) -> None:
        result = CrudiniResult(success=True)
        assert result.output == ""
        assert result.error is None
        assert result.exit_code == 0

    def test_serialization_roundtrip(self) -> None:
        result = CrudiniResult(
            success=True,
            output="value",
            exit_code=0,
        )
        data = result.model_dump()
        restored = CrudiniResult.model_validate(data)
        assert restored.success == result.success
        assert restored.output == result.output


# =============================================================================
# CrudiniEditRequest
# =============================================================================


class TestCrudiniEditRequest:
    """Tests for CrudiniEditRequest model."""

    @pytest.fixture()
    def sample_operations(self) -> tuple[CrudiniOperation, ...]:
        return (
            CrudiniOperation(kind="set", section="db", parameter="host", value="localhost"),
            CrudiniOperation(kind="set", section="db", parameter="port", value="5432"),
        )

    def test_minimal(self) -> None:
        req = CrudiniEditRequest(
            file_path="/etc/myapp.conf",
            description="Update database settings",
        )
        assert req.file_path == "/etc/myapp.conf"
        assert req.operations == ()
        assert req.incident_id is None
        assert req.dry_run is False
        assert req.skip_backup is False
        assert req.skip_validation is False

    def test_with_operations(self, sample_operations: tuple[CrudiniOperation, ...]) -> None:
        req = CrudiniEditRequest(
            file_path="/etc/myapp.conf",
            operations=sample_operations,
            description="Update database settings",
        )
        assert len(req.operations) == 2
        assert req.operations[0].parameter == "host"
        assert req.operations[1].parameter == "port"

    def test_with_all_options(self, sample_operations: tuple[CrudiniOperation, ...]) -> None:
        req = CrudiniEditRequest(
            file_path="/etc/myapp.conf",
            operations=sample_operations,
            description="Update db",
            incident_id="INC-123",
            dry_run=True,
            skip_backup=True,
            skip_validation=True,
        )
        assert req.incident_id == "INC-123"
        assert req.dry_run is True
        assert req.skip_backup is True
        assert req.skip_validation is True

    def test_missing_description(self) -> None:
        with pytest.raises(ValidationError):
            CrudiniEditRequest(file_path="/etc/test.conf")

    def test_serialization_roundtrip(self, sample_operations: tuple[CrudiniOperation, ...]) -> None:
        req = CrudiniEditRequest(
            file_path="/etc/test.conf",
            operations=sample_operations,
            description="test",
            dry_run=True,
        )
        data = req.model_dump()
        restored = CrudiniEditRequest.model_validate(data)
        assert restored == req


# =============================================================================
# CrudiniEditResult
# =============================================================================


class TestCrudiniEditResult:
    """Tests for CrudiniEditResult model."""

    def test_success_result(self) -> None:
        changes = (
            CrudiniChange(section="db", parameter="host", old_value="old", new_value="new"),
        )
        result = CrudiniEditResult(
            success=True,
            file_path="/etc/myapp.conf",
            diff="--- a\n+++ b",
            changes=changes,
        )
        assert result.success is True
        assert result.file_path == "/etc/myapp.conf"
        assert len(result.changes) == 1
        assert result.validation_passed is True
        assert result.rollback_applied is False

    def test_failure_with_rollback(self) -> None:
        result = CrudiniEditResult(
            success=False,
            file_path="/etc/myapp.conf",
            error="Invalid INI after edit",
            rollback_applied=True,
            backup_path="/tmp/myapp.conf.bak",
            validation_passed=False,
        )
        assert result.success is False
        assert result.rollback_applied is True
        assert result.backup_path == "/tmp/myapp.conf.bak"
        assert result.validation_passed is False

    def test_defaults(self) -> None:
        result = CrudiniEditResult(success=True, file_path="/etc/test.conf")
        assert result.diff == ""
        assert result.diff_colored == ""
        assert result.backup_path is None
        assert result.validation_passed is True
        assert result.error is None
        assert result.rollback_applied is False
        assert result.changes == ()
        assert result.requires_privilege is False
        assert isinstance(result.completed_at, datetime)

    def test_serialization_roundtrip(self) -> None:
        result = CrudiniEditResult(
            success=True,
            file_path="/etc/test.conf",
            diff="diff output",
            requires_privilege=True,
        )
        data = result.model_dump()
        restored = CrudiniEditResult.model_validate(data)
        assert restored.file_path == result.file_path
        assert restored.requires_privilege is True


# =============================================================================
# INISection / INIFileContent
# =============================================================================


class TestINISection:
    """Tests for INISection model."""

    def test_create(self) -> None:
        section = INISection(
            name="database",
            parameters={"host": "localhost", "port": "5432"},
        )
        assert section.name == "database"
        assert section.parameters["host"] == "localhost"
        assert section.parameters["port"] == "5432"

    def test_empty_parameters(self) -> None:
        section = INISection(name="empty")
        assert section.parameters == {}

    def test_frozen(self) -> None:
        section = INISection(name="test")
        with pytest.raises(ValidationError):
            section.name = "changed"

    def test_serialization_roundtrip(self) -> None:
        section = INISection(
            name="server",
            parameters={"bind": "0.0.0.0", "port": "8080"},
        )
        data = section.model_dump()
        restored = INISection.model_validate(data)
        assert restored == section


class TestINIFileContent:
    """Tests for INIFileContent model."""

    def test_create(self) -> None:
        sections = (
            INISection(name="db", parameters={"host": "localhost"}),
            INISection(name="cache", parameters={"ttl": "300"}),
        )
        content = INIFileContent(
            file_path="/etc/myapp.conf",
            sections=sections,
        )
        assert content.file_path == "/etc/myapp.conf"
        assert len(content.sections) == 2
        assert content.global_parameters == {}

    def test_with_global_parameters(self) -> None:
        content = INIFileContent(
            file_path="/etc/test.conf",
            global_parameters={"encoding": "utf-8"},
        )
        assert content.global_parameters["encoding"] == "utf-8"
        assert content.sections == ()

    def test_serialization_roundtrip(self) -> None:
        content = INIFileContent(
            file_path="/etc/test.conf",
            sections=(INISection(name="main", parameters={"key": "val"}),),
            global_parameters={"version": "1"},
        )
        data = content.model_dump()
        restored = INIFileContent.model_validate(data)
        assert restored == content


# =============================================================================
# CrudiniEditPreview
# =============================================================================


class TestCrudiniEditPreview:
    """Tests for CrudiniEditPreview model."""

    def test_create(self) -> None:
        preview = CrudiniEditPreview(
            file_path="/etc/myapp.conf",
            original_content="[db]\nhost=old\n",
            proposed_content="[db]\nhost=new\n",
            diff="--- a\n+++ b\n-host=old\n+host=new",
            diff_colored="\x1b[31m-host=old\x1b[0m",
        )
        assert preview.file_path == "/etc/myapp.conf"
        assert preview.requires_privilege is False
        assert preview.is_valid_ini is True

    def test_invalid_ini(self) -> None:
        preview = CrudiniEditPreview(
            file_path="/etc/broken.conf",
            original_content="[valid]",
            proposed_content="not[valid",
            diff="diff",
            diff_colored="diff",
            is_valid_ini=False,
        )
        assert preview.is_valid_ini is False


# =============================================================================
# CrudiniStatus
# =============================================================================


class TestCrudiniStatus:
    """Tests for CrudiniStatus model."""

    def test_available(self) -> None:
        status = CrudiniStatus(
            available=True,
            version="0.9.3",
            path="/usr/bin/crudini",
        )
        assert status.available is True
        assert status.version == "0.9.3"
        assert status.path == "/usr/bin/crudini"

    def test_unavailable(self) -> None:
        status = CrudiniStatus(available=False)
        assert status.available is False
        assert status.version is None
        assert status.path is None

    def test_serialization_roundtrip(self) -> None:
        status = CrudiniStatus(available=True, version="0.9.3", path="/usr/bin/crudini")
        data = status.model_dump()
        restored = CrudiniStatus.model_validate(data)
        assert restored == status


# =============================================================================
# Error Types
# =============================================================================


class TestCrudiniErrors:
    """Tests for crudini error types."""

    def test_base_error(self) -> None:
        err = CrudiniError("general error")
        assert str(err) == "general error"
        assert isinstance(err, Exception)

    def test_unavailable_error(self) -> None:
        err = CrudiniUnavailableError("crudini not found")
        assert isinstance(err, CrudiniError)
        assert "crudini not found" in str(err)

    def test_section_error_without_details(self) -> None:
        err = CrudiniSectionError(section="missing")
        assert err.section == "missing"
        assert err.details is None
        assert "[missing]" in str(err)

    def test_section_error_with_details(self) -> None:
        err = CrudiniSectionError(section="bad", details="not found")
        assert err.section == "bad"
        assert err.details == "not found"
        assert "[bad]" in str(err)
        assert "not found" in str(err)

    def test_parameter_error_without_details(self) -> None:
        err = CrudiniParameterError(section="db", parameter="host")
        assert err.section == "db"
        assert err.parameter == "host"
        assert err.details is None
        assert "[db]" in str(err)
        assert "host" in str(err)

    def test_parameter_error_with_details(self) -> None:
        err = CrudiniParameterError(
            section="db", parameter="host", details="type mismatch"
        )
        assert err.details == "type mismatch"
        assert "type mismatch" in str(err)

    def test_parse_error_without_details(self) -> None:
        err = CrudiniParseError(file_path="/etc/broken.conf")
        assert err.file_path == "/etc/broken.conf"
        assert err.details is None
        assert "/etc/broken.conf" in str(err)

    def test_parse_error_with_details(self) -> None:
        err = CrudiniParseError(
            file_path="/etc/bad.conf", details="unexpected token at line 5"
        )
        assert err.details == "unexpected token at line 5"
        assert "unexpected token" in str(err)

    def test_error_hierarchy(self) -> None:
        assert issubclass(CrudiniUnavailableError, CrudiniError)
        assert issubclass(CrudiniSectionError, CrudiniError)
        assert issubclass(CrudiniParameterError, CrudiniError)
        assert issubclass(CrudiniParseError, CrudiniError)
