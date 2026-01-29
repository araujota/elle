"""Pydantic models for XMLStarlet XML processing operations.

Defines data structures for XML file manipulation using
the xmlstarlet command-line tool.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Operation Types
# =============================================================================


class XMLOperation(BaseModel):
    """A single XMLStarlet operation to perform on XML data.

    Supports select (query), edit (modify), validate, and format operations.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["select", "edit", "validate", "format"] = Field(
        description="Type of operation: select (query), edit (modify), validate (check), format (pretty-print)"
    )
    xpath: str | None = Field(
        default=None,
        description="XPath expression for select/edit operations",
    )
    value: str | None = Field(
        default=None,
        description="Value for edit operations (update/insert)",
    )
    edit_type: Literal["update", "insert", "append", "delete", "rename", "move"] | None = Field(
        default=None,
        description="Type of edit operation",
    )
    namespace: dict[str, str] | None = Field(
        default=None,
        description="Namespace prefix to URI mappings for XPath",
    )


class XMLChange(BaseModel):
    """Record of an XML change made by an operation.

    Captures what was changed for diff generation and audit trail.
    """

    model_config = ConfigDict(frozen=True)

    xpath: str = Field(description="XPath of the modified element/attribute")
    old_value: str | None = Field(
        default=None,
        description="Previous value (None if newly created)",
    )
    new_value: str | None = Field(
        default=None,
        description="New value (None if deleted)",
    )
    operation: str = Field(
        default="edit",
        description="Description of the operation performed",
    )


# =============================================================================
# Request/Result Models
# =============================================================================


class XMLRequest(BaseModel):
    """Request to process XML data via XMLStarlet.

    Encapsulates all information needed for an XMLStarlet operation
    on XML content or an XML file.
    """

    model_config = ConfigDict(frozen=True)

    # Input source (one of these must be provided)
    file_path: str | None = Field(
        default=None,
        description="Path to XML file to process",
    )
    xml_input: str | None = Field(
        default=None,
        description="XML string to process (alternative to file_path)",
    )

    # Operation
    operation: XMLOperation = Field(
        description="The operation to perform",
    )

    # Metadata
    description: str = Field(
        default="",
        description="Human-readable summary of the operation",
    )


class XMLResult(BaseModel):
    """Result of an XMLStarlet operation.

    Contains the output, any errors, and metadata about the operation.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the operation completed successfully")
    output: str = Field(
        default="",
        description="XMLStarlet output",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    exit_code: int = Field(
        default=0,
        description="xmlstarlet process exit code",
    )
    xpath_used: str | None = Field(
        default=None,
        description="The XPath expression that was used",
    )
    completed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the operation completed",
    )


# =============================================================================
# File Edit Models
# =============================================================================


class XMLEditOperation(BaseModel):
    """A single edit operation for XML file editing."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["update", "insert", "append", "delete", "rename", "move"] = Field(
        description="Type of edit operation",
    )
    xpath: str = Field(
        description="XPath expression targeting elements to modify",
    )
    value: str | None = Field(
        default=None,
        description="Value for update/insert operations",
    )
    node_type: Literal["elem", "attr", "text", "comment", "pi"] | None = Field(
        default=None,
        description="Node type for insert operations",
    )
    position: Literal["before", "after", "into"] | None = Field(
        default=None,
        description="Insert position relative to XPath match",
    )
    name: str | None = Field(
        default=None,
        description="Element/attribute name for insert/rename operations",
    )


class XMLEditRequest(BaseModel):
    """Request to edit an XML file using XMLStarlet.

    Supports multiple edit operations in a single request.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        description="Absolute path to the XML file to edit",
    )
    operations: tuple[XMLEditOperation, ...] = Field(
        default_factory=tuple,
        description="Sequence of edit operations to perform",
    )
    description: str = Field(
        description="Human-readable summary of the changes",
    )
    incident_id: str | None = Field(
        default=None,
        description="Optional incident ID to link this edit to",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, preview changes without applying",
    )
    skip_backup: bool = Field(
        default=False,
        description="If True, skip backup creation (use with caution)",
    )
    skip_validation: bool = Field(
        default=False,
        description="If True, skip validation after apply",
    )
    namespaces: dict[str, str] = Field(
        default_factory=dict,
        description="Namespace prefix to URI mappings for XPath expressions",
    )
    preserve_formatting: bool = Field(
        default=True,
        description="Preserve original formatting where possible",
    )


class XMLEditResult(BaseModel):
    """Result of an XMLStarlet file edit operation.

    Contains the outcome, diff, backup info, and validation results.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the edit completed successfully")
    file_path: str = Field(description="Path to the edited file")
    diff: str = Field(
        default="",
        description="Unified diff showing changes",
    )
    diff_colored: str = Field(
        default="",
        description="ANSI-colored diff for terminal display",
    )
    backup_path: str | None = Field(
        default=None,
        description="Path to backup file (if created)",
    )
    validation_passed: bool = Field(
        default=True,
        description="Whether the output is valid XML",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )
    rollback_applied: bool = Field(
        default=False,
        description="Whether rollback was needed and applied",
    )
    changes: tuple[XMLChange, ...] = Field(
        default_factory=tuple,
        description="List of changes made",
    )
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access was required",
    )
    completed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the operation completed",
    )


# =============================================================================
# Preview Models
# =============================================================================


class XMLEditPreview(BaseModel):
    """Preview of proposed XML changes before applying.

    Generated during dry_run to show what would change.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(description="Path to the file that would be edited")
    original_content: str = Field(description="Current file content")
    proposed_content: str = Field(description="Content after proposed changes")
    diff: str = Field(description="Unified diff of changes")
    diff_colored: str = Field(description="ANSI-colored diff")
    requires_privilege: bool = Field(
        default=False,
        description="Whether privileged access is required",
    )
    is_valid_xml: bool = Field(
        default=True,
        description="Whether the proposed content is valid XML",
    )


# =============================================================================
# Validation Models
# =============================================================================


class XMLValidationResult(BaseModel):
    """Result of XML validation."""

    model_config = ConfigDict(frozen=True)

    valid: bool = Field(description="Whether the XML is valid")
    well_formed: bool = Field(
        default=True,
        description="Whether the XML is well-formed (syntactically correct)",
    )
    schema_valid: bool | None = Field(
        default=None,
        description="Whether the XML validates against a schema (None if no schema)",
    )
    errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of validation errors",
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of validation warnings",
    )


# =============================================================================
# Status Models
# =============================================================================


class XMLStarletStatus(BaseModel):
    """Status of the XMLStarlet tool availability."""

    model_config = ConfigDict(frozen=True)

    available: bool = Field(
        description="Whether xmlstarlet is installed and working",
    )
    version: str | None = Field(
        default=None,
        description="xmlstarlet version string",
    )
    path: str | None = Field(
        default=None,
        description="Path to xmlstarlet executable",
    )


# =============================================================================
# Error Types
# =============================================================================


class XMLStarletError(Exception):
    """Base exception for XMLStarlet operations."""

    pass


class XMLStarletUnavailableError(XMLStarletError):
    """Raised when xmlstarlet is not installed."""

    pass


class XMLXPathError(XMLStarletError):
    """Raised when an XPath expression is invalid or returns no results."""

    def __init__(self, xpath: str, details: str | None = None) -> None:
        self.xpath = xpath
        self.details = details
        msg = f"XPath error for: {xpath}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


class XMLParseError(XMLStarletError):
    """Raised when XML cannot be parsed."""

    def __init__(self, source: str, details: str | None = None) -> None:
        self.source = source
        self.details = details
        msg = f"Failed to parse XML from: {source}"
        if details:
            msg += f" - {details}"
        super().__init__(msg)


class XMLValidationError(XMLStarletError):
    """Raised when XML validation fails."""

    def __init__(self, file_path: str, errors: list[str]) -> None:
        self.file_path = file_path
        self.errors = errors
        msg = f"XML validation failed for {file_path}: {'; '.join(errors[:3])}"
        if len(errors) > 3:
            msg += f" (and {len(errors) - 3} more errors)"
        super().__init__(msg)
