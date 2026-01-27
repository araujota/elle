"""Efficacy tracking for machine-specific success learning.

Provides aggregation logic and incremental updates for tracking
what actually works on THIS machine.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from elle.daemon.incidents.efficacy import (
    DECAY_HALF_LIFE_DAYS,
    MIN_SAMPLES_FOR_CONFIDENCE,
    PRIOR_SUCCESS_RATE,
    DomainEfficacy,
    EfficacyContext,
    EfficacyStats,
    EntityEfficacy,
    SolutionApproachEfficacy,
)
from elle.daemon.incidents.models import (
    IncidentReport,
)
from elle.daemon.incidents.schema import ensure_schema, get_connection


@contextmanager
def _ensure_connection(
    conn: sqlite3.Connection | None = None,
) -> Iterator[sqlite3.Connection]:
    """Context manager that ensures a valid connection with schema."""
    own_conn = conn is None
    if own_conn:
        actual_conn = get_connection()
        ensure_schema(actual_conn)
    else:
        # conn is guaranteed non-None here due to the if check above
        actual_conn = conn  # type: ignore[assignment]
    try:
        yield actual_conn
    finally:
        if own_conn:
            actual_conn.close()


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format string."""
    return dt.isoformat()


def _parse_datetime(s: str) -> datetime:
    """Parse ISO format string to datetime."""
    return datetime.fromisoformat(s)


def _json_dumps(obj: Any) -> str:
    """Serialize object to JSON string."""
    import json

    return json.dumps(obj, default=str)


def _json_loads(s: str | None) -> Any:
    """Parse JSON string."""
    import json

    if not s:
        return []
    return json.loads(s)


# =============================================================================
# Core tracking functions
# =============================================================================


def record_outcome(
    incident: IncidentReport,
    outcome: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Record an incident outcome for efficacy tracking.

    Called from finalize_outcome() to update efficacy statistics.
    Updates domain, entity, and solution approach efficacy.

    Args:
        incident: The incident being finalized.
        outcome: The outcome (improved, partial, no_change, worse).
        conn: SQLite connection.
    """
    with _ensure_connection(conn) as c:
        now = datetime.utcnow()

        # Update domain efficacy
        _update_domain_efficacy(incident.domain, outcome, now, c)

        # Update entity efficacy for each involved entity
        for entity in incident.fingerprint.entities:
            _update_entity_efficacy(entity, outcome, now, c)

        # Update solution approach efficacy
        approach_signature = _compute_approach_signature(incident)
        if approach_signature:
            _update_approach_efficacy(
                approach_signature=approach_signature,
                domain=incident.domain,
                precondition_summary=_summarize_preconditions(incident),
                key_commands=_extract_key_commands(incident),
                outcome=outcome,
                time_to_resolve=incident.time_to_resolve_sec,
                now=now,
                conn=c,
            )

        # Commit if we created our own connection
        if conn is None:
            c.commit()


def _update_domain_efficacy(
    domain: str,
    outcome: str,
    now: datetime,
    conn: sqlite3.Connection,
) -> None:
    """Update domain efficacy statistics."""
    cursor = conn.cursor()

    # Get existing record
    cursor.execute(
        "SELECT * FROM domain_efficacy WHERE domain = ?",
        (domain,),
    )
    row = cursor.fetchone()

    if row:
        # Update existing
        total = row["total_incidents"] + 1
        improved = row["improved_count"] + (1 if outcome == "improved" else 0)
        partial = row["partial_count"] + (1 if outcome == "partial" else 0)
        no_change = row["no_change_count"] + (1 if outcome == "no_change" else 0)
        worse = row["worse_count"] + (1 if outcome == "worse" else 0)
        last_updated = _parse_datetime(row["last_updated"])
    else:
        # New record
        total = 1
        improved = 1 if outcome == "improved" else 0
        partial = 1 if outcome == "partial" else 0
        no_change = 1 if outcome == "no_change" else 0
        worse = 1 if outcome == "worse" else 0
        last_updated = now

    # Compute decayed success rate
    success_rate = _compute_decayed_rate(improved, partial, no_change, worse, total, last_updated, now)

    cursor.execute(
        """
        INSERT OR REPLACE INTO domain_efficacy (
            domain, total_incidents, improved_count, partial_count,
            no_change_count, worse_count, success_rate, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            domain,
            total,
            improved,
            partial,
            no_change,
            worse,
            success_rate,
            _serialize_datetime(now),
        ),
    )


def _update_entity_efficacy(
    entity: str,
    outcome: str,
    now: datetime,
    conn: sqlite3.Connection,
) -> None:
    """Update entity efficacy statistics."""
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM entity_efficacy WHERE entity = ?",
        (entity,),
    )
    row = cursor.fetchone()

    if row:
        total = row["total_incidents"] + 1
        improved = row["improved_count"] + (1 if outcome == "improved" else 0)
        partial = row["partial_count"] + (1 if outcome == "partial" else 0)
        no_change = row["no_change_count"] + (1 if outcome == "no_change" else 0)
        worse = row["worse_count"] + (1 if outcome == "worse" else 0)
        last_updated = _parse_datetime(row["last_updated"])
    else:
        total = 1
        improved = 1 if outcome == "improved" else 0
        partial = 1 if outcome == "partial" else 0
        no_change = 1 if outcome == "no_change" else 0
        worse = 1 if outcome == "worse" else 0
        last_updated = now

    success_rate = _compute_decayed_rate(improved, partial, no_change, worse, total, last_updated, now)

    cursor.execute(
        """
        INSERT OR REPLACE INTO entity_efficacy (
            entity, total_incidents, improved_count, partial_count,
            no_change_count, worse_count, success_rate, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity,
            total,
            improved,
            partial,
            no_change,
            worse,
            success_rate,
            _serialize_datetime(now),
        ),
    )


def _update_approach_efficacy(
    approach_signature: str,
    domain: str,
    precondition_summary: str,
    key_commands: tuple[str, ...],
    outcome: str,
    time_to_resolve: int | None,
    now: datetime,
    conn: sqlite3.Connection,
) -> None:
    """Update solution approach efficacy statistics."""
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM solution_approach_efficacy WHERE approach_signature = ?",
        (approach_signature,),
    )
    row = cursor.fetchone()

    avg_time: int | None
    if row:
        total = row["total_uses"] + 1
        improved = row["improved_count"] + (1 if outcome == "improved" else 0)
        partial = row["partial_count"] + (1 if outcome == "partial" else 0)
        no_change = row["no_change_count"] + (1 if outcome == "no_change" else 0)
        worse = row["worse_count"] + (1 if outcome == "worse" else 0)
        last_used = _parse_datetime(row["last_used"])

        # Update average time to resolve
        old_avg = row["avg_time_to_resolve_sec"]
        if time_to_resolve is not None:
            if old_avg is not None:
                # Incremental average
                avg_time = int((old_avg * (total - 1) + time_to_resolve) / total)
            else:
                avg_time = time_to_resolve
        else:
            avg_time = old_avg
    else:
        total = 1
        improved = 1 if outcome == "improved" else 0
        partial = 1 if outcome == "partial" else 0
        no_change = 1 if outcome == "no_change" else 0
        worse = 1 if outcome == "worse" else 0
        last_used = now
        avg_time = time_to_resolve

    success_rate = _compute_decayed_rate(improved, partial, no_change, worse, total, last_used, now)

    cursor.execute(
        """
        INSERT OR REPLACE INTO solution_approach_efficacy (
            approach_signature, domain, precondition_summary, key_commands_json,
            total_uses, improved_count, partial_count, no_change_count, worse_count,
            success_rate, avg_time_to_resolve_sec, last_used
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            approach_signature,
            domain,
            precondition_summary,
            _json_dumps(list(key_commands)),
            total,
            improved,
            partial,
            no_change,
            worse,
            success_rate,
            avg_time,
            _serialize_datetime(now),
        ),
    )


def _compute_decayed_rate(
    improved: int,
    partial: int,
    no_change: int,
    worse: int,
    total: int,
    last_updated: datetime,
    now: datetime,
) -> float:
    """Compute time-decayed success rate.

    Uses exponential decay to weight recent outcomes more heavily.
    Regresses toward a prior of 0.5 based on sample age.

    Args:
        improved: Count of improved outcomes.
        partial: Count of partial outcomes.
        no_change: Count of no_change outcomes.
        worse: Count of worse outcomes.
        total: Total incident count.
        last_updated: When the record was last updated.
        now: Current time.

    Returns:
        Decayed success rate between 0.0 and 1.0.
    """
    if total == 0:
        return float(PRIOR_SUCCESS_RATE)

    # Weighted sum of outcomes
    # improved = 1.0, partial = 0.6, no_change = 0.0, worse = -0.5
    weighted_sum = improved * 1.0 + partial * 0.6 + no_change * 0.0 + worse * -0.5

    # Raw rate: normalize to 0-1 range
    # Max positive = total * 1.0, min = total * -0.5
    # We want to map this to 0-1
    raw_rate = (weighted_sum / total + 0.5) / 1.5
    raw_rate = max(0.0, min(1.0, raw_rate))

    # Decay toward prior based on age
    days_old = (now - last_updated).total_seconds() / 86400
    decay_factor = 0.5 ** (days_old / DECAY_HALF_LIFE_DAYS)

    # Blend with prior based on sample size confidence
    confidence = min(1.0, total / MIN_SAMPLES_FOR_CONFIDENCE)

    # Final rate combines:
    # - Data-driven rate (weighted by confidence and recency)
    # - Prior (weighted by lack of confidence and age)
    effective_confidence = confidence * decay_factor
    return float(effective_confidence * raw_rate + (1 - effective_confidence) * PRIOR_SUCCESS_RATE)


def _compute_approach_signature(incident: IncidentReport) -> str | None:
    """Compute a signature for the solution approach.

    Combines domain, precondition patterns, and key commands
    into a stable hash for grouping similar approaches.
    """
    # Get key commands from decision or actions
    key_commands = _extract_key_commands(incident)
    if not key_commands:
        return None

    # Build signature components
    components = [
        incident.domain,
        _summarize_preconditions(incident),
        "|".join(sorted(key_commands)),
    ]

    signature_str = "::".join(components)
    return hashlib.sha256(signature_str.encode()).hexdigest()[:16]


def _summarize_preconditions(incident: IncidentReport) -> str:
    """Summarize preconditions into a stable string."""
    if not incident.preconditions:
        return ""

    # Extract key precondition expressions
    expressions = [p.expression for p in incident.preconditions if p.required]
    return "|".join(sorted(expressions))


def _extract_key_commands(incident: IncidentReport) -> tuple[str, ...]:
    """Extract key commands from an incident.

    Normalizes commands to remove variable parts (paths, names)
    for better grouping.
    """
    commands = []

    # From decision (planned commands)
    if incident.decision:
        planned = incident.decision.get("commands", [])
        if isinstance(planned, list):
            commands.extend(planned)

    # From decision record (if available)
    if incident.decision_record:
        commands.extend(incident.decision_record.planned_commands)

    # Normalize commands
    normalized = []
    for cmd in commands:
        norm = _normalize_command(cmd)
        if norm:
            normalized.append(norm)

    return tuple(sorted(set(normalized)))


def _normalize_command(cmd: str) -> str | None:
    """Normalize a command for signature computation.

    Extracts the base command and key flags, removing
    variable arguments like paths and service names.
    """
    if not cmd:
        return None

    parts = cmd.strip().split()
    if not parts:
        return None

    # Get base command
    base = parts[0]

    # Known command patterns to normalize
    if base in ("systemctl", "service"):
        # Keep systemctl restart but not the service name
        if len(parts) >= 2:
            return f"{base} {parts[1]}"
    elif base == "apt" or base == "apt-get":
        # Keep apt install but not package names
        if len(parts) >= 2:
            return f"apt {parts[1]}"
    elif base == "rm":
        # Keep rm flags but not paths
        flags = [p for p in parts[1:] if p.startswith("-")]
        return f"rm {' '.join(flags)}" if flags else "rm"
    elif base == "chmod" or base == "chown":
        return base
    elif base == "docker" and len(parts) >= 2:
        return f"docker {parts[1]}"

    # Default: just the base command
    return base


# =============================================================================
# Retrieval functions
# =============================================================================


def get_domain_efficacy(
    domain: str,
    conn: sqlite3.Connection | None = None,
) -> DomainEfficacy | None:
    """Get efficacy statistics for a domain.

    Args:
        domain: The incident domain.
        conn: SQLite connection.

    Returns:
        DomainEfficacy if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            "SELECT * FROM domain_efficacy WHERE domain = ?",
            (domain,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return DomainEfficacy(
            domain=row["domain"],
            total_incidents=row["total_incidents"],
            improved_count=row["improved_count"],
            partial_count=row["partial_count"],
            no_change_count=row["no_change_count"],
            worse_count=row["worse_count"],
            success_rate=row["success_rate"],
            last_updated=_parse_datetime(row["last_updated"]),
        )


def get_entity_efficacy(
    entity: str,
    conn: sqlite3.Connection | None = None,
) -> EntityEfficacy | None:
    """Get efficacy statistics for an entity.

    Args:
        entity: The entity identifier.
        conn: SQLite connection.

    Returns:
        EntityEfficacy if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            "SELECT * FROM entity_efficacy WHERE entity = ?",
            (entity,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return EntityEfficacy(
            entity=row["entity"],
            total_incidents=row["total_incidents"],
            improved_count=row["improved_count"],
            partial_count=row["partial_count"],
            no_change_count=row["no_change_count"],
            worse_count=row["worse_count"],
            success_rate=row["success_rate"],
            last_updated=_parse_datetime(row["last_updated"]),
        )


def get_approach_efficacy(
    approach_signature: str,
    conn: sqlite3.Connection | None = None,
) -> SolutionApproachEfficacy | None:
    """Get efficacy statistics for a solution approach.

    Args:
        approach_signature: The approach signature hash.
        conn: SQLite connection.

    Returns:
        SolutionApproachEfficacy if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            "SELECT * FROM solution_approach_efficacy WHERE approach_signature = ?",
            (approach_signature,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return SolutionApproachEfficacy(
            approach_signature=row["approach_signature"],
            domain=row["domain"],
            precondition_summary=row["precondition_summary"] or "",
            key_commands=tuple(_json_loads(row["key_commands_json"])),
            total_uses=row["total_uses"],
            improved_count=row["improved_count"],
            partial_count=row["partial_count"],
            no_change_count=row["no_change_count"],
            worse_count=row["worse_count"],
            success_rate=row["success_rate"],
            avg_time_to_resolve_sec=row["avg_time_to_resolve_sec"],
            last_used=_parse_datetime(row["last_used"]),
        )


def get_efficacy_context(
    domain: str,
    entities: tuple[str, ...] | None = None,
    incident: IncidentReport | None = None,
    conn: sqlite3.Connection | None = None,
) -> EfficacyContext:
    """Get efficacy context for dynamic weight computation.

    Gathers all relevant efficacy data for computing dynamic
    outcome weights during retrieval.

    Args:
        domain: The incident domain.
        entities: Involved entity identifiers.
        incident: The candidate incident (for approach matching).
        conn: SQLite connection.

    Returns:
        EfficacyContext with available efficacy data.
    """
    with _ensure_connection(conn) as c:
        # Domain efficacy
        domain_eff = get_domain_efficacy(domain, c)
        domain_success_rate = domain_eff.success_rate if domain_eff else None
        domain_sample_size = domain_eff.total_incidents if domain_eff else 0

        # Entity efficacy
        entity_success_rates = {}
        if entities:
            for entity in entities:
                ent_eff = get_entity_efficacy(entity, c)
                if ent_eff:
                    entity_success_rates[entity] = ent_eff.success_rate

        # Approach efficacy
        approach_success_rate = None
        approach_sample_size = 0
        if incident:
            sig = _compute_approach_signature(incident)
            if sig:
                app_eff = get_approach_efficacy(sig, c)
                if app_eff:
                    approach_success_rate = app_eff.success_rate
                    approach_sample_size = app_eff.total_uses

        return EfficacyContext(
            domain_success_rate=domain_success_rate,
            domain_sample_size=domain_sample_size,
            entity_success_rates=entity_success_rates,
            approach_success_rate=approach_success_rate,
            approach_sample_size=approach_sample_size,
        )


def get_all_domain_efficacy(
    conn: sqlite3.Connection | None = None,
) -> list[DomainEfficacy]:
    """Get efficacy statistics for all domains.

    Args:
        conn: SQLite connection.

    Returns:
        List of DomainEfficacy records.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute("SELECT * FROM domain_efficacy ORDER BY total_incidents DESC")

        results = []
        for row in cursor.fetchall():
            results.append(
                DomainEfficacy(
                    domain=row["domain"],
                    total_incidents=row["total_incidents"],
                    improved_count=row["improved_count"],
                    partial_count=row["partial_count"],
                    no_change_count=row["no_change_count"],
                    worse_count=row["worse_count"],
                    success_rate=row["success_rate"],
                    last_updated=_parse_datetime(row["last_updated"]),
                )
            )

        return results


def get_efficacy_stats(
    conn: sqlite3.Connection | None = None,
) -> EfficacyStats:
    """Get aggregated efficacy statistics.

    Args:
        conn: SQLite connection.

    Returns:
        EfficacyStats summary.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()

        # Overall stats
        cursor.execute("SELECT SUM(total_incidents) as total FROM domain_efficacy")
        row = cursor.fetchone()
        total_finalized: int = row["total"] if row and row["total"] else 0

        # Overall success rate (weighted average)
        cursor.execute(
            """
            SELECT SUM(success_rate * total_incidents) / SUM(total_incidents) as avg
            FROM domain_efficacy
            WHERE total_incidents > 0
            """
        )
        row = cursor.fetchone()
        overall_rate: float = row["avg"] if row and row["avg"] else 0.5

        # Domain stats
        domain_stats = tuple(get_all_domain_efficacy(c))

        # Top entities
        cursor.execute(
            """
            SELECT * FROM entity_efficacy
            ORDER BY total_incidents DESC
            LIMIT 10
            """
        )
        top_entities = []
        for row in cursor.fetchall():
            top_entities.append(
                EntityEfficacy(
                    entity=row["entity"],
                    total_incidents=row["total_incidents"],
                    improved_count=row["improved_count"],
                    partial_count=row["partial_count"],
                    no_change_count=row["no_change_count"],
                    worse_count=row["worse_count"],
                    success_rate=row["success_rate"],
                    last_updated=_parse_datetime(row["last_updated"]),
                )
            )

        # Top approaches (by success rate with min 3 uses)
        cursor.execute(
            """
            SELECT * FROM solution_approach_efficacy
            WHERE total_uses >= 3
            ORDER BY success_rate DESC
            LIMIT 10
            """
        )
        top_approaches = []
        for row in cursor.fetchall():
            top_approaches.append(
                SolutionApproachEfficacy(
                    approach_signature=row["approach_signature"],
                    domain=row["domain"],
                    precondition_summary=row["precondition_summary"] or "",
                    key_commands=tuple(_json_loads(row["key_commands_json"])),
                    total_uses=row["total_uses"],
                    improved_count=row["improved_count"],
                    partial_count=row["partial_count"],
                    no_change_count=row["no_change_count"],
                    worse_count=row["worse_count"],
                    success_rate=row["success_rate"],
                    avg_time_to_resolve_sec=row["avg_time_to_resolve_sec"],
                    last_used=_parse_datetime(row["last_used"]),
                )
            )

        return EfficacyStats(
            total_finalized_incidents=total_finalized,
            overall_success_rate=overall_rate,
            domain_stats=domain_stats,
            top_entities=tuple(top_entities),
            top_approaches=tuple(top_approaches),
        )


# =============================================================================
# Bootstrap migration
# =============================================================================


def bootstrap_from_historical(
    conn: sqlite3.Connection | None = None,
) -> int:
    """Bootstrap efficacy data from historical incidents.

    Processes all finalized incidents to populate efficacy tables.
    Safe to run multiple times (idempotent).

    Args:
        conn: SQLite connection.

    Returns:
        Number of incidents processed.
    """
    from elle.daemon.incidents.store import list_incidents

    with _ensure_connection(conn) as c:
        # Get all resolved/mitigated incidents
        incidents = list_incidents(status="resolved", limit=10000, conn=c)
        incidents.extend(list_incidents(status="mitigated", limit=10000, conn=c))

        processed = 0
        for incident in incidents:
            if incident.outcome in ("improved", "partial", "no_change", "worse"):
                record_outcome(incident, incident.outcome, conn=c)
                processed += 1

        # Commit if we created our own connection
        if conn is None:
            c.commit()

        return processed
