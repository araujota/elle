"""XMLStarlet XML processor wrapper.

Provides a Python interface to the xmlstarlet command-line tool for
XML querying, editing, validation, and formatting.

Public API:
    Availability:
        - is_available(): Check if xmlstarlet is installed
        - get_version(): Get xmlstarlet version string
        - get_status(): Get detailed availability status

    Engine:
        - XMLStarletEngine: Main wrapper class for xmlstarlet operations
            - select(): Query XML with XPath
            - select_file(): Query XML file with XPath
            - select_nodes(): Query and return list of matches
            - edit(): Update XML element/attribute values
            - insert(): Insert new nodes
            - delete(): Delete nodes
            - rename(): Rename nodes
            - validate(): Validate XML file
            - validate_content(): Validate XML string
            - format(): Pretty-print XML
            - format_file(): Pretty-print XML file
            - preview_edit(): Preview file edit
            - execute_edit(): Execute file edit

    Models:
        - XMLOperation: Single operation specification
        - XMLEditOperation: Edit operation for file editing
        - XMLChange: Record of a change made
        - XMLRequest: Request for processing
        - XMLResult: Result of operation
        - XMLEditRequest: Request for file editing
        - XMLEditResult: Result of file edit
        - XMLEditPreview: Preview of proposed changes
        - XMLValidationResult: Validation outcome
        - XMLStarletStatus: Tool availability status

    Exceptions:
        - XMLStarletError: Base exception
        - XMLStarletUnavailableError: xmlstarlet not installed
        - XMLXPathError: Invalid XPath expression
        - XMLParseError: XML parse error
        - XMLValidationError: Validation failure

Usage:
    from elle.ops.xmlstarlet import is_available, XMLStarletEngine

    if is_available():
        engine = XMLStarletEngine()

        # Query XML with XPath
        result = engine.select('<root><item>value</item></root>', '//item/text()')
        print(result.output)  # value

        # Edit XML
        result = engine.edit(
            '<root><item>old</item></root>',
            '//item',
            'new',
        )
        print(result.output)  # <root><item>new</item></root>

        # Validate XML
        result = engine.validate('/path/to/file.xml')
        print(f"Valid: {result.valid}")

        # Format XML
        result = engine.format('<root><item>value</item></root>')
        print(result.output)  # Pretty-printed XML
"""

# Engine
from elle.ops.xmlstarlet.engine import (
    XMLStarletEngine,
    get_status,
    get_version,
    is_available,
)

# Models
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

__all__ = [
    # Availability
    "is_available",
    "get_version",
    "get_status",
    # Engine
    "XMLStarletEngine",
    # Models
    "XMLChange",
    "XMLEditOperation",
    "XMLEditPreview",
    "XMLEditRequest",
    "XMLEditResult",
    "XMLOperation",
    "XMLRequest",
    "XMLResult",
    "XMLStarletStatus",
    "XMLValidationResult",
    # Exceptions
    "XMLParseError",
    "XMLStarletError",
    "XMLStarletUnavailableError",
    "XMLValidationError",
    "XMLXPathError",
]
