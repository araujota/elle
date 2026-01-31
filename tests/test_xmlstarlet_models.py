"""Tests for XMLStarlet XML processing Pydantic models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from elle.ops.xmlstarlet.models import (
    XMLChange,
    XMLEditOperation,
    XMLEditPreview,
    XMLEditRequest,
    XMLEditResult,
    XMLOperation,
    XMLParseError,
    XMLRequest,
    XMLResult,
    XMLStarletError,
    XMLStarletStatus,
    XMLStarletUnavailableError,
    XMLValidationError,
    XMLValidationResult,
    XMLXPathError,
)

# =============================================================================
# XMLOperation
# =============================================================================


class TestXMLOperation:
    """Tests for XMLOperation model."""

    def test_select_operation(self) -> None:
        op = XMLOperation(
            kind="select",
            xpath="//server/port",
        )
        assert op.kind == "select"
        assert op.xpath == "//server/port"
        assert op.value is None
        assert op.edit_type is None
        assert op.namespace is None

    def test_edit_operation(self) -> None:
        op = XMLOperation(
            kind="edit",
            xpath="//config/setting[@name='debug']",
            value="false",
            edit_type="update",
        )
        assert op.kind == "edit"
        assert op.value == "false"
        assert op.edit_type == "update"

    def test_validate_operation(self) -> None:
        op = XMLOperation(kind="validate")
        assert op.kind == "validate"
        assert op.xpath is None

    def test_format_operation(self) -> None:
        op = XMLOperation(kind="format")
        assert op.kind == "format"

    def test_with_namespace(self) -> None:
        op = XMLOperation(
            kind="select",
            xpath="//ns:element",
            namespace={"ns": "http://example.com/ns"},
        )
        assert op.namespace == {"ns": "http://example.com/ns"}

    def test_all_edit_types(self) -> None:
        for edit_type in ("update", "insert", "append", "delete", "rename", "move"):
            op = XMLOperation(kind="edit", edit_type=edit_type)
            assert op.edit_type == edit_type

    def test_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            XMLOperation(kind="transform")

    def test_invalid_edit_type(self) -> None:
        with pytest.raises(ValidationError):
            XMLOperation(kind="edit", edit_type="replace")

    def test_frozen(self) -> None:
        op = XMLOperation(kind="select", xpath="//root")
        with pytest.raises(ValidationError):
            op.xpath = "//other"

    def test_defaults(self) -> None:
        op = XMLOperation(kind="format")
        assert op.xpath is None
        assert op.value is None
        assert op.edit_type is None
        assert op.namespace is None

    def test_serialization_roundtrip(self) -> None:
        op = XMLOperation(
            kind="edit",
            xpath="//config/port",
            value="8080",
            edit_type="update",
            namespace={"cfg": "http://example.com/cfg"},
        )
        data = op.model_dump()
        restored = XMLOperation.model_validate(data)
        assert restored == op


# =============================================================================
# XMLChange
# =============================================================================


class TestXMLChange:
    """Tests for XMLChange model."""

    def test_update_change(self) -> None:
        change = XMLChange(
            xpath="//config/port",
            old_value="80",
            new_value="8080",
            operation="update",
        )
        assert change.xpath == "//config/port"
        assert change.old_value == "80"
        assert change.new_value == "8080"

    def test_insert_change(self) -> None:
        change = XMLChange(
            xpath="//config/new_element",
            new_value="<new/>",
            operation="insert",
        )
        assert change.old_value is None
        assert change.new_value == "<new/>"

    def test_delete_change(self) -> None:
        change = XMLChange(
            xpath="//config/removed",
            old_value="was_here",
            operation="delete",
        )
        assert change.new_value is None

    def test_defaults(self) -> None:
        change = XMLChange(xpath="//root")
        assert change.old_value is None
        assert change.new_value is None
        assert change.operation == "edit"

    def test_frozen(self) -> None:
        change = XMLChange(xpath="//root", new_value="v")
        with pytest.raises(ValidationError):
            change.xpath = "//other"

    def test_serialization_roundtrip(self) -> None:
        change = XMLChange(
            xpath="//server/host",
            old_value="old.example.com",
            new_value="new.example.com",
            operation="update",
        )
        data = change.model_dump()
        restored = XMLChange.model_validate(data)
        assert restored == change


# =============================================================================
# XMLRequest
# =============================================================================


class TestXMLRequest:
    """Tests for XMLRequest model."""

    @pytest.fixture()
    def select_op(self) -> XMLOperation:
        return XMLOperation(kind="select", xpath="//root")

    def test_with_file_path(self, select_op: XMLOperation) -> None:
        req = XMLRequest(
            file_path="/etc/config.xml",
            operation=select_op,
        )
        assert req.file_path == "/etc/config.xml"
        assert req.xml_input is None
        assert req.description == ""

    def test_with_xml_input(self, select_op: XMLOperation) -> None:
        req = XMLRequest(
            xml_input="<root><child/></root>",
            operation=select_op,
        )
        assert req.xml_input == "<root><child/></root>"
        assert req.file_path is None

    def test_with_description(self, select_op: XMLOperation) -> None:
        req = XMLRequest(
            file_path="/etc/config.xml",
            operation=select_op,
            description="Query the root element",
        )
        assert req.description == "Query the root element"

    def test_missing_operation(self) -> None:
        with pytest.raises(ValidationError):
            XMLRequest(file_path="/etc/config.xml")

    def test_serialization_roundtrip(self, select_op: XMLOperation) -> None:
        req = XMLRequest(
            file_path="/etc/test.xml",
            operation=select_op,
            description="test query",
        )
        data = req.model_dump()
        restored = XMLRequest.model_validate(data)
        assert restored == req


# =============================================================================
# XMLResult
# =============================================================================


class TestXMLResult:
    """Tests for XMLResult model."""

    def test_success(self) -> None:
        result = XMLResult(
            success=True,
            output="<port>8080</port>",
            xpath_used="//config/port",
        )
        assert result.success is True
        assert result.output == "<port>8080</port>"
        assert result.error is None
        assert result.exit_code == 0
        assert result.xpath_used == "//config/port"
        assert isinstance(result.completed_at, datetime)

    def test_failure(self) -> None:
        result = XMLResult(
            success=False,
            error="XPath returned no results",
            exit_code=1,
        )
        assert result.success is False
        assert result.output == ""
        assert result.error == "XPath returned no results"

    def test_defaults(self) -> None:
        result = XMLResult(success=True)
        assert result.output == ""
        assert result.error is None
        assert result.exit_code == 0
        assert result.xpath_used is None

    def test_serialization_roundtrip(self) -> None:
        result = XMLResult(
            success=True,
            output="value",
            exit_code=0,
            xpath_used="//test",
        )
        data = result.model_dump()
        restored = XMLResult.model_validate(data)
        assert restored.success == result.success
        assert restored.xpath_used == result.xpath_used


# =============================================================================
# XMLEditOperation
# =============================================================================


class TestXMLEditOperation:
    """Tests for XMLEditOperation model."""

    def test_update(self) -> None:
        op = XMLEditOperation(
            kind="update",
            xpath="//config/port",
            value="9090",
        )
        assert op.kind == "update"
        assert op.xpath == "//config/port"
        assert op.value == "9090"
        assert op.node_type is None
        assert op.position is None
        assert op.name is None

    def test_insert_element(self) -> None:
        op = XMLEditOperation(
            kind="insert",
            xpath="//config",
            value="default_value",
            node_type="elem",
            position="into",
            name="new_setting",
        )
        assert op.kind == "insert"
        assert op.node_type == "elem"
        assert op.position == "into"
        assert op.name == "new_setting"

    def test_delete(self) -> None:
        op = XMLEditOperation(
            kind="delete",
            xpath="//config/deprecated",
        )
        assert op.kind == "delete"
        assert op.value is None

    def test_rename(self) -> None:
        op = XMLEditOperation(
            kind="rename",
            xpath="//config/old_name",
            name="new_name",
        )
        assert op.kind == "rename"
        assert op.name == "new_name"

    def test_append(self) -> None:
        op = XMLEditOperation(
            kind="append",
            xpath="//config/list",
            value="<item>new</item>",
            node_type="elem",
            name="item",
        )
        assert op.kind == "append"

    def test_move(self) -> None:
        op = XMLEditOperation(
            kind="move",
            xpath="//source/elem",
            position="into",
        )
        assert op.kind == "move"
        assert op.position == "into"

    def test_all_kinds(self) -> None:
        for kind in ("update", "insert", "append", "delete", "rename", "move"):
            op = XMLEditOperation(kind=kind, xpath="//test")
            assert op.kind == kind

    def test_all_node_types(self) -> None:
        for nt in ("elem", "attr", "text", "comment", "pi"):
            op = XMLEditOperation(kind="insert", xpath="//test", node_type=nt)
            assert op.node_type == nt

    def test_all_positions(self) -> None:
        for pos in ("before", "after", "into"):
            op = XMLEditOperation(kind="insert", xpath="//test", position=pos)
            assert op.position == pos

    def test_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            XMLEditOperation(kind="replace", xpath="//test")

    def test_invalid_node_type(self) -> None:
        with pytest.raises(ValidationError):
            XMLEditOperation(kind="insert", xpath="//test", node_type="document")

    def test_invalid_position(self) -> None:
        with pytest.raises(ValidationError):
            XMLEditOperation(kind="insert", xpath="//test", position="above")

    def test_frozen(self) -> None:
        op = XMLEditOperation(kind="update", xpath="//test", value="v")
        with pytest.raises(ValidationError):
            op.value = "new"

    def test_serialization_roundtrip(self) -> None:
        op = XMLEditOperation(
            kind="insert",
            xpath="//config",
            value="val",
            node_type="attr",
            position="into",
            name="attr_name",
        )
        data = op.model_dump()
        restored = XMLEditOperation.model_validate(data)
        assert restored == op


# =============================================================================
# XMLEditRequest
# =============================================================================


class TestXMLEditRequest:
    """Tests for XMLEditRequest model."""

    @pytest.fixture()
    def sample_operations(self) -> tuple[XMLEditOperation, ...]:
        return (
            XMLEditOperation(kind="update", xpath="//port", value="9090"),
            XMLEditOperation(kind="delete", xpath="//deprecated"),
        )

    def test_minimal(self) -> None:
        req = XMLEditRequest(
            file_path="/etc/config.xml",
            description="Update XML config",
        )
        assert req.file_path == "/etc/config.xml"
        assert req.operations == ()
        assert req.incident_id is None
        assert req.dry_run is False
        assert req.skip_backup is False
        assert req.skip_validation is False
        assert req.namespaces == {}
        assert req.preserve_formatting is True

    def test_with_operations(self, sample_operations: tuple[XMLEditOperation, ...]) -> None:
        req = XMLEditRequest(
            file_path="/etc/config.xml",
            operations=sample_operations,
            description="Multi-op edit",
        )
        assert len(req.operations) == 2

    def test_all_options(self, sample_operations: tuple[XMLEditOperation, ...]) -> None:
        req = XMLEditRequest(
            file_path="/etc/config.xml",
            operations=sample_operations,
            description="Full edit",
            incident_id="INC-456",
            dry_run=True,
            skip_backup=True,
            skip_validation=True,
            namespaces={"ns": "http://example.com/ns"},
            preserve_formatting=False,
        )
        assert req.incident_id == "INC-456"
        assert req.dry_run is True
        assert req.skip_backup is True
        assert req.skip_validation is True
        assert req.namespaces == {"ns": "http://example.com/ns"}
        assert req.preserve_formatting is False

    def test_missing_description(self) -> None:
        with pytest.raises(ValidationError):
            XMLEditRequest(file_path="/etc/config.xml")

    def test_serialization_roundtrip(self, sample_operations: tuple[XMLEditOperation, ...]) -> None:
        req = XMLEditRequest(
            file_path="/etc/test.xml",
            operations=sample_operations,
            description="test",
            namespaces={"x": "http://example.com"},
        )
        data = req.model_dump()
        restored = XMLEditRequest.model_validate(data)
        assert restored == req


# =============================================================================
# XMLEditResult
# =============================================================================


class TestXMLEditResult:
    """Tests for XMLEditResult model."""

    def test_success(self) -> None:
        changes = (XMLChange(xpath="//port", old_value="80", new_value="8080", operation="update"),)
        result = XMLEditResult(
            success=True,
            file_path="/etc/config.xml",
            diff="--- a\n+++ b",
            changes=changes,
        )
        assert result.success is True
        assert len(result.changes) == 1
        assert result.validation_passed is True
        assert result.rollback_applied is False

    def test_failure_with_rollback(self) -> None:
        result = XMLEditResult(
            success=False,
            file_path="/etc/config.xml",
            error="Invalid XML after edit",
            rollback_applied=True,
            backup_path="/tmp/config.xml.bak",
            validation_passed=False,
        )
        assert result.success is False
        assert result.rollback_applied is True
        assert result.validation_passed is False

    def test_defaults(self) -> None:
        result = XMLEditResult(success=True, file_path="/etc/test.xml")
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
        result = XMLEditResult(
            success=True,
            file_path="/etc/test.xml",
            diff="some diff",
            requires_privilege=True,
        )
        data = result.model_dump()
        restored = XMLEditResult.model_validate(data)
        assert restored.requires_privilege is True


# =============================================================================
# XMLEditPreview
# =============================================================================


class TestXMLEditPreview:
    """Tests for XMLEditPreview model."""

    def test_create(self) -> None:
        preview = XMLEditPreview(
            file_path="/etc/config.xml",
            original_content="<root><port>80</port></root>",
            proposed_content="<root><port>8080</port></root>",
            diff="--- a\n+++ b\n-<port>80</port>\n+<port>8080</port>",
            diff_colored="\x1b[31m-...\x1b[0m",
        )
        assert preview.file_path == "/etc/config.xml"
        assert preview.requires_privilege is False
        assert preview.is_valid_xml is True

    def test_invalid_xml_preview(self) -> None:
        preview = XMLEditPreview(
            file_path="/etc/broken.xml",
            original_content="<root/>",
            proposed_content="<root><broken",
            diff="diff",
            diff_colored="diff",
            is_valid_xml=False,
        )
        assert preview.is_valid_xml is False

    def test_privileged_preview(self) -> None:
        preview = XMLEditPreview(
            file_path="/etc/config.xml",
            original_content="<r/>",
            proposed_content="<r/>",
            diff="",
            diff_colored="",
            requires_privilege=True,
        )
        assert preview.requires_privilege is True


# =============================================================================
# XMLValidationResult
# =============================================================================


class TestXMLValidationResult:
    """Tests for XMLValidationResult model."""

    def test_valid(self) -> None:
        vr = XMLValidationResult(valid=True)
        assert vr.valid is True
        assert vr.well_formed is True
        assert vr.schema_valid is None
        assert vr.errors == ()
        assert vr.warnings == ()

    def test_invalid_with_errors(self) -> None:
        vr = XMLValidationResult(
            valid=False,
            well_formed=False,
            errors=("Unclosed element at line 5", "Missing root element"),
        )
        assert vr.valid is False
        assert vr.well_formed is False
        assert len(vr.errors) == 2

    def test_schema_validation(self) -> None:
        vr = XMLValidationResult(
            valid=False,
            well_formed=True,
            schema_valid=False,
            errors=("Element 'foo' not expected",),
            warnings=("Deprecated attribute 'bar'",),
        )
        assert vr.well_formed is True
        assert vr.schema_valid is False
        assert len(vr.warnings) == 1

    def test_serialization_roundtrip(self) -> None:
        vr = XMLValidationResult(
            valid=True,
            well_formed=True,
            schema_valid=True,
            errors=(),
            warnings=("minor issue",),
        )
        data = vr.model_dump()
        restored = XMLValidationResult.model_validate(data)
        assert restored == vr


# =============================================================================
# XMLStarletStatus
# =============================================================================


class TestXMLStarletStatus:
    """Tests for XMLStarletStatus model."""

    def test_available(self) -> None:
        status = XMLStarletStatus(
            available=True,
            version="1.6.1",
            path="/usr/bin/xmlstarlet",
        )
        assert status.available is True
        assert status.version == "1.6.1"
        assert status.path == "/usr/bin/xmlstarlet"

    def test_unavailable(self) -> None:
        status = XMLStarletStatus(available=False)
        assert status.available is False
        assert status.version is None
        assert status.path is None

    def test_serialization_roundtrip(self) -> None:
        status = XMLStarletStatus(available=True, version="1.6.1", path="/usr/bin/xmlstarlet")
        data = status.model_dump()
        restored = XMLStarletStatus.model_validate(data)
        assert restored == status


# =============================================================================
# Error Types
# =============================================================================


class TestXMLStarletErrors:
    """Tests for XMLStarlet error types."""

    def test_base_error(self) -> None:
        err = XMLStarletError("general error")
        assert str(err) == "general error"
        assert isinstance(err, Exception)

    def test_unavailable_error(self) -> None:
        err = XMLStarletUnavailableError("xmlstarlet not found")
        assert isinstance(err, XMLStarletError)
        assert "xmlstarlet not found" in str(err)

    def test_xpath_error_without_details(self) -> None:
        err = XMLXPathError(xpath="//bad[xpath")
        assert err.xpath == "//bad[xpath"
        assert err.details is None
        assert "//bad[xpath" in str(err)

    def test_xpath_error_with_details(self) -> None:
        err = XMLXPathError(xpath="//foo", details="no matching nodes")
        assert err.details == "no matching nodes"
        assert "no matching nodes" in str(err)

    def test_parse_error_without_details(self) -> None:
        err = XMLParseError(source="/etc/broken.xml")
        assert err.source == "/etc/broken.xml"
        assert err.details is None
        assert "/etc/broken.xml" in str(err)

    def test_parse_error_with_details(self) -> None:
        err = XMLParseError(source="stdin", details="unclosed tag at line 3")
        assert err.details == "unclosed tag at line 3"
        assert "unclosed tag" in str(err)

    def test_validation_error(self) -> None:
        errors = ["Element 'x' not expected", "Missing required 'y'", "Wrong type for 'z'"]
        err = XMLValidationError(file_path="/etc/config.xml", errors=errors)
        assert err.file_path == "/etc/config.xml"
        assert len(err.errors) == 3
        assert "Element 'x' not expected" in str(err)
        assert "Missing required 'y'" in str(err)
        assert "Wrong type for 'z'" in str(err)

    def test_validation_error_truncation(self) -> None:
        errors = ["err1", "err2", "err3", "err4", "err5"]
        err = XMLValidationError(file_path="/test.xml", errors=errors)
        msg = str(err)
        assert "err1" in msg
        assert "err2" in msg
        assert "err3" in msg
        assert "2 more errors" in msg

    def test_error_hierarchy(self) -> None:
        assert issubclass(XMLStarletUnavailableError, XMLStarletError)
        assert issubclass(XMLXPathError, XMLStarletError)
        assert issubclass(XMLParseError, XMLStarletError)
        assert issubclass(XMLValidationError, XMLStarletError)
