"""Core XMLStarlet wrapper for XML processing.

Provides a high-level interface to the xmlstarlet command-line tool for
XML querying, editing, validation, and formatting.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

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
    XMLValidationResult,
    XMLXPathError,
)

logger = logging.getLogger(__name__)

# Cache for availability check
_xmlstarlet_path: str | None = None
_xmlstarlet_version: str | None = None
_xmlstarlet_checked: bool = False


def _find_xmlstarlet() -> str | None:
    """Find the xmlstarlet executable."""
    global _xmlstarlet_path, _xmlstarlet_checked
    if not _xmlstarlet_checked:
        # Try both 'xmlstarlet' and 'xml' (some systems use the shorter name)
        _xmlstarlet_path = shutil.which("xmlstarlet") or shutil.which("xml")
        _xmlstarlet_checked = True
    return _xmlstarlet_path


def is_available() -> bool:
    """Check if xmlstarlet is available on the system.

    Returns:
        True if xmlstarlet can be executed.
    """
    return _find_xmlstarlet() is not None


def get_version() -> str | None:
    """Get the xmlstarlet version string.

    Returns:
        Version string or None if unavailable.
    """
    global _xmlstarlet_version
    if _xmlstarlet_version is not None:
        return _xmlstarlet_version

    xmlstarlet_path = _find_xmlstarlet()
    if xmlstarlet_path is None:
        return None

    try:
        result = subprocess.run(
            [xmlstarlet_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _xmlstarlet_version = result.stdout.strip().split("\n")[0]
            return _xmlstarlet_version
    except Exception:
        pass
    return None


def get_status() -> XMLStarletStatus:
    """Get the current status of xmlstarlet availability.

    Returns:
        XMLStarletStatus with availability information.
    """
    xmlstarlet_path = _find_xmlstarlet()
    return XMLStarletStatus(
        available=xmlstarlet_path is not None,
        version=get_version(),
        path=xmlstarlet_path,
    )


class XMLStarletEngine:
    """Wrapper around the xmlstarlet command-line tool for XML processing.

    Provides methods to select (query), edit, validate, and format XML data.

    Usage:
        engine = XMLStarletEngine()

        # Query XML with XPath
        result = engine.select('<root><item>value</item></root>', '//item/text()')
        # result.output == 'value'

        # Edit XML
        result = engine.edit(
            '<root><item>old</item></root>',
            xpath='//item',
            value='new',
        )

        # Validate XML
        result = engine.validate('/path/to/file.xml')

        # Format/pretty-print XML
        result = engine.format('<root><item>value</item></root>')

    Context manager usage:
        with XMLStarletEngine() as engine:
            result = engine.select_file('/etc/dbus-1/system.conf', '//policy/@context')
    """

    def __init__(self) -> None:
        """Initialize the XMLStarlet engine.

        Raises:
            XMLStarletUnavailableError: If xmlstarlet is not installed.
        """
        self._xmlstarlet_path = _find_xmlstarlet()
        if self._xmlstarlet_path is None:
            raise XMLStarletUnavailableError(
                "xmlstarlet is not installed. Install with: sudo apt install xmlstarlet"
            )
        logger.debug(f"Initialized XMLStarletEngine with xmlstarlet at {self._xmlstarlet_path}")

    def __enter__(self) -> XMLStarletEngine:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        pass  # No cleanup needed

    # =========================================================================
    # Core Operations
    # =========================================================================

    def _run_xmlstarlet(
        self,
        *args: str,
        input_data: str | None = None,
    ) -> tuple[bool, str, str, int]:
        """Run xmlstarlet with the given arguments.

        Args:
            *args: Command-line arguments for xmlstarlet.
            input_data: Optional XML input for stdin.

        Returns:
            Tuple of (success, stdout, stderr, exit_code).
        """
        if self._xmlstarlet_path is None:
            raise XMLStarletUnavailableError("xmlstarlet is not available")

        cmd = [self._xmlstarlet_path] + list(args)

        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return (
                result.returncode == 0,
                result.stdout,
                result.stderr,
                result.returncode,
            )
        except subprocess.TimeoutExpired:
            return (False, "", "xmlstarlet operation timed out", -1)
        except Exception as e:
            return (False, "", str(e), -1)

    # =========================================================================
    # Select (Query) Operations
    # =========================================================================

    def select(
        self,
        xml_input: str,
        xpath: str,
        *,
        namespaces: dict[str, str] | None = None,
        template: str | None = None,
    ) -> XMLResult:
        """Query XML data using XPath.

        Args:
            xml_input: XML string to query.
            xpath: XPath expression.
            namespaces: Namespace prefix to URI mappings.
            template: Optional output template (for complex queries).

        Returns:
            XMLResult with query output.
        """
        args = ["sel", "-t"]

        # Add namespace declarations
        if namespaces:
            for prefix, uri in namespaces.items():
                args.extend(["-N", f"{prefix}={uri}"])

        # Add template or default value-of
        if template:
            args.extend(["-c", template])
        else:
            args.extend(["-v", xpath])

        success, stdout, stderr, exit_code = self._run_xmlstarlet(
            *args,
            input_data=xml_input,
        )

        return XMLResult(
            success=success,
            output=stdout.rstrip("\n"),
            error=stderr if not success else None,
            exit_code=exit_code,
            xpath_used=xpath,
        )

    def select_file(
        self,
        file_path: str | Path,
        xpath: str,
        *,
        namespaces: dict[str, str] | None = None,
    ) -> XMLResult:
        """Query an XML file using XPath.

        Args:
            file_path: Path to the XML file.
            xpath: XPath expression.
            namespaces: Namespace prefix to URI mappings.

        Returns:
            XMLResult with query output.
        """
        path_str = str(file_path)

        if not Path(path_str).exists():
            return XMLResult(
                success=False,
                error=f"File not found: {path_str}",
                exit_code=1,
                xpath_used=xpath,
            )

        args = ["sel", "-t"]

        # Add namespace declarations
        if namespaces:
            for prefix, uri in namespaces.items():
                args.extend(["-N", f"{prefix}={uri}"])

        args.extend(["-v", xpath, path_str])

        success, stdout, stderr, exit_code = self._run_xmlstarlet(*args)

        return XMLResult(
            success=success,
            output=stdout.rstrip("\n"),
            error=stderr if not success else None,
            exit_code=exit_code,
            xpath_used=xpath,
        )

    def select_nodes(
        self,
        xml_input: str,
        xpath: str,
        *,
        namespaces: dict[str, str] | None = None,
    ) -> list[str]:
        """Query XML and return matching nodes as a list.

        Args:
            xml_input: XML string to query.
            xpath: XPath expression.
            namespaces: Namespace prefix to URI mappings.

        Returns:
            List of matching node values.
        """
        result = self.select(xml_input, xpath, namespaces=namespaces)
        if not result.success:
            return []
        return [line for line in result.output.split("\n") if line.strip()]

    # =========================================================================
    # Edit Operations
    # =========================================================================

    def edit(
        self,
        xml_input: str,
        xpath: str,
        value: str,
        *,
        namespaces: dict[str, str] | None = None,
    ) -> XMLResult:
        """Update XML element/attribute value.

        Args:
            xml_input: XML string to edit.
            xpath: XPath expression targeting elements to update.
            value: New value.
            namespaces: Namespace prefix to URI mappings.

        Returns:
            XMLResult with modified XML.
        """
        args = ["ed", "-u", xpath, "-v", value]

        if namespaces:
            for prefix, uri in namespaces.items():
                args.extend(["-N", f"{prefix}={uri}"])

        success, stdout, stderr, exit_code = self._run_xmlstarlet(
            *args,
            input_data=xml_input,
        )

        return XMLResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
            xpath_used=xpath,
        )

    def insert(
        self,
        xml_input: str,
        xpath: str,
        name: str,
        value: str | None = None,
        *,
        node_type: str = "elem",
        position: str = "after",
        namespaces: dict[str, str] | None = None,
    ) -> XMLResult:
        """Insert a new node into XML.

        Args:
            xml_input: XML string to edit.
            xpath: XPath expression for insertion point.
            name: Name of the new element/attribute.
            value: Value of the new node (optional).
            node_type: Type of node: 'elem', 'attr', 'text'.
            position: Where to insert: 'before', 'after', 'into' (subnode).
            namespaces: Namespace prefix to URI mappings.

        Returns:
            XMLResult with modified XML.
        """
        # Build edit command based on position
        if position == "before":
            insert_flag = "-i"
        elif position == "after":
            insert_flag = "-a"
        else:  # into
            insert_flag = "-s"

        # Build node type flag
        if node_type == "elem":
            type_flag = "-t elem"
        elif node_type == "attr":
            type_flag = "-t attr"
        else:
            type_flag = "-t text"

        args = ["ed", insert_flag, xpath, type_flag.split()[0], type_flag.split()[1], "-n", name]

        if value:
            args.extend(["-v", value])

        if namespaces:
            for prefix, uri in namespaces.items():
                args.extend(["-N", f"{prefix}={uri}"])

        success, stdout, stderr, exit_code = self._run_xmlstarlet(
            *args,
            input_data=xml_input,
        )

        return XMLResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
            xpath_used=xpath,
        )

    def delete(
        self,
        xml_input: str,
        xpath: str,
        *,
        namespaces: dict[str, str] | None = None,
    ) -> XMLResult:
        """Delete nodes matching XPath.

        Args:
            xml_input: XML string to edit.
            xpath: XPath expression targeting nodes to delete.
            namespaces: Namespace prefix to URI mappings.

        Returns:
            XMLResult with modified XML.
        """
        args = ["ed", "-d", xpath]

        if namespaces:
            for prefix, uri in namespaces.items():
                args.extend(["-N", f"{prefix}={uri}"])

        success, stdout, stderr, exit_code = self._run_xmlstarlet(
            *args,
            input_data=xml_input,
        )

        return XMLResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
            xpath_used=xpath,
        )

    def rename(
        self,
        xml_input: str,
        xpath: str,
        new_name: str,
        *,
        namespaces: dict[str, str] | None = None,
    ) -> XMLResult:
        """Rename nodes matching XPath.

        Args:
            xml_input: XML string to edit.
            xpath: XPath expression targeting nodes to rename.
            new_name: New name for the nodes.
            namespaces: Namespace prefix to URI mappings.

        Returns:
            XMLResult with modified XML.
        """
        args = ["ed", "-r", xpath, "-v", new_name]

        if namespaces:
            for prefix, uri in namespaces.items():
                args.extend(["-N", f"{prefix}={uri}"])

        success, stdout, stderr, exit_code = self._run_xmlstarlet(
            *args,
            input_data=xml_input,
        )

        return XMLResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
            xpath_used=xpath,
        )

    # =========================================================================
    # Validation
    # =========================================================================

    def validate(
        self,
        file_path: str | Path,
        *,
        schema_path: str | None = None,
        schema_type: str | None = None,
    ) -> XMLValidationResult:
        """Validate an XML file.

        Args:
            file_path: Path to the XML file.
            schema_path: Optional path to XSD/DTD schema.
            schema_type: Schema type: 'xsd', 'dtd', 'relaxng', or None for well-formed check.

        Returns:
            XMLValidationResult with validation outcome.
        """
        path_str = str(file_path)

        if not Path(path_str).exists():
            return XMLValidationResult(
                valid=False,
                well_formed=False,
                errors=(f"File not found: {path_str}",),
            )

        # Check well-formedness first
        args = ["val", "-e", path_str]

        if schema_path and schema_type:
            if schema_type == "xsd":
                args = ["val", "-e", "-s", schema_path, path_str]
            elif schema_type == "dtd":
                args = ["val", "-e", "-d", schema_path, path_str]
            elif schema_type == "relaxng":
                args = ["val", "-e", "-r", schema_path, path_str]

        success, stdout, stderr, exit_code = self._run_xmlstarlet(*args)

        errors: list[str] = []
        warnings: list[str] = []

        if not success:
            # Parse error output
            for line in (stderr + stdout).split("\n"):
                line = line.strip()
                if line and "error" in line.lower():
                    errors.append(line)
                elif line and "warning" in line.lower():
                    warnings.append(line)
                elif line and line != path_str:
                    errors.append(line)

        return XMLValidationResult(
            valid=success,
            well_formed=success or "well-formed" not in stderr.lower(),
            schema_valid=success if schema_path else None,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def validate_content(
        self,
        xml_content: str,
    ) -> XMLValidationResult:
        """Validate XML content string.

        Args:
            xml_content: XML string to validate.

        Returns:
            XMLValidationResult with validation outcome.
        """
        # Write to temp file for validation
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml_content)
            temp_path = f.name

        try:
            return self.validate(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    # =========================================================================
    # Format
    # =========================================================================

    def format(
        self,
        xml_input: str,
        *,
        indent: int = 2,
        omit_declaration: bool = False,
    ) -> XMLResult:
        """Format/pretty-print XML.

        Args:
            xml_input: XML string to format.
            indent: Indentation width (spaces).
            omit_declaration: If True, omit XML declaration.

        Returns:
            XMLResult with formatted XML.
        """
        args = ["fo", f"--indent-spaces={indent}"]

        if omit_declaration:
            args.append("--omit-decl")

        success, stdout, stderr, exit_code = self._run_xmlstarlet(
            *args,
            input_data=xml_input,
        )

        return XMLResult(
            success=success,
            output=stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
        )

    def format_file(
        self,
        file_path: str | Path,
        *,
        indent: int = 2,
        in_place: bool = False,
    ) -> XMLResult:
        """Format/pretty-print an XML file.

        Args:
            file_path: Path to the XML file.
            indent: Indentation width (spaces).
            in_place: If True, modify file in place.

        Returns:
            XMLResult with formatted XML (or empty if in_place).
        """
        path_str = str(file_path)

        if not Path(path_str).exists():
            return XMLResult(
                success=False,
                error=f"File not found: {path_str}",
                exit_code=1,
            )

        args = ["fo", f"--indent-spaces={indent}"]

        if in_place:
            args.extend(["--in-place", path_str])
        else:
            args.append(path_str)

        success, stdout, stderr, exit_code = self._run_xmlstarlet(*args)

        return XMLResult(
            success=success,
            output="" if in_place else stdout,
            error=stderr if not success else None,
            exit_code=exit_code,
        )

    # =========================================================================
    # Process Operation Request
    # =========================================================================

    def process(self, request: XMLRequest) -> XMLResult:
        """Process an XMLRequest.

        Args:
            request: The request to process.

        Returns:
            XMLResult with the operation outcome.
        """
        op = request.operation

        if op.kind == "select":
            if not op.xpath:
                return XMLResult(
                    success=False,
                    error="select operation requires xpath",
                    exit_code=1,
                )
            if request.file_path:
                return self.select_file(
                    request.file_path,
                    op.xpath,
                    namespaces=op.namespace,
                )
            elif request.xml_input:
                return self.select(
                    request.xml_input,
                    op.xpath,
                    namespaces=op.namespace,
                )

        elif op.kind == "edit":
            if not op.xpath or not op.value:
                return XMLResult(
                    success=False,
                    error="edit operation requires xpath and value",
                    exit_code=1,
                )
            if request.xml_input:
                return self.edit(
                    request.xml_input,
                    op.xpath,
                    op.value,
                    namespaces=op.namespace,
                )

        elif op.kind == "validate":
            if request.file_path:
                result = self.validate(request.file_path)
                return XMLResult(
                    success=result.valid,
                    output="Valid XML" if result.valid else "\n".join(result.errors),
                    error=None if result.valid else "Validation failed",
                )
            elif request.xml_input:
                result = self.validate_content(request.xml_input)
                return XMLResult(
                    success=result.valid,
                    output="Valid XML" if result.valid else "\n".join(result.errors),
                    error=None if result.valid else "Validation failed",
                )

        elif op.kind == "format":
            if request.xml_input:
                return self.format(request.xml_input)
            elif request.file_path:
                return self.format_file(request.file_path)

        return XMLResult(
            success=False,
            error="No input source specified or invalid operation",
            exit_code=1,
        )

    # =========================================================================
    # File Editing
    # =========================================================================

    def preview_edit(self, request: XMLEditRequest) -> XMLEditPreview:
        """Preview changes that would be made to an XML file.

        Args:
            request: The edit request to preview.

        Returns:
            XMLEditPreview showing the proposed changes.
        """
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)

        # Check file exists
        if not file_path.exists():
            return XMLEditPreview(
                file_path=request.file_path,
                original_content="",
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid_xml=False,
            )

        # Read original content
        original_content = file_path.read_text()

        # Apply operations sequentially
        current_content = original_content
        for op in request.operations:
            result = self._apply_edit_operation(current_content, op, request.namespaces)
            if not result.success:
                return XMLEditPreview(
                    file_path=request.file_path,
                    original_content=original_content,
                    proposed_content="",
                    diff="",
                    diff_colored="",
                    is_valid_xml=False,
                )
            current_content = result.output

        proposed_content = current_content

        # Generate diffs
        diff = unified_diff(original_content, proposed_content, request.file_path)
        diff_col = colored_diff(original_content, proposed_content, request.file_path)

        # Check privileges
        requires_privilege = False
        try:
            import os

            if file_path.exists():
                requires_privilege = not os.access(file_path, os.W_OK)
        except Exception:
            pass

        return XMLEditPreview(
            file_path=request.file_path,
            original_content=original_content,
            proposed_content=proposed_content,
            diff=diff,
            diff_colored=diff_col,
            requires_privilege=requires_privilege,
            is_valid_xml=True,
        )

    def _apply_edit_operation(
        self,
        xml_content: str,
        op: XMLEditOperation,
        namespaces: dict[str, str] | None = None,
    ) -> XMLResult:
        """Apply a single edit operation to XML content.

        Args:
            xml_content: XML string to edit.
            op: Edit operation to apply.
            namespaces: Namespace mappings.

        Returns:
            XMLResult with modified XML.
        """
        if op.kind == "update":
            if op.value is None:
                return XMLResult(success=False, error="update requires value")
            return self.edit(xml_content, op.xpath, op.value, namespaces=namespaces)

        elif op.kind == "delete":
            return self.delete(xml_content, op.xpath, namespaces=namespaces)

        elif op.kind == "rename":
            if op.name is None:
                return XMLResult(success=False, error="rename requires name")
            return self.rename(xml_content, op.xpath, op.name, namespaces=namespaces)

        elif op.kind in ("insert", "append"):
            if op.name is None:
                return XMLResult(success=False, error="insert requires name")
            position = op.position or ("into" if op.kind == "append" else "after")
            return self.insert(
                xml_content,
                op.xpath,
                op.name,
                op.value,
                node_type=op.node_type or "elem",
                position=position,
                namespaces=namespaces,
            )

        return XMLResult(success=False, error=f"Unknown operation: {op.kind}")

    def execute_edit(self, request: XMLEditRequest) -> XMLEditResult:
        """Execute XML file edits.

        Args:
            request: The edit request to execute.

        Returns:
            XMLEditResult with the operation outcome.
        """
        from elle.ops.augeas.backup import backup_file, restore_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

        file_path = Path(request.file_path)
        backup_path: str | None = None

        # Check file exists
        if not file_path.exists():
            return XMLEditResult(
                success=False,
                file_path=request.file_path,
                error=f"File not found: {request.file_path}",
            )

        # Read original content
        original_content = file_path.read_text()

        # Create backup unless skipped
        if not request.skip_backup:
            try:
                backup_record = backup_file(request.file_path, domain="xml")
                backup_path = backup_record.backup_path
            except Exception as e:
                return XMLEditResult(
                    success=False,
                    file_path=request.file_path,
                    error=f"Failed to create backup: {e}",
                )

        # Apply operations sequentially
        current_content = original_content
        changes: list[XMLChange] = []

        for op in request.operations:
            # Get old value for change tracking
            old_value = None
            if op.kind in ("update", "delete", "rename"):
                old_result = self.select(current_content, op.xpath, namespaces=request.namespaces)
                if old_result.success:
                    old_value = old_result.output

            result = self._apply_edit_operation(current_content, op, request.namespaces)

            if not result.success:
                # Rollback on failure
                if backup_path:
                    try:
                        restore_file(backup_path)
                        return XMLEditResult(
                            success=False,
                            file_path=request.file_path,
                            error=f"Operation failed, changes rolled back: {result.error}",
                            backup_path=backup_path,
                            rollback_applied=True,
                        )
                    except Exception as e:
                        return XMLEditResult(
                            success=False,
                            file_path=request.file_path,
                            error=f"Operation failed and rollback failed: {result.error} / {e}",
                            backup_path=backup_path,
                        )
                return XMLEditResult(
                    success=False,
                    file_path=request.file_path,
                    error=result.error,
                    backup_path=backup_path,
                )

            current_content = result.output

            # Record the change
            changes.append(
                XMLChange(
                    xpath=op.xpath,
                    old_value=old_value,
                    new_value=op.value if op.kind in ("update", "insert", "append") else None,
                    operation=op.kind,
                )
            )

        proposed_content = current_content

        # Dry run - just return preview
        if request.dry_run:
            diff = unified_diff(original_content, proposed_content, request.file_path)
            diff_col = colored_diff(original_content, proposed_content, request.file_path)
            return XMLEditResult(
                success=True,
                file_path=request.file_path,
                diff=diff,
                diff_colored=diff_col,
                backup_path=backup_path,
                validation_passed=True,
                changes=tuple(changes),
            )

        # Write the new content
        try:
            file_path.write_text(proposed_content)
        except PermissionError:
            return XMLEditResult(
                success=False,
                file_path=request.file_path,
                error=f"Permission denied: {request.file_path}",
                backup_path=backup_path,
                requires_privilege=True,
            )
        except Exception as e:
            return XMLEditResult(
                success=False,
                file_path=request.file_path,
                error=f"Failed to write file: {e}",
                backup_path=backup_path,
            )

        # Validate unless skipped
        validation_passed = True
        if not request.skip_validation:
            validation_result = self.validate(request.file_path)
            validation_passed = validation_result.valid

            if not validation_passed and backup_path:
                try:
                    restore_file(backup_path)
                    return XMLEditResult(
                        success=False,
                        file_path=request.file_path,
                        error="Validation failed, changes rolled back",
                        backup_path=backup_path,
                        validation_passed=False,
                        rollback_applied=True,
                    )
                except Exception as e:
                    return XMLEditResult(
                        success=False,
                        file_path=request.file_path,
                        error=f"Validation failed and rollback failed: {e}",
                        backup_path=backup_path,
                        validation_passed=False,
                    )

        # Generate diffs
        diff = unified_diff(original_content, proposed_content, request.file_path)
        diff_col = colored_diff(original_content, proposed_content, request.file_path)

        return XMLEditResult(
            success=True,
            file_path=request.file_path,
            diff=diff,
            diff_colored=diff_col,
            backup_path=backup_path,
            validation_passed=validation_passed,
            changes=tuple(changes),
            completed_at=datetime.utcnow(),
        )
