"""Tests for the XMLStarlet engine (elle.ops.xmlstarlet.engine)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from elle.ops.xmlstarlet.models import (
    XMLEditOperation,
    XMLEditRequest,
    XMLOperation,
    XMLRequest,
    XMLResult,
    XMLStarletUnavailableError,
    XMLValidationResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_module_cache():
    """Reset the module-level caches so each test starts fresh."""
    import elle.ops.xmlstarlet.engine as eng

    eng._xmlstarlet_path = None
    eng._xmlstarlet_version = None
    eng._xmlstarlet_checked = False


@pytest.fixture(autouse=True)
def _reset_cache():
    """Auto-reset engine cache between tests."""
    _reset_module_cache()
    yield
    _reset_module_cache()


# ============================================================================
# Module-level Functions
# ============================================================================


class TestModuleFunctions:
    """Tests for module-level functions: _find_xmlstarlet, is_available, get_version, get_status."""

    def test_find_xmlstarlet_found(self):
        from elle.ops.xmlstarlet.engine import _find_xmlstarlet

        with patch("shutil.which", side_effect=lambda n: "/usr/bin/xmlstarlet" if n == "xmlstarlet" else None):
            assert _find_xmlstarlet() == "/usr/bin/xmlstarlet"

    def test_find_xmlstarlet_xml_name(self):
        from elle.ops.xmlstarlet.engine import _find_xmlstarlet

        with patch("shutil.which", side_effect=lambda n: "/usr/bin/xml" if n == "xml" else None):
            assert _find_xmlstarlet() == "/usr/bin/xml"

    def test_find_xmlstarlet_not_found(self):
        from elle.ops.xmlstarlet.engine import _find_xmlstarlet

        with patch("shutil.which", return_value=None):
            assert _find_xmlstarlet() is None

    def test_is_available_true(self):
        from elle.ops.xmlstarlet.engine import is_available

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            assert is_available() is True

    def test_is_available_false(self):
        from elle.ops.xmlstarlet.engine import is_available

        with patch("shutil.which", return_value=None):
            assert is_available() is False

    def test_get_version_available(self):
        from elle.ops.xmlstarlet.engine import get_version

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1.6.1\ncompiled"
        with (
            patch("shutil.which", return_value="/usr/bin/xmlstarlet"),
            patch("subprocess.run", return_value=mock_result),
        ):
            version = get_version()
        assert version == "1.6.1"

    def test_get_version_cached(self):
        import elle.ops.xmlstarlet.engine as eng

        eng._xmlstarlet_version = "cached"
        version = eng.get_version()
        assert version == "cached"

    def test_get_version_not_available(self):
        from elle.ops.xmlstarlet.engine import get_version

        with patch("shutil.which", return_value=None):
            assert get_version() is None

    def test_get_version_exception(self):
        from elle.ops.xmlstarlet.engine import get_version

        with (
            patch("shutil.which", return_value="/usr/bin/xmlstarlet"),
            patch("subprocess.run", side_effect=OSError("fail")),
        ):
            assert get_version() is None

    def test_get_status_available(self):
        from elle.ops.xmlstarlet.engine import get_status

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1.6.1\n"
        with (
            patch("shutil.which", return_value="/usr/bin/xmlstarlet"),
            patch("subprocess.run", return_value=mock_result),
        ):
            status = get_status()
        assert status.available is True
        assert status.path == "/usr/bin/xmlstarlet"

    def test_get_status_unavailable(self):
        from elle.ops.xmlstarlet.engine import get_status

        with patch("shutil.which", return_value=None):
            status = get_status()
        assert status.available is False


# ============================================================================
# XMLStarletEngine Construction
# ============================================================================


class TestXMLStarletEngineInit:
    def test_init_available(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            engine = XMLStarletEngine()
        assert engine._xmlstarlet_path == "/usr/bin/xmlstarlet"

    def test_init_not_available(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with (
            patch("shutil.which", return_value=None),
            pytest.raises(XMLStarletUnavailableError),
        ):
            XMLStarletEngine()

    def test_context_manager(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            with XMLStarletEngine() as engine:
                assert engine._xmlstarlet_path == "/usr/bin/xmlstarlet"


# ============================================================================
# _run_xmlstarlet
# ============================================================================


class TestRunXMLStarlet:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_run_success(self):
        engine = self._make_engine()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "output"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            success, stdout, stderr, code = engine._run_xmlstarlet("sel", "-t")
        assert success is True
        assert stdout == "output"
        assert code == 0

    def test_run_failure(self):
        engine = self._make_engine()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"
        with patch("subprocess.run", return_value=mock_result):
            success, stdout, stderr, code = engine._run_xmlstarlet("sel", "-t")
        assert success is False
        assert code == 1

    def test_run_timeout(self):
        engine = self._make_engine()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)):
            success, stdout, stderr, code = engine._run_xmlstarlet("sel")
        assert success is False
        assert "timed out" in stderr

    def test_run_exception(self):
        engine = self._make_engine()
        with patch("subprocess.run", side_effect=OSError("fail")):
            success, stdout, stderr, code = engine._run_xmlstarlet("sel")
        assert success is False
        assert code == -1

    def test_run_path_none(self):
        engine = self._make_engine()
        engine._xmlstarlet_path = None
        with pytest.raises(XMLStarletUnavailableError):
            engine._run_xmlstarlet("sel")


# ============================================================================
# Select Operations
# ============================================================================


class TestSelectOperations:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_select_basic(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "value\n", "", 0)):
            result = engine.select("<root><item>value</item></root>", "//item/text()")
        assert result.success is True
        assert result.output == "value"

    def test_select_with_namespaces(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "val\n", "", 0)) as mock_run:
            engine.select("<root/>", "//ns:item", namespaces={"ns": "http://example.com"})
        args = mock_run.call_args[0]
        assert "-N" in args
        assert "ns=http://example.com" in args

    def test_select_with_template(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out\n", "", 0)) as mock_run:
            engine.select("<root/>", "//item", template="//item/text()")
        args = mock_run.call_args[0]
        assert "-c" in args

    def test_select_failure(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(False, "", "error", 1)):
            result = engine.select("<root/>", "//missing")
        assert result.success is False
        assert result.error == "error"

    def test_select_file_exists(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root><item>val</item></root>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "val\n", "", 0)):
            result = engine.select_file(str(f), "//item/text()")
        assert result.success is True

    def test_select_file_not_found(self, tmp_path):
        engine = self._make_engine()
        result = engine.select_file(str(tmp_path / "nope.xml"), "//item")
        assert result.success is False
        assert "not found" in result.error

    def test_select_file_with_namespaces(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "v\n", "", 0)) as mock_run:
            engine.select_file(str(f), "//ns:item", namespaces={"ns": "http://example.com"})
        args = mock_run.call_args[0]
        assert "-N" in args

    def test_select_nodes(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "a\nb\nc\n", "", 0)):
            result = engine.select_nodes("<root/>", "//item")
        assert result == ["a", "b", "c"]

    def test_select_nodes_failure(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(False, "", "err", 1)):
            result = engine.select_nodes("<root/>", "//item")
        assert result == []


# ============================================================================
# Edit Operations
# ============================================================================


class TestEditOperations:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_edit_basic(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "<root><item>new</item></root>", "", 0)):
            result = engine.edit("<root><item>old</item></root>", "//item", "new")
        assert result.success is True

    def test_edit_with_namespaces(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.edit("<root/>", "//ns:item", "val", namespaces={"ns": "http://x"})
        args = mock_run.call_args[0]
        assert "-N" in args

    def test_insert_after(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.insert("<root/>", "//item", "new_elem", "val", position="after")
        args = mock_run.call_args[0]
        assert "-a" in args

    def test_insert_before(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.insert("<root/>", "//item", "new_elem", position="before")
        args = mock_run.call_args[0]
        assert "-i" in args

    def test_insert_into(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.insert("<root/>", "//item", "new_elem", position="into")
        args = mock_run.call_args[0]
        assert "-s" in args

    def test_insert_attr_type(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.insert("<root/>", "//item", "attrname", "attrval", node_type="attr")
        args = mock_run.call_args[0]
        assert "attr" in args

    def test_insert_text_type(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.insert("<root/>", "//item", "textnode", node_type="text")
        args = mock_run.call_args[0]
        assert "text" in args

    def test_insert_with_namespaces(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.insert("<root/>", "//item", "new", namespaces={"ns": "http://x"})
        args = mock_run.call_args[0]
        assert "-N" in args

    def test_delete(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "<root/>", "", 0)) as mock_run:
            result = engine.delete("<root><item/></root>", "//item")
        assert result.success is True
        args = mock_run.call_args[0]
        assert "-d" in args

    def test_delete_with_namespaces(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "<root/>", "", 0)) as mock_run:
            engine.delete("<root/>", "//ns:item", namespaces={"ns": "http://x"})
        args = mock_run.call_args[0]
        assert "-N" in args

    def test_rename(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "<root><renamed/></root>", "", 0)) as mock_run:
            result = engine.rename("<root><item/></root>", "//item", "renamed")
        assert result.success is True
        args = mock_run.call_args[0]
        assert "-r" in args

    def test_rename_with_namespaces(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.rename("<root/>", "//ns:item", "new", namespaces={"ns": "http://x"})
        args = mock_run.call_args[0]
        assert "-N" in args


# ============================================================================
# Validation
# ============================================================================


class TestValidation:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_validate_file_not_found(self, tmp_path):
        engine = self._make_engine()
        result = engine.validate(str(tmp_path / "nope.xml"))
        assert result.valid is False
        assert result.well_formed is False

    def test_validate_well_formed(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "", "", 0)):
            result = engine.validate(str(f))
        assert result.valid is True
        assert result.well_formed is True

    def test_validate_not_well_formed(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "bad.xml"
        f.write_text("<root>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(False, "", "error: not well-formed", 1)):
            result = engine.validate(str(f))
        assert result.valid is False
        assert len(result.errors) > 0

    def test_validate_with_xsd_schema(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "", "", 0)) as mock_run:
            engine.validate(str(f), schema_path="/path/to/schema.xsd", schema_type="xsd")
        args = mock_run.call_args[0]
        assert "-s" in args

    def test_validate_with_dtd_schema(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "", "", 0)) as mock_run:
            engine.validate(str(f), schema_path="/path/to/schema.dtd", schema_type="dtd")
        args = mock_run.call_args[0]
        assert "-d" in args

    def test_validate_with_relaxng_schema(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "", "", 0)) as mock_run:
            engine.validate(str(f), schema_path="/path/to/schema.rng", schema_type="relaxng")
        args = mock_run.call_args[0]
        assert "-r" in args

    def test_validate_warnings(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(False, "", "warning: something\nerror: bad", 1)):
            result = engine.validate(str(f))
        assert len(result.warnings) > 0
        assert len(result.errors) > 0

    def test_validate_content(self):
        engine = self._make_engine()
        with patch.object(engine, "validate", return_value=XMLValidationResult(valid=True, well_formed=True)):
            result = engine.validate_content("<root/>")
        assert result.valid is True


# ============================================================================
# Format
# ============================================================================


class TestFormat:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_format_basic(self):
        engine = self._make_engine()
        formatted = "<root>\n  <item>val</item>\n</root>\n"
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, formatted, "", 0)):
            result = engine.format("<root><item>val</item></root>")
        assert result.success is True
        assert result.output == formatted

    def test_format_omit_declaration(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "<root/>", "", 0)) as mock_run:
            engine.format("<root/>", omit_declaration=True)
        args = mock_run.call_args[0]
        assert "--omit-decl" in args

    def test_format_custom_indent(self):
        engine = self._make_engine()
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "out", "", 0)) as mock_run:
            engine.format("<root/>", indent=4)
        args = mock_run.call_args[0]
        assert "--indent-spaces=4" in args

    def test_format_file_exists(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "<root/>\n", "", 0)):
            result = engine.format_file(str(f))
        assert result.success is True

    def test_format_file_not_found(self, tmp_path):
        engine = self._make_engine()
        result = engine.format_file(str(tmp_path / "nope.xml"))
        assert result.success is False

    def test_format_file_in_place(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        with patch.object(engine, "_run_xmlstarlet", return_value=(True, "", "", 0)) as mock_run:
            result = engine.format_file(str(f), in_place=True)
        assert result.output == ""
        args = mock_run.call_args[0]
        assert "--in-place" in args


# ============================================================================
# Process (XMLRequest)
# ============================================================================


class TestProcess:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_process_select_xml_input(self):
        engine = self._make_engine()
        request = XMLRequest(
            xml_input="<root><item>val</item></root>",
            operation=XMLOperation(kind="select", xpath="//item/text()"),
        )
        with patch.object(engine, "select", return_value=XMLResult(success=True, output="val")):
            result = engine.process(request)
        assert result.success is True

    def test_process_select_file(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLRequest(
            file_path=str(f),
            operation=XMLOperation(kind="select", xpath="//item"),
        )
        with patch.object(engine, "select_file", return_value=XMLResult(success=True, output="v")):
            result = engine.process(request)
        assert result.success is True

    def test_process_select_no_xpath(self):
        engine = self._make_engine()
        request = XMLRequest(
            xml_input="<root/>",
            operation=XMLOperation(kind="select"),
        )
        result = engine.process(request)
        assert result.success is False

    def test_process_edit(self):
        engine = self._make_engine()
        request = XMLRequest(
            xml_input="<root><item>old</item></root>",
            operation=XMLOperation(kind="edit", xpath="//item", value="new"),
        )
        with patch.object(engine, "edit", return_value=XMLResult(success=True, output="<root><item>new</item></root>")):
            result = engine.process(request)
        assert result.success is True

    def test_process_edit_missing_args(self):
        engine = self._make_engine()
        request = XMLRequest(
            xml_input="<root/>",
            operation=XMLOperation(kind="edit"),
        )
        result = engine.process(request)
        assert result.success is False

    def test_process_validate_file(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLRequest(
            file_path=str(f),
            operation=XMLOperation(kind="validate"),
        )
        with patch.object(engine, "validate", return_value=XMLValidationResult(valid=True, well_formed=True)):
            result = engine.process(request)
        assert result.success is True

    def test_process_validate_content(self):
        engine = self._make_engine()
        request = XMLRequest(
            xml_input="<root/>",
            operation=XMLOperation(kind="validate"),
        )
        with patch.object(
            engine,
            "validate_content",
            return_value=XMLValidationResult(valid=False, well_formed=False, errors=("bad",)),
        ):
            result = engine.process(request)
        assert result.success is False

    def test_process_format_xml_input(self):
        engine = self._make_engine()
        request = XMLRequest(
            xml_input="<root/>",
            operation=XMLOperation(kind="format"),
        )
        with patch.object(engine, "format", return_value=XMLResult(success=True, output="<root/>\n")):
            result = engine.process(request)
        assert result.success is True

    def test_process_format_file(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLRequest(
            file_path=str(f),
            operation=XMLOperation(kind="format"),
        )
        with patch.object(engine, "format_file", return_value=XMLResult(success=True, output="<root/>\n")):
            result = engine.process(request)
        assert result.success is True

    def test_process_no_input(self):
        engine = self._make_engine()
        request = XMLRequest(
            operation=XMLOperation(kind="select", xpath="//item"),
        )
        result = engine.process(request)
        assert result.success is False


# ============================================================================
# Preview Edit
# ============================================================================


class TestPreviewEdit:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_preview_file_not_found(self, tmp_path):
        engine = self._make_engine()
        request = XMLEditRequest(
            file_path=str(tmp_path / "nope.xml"),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}):
            result = engine.preview_edit(request)
        assert result.is_valid_xml is False

    def test_preview_success(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root><item>old</item></root>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        with (
            patch.object(
                engine,
                "_apply_edit_operation",
                return_value=XMLResult(success=True, output="<root><item>new</item></root>"),
            ),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = engine.preview_edit(request)
        assert result.is_valid_xml is True

    def test_preview_operation_failure(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=False, error="bad xpath")),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = engine.preview_edit(request)
        assert result.is_valid_xml is False


# ============================================================================
# _apply_edit_operation
# ============================================================================


class TestApplyEditOperation:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_update(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="update", xpath="//item", value="new")
        with patch.object(engine, "edit", return_value=XMLResult(success=True, output="<root/>")):
            result = engine._apply_edit_operation("<root/>", op)
        assert result.success is True

    def test_update_no_value(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="update", xpath="//item")
        result = engine._apply_edit_operation("<root/>", op)
        assert result.success is False

    def test_delete(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="delete", xpath="//item")
        with patch.object(engine, "delete", return_value=XMLResult(success=True, output="<root/>")):
            result = engine._apply_edit_operation("<root/>", op)
        assert result.success is True

    def test_rename(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="rename", xpath="//item", name="new_name")
        with patch.object(engine, "rename", return_value=XMLResult(success=True, output="<root/>")):
            result = engine._apply_edit_operation("<root/>", op)
        assert result.success is True

    def test_rename_no_name(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="rename", xpath="//item")
        result = engine._apply_edit_operation("<root/>", op)
        assert result.success is False

    def test_insert(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="insert", xpath="//item", name="new_elem", value="val")
        with patch.object(engine, "insert", return_value=XMLResult(success=True, output="<root/>")):
            result = engine._apply_edit_operation("<root/>", op)
        assert result.success is True

    def test_insert_no_name(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="insert", xpath="//item")
        result = engine._apply_edit_operation("<root/>", op)
        assert result.success is False

    def test_append(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="append", xpath="//root", name="child", value="v")
        with patch.object(engine, "insert", return_value=XMLResult(success=True, output="<root/>")):
            result = engine._apply_edit_operation("<root/>", op)
        assert result.success is True

    def test_unknown_operation(self):
        engine = self._make_engine()
        op = XMLEditOperation(kind="move", xpath="//item")
        result = engine._apply_edit_operation("<root/>", op)
        assert result.success is False


# ============================================================================
# Execute Edit
# ============================================================================


class TestExecuteEdit:
    def _make_engine(self):
        from elle.ops.xmlstarlet.engine import XMLStarletEngine

        with patch("shutil.which", return_value="/usr/bin/xmlstarlet"):
            return XMLStarletEngine()

    def test_execute_file_not_found(self, tmp_path):
        engine = self._make_engine()
        request = XMLEditRequest(
            file_path=str(tmp_path / "nope.xml"),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        backup_mod = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = engine.execute_edit(request)
        assert result.success is False

    def test_execute_backup_failure(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        backup_mod = MagicMock()
        backup_mod.backup_file = MagicMock(side_effect=RuntimeError("backup err"))
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = engine.execute_edit(request)
        assert result.success is False

    def test_execute_dry_run(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root><item>old</item></root>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            dry_run=True,
            skip_backup=True,
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        backup_mod = MagicMock()
        with (
            patch.object(
                engine,
                "_apply_edit_operation",
                return_value=XMLResult(success=True, output="<root><item>new</item></root>"),
            ),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="old")),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is True
        assert len(result.changes) == 1

    def test_execute_success(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root><item>old</item></root>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            skip_backup=True,
            skip_validation=True,
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        backup_mod = MagicMock()
        with (
            patch.object(
                engine,
                "_apply_edit_operation",
                return_value=XMLResult(success=True, output="<root><item>new</item></root>"),
            ),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="old")),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is True

    def test_execute_operation_fails_with_rollback(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        backup_mod = MagicMock()
        record = MagicMock()
        record.backup_path = "/tmp/bk"
        backup_mod.backup_file = MagicMock(return_value=record)
        backup_mod.restore_file = MagicMock()
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=False, error="xpath err")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="old")),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is False
        assert result.rollback_applied is True

    def test_execute_operation_fails_rollback_fails(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        backup_mod = MagicMock()
        record = MagicMock()
        record.backup_path = "/tmp/bk"
        backup_mod.backup_file = MagicMock(return_value=record)
        backup_mod.restore_file = MagicMock(side_effect=RuntimeError("restore err"))
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=False, error="xpath err")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="")),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is False
        assert "rollback failed" in result.error

    def test_execute_operation_fails_no_backup(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            skip_backup=True,
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        backup_mod = MagicMock()
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=False, error="bad")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="")),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is False

    def test_execute_permission_denied(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            skip_backup=True,
            skip_validation=True,
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        backup_mod = MagicMock()
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=True, output="<root/>")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="")),
            patch("pathlib.Path.write_text", side_effect=PermissionError),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is False
        assert result.requires_privilege is True

    def test_execute_write_exception(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            skip_backup=True,
            skip_validation=True,
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        backup_mod = MagicMock()
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=True, output="<root/>")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="")),
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is False

    def test_execute_validation_fails_with_rollback(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        backup_mod = MagicMock()
        record = MagicMock()
        record.backup_path = "/tmp/bk"
        backup_mod.backup_file = MagicMock(return_value=record)
        backup_mod.restore_file = MagicMock()
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=True, output="<root/>")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="")),
            patch.object(
                engine, "validate", return_value=XMLValidationResult(valid=False, well_formed=False, errors=("bad",))
            ),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is False
        assert result.rollback_applied is True

    def test_execute_validation_fails_rollback_fails(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root/>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            operations=(XMLEditOperation(kind="update", xpath="//item", value="new"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        backup_mod = MagicMock()
        record = MagicMock()
        record.backup_path = "/tmp/bk"
        backup_mod.backup_file = MagicMock(return_value=record)
        backup_mod.restore_file = MagicMock(side_effect=RuntimeError("restore err"))
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=True, output="<root/>")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="")),
            patch.object(
                engine, "validate", return_value=XMLValidationResult(valid=False, well_formed=False, errors=("bad",))
            ),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is False
        assert result.validation_passed is False

    def test_execute_with_delete_operation_change_tracking(self, tmp_path):
        engine = self._make_engine()
        f = tmp_path / "test.xml"
        f.write_text("<root><item>val</item></root>")
        request = XMLEditRequest(
            file_path=str(f),
            description="test",
            dry_run=True,
            skip_backup=True,
            operations=(XMLEditOperation(kind="delete", xpath="//item"),),
        )
        diff_mod = MagicMock()
        diff_mod.unified_diff = MagicMock(return_value="diff")
        diff_mod.colored_diff = MagicMock(return_value="cdiff")
        backup_mod = MagicMock()
        with (
            patch.object(engine, "_apply_edit_operation", return_value=XMLResult(success=True, output="<root/>")),
            patch.object(engine, "select", return_value=XMLResult(success=True, output="val")),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = engine.execute_edit(request)
        assert result.success is True
        assert len(result.changes) == 1
        assert result.changes[0].old_value == "val"
        assert result.changes[0].new_value is None
