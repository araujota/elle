#!/usr/bin/env python3
"""One-time migration script: SQLite databases → PostgreSQL.

Reads data from all ELLE SQLite databases and inserts it into
the PostgreSQL database.  Safe to run multiple times (uses
ON CONFLICT DO NOTHING for idempotency).

Usage:
    python scripts/migrate_sqlite_to_pg.py \
        --pg-url "postgresql://elle@/elle" \
        --elle-dir /var/lib/elle

The PostgreSQL database must already be provisioned (schemas,
extensions, tables) via ``elle setup database`` before running
this script.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_sqlite(path: Path) -> sqlite3.Connection | None:
    """Open a SQLite database if it exists."""
    if not path.exists():
        logger.warning("SQLite database not found: %s", path)
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _bool(val: Any) -> bool:
    """Convert SQLite 0/1 to Python bool."""
    if val is None:
        return False
    return bool(val)


def _ts(val: str | None) -> datetime | None:
    """Parse ISO 8601 string to timezone-aware datetime."""
    if not val:
        return None
    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _json_or_none(val: str | None) -> Any:
    """Parse JSON string or return None."""
    if not val:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def _unpack_embedding(blob: bytes, dim: int) -> list[float]:
    """Unpack a struct-packed embedding blob to a list of floats."""
    return list(struct.unpack(f"{dim}f", blob))


# ---------------------------------------------------------------------------
# Per-database migration functions
# ---------------------------------------------------------------------------


def migrate_telemetry(sqlite_path: Path, pg_conn: psycopg.Connection) -> int:  # type: ignore[type-arg]
    """Migrate telemetry events from elle.db."""
    conn = _open_sqlite(sqlite_path)
    if not conn:
        return 0

    pg_conn.execute("SET search_path TO telemetry, public")
    count = 0

    # Events
    cursor = conn.execute("SELECT * FROM events")
    for row in cursor:
        pg_conn.execute(
            """
            INSERT INTO events (event_id, ts, source, severity, category, message,
                                entity, fingerprint, raw, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (
                row["event_id"],
                _ts(row["ts"]),
                row["source"],
                row["severity"],
                row["category"],
                row["message"],
                row.get("entity"),
                row.get("fingerprint"),
                row["raw"],
                _ts(row.get("created_at")),
            ),
        )
        count += 1

    # Probe results
    cursor = conn.execute("SELECT * FROM probe_results")
    for row in cursor:
        pg_conn.execute(
            """
            INSERT INTO probe_results (probe_name, ts, success, data, error, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                row["probe_name"],
                _ts(row["ts"]),
                _bool(row["success"]),
                row["data"],
                row.get("error"),
                _ts(row.get("created_at")),
            ),
        )
        count += 1

    # Forecast model cache
    try:
        cursor = conn.execute("SELECT * FROM forecast_model_cache")
        for row in cursor:
            pg_conn.execute(
                """
                INSERT INTO forecast_model_cache (metric, method, parameters, mape, cached_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (metric) DO NOTHING
                """,
                (row["metric"], row["method"], row.get("parameters"), row.get("mape"), _ts(row["cached_at"])),
            )
            count += 1
    except sqlite3.OperationalError:
        pass

    pg_conn.commit()
    conn.close()
    logger.info("Migrated %d telemetry rows", count)
    return count


def migrate_incidents(sqlite_path: Path, pg_conn: psycopg.Connection) -> int:  # type: ignore[type-arg]
    """Migrate incidents from incidents.db."""
    conn = _open_sqlite(sqlite_path)
    if not conn:
        return 0

    pg_conn.execute("SET search_path TO incidents, public")
    count = 0

    # Core incidents
    cursor = conn.execute("SELECT * FROM incidents")
    for row in cursor:
        row_dict = dict(row)
        pg_conn.execute(
            """
            INSERT INTO incidents (
                id, created_at, updated_at, domain, severity, status,
                title, summary, symptoms_json, suspected_causes_json, root_cause,
                event_ids_json, log_snippets_json, metrics_json,
                decision_json, preconditions_json,
                outcome, verification_steps_json,
                time_to_mitigate_sec, time_to_resolve_sec,
                fingerprint_json, tags_json, confidence,
                trigger_source, trigger_command,
                forecast_metric, forecast_urgency,
                prepared_plan_json, plan_executed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s
            )
            ON CONFLICT (id) DO NOTHING
            """,
            (
                row["id"],
                _ts(row["created_at"]),
                _ts(row["updated_at"]),
                row["domain"],
                row["severity"],
                row["status"],
                row["title"],
                row["summary"],
                _json_or_none(row["symptoms_json"]),
                _json_or_none(row["suspected_causes_json"]),
                row.get("root_cause"),
                _json_or_none(row["event_ids_json"]),
                _json_or_none(row["log_snippets_json"]),
                _json_or_none(row["metrics_json"]),
                _json_or_none(row["decision_json"]),
                _json_or_none(row["preconditions_json"]),
                row["outcome"],
                _json_or_none(row["verification_steps_json"]),
                row.get("time_to_mitigate_sec"),
                row.get("time_to_resolve_sec"),
                _json_or_none(row["fingerprint_json"]),
                _json_or_none(row["tags_json"]),
                row["confidence"],
                row["trigger_source"],
                row.get("trigger_command"),
                row_dict.get("forecast_metric"),
                row_dict.get("forecast_urgency"),
                _json_or_none(row_dict.get("prepared_plan_json")),
                _ts(row_dict.get("plan_executed_at")),
            ),
        )
        count += 1

    # Incident actions
    try:
        cursor = conn.execute("SELECT * FROM incident_actions")
        for row in cursor:
            pg_conn.execute(
                """
                INSERT INTO incident_actions (
                    incident_id, step_index, kind, command, payload_json,
                    exit_code, stdout, stderr, success, privileged,
                    created_at, duration_ms
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (incident_id, step_index) DO NOTHING
                """,
                (
                    row["incident_id"],
                    row["step_index"],
                    row["kind"],
                    row.get("command"),
                    _json_or_none(row["payload_json"]),
                    row.get("exit_code"),
                    row.get("stdout"),
                    row.get("stderr"),
                    _bool(row["success"]),
                    _bool(row["privileged"]),
                    _ts(row["created_at"]),
                    row.get("duration_ms"),
                ),
            )
            count += 1
    except sqlite3.OperationalError:
        pass

    # Embeddings
    try:
        cursor = conn.execute("SELECT * FROM incident_embeddings")
        for row in cursor:
            dim = row["dim"]
            embedding = _unpack_embedding(row["embedding"], dim)
            pg_conn.execute(
                """
                INSERT INTO incident_embeddings (incident_id, embedding, model, created_at)
                VALUES (%s, %s::vector, %s, %s)
                ON CONFLICT (incident_id) DO NOTHING
                """,
                (row["incident_id"], embedding, row["model"], _ts(row["created_at"])),
            )
            count += 1
    except sqlite3.OperationalError:
        pass

    pg_conn.commit()
    conn.close()
    logger.info("Migrated %d incident rows", count)
    return count


def migrate_manvault(sqlite_path: Path, pg_conn: psycopg.Connection) -> int:  # type: ignore[type-arg]
    """Migrate Man Vault from manvault.db."""
    conn = _open_sqlite(sqlite_path)
    if not conn:
        return 0

    pg_conn.execute("SET search_path TO manvault, public")
    count = 0

    # Docs (need to track old_id -> new_id mapping for FK references)
    doc_id_map: dict[int, int] = {}
    cursor = conn.execute("SELECT * FROM docs")
    for row in cursor:
        result = pg_conn.execute(
            """
            INSERT INTO docs (name, section, lang, source_path, sha256, text,
                              updated_at, package_hint, priority)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name, section, lang) DO UPDATE SET sha256 = EXCLUDED.sha256
            RETURNING id
            """,
            (
                row["name"],
                row["section"],
                row["lang"],
                row["source_path"],
                row["sha256"],
                row["text"],
                _ts(row["updated_at"]),
                row.get("package_hint"),
                row.get("priority", 0),
            ),
        ).fetchone()
        if result:
            doc_id_map[row["id"]] = result["id"]
        count += 1

    # Chunks
    chunk_id_map: dict[int, int] = {}
    cursor = conn.execute("SELECT * FROM chunks")
    for row in cursor:
        new_doc_id = doc_id_map.get(row["doc_id"])
        if new_doc_id is None:
            continue
        result = pg_conn.execute(
            """
            INSERT INTO chunks (doc_id, chunk_index, text, heading, start_line, end_line)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (doc_id, chunk_index) DO NOTHING
            RETURNING id
            """,
            (
                new_doc_id,
                row["chunk_index"],
                row["text"],
                row.get("heading"),
                row.get("start_line"),
                row.get("end_line"),
            ),
        ).fetchone()
        if result:
            chunk_id_map[row["id"]] = result["id"]
        count += 1

    # Embeddings
    cursor = conn.execute("SELECT * FROM embeddings")
    for row in cursor:
        new_chunk_id = chunk_id_map.get(row["chunk_id"])
        if new_chunk_id is None:
            continue
        dim = row["dim"]
        embedding = _unpack_embedding(row["embedding"], dim)
        pg_conn.execute(
            """
            INSERT INTO embeddings (chunk_id, embedding, model, created_at)
            VALUES (%s, %s::vector, %s, %s)
            ON CONFLICT (chunk_id) DO NOTHING
            """,
            (new_chunk_id, embedding, row["model"], _ts(row["created_at"])),
        )
        count += 1

    pg_conn.commit()
    conn.close()
    logger.info("Migrated %d manvault rows", count)
    return count


def migrate_simple_db(
    sqlite_path: Path,
    pg_conn: psycopg.Connection,  # type: ignore[type-arg]
    pg_schema: str,
    tables: list[str],
    bool_columns: set[str] | None = None,
    json_columns: set[str] | None = None,
    ts_columns: set[str] | None = None,
    skip_columns: set[str] | None = None,
) -> int:
    """Generic migration for simple databases.

    Reads all rows from each table and inserts them into PostgreSQL.
    """
    conn = _open_sqlite(sqlite_path)
    if not conn:
        return 0

    bool_cols = bool_columns or set()
    json_cols = json_columns or set()
    ts_cols = ts_columns or set()
    skip_cols = skip_columns or {"rowid"}
    pg_conn.execute("SET search_path TO %s, public", (pg_schema,))
    count = 0

    for table in tables:
        try:
            cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
        except sqlite3.OperationalError:
            logger.warning("Table %s not found in %s, skipping", table, sqlite_path)
            continue

        rows = cursor.fetchall()
        if not rows:
            continue

        # Get column names from first row
        columns = [desc[0] for desc in cursor.description if desc[0] not in skip_cols]

        for row in rows:
            values = []
            for col in columns:
                val = row[col]
                if col in bool_cols:
                    val = _bool(val)
                elif col in json_cols and isinstance(val, str):
                    val = _json_or_none(val)
                elif col in ts_cols and isinstance(val, str):
                    val = _ts(val)
                values.append(val)

            placeholders = ", ".join(["%s"] * len(columns))
            col_names = ", ".join(columns)
            pg_conn.execute(
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",  # noqa: S608
                values,
            )
            count += 1

    pg_conn.commit()
    conn.close()
    logger.info("Migrated %d rows from %s → %s", count, sqlite_path.name, pg_schema)
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate ELLE SQLite databases to PostgreSQL")
    parser.add_argument("--pg-url", required=True, help="PostgreSQL connection string")
    parser.add_argument("--elle-dir", default="/var/lib/elle", help="ELLE data directory")
    parser.add_argument("--dry-run", action="store_true", help="Read but don't write")
    args = parser.parse_args()

    elle_dir = Path(args.elle_dir)
    total = 0

    with psycopg.connect(args.pg_url, row_factory=dict_row) as pg_conn:
        # 1. Telemetry (elle.db)
        total += migrate_telemetry(elle_dir / "elle.db", pg_conn)

        # 2. Incidents (incidents.db)
        total += migrate_incidents(elle_dir / "incidents.db", pg_conn)

        # 3. Man Vault (manvault.db)
        total += migrate_manvault(elle_dir / "manvault.db", pg_conn)

        # 4. Reactive (reactive.db)
        total += migrate_simple_db(
            elle_dir / "reactive.db",
            pg_conn,
            "reactive",
            ["reactive_functions", "execution_history", "function_state"],
            bool_columns={"enabled", "condition_result", "success"},
            json_columns={
                "trigger_json", "condition_json", "actions_json", "policy_json",
                "state_json", "tags_json", "trigger_event_json",
                "actions_executed_json", "actions_results_json", "value_json",
            },
            ts_columns={"created_at", "updated_at", "triggered_at"},
        )

        # 5. Reboot (reboot.db)
        total += migrate_simple_db(
            elle_dir / "reboot.db",
            pg_conn,
            "reboot",
            ["reboot_intents", "pending_verifications"],
            bool_columns={"required", "passed"},
            json_columns={
                "session_history_json", "plan_json", "pre_snapshot_json",
                "post_snapshot_json", "grub_state_json",
            },
            ts_columns={
                "created_at", "updated_at", "last_verification_at", "executed_at",
            },
        )

        # 6. Autogen (autogen.db)
        total += migrate_simple_db(
            elle_dir / "autogen.db",
            pg_conn,
            "autogen",
            ["generated_capabilities"],
            bool_columns={"approved", "enabled"},
            json_columns={"spec_json", "validation_json"},
            ts_columns={"generated_at"},
        )

        # 7. Mobile (mobile.db)
        total += migrate_simple_db(
            elle_dir / "mobile.db",
            pg_conn,
            "mobile",
            ["devices", "elevations", "pairing_tokens"],
            bool_columns={"used"},
            ts_columns={
                "paired_at", "last_seen_at", "created_at",
                "expires_at",
            },
        )

        # 8. Docker (docker_env.db - if exists)
        docker_db = elle_dir / "docker_env.db"
        if docker_db.exists():
            total += migrate_simple_db(
                docker_db,
                pg_conn,
                "docker",
                ["docker_env_values"],
                bool_columns={"is_sensitive"},
                ts_columns={"updated_at"},
            )

        # 9. API keys (api_keys.db)
        api_db = elle_dir / "api_keys.db"
        if api_db.exists():
            total += migrate_simple_db(
                api_db,
                pg_conn,
                "api",
                ["api_keys"],
                bool_columns={"revoked"},
                ts_columns={"created_at", "last_used_at"},
            )

        # 10. Dependencies (dependencies.db)
        total += migrate_simple_db(
            elle_dir / "dependencies.db",
            pg_conn,
            "deps",
            ["dependency_preferences", "installation_history"],
            bool_columns={"success"},
            json_columns={"packages_json"},
            ts_columns={"created_at", "updated_at", "installed_at"},
        )

        if args.dry_run:
            pg_conn.rollback()
            logger.info("DRY RUN: Would have migrated %d total rows", total)
        else:
            logger.info("Migration complete: %d total rows migrated", total)


if __name__ == "__main__":
    main()
