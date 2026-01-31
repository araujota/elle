"""PostgreSQL persistence for auto-generated capabilities.

Stores:
- Generated capability specifications
- Input/output model code
- Capability class code
- Validation results and approval status
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    StoredCapability,
    TrustLevel,
)
from elle.common.pydantic_compat import safe_model_dump_json
from elle.storage.engine import get_conn
from elle.storage.migrate import register_migration

logger = logging.getLogger(__name__)

# PostgreSQL schema name
PG_SCHEMA = "autogen"


# =============================================================================
# Schema DDL
# =============================================================================

GENERATED_CAPABILITIES_TABLE = """
CREATE TABLE IF NOT EXISTS generated_capabilities (
    id TEXT PRIMARY KEY,
    capability_name TEXT UNIQUE NOT NULL,
    spec_json JSONB NOT NULL,
    input_model_code TEXT NOT NULL,
    output_model_code TEXT NOT NULL,
    capability_class_code TEXT NOT NULL,
    source_command TEXT NOT NULL,
    man_page_hash TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    trust_level TEXT DEFAULT 'third_party',
    approved BOOLEAN DEFAULT FALSE,
    enabled BOOLEAN DEFAULT TRUE,
    validation_json JSONB,
    source_package TEXT,
    package_version TEXT,
    binary_path TEXT
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_capability_name ON generated_capabilities(capability_name)",
    "CREATE INDEX IF NOT EXISTS idx_source_command ON generated_capabilities(source_command)",
    "CREATE INDEX IF NOT EXISTS idx_trust_level ON generated_capabilities(trust_level)",
    "CREATE INDEX IF NOT EXISTS idx_approved ON generated_capabilities(approved)",
    "CREATE INDEX IF NOT EXISTS idx_enabled ON generated_capabilities(enabled)",
    "CREATE INDEX IF NOT EXISTS idx_source_package ON generated_capabilities(source_package)",
]


# =============================================================================
# Migration
# =============================================================================


def _migrate_to_v1(conn: psycopg.Connection) -> None:
    """Create the initial autogen schema."""
    conn.execute(GENERATED_CAPABILITIES_TABLE)
    for idx in INDEXES:
        conn.execute(idx)


register_migration(PG_SCHEMA, 1, _migrate_to_v1)


# =============================================================================
# Store Class
# =============================================================================


class AutogenStore:
    """PostgreSQL store for auto-generated capabilities."""

    def save(
        self,
        spec: GeneratedCapabilitySpec,
        input_model_code: str,
        output_model_code: str,
        capability_class_code: str,
        man_page_text: str,
        trust_level: TrustLevel = TrustLevel.THIRD_PARTY,
        validation_json: str | None = None,
        source_package: str | None = None,
        package_version: str | None = None,
        binary_path: str | None = None,
    ) -> str:
        """Save a generated capability.

        Args:
            spec: Generated capability spec.
            input_model_code: Python code for input model.
            output_model_code: Python code for output model.
            capability_class_code: Python code for capability class.
            man_page_text: Raw man page text (for hash).
            trust_level: Assigned trust level.
            validation_json: Optional validation results JSON.
            source_package: dpkg package name providing the binary.
            package_version: Package version at generation time.
            binary_path: Full path to source binary.

        Returns:
            Capability ID.
        """
        cap_id = str(uuid.uuid4())
        man_page_hash = hashlib.sha256(man_page_text.encode()).hexdigest()[:16]
        spec_json = safe_model_dump_json(spec)
        now = datetime.now(timezone.utc)

        with get_conn(schema=PG_SCHEMA) as conn:
            # Upsert: insert or update on capability_name conflict
            row = conn.execute(
                """
                INSERT INTO generated_capabilities (
                    id, capability_name, spec_json, input_model_code,
                    output_model_code, capability_class_code, source_command,
                    man_page_hash, generated_at, trust_level, validation_json,
                    source_package, package_version, binary_path
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (capability_name) DO UPDATE SET
                    spec_json = EXCLUDED.spec_json,
                    input_model_code = EXCLUDED.input_model_code,
                    output_model_code = EXCLUDED.output_model_code,
                    capability_class_code = EXCLUDED.capability_class_code,
                    man_page_hash = EXCLUDED.man_page_hash,
                    generated_at = EXCLUDED.generated_at,
                    trust_level = EXCLUDED.trust_level,
                    validation_json = EXCLUDED.validation_json,
                    source_package = EXCLUDED.source_package,
                    package_version = EXCLUDED.package_version,
                    binary_path = EXCLUDED.binary_path
                RETURNING id
                """,
                (
                    cap_id,
                    spec.name,
                    spec_json,
                    input_model_code,
                    output_model_code,
                    capability_class_code,
                    spec.source_command,
                    man_page_hash,
                    now,
                    trust_level.value,
                    validation_json,
                    source_package,
                    package_version,
                    binary_path,
                ),
            ).fetchone()

            if row:
                cap_id = row["id"]

        return cap_id

    def get(self, capability_name: str) -> StoredCapability | None:
        """Get a stored capability by name.

        Args:
            capability_name: Capability name.

        Returns:
            StoredCapability or None.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            row = conn.execute(
                "SELECT * FROM generated_capabilities WHERE capability_name = %s",
                (capability_name,),
            ).fetchone()

            if not row:
                return None
            return self._row_to_stored(row)

    def get_by_id(self, cap_id: str) -> StoredCapability | None:
        """Get a stored capability by ID.

        Args:
            cap_id: Capability ID.

        Returns:
            StoredCapability or None.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            row = conn.execute(
                "SELECT * FROM generated_capabilities WHERE id = %s",
                (cap_id,),
            ).fetchone()

            if not row:
                return None
            return self._row_to_stored(row)

    def list_all(self) -> list[StoredCapability]:
        """List all stored capabilities."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute("SELECT * FROM generated_capabilities ORDER BY capability_name").fetchall()
            return [self._row_to_stored(row) for row in rows]

    def list_approved_enabled(self) -> list[StoredCapability]:
        """List all approved and enabled capabilities."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE approved = TRUE AND enabled = TRUE
                ORDER BY capability_name
                """
            ).fetchall()
            return [self._row_to_stored(row) for row in rows]

    def list_pending_approval(self) -> list[StoredCapability]:
        """List capabilities pending approval."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE approved = FALSE AND trust_level = 'third_party'
                ORDER BY generated_at DESC
                """
            ).fetchall()
            return [self._row_to_stored(row) for row in rows]

    def list_core(self) -> list[StoredCapability]:
        """List all core capabilities."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE trust_level = 'core'
                ORDER BY capability_name
                """
            ).fetchall()
            return [self._row_to_stored(row) for row in rows]

    def list_by_trust_level(self, trust_level: TrustLevel) -> list[StoredCapability]:
        """List capabilities by trust level."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE trust_level = %s
                ORDER BY capability_name
                """,
                (trust_level.value,),
            ).fetchall()
            return [self._row_to_stored(row) for row in rows]

    def list_by_command(self, command: str) -> list[StoredCapability]:
        """List capabilities for a source command."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE source_command = %s
                ORDER BY capability_name
                """,
                (command,),
            ).fetchall()
            return [self._row_to_stored(row) for row in rows]

    def list_by_package(self, package_name: str) -> list[StoredCapability]:
        """List capabilities derived from a package."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE source_package = %s
                ORDER BY capability_name
                """,
                (package_name,),
            ).fetchall()
            return [self._row_to_stored(row) for row in rows]

    def list_packages_with_capabilities(self) -> list[tuple[str, str | None]]:
        """List all packages that have generated capabilities."""
        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT source_package, package_version
                FROM generated_capabilities
                WHERE source_package IS NOT NULL
                ORDER BY source_package
                """
            ).fetchall()
            return [(row["source_package"], row["package_version"]) for row in rows]

    def approve(self, capability_name: str) -> bool:
        """Approve a capability for use."""
        with get_conn(schema=PG_SCHEMA) as conn:
            result = conn.execute(
                "UPDATE generated_capabilities SET approved = TRUE WHERE capability_name = %s",
                (capability_name,),
            )
            return result.rowcount > 0

    def disable(self, capability_name: str) -> bool:
        """Disable a capability."""
        with get_conn(schema=PG_SCHEMA) as conn:
            result = conn.execute(
                "UPDATE generated_capabilities SET enabled = FALSE WHERE capability_name = %s",
                (capability_name,),
            )
            return result.rowcount > 0

    def enable(self, capability_name: str) -> bool:
        """Enable a capability."""
        with get_conn(schema=PG_SCHEMA) as conn:
            result = conn.execute(
                "UPDATE generated_capabilities SET enabled = TRUE WHERE capability_name = %s",
                (capability_name,),
            )
            return result.rowcount > 0

    def delete(self, capability_name: str) -> bool:
        """Delete a capability."""
        with get_conn(schema=PG_SCHEMA) as conn:
            result = conn.execute(
                "DELETE FROM generated_capabilities WHERE capability_name = %s",
                (capability_name,),
            )
            return result.rowcount > 0

    def check_needs_regeneration(
        self,
        capability_name: str,
        new_man_page_text: str,
    ) -> bool:
        """Check if a capability needs regeneration due to man page changes."""
        stored = self.get(capability_name)
        if not stored:
            return True
        new_hash = hashlib.sha256(new_man_page_text.encode()).hexdigest()[:16]
        return stored.man_page_hash != new_hash

    def _row_to_stored(self, row: dict[str, Any]) -> StoredCapability:
        """Convert database row to StoredCapability."""
        generated_at = row["generated_at"]
        if isinstance(generated_at, str):
            generated_at = datetime.fromisoformat(generated_at)

        return StoredCapability(
            id=row["id"],
            capability_name=row["capability_name"],
            spec_json=row["spec_json"] if isinstance(row["spec_json"], str) else str(row["spec_json"]),
            input_model_code=row["input_model_code"],
            output_model_code=row["output_model_code"],
            capability_class_code=row["capability_class_code"],
            source_command=row["source_command"],
            man_page_hash=row["man_page_hash"],
            generated_at=generated_at,
            trust_level=TrustLevel(row["trust_level"]),
            approved=bool(row["approved"]),
            enabled=bool(row["enabled"]),
            source_package=row.get("source_package"),
            package_version=row.get("package_version"),
            binary_path=row.get("binary_path"),
        )


# =============================================================================
# Module-level store instance
# =============================================================================

_store: AutogenStore | None = None


def get_store() -> AutogenStore:
    """Get the shared store instance.

    Returns:
        AutogenStore instance.
    """
    global _store
    if _store is None:
        _store = AutogenStore()
    return _store


def reset_store() -> None:
    """Reset the shared store (for testing)."""
    global _store
    _store = None
