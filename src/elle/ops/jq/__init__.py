"""jq JSON processor wrapper.

Provides a Python interface to the jq command-line tool for
JSON querying, transformation, and validation.

Public API:
    Availability:
        - is_available(): Check if jq is installed
        - get_version(): Get jq version string
        - get_status(): Get detailed availability status

    Engine:
        - JQEngine: Main wrapper class for jq operations
            - query(): Query JSON with a filter
            - query_file(): Query a JSON file
            - transform(): Transform JSON with a filter
            - validate(): Validate JSON syntax
            - validate_file(): Validate a JSON file
            - format(): Pretty-print JSON
            - get_value(): Get value at path
            - set_value(): Set value at path
            - delete_value(): Delete value at path
            - has_path(): Check if path exists
            - preview_edit(): Preview file edit
            - execute_edit(): Execute file edit

    Models:
        - JQOperation: Single jq operation specification
        - JQChange: Record of a change made
        - JQRequest: Request to process JSON
        - JQResult: Result of a jq operation
        - JQEditRequest: Request to edit a JSON file
        - JQEditResult: Result of file edit
        - JQEditPreview: Preview of proposed changes
        - JQStatus: Tool availability status

    Exceptions:
        - JQError: Base exception
        - JQUnavailableError: jq not installed
        - JQFilterError: Invalid filter expression
        - JQParseError: Invalid JSON input

Usage:
    from elle.ops.jq import is_available, JQEngine

    if is_available():
        engine = JQEngine()

        # Query JSON
        result = engine.query('{"name": "test", "value": 42}', '.name')
        print(result.output)  # "test"

        # Transform JSON
        result = engine.transform('{"a": 1}', '.b = 2')
        print(result.output)  # {"a": 1, "b": 2}

        # Edit a file
        from elle.ops.jq import JQEditRequest
        request = JQEditRequest(
            file_path="/etc/docker/daemon.json",
            filter='.["log-driver"] = "json-file"',
            description="Set Docker log driver",
        )
        result = engine.execute_edit(request)
"""

# Engine
from elle.ops.jq.engine import (
    JQEngine,
    get_status,
    get_version,
    is_available,
)

# Models
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

__all__ = [
    # Availability
    "is_available",
    "get_version",
    "get_status",
    # Engine
    "JQEngine",
    # Models
    "JQChange",
    "JQEditPreview",
    "JQEditRequest",
    "JQEditResult",
    "JQOperation",
    "JQRequest",
    "JQResult",
    "JQStatus",
    # Exceptions
    "JQError",
    "JQFilterError",
    "JQParseError",
    "JQUnavailableError",
]
