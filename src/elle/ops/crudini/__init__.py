"""crudini INI file editor wrapper.

Provides a Python interface to the crudini command-line tool for
INI/CFG file querying, editing, and validation.

Public API:
    Availability:
        - is_available(): Check if crudini is installed
        - get_version(): Get crudini version string
        - get_status(): Get detailed availability status

    Engine:
        - CrudiniEngine: Main wrapper class for crudini operations
            - get(): Get parameter value
            - get_section(): Get all parameters in a section
            - list_sections(): List all sections
            - list_parameters(): List parameters in a section
            - get_file_content(): Parse entire INI file
            - set(): Set parameter value
            - set_section(): Set multiple parameters
            - delete(): Delete parameter or section
            - merge(): Merge content into file
            - validate(): Validate INI file
            - preview_edit(): Preview file edit
            - execute_edit(): Execute file edit

    Models:
        - CrudiniOperation: Single operation specification
        - CrudiniChange: Record of a change made
        - CrudiniRequest: Request for single operation
        - CrudiniResult: Result of operation
        - CrudiniEditRequest: Request for file editing
        - CrudiniEditResult: Result of file edit
        - CrudiniEditPreview: Preview of proposed changes
        - INISection: Represents a section
        - INIFileContent: Complete file content
        - CrudiniStatus: Tool availability status

    Exceptions:
        - CrudiniError: Base exception
        - CrudiniUnavailableError: crudini not installed
        - CrudiniSectionError: Section operation error
        - CrudiniParameterError: Parameter operation error
        - CrudiniParseError: INI parse error

Usage:
    from elle.ops.crudini import is_available, CrudiniEngine

    if is_available():
        engine = CrudiniEngine()

        # Get a value
        value = engine.get("/etc/myapp.conf", "database", "host")

        # Set a value
        result = engine.set("/etc/myapp.conf", "database", "port", "5432")

        # List sections
        sections = engine.list_sections("/etc/myapp.conf")

        # Edit a file
        from elle.ops.crudini import CrudiniEditRequest, CrudiniOperation
        request = CrudiniEditRequest(
            file_path="/etc/myapp.conf",
            operations=(
                CrudiniOperation(kind="set", section="database", parameter="host", value="localhost"),
                CrudiniOperation(kind="set", section="database", parameter="port", value="5432"),
            ),
            description="Configure database settings",
        )
        result = engine.execute_edit(request)
"""

# Engine
from elle.ops.crudini.engine import (
    CrudiniEngine,
    get_status,
    get_version,
    is_available,
)

# Models
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

__all__ = [
    # Availability
    "is_available",
    "get_version",
    "get_status",
    # Engine
    "CrudiniEngine",
    # Models
    "CrudiniChange",
    "CrudiniEditPreview",
    "CrudiniEditRequest",
    "CrudiniEditResult",
    "CrudiniOperation",
    "CrudiniRequest",
    "CrudiniResult",
    "INIFileContent",
    "INISection",
    "CrudiniStatus",
    # Exceptions
    "CrudiniError",
    "CrudiniParameterError",
    "CrudiniParseError",
    "CrudiniSectionError",
    "CrudiniUnavailableError",
]
