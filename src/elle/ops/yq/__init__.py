"""yq YAML processor wrapper.

Provides a Python interface to the yq command-line tool for
YAML querying, transformation, and validation.

Supports both yq variants:
- kislyuk/yq (Python, jq wrapper): Uses jq syntax, requires jq installed
- mikefarah/yq (Go): Native Go implementation with extended syntax

Public API:
    Availability:
        - is_available(): Check if yq is installed
        - get_version(): Get yq version string
        - get_status(): Get detailed availability status

    Engine:
        - YQEngine: Main wrapper class for yq operations
            - query(): Query YAML with a filter
            - query_file(): Query a YAML file
            - transform(): Transform YAML with a filter
            - validate(): Validate YAML syntax
            - validate_file(): Validate a YAML file
            - get_value(): Get value at path
            - set_value(): Set value at path
            - delete_value(): Delete value at path
            - preview_edit(): Preview file edit
            - execute_edit(): Execute file edit

    Models:
        - YQOperation: Single yq operation specification
        - YQChange: Record of a change made
        - YQRequest: Request to process YAML
        - YQResult: Result of a yq operation
        - YQEditRequest: Request to edit a YAML file
        - YQEditResult: Result of file edit
        - YQEditPreview: Preview of proposed changes
        - YQStatus: Tool availability status

    Exceptions:
        - YQError: Base exception
        - YQUnavailableError: yq not installed
        - YQFilterError: Invalid filter expression
        - YQParseError: Invalid YAML input

Usage:
    from elle.ops.yq import is_available, YQEngine

    if is_available():
        engine = YQEngine()

        # Query YAML
        result = engine.query('name: test\\nvalue: 42', '.name')
        print(result.output)  # test

        # Transform YAML
        result = engine.transform('a: 1', '.b = 2')
        print(result.output)  # a: 1\\nb: 2

        # Edit a file
        from elle.ops.yq import YQEditRequest
        request = YQEditRequest(
            file_path="/etc/netplan/01-netcfg.yaml",
            filter='.network.ethernets.eth0.dhcp4 = true',
            description="Enable DHCP on eth0",
        )
        result = engine.execute_edit(request)
"""

# Engine
from elle.ops.yq.engine import (
    YQEngine,
    get_status,
    get_version,
    is_available,
)

# Models
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

__all__ = [
    # Availability
    "is_available",
    "get_version",
    "get_status",
    # Engine
    "YQEngine",
    # Models
    "YQChange",
    "YQEditPreview",
    "YQEditRequest",
    "YQEditResult",
    "YQOperation",
    "YQRequest",
    "YQResult",
    "YQStatus",
    # Exceptions
    "YQError",
    "YQFilterError",
    "YQParseError",
    "YQUnavailableError",
]
