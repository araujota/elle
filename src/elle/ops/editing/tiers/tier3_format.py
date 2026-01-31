"""Tier 3: Format Editors (INI/XML).

Uses crudini for INI files and xmlstarlet for XML files.
Falls back to Python configparser and xml.etree when tools unavailable.
"""

from __future__ import annotations

import configparser
import io
import logging
from pathlib import Path

from elle.ops.editing.models import (
    EditOperation,
    FileEditPreview,
    TierApplicability,
    TierEditResult,
    ValidationResult,
)
from elle.ops.editing.tier_registry import (
    TIER3_INI_PATTERNS,
    TIER3_XML_PATTERNS,
    matches_pattern,
)
from elle.ops.editing.tiers.base import BaseTier

logger = logging.getLogger(__name__)


class Tier3Format(BaseTier):
    """Tier 3: Format-specific editors.

    Handles INI/CFG files using crudini (or Python configparser)
    and XML files using xmlstarlet (or Python xml.etree).
    """

    tier_number = 3
    tier_name = "Format Editors"

    def _detect_format(self, file_path: str) -> str | None:
        """Detect the file format."""
        if matches_pattern(file_path, TIER3_INI_PATTERNS):
            return "ini"
        if matches_pattern(file_path, TIER3_XML_PATTERNS):
            return "xml"
        return None

    def _is_crudini_available(self) -> bool:
        """Check if crudini is available."""
        try:
            from elle.ops.crudini import is_available

            return is_available()
        except ImportError:
            return False

    def _is_xmlstarlet_available(self) -> bool:
        """Check if xmlstarlet is available."""
        try:
            from elle.ops.xmlstarlet import is_available

            return is_available()
        except ImportError:
            return False

    def can_handle(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> TierApplicability:
        """Check if this tier can handle the file."""
        fmt = self._detect_format(str(file_path))

        if not fmt:
            return TierApplicability(
                tier=3,
                applicable=False,
                reason="File is not INI/CFG or XML",
            )

        if fmt == "ini":
            if self._is_crudini_available():
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="INI file - using crudini",
                    confidence=0.9,
                )
            else:
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="INI file - using Python configparser",
                    confidence=0.8,
                )

        if fmt == "xml":
            if self._is_xmlstarlet_available():
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="XML file - using xmlstarlet",
                    confidence=0.9,
                )
            else:
                return TierApplicability(
                    tier=3,
                    applicable=True,
                    reason="XML file - using Python xml.etree",
                    confidence=0.75,
                )

        return TierApplicability(
            tier=3,
            applicable=False,
            reason="Unknown format",
        )

    def validate_syntax(
        self,
        content: str,
        file_path: Path,
    ) -> ValidationResult:
        """Validate format syntax."""
        fmt = self._detect_format(str(file_path))

        if fmt == "ini":
            try:
                parser = configparser.ConfigParser()
                parser.read_string(content)
                return ValidationResult(passed=True, method="configparser")
            except configparser.Error as e:
                return ValidationResult(
                    passed=False,
                    method="configparser",
                    errors=(str(e),),
                )

        if fmt == "xml":
            try:
                import xml.etree.ElementTree as ET  # nosec B405

                ET.fromstring(content)  # nosec B314
                return ValidationResult(passed=True, method="xml.etree")
            except ET.ParseError as e:
                return ValidationResult(
                    passed=False,
                    method="xml.etree",
                    errors=(str(e),),
                )

        return ValidationResult(
            passed=True,
            method="none",
        )

    def preview(
        self,
        file_path: Path,
        operation: EditOperation,
    ) -> FileEditPreview:
        """Preview format-specific edit."""
        from elle.ops.augeas.diff import colored_diff, unified_diff

        fmt = self._detect_format(str(file_path))
        original = file_path.read_text() if file_path.exists() else ""

        try:
            if fmt == "ini":
                proposed = self._apply_ini_edit(original, operation)
                tool = "crudini" if self._is_crudini_available() else "configparser"
            elif fmt == "xml":
                proposed = self._apply_xml_edit(original, operation)
                tool = "xmlstarlet" if self._is_xmlstarlet_available() else "xml.etree"
            else:
                proposed = original
                tool = "none"

            diff = unified_diff(original, proposed, str(file_path))
            diff_col = colored_diff(original, proposed, str(file_path))

            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=3,
                tier_name="Format Editors",
                original_content=original,
                proposed_content=proposed,
                diff=diff,
                diff_colored=diff_col,
                is_valid=True,
                tool_to_use=tool,
            )
        except Exception:
            return FileEditPreview(
                file_path=str(file_path),
                tier_selected=3,
                tier_name="Format Editors",
                original_content=original,
                proposed_content="",
                diff="",
                diff_colored="",
                is_valid=False,
                tool_to_use=fmt,
            )

    async def apply(
        self,
        file_path: Path,
        operation: EditOperation,
        *,
        skip_backup: bool = False,
        skip_validation: bool = False,
        dropin_name: str | None = None,
        dropin_priority: int = 99,
    ) -> TierEditResult:
        """Apply format-specific edit."""
        from elle.ops.augeas.backup import backup_file
        from elle.ops.augeas.diff import colored_diff, unified_diff

        fmt = self._detect_format(str(file_path))

        if not fmt:
            return self._create_failure_result(
                str(file_path),
                "Unknown format",
            )

        if not file_path.exists():
            return self._create_failure_result(
                str(file_path),
                "File not found",
            )

        original = file_path.read_text()

        # Create backup
        backup_path = None
        if not skip_backup:
            try:
                record = backup_file(str(file_path), domain=fmt)
                backup_path = record.backup_path
            except Exception as e:
                return self._create_failure_result(
                    str(file_path),
                    f"Backup failed: {e}",
                )

        # Apply edit
        try:
            if fmt == "ini":
                proposed = self._apply_ini_edit(original, operation)
                tool = "crudini" if self._is_crudini_available() else "configparser"
            elif fmt == "xml":
                proposed = self._apply_xml_edit(original, operation)
                tool = "xmlstarlet" if self._is_xmlstarlet_available() else "xml.etree"
            else:
                return self._create_failure_result(
                    str(file_path),
                    "Unknown format",
                    backup_path=backup_path,
                )
        except Exception as e:
            return self._create_failure_result(
                str(file_path),
                f"Edit failed: {e}",
                backup_path=backup_path,
            )

        # Validate
        validation = None
        if not skip_validation:
            validation = self.validate_syntax(proposed, file_path)
            if not validation.passed:
                return self._create_failure_result(
                    str(file_path),
                    f"Validation failed: {'; '.join(validation.errors)}",
                    backup_path=backup_path,
                )

        # Write
        try:
            file_path.write_text(proposed)
        except PermissionError:
            return self._create_failure_result(
                str(file_path),
                "Permission denied",
                backup_path=backup_path,
                requires_privilege=True,
            )

        # Generate diff
        diff = unified_diff(original, proposed, str(file_path))
        diff_col = colored_diff(original, proposed, str(file_path))

        return self._create_success_result(
            str(file_path),
            diff,
            diff_col,
            backup_path=backup_path,
            validation=validation,
            tool_used=tool,
        )

    def _apply_ini_edit(self, content: str, operation: EditOperation) -> str:
        """Apply edit to INI content."""
        parser = configparser.ConfigParser()
        parser.read_string(content)

        section = operation.section or "DEFAULT"
        key = operation.key or (operation.path.split(".")[-1] if operation.path else None)

        if operation.kind == "set" and key and operation.value is not None:
            if not parser.has_section(section) and section != "DEFAULT":
                parser.add_section(section)
            parser.set(section, key, operation.value)

        elif operation.kind == "rm":
            if key:
                if parser.has_option(section, key):
                    parser.remove_option(section, key)
            else:
                if parser.has_section(section):
                    parser.remove_section(section)

        # Write to string
        output = io.StringIO()
        parser.write(output)
        return output.getvalue()

    def _apply_xml_edit(self, content: str, operation: EditOperation) -> str:
        """Apply edit to XML content."""
        if self._is_xmlstarlet_available():
            from elle.ops.xmlstarlet import XMLStarletEngine

            engine = XMLStarletEngine()

            if operation.kind == "set" and operation.path and operation.value is not None:
                result = engine.edit(content, operation.path, operation.value)
                if result.success:
                    return result.output
                raise ValueError(result.error)
            elif operation.kind == "rm" and operation.path:
                result = engine.delete(content, operation.path)
                if result.success:
                    return result.output
                raise ValueError(result.error)
            else:
                return content
        else:
            # Python fallback
            import xml.etree.ElementTree as ET  # nosec B405

            root = ET.fromstring(content)  # nosec B314

            if operation.kind == "set" and operation.path and operation.value is not None:
                # Simple XPath support
                elements = root.findall(operation.path)
                for elem in elements:
                    elem.text = operation.value

            elif operation.kind == "rm" and operation.path:
                # Find and remove elements
                for elem in root.findall(operation.path):
                    parent = root.find(f".//{operation.path}/..")
                    if parent is not None:
                        parent.remove(elem)

            # Write with declaration
            return ET.tostring(root, encoding="unicode", xml_declaration=True)
