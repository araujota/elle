"""Precondition evaluation for incident matching.

Provides a safe DSL for expressing and evaluating conditions
against system snapshots and fingerprints.
"""

import operator
import re
from typing import Any

from elle.daemon.incidents.models import Fingerprint, Precondition, SystemSnapshot

# Safe operators for comparison
OPERATORS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "in": lambda a, b: a in b if b else False,
    "not in": lambda a, b: a not in b if b else True,
    "contains": lambda a, b: b in a if a else False,
}

# Pattern for parsing expressions
# Examples: disk./.used_pct > 95, services.NetworkManager.active == true
EXPR_PATTERN = re.compile(
    r"^(?P<path>[\w./\-]+)\s*"
    r"(?P<op>==|!=|>=|<=|>|<|in|not in|contains)\s*"
    r"(?P<value>.+)$"
)


class PreconditionError(Exception):
    """Error evaluating a precondition."""

    pass


def evaluate_precondition(
    precondition: Precondition,
    snapshot: SystemSnapshot | None = None,
    fingerprint: Fingerprint | None = None,
) -> bool:
    """Evaluate a single precondition against current state.

    Args:
        precondition: The precondition to evaluate.
        snapshot: Current system snapshot.
        fingerprint: Current fingerprint features.

    Returns:
        True if the precondition matches.
    """
    return evaluate_expression(
        precondition.expression,
        snapshot=snapshot,
        fingerprint=fingerprint,
    )


def evaluate_expression(
    expression: str,
    snapshot: SystemSnapshot | None = None,
    fingerprint: Fingerprint | None = None,
) -> bool:
    """Evaluate a precondition expression.

    Expression format: path operator value
    Examples:
        - disk./.used_pct > 95
        - mem_pressure > 0.9
        - services.NetworkManager.active == true
        - oom_count_1h > 0
        - docker.running > 0

    Args:
        expression: The expression to evaluate.
        snapshot: Current system snapshot.
        fingerprint: Current fingerprint features.

    Returns:
        True if the expression evaluates to True.

    Raises:
        PreconditionError: If the expression is invalid.
    """
    expression = expression.strip()
    if not expression:
        return True

    match = EXPR_PATTERN.match(expression)
    if not match:
        raise PreconditionError(f"Invalid expression format: {expression}")

    path = match.group("path")
    op_str = match.group("op")
    value_str = match.group("value").strip()

    # Get operator
    op_func = OPERATORS.get(op_str)
    if not op_func:
        raise PreconditionError(f"Unknown operator: {op_str}")

    # Resolve the left side (path)
    actual_value = _resolve_path(path, snapshot, fingerprint)

    # Parse the right side (expected value)
    expected_value = _parse_value(value_str)

    # Compare
    try:
        return op_func(actual_value, expected_value)
    except Exception as e:
        raise PreconditionError(f"Error comparing values: {e}") from e


def evaluate_preconditions(
    preconditions: list[Precondition],
    snapshot: SystemSnapshot | None = None,
    fingerprint: Fingerprint | None = None,
) -> tuple[float, list[tuple[Precondition, bool]]]:
    """Evaluate multiple preconditions and compute match ratio.

    Args:
        preconditions: List of preconditions to evaluate.
        snapshot: Current system snapshot.
        fingerprint: Current fingerprint features.

    Returns:
        Tuple of (match_ratio, list of (precondition, matched) pairs).
    """
    if not preconditions:
        return 1.0, []

    results = []
    required_matched = 0
    required_total = 0

    for precond in preconditions:
        try:
            matched = evaluate_precondition(precond, snapshot, fingerprint)
        except PreconditionError:
            matched = False

        results.append((precond, matched))

        if precond.required:
            required_total += 1
            if matched:
                required_matched += 1

    # Compute match ratio based on required preconditions only
    if required_total == 0:
        ratio = 1.0
    else:
        ratio = required_matched / required_total

    return ratio, results


def _resolve_path(
    path: str,
    snapshot: SystemSnapshot | None,
    fingerprint: Fingerprint | None,
) -> Any:
    """Resolve a dotted path to a value.

    Paths can reference:
    - Fingerprint fields: mem_pressure, disk_pressure, oom_count_1h, etc.
    - Snapshot fields: disk./.used_pct, services.NetworkManager.active, etc.
    - Special paths: docker.running, docker.exited

    Args:
        path: Dotted path like "disk./.used_pct" or "mem_pressure".
        snapshot: System snapshot.
        fingerprint: Fingerprint features.

    Returns:
        The resolved value.
    """
    parts = path.split(".")

    # First, check if it's a fingerprint field
    if fingerprint and hasattr(fingerprint, parts[0]):
        if len(parts) == 1:
            return getattr(fingerprint, parts[0])
        # For nested fingerprint access like entities.nginx
        if parts[0] == "entities":
            entities = fingerprint.entities
            if len(parts) > 1:
                return parts[1] in entities
            return entities

    # Check snapshot fields
    if snapshot:
        # Simple snapshot fields
        if len(parts) == 1:
            if hasattr(snapshot, parts[0]):
                return getattr(snapshot, parts[0])

        # Disk info: disk./.used_pct or disk./home.avail_gb
        if parts[0] == "disk" and len(parts) >= 3:
            mount = parts[1]  # e.g., "/" or "/home"
            field = parts[2]  # e.g., "used_pct" or "avail_gb"
            for disk in snapshot.disks:
                if disk.get("mount") == mount:
                    return disk.get(field, 0)
            return 0

        # Service info: services.NetworkManager.active
        if parts[0] == "services" and len(parts) >= 3:
            service_name = parts[1]
            field = parts[2]
            for svc in snapshot.services:
                if svc.get("name") == service_name:
                    return svc.get(field, False)
            return False

        # Network info: net.eth0.state or interfaces.wlan0.rx_err
        if parts[0] in ("net", "interfaces") and len(parts) >= 3:
            iface_name = parts[1]
            field = parts[2]
            for iface in snapshot.interfaces:
                if iface.get("name") == iface_name:
                    return iface.get(field, "UNKNOWN")
            return "UNKNOWN"

        # Docker shortcuts
        if parts[0] == "docker":
            if len(parts) >= 2:
                if parts[1] == "running":
                    return snapshot.docker_running
                elif parts[1] == "exited":
                    return snapshot.docker_exited
            return 0

        # Memory shortcuts
        if parts[0] == "mem" and len(parts) >= 2:
            if parts[1] == "total":
                return snapshot.mem_total_mb
            elif parts[1] == "free":
                return snapshot.mem_free_mb
            elif parts[1] == "available":
                return snapshot.mem_available_mb

        # Swap shortcuts
        if parts[0] == "swap" and len(parts) >= 2:
            if parts[1] == "total":
                return snapshot.swap_total_mb
            elif parts[1] == "used":
                return snapshot.swap_used_mb

        # CPU load: cpu.load_1m, cpu.load_5m, cpu.load_15m
        if parts[0] == "cpu" and len(parts) >= 2:
            if parts[1] == "load_1m":
                return snapshot.cpu_load[0]
            elif parts[1] == "load_5m":
                return snapshot.cpu_load[1]
            elif parts[1] == "load_15m":
                return snapshot.cpu_load[2]

    # Default to None if not found
    return None


def _parse_value(value_str: str) -> Any:
    """Parse a value string to appropriate type.

    Args:
        value_str: String representation of the value.

    Returns:
        Parsed value.
    """
    # Boolean
    if value_str.lower() == "true":
        return True
    if value_str.lower() == "false":
        return False

    # None/null
    if value_str.lower() in ("none", "null"):
        return None

    # Quoted string
    if (value_str.startswith('"') and value_str.endswith('"')) or \
       (value_str.startswith("'") and value_str.endswith("'")):
        return value_str[1:-1]

    # Number
    try:
        if "." in value_str:
            return float(value_str)
        return int(value_str)
    except ValueError:
        pass

    # Default to string
    return value_str


def create_precondition(
    expression: str,
    description: str = "",
    required: bool = True,
) -> Precondition:
    """Create a Precondition from an expression string.

    Validates the expression format.

    Args:
        expression: Condition expression.
        description: Human-readable explanation.
        required: Whether this precondition must match.

    Returns:
        Precondition instance.

    Raises:
        PreconditionError: If the expression is invalid.
    """
    expression = expression.strip()
    if expression:
        match = EXPR_PATTERN.match(expression)
        if not match:
            raise PreconditionError(f"Invalid expression format: {expression}")

        op_str = match.group("op")
        if op_str not in OPERATORS:
            raise PreconditionError(f"Unknown operator: {op_str}")

    return Precondition(
        expression=expression,
        description=description,
        required=required,
    )


def infer_preconditions(
    snapshot: SystemSnapshot,
    fingerprint: Fingerprint,
    domain: str,
) -> list[Precondition]:
    """Infer likely preconditions from current state and domain.

    Generates preconditions that capture the notable aspects
    of the current system state relevant to the incident domain.

    Args:
        snapshot: Current system snapshot.
        fingerprint: Current fingerprint.
        domain: Incident domain.

    Returns:
        List of inferred Preconditions.
    """
    preconditions = []

    # Disk-related
    if domain == "disk" or fingerprint.disk_pressure > 0.8:
        for disk in snapshot.disks:
            mount = disk.get("mount", "")
            pct = disk.get("used_pct", 0)
            if pct > 80:
                preconditions.append(create_precondition(
                    f"disk.{mount}.used_pct > {pct - 10}",
                    f"Disk at {mount} above {pct - 10}% used",
                ))

    # Memory-related
    if domain == "oom" or fingerprint.mem_pressure > 0.8:
        preconditions.append(create_precondition(
            f"mem_pressure > {round(fingerprint.mem_pressure - 0.1, 2)}",
            "High memory pressure",
        ))

    # OOM events
    if fingerprint.oom_count_1h > 0:
        preconditions.append(create_precondition(
            "oom_count_1h > 0",
            "OOM kills observed in last hour",
        ))

    # Network-related
    if domain == "net":
        for iface in snapshot.interfaces:
            name = iface.get("name", "")
            state = iface.get("state", "")
            if state and state != "UP":
                preconditions.append(create_precondition(
                    f"interfaces.{name}.state == {state}",
                    f"Interface {name} in {state} state",
                ))

    # Docker-related
    if domain == "docker":
        if snapshot.docker_exited > 0:
            preconditions.append(create_precondition(
                "docker.exited > 0",
                "Docker containers in exited state",
            ))

    # Service-related
    if domain == "service":
        for svc in snapshot.services:
            if svc.get("failed"):
                name = svc.get("name", "")
                preconditions.append(create_precondition(
                    f"services.{name}.failed == true",
                    f"Service {name} in failed state",
                ))

    return preconditions
