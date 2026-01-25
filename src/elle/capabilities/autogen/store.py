"""SQLite persistence for auto-generated capabilities.

Stores:
- Generated capability specifications
- Input/output model code
- Capability class code
- Validation results and approval status
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    StoredCapability,
    TrustLevel,
)

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path("/var/lib/elle/autogen.db")


# =============================================================================
# Schema
# =============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS generated_capabilities (
    id TEXT PRIMARY KEY,
    capability_name TEXT UNIQUE NOT NULL,
    spec_json TEXT NOT NULL,
    input_model_code TEXT NOT NULL,
    output_model_code TEXT NOT NULL,
    capability_class_code TEXT NOT NULL,
    source_command TEXT NOT NULL,
    man_page_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    trust_level TEXT DEFAULT 'third_party',
    approved INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    validation_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_capability_name ON generated_capabilities(capability_name);
CREATE INDEX IF NOT EXISTS idx_source_command ON generated_capabilities(source_command);
CREATE INDEX IF NOT EXISTS idx_trust_level ON generated_capabilities(trust_level);
CREATE INDEX IF NOT EXISTS idx_approved ON generated_capabilities(approved);
CREATE INDEX IF NOT EXISTS idx_enabled ON generated_capabilities(enabled);
"""


# =============================================================================
# Store Class
# =============================================================================


class AutogenStore:
    """SQLite store for auto-generated capabilities."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialize the store.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
        try:
            # Create parent directory if needed
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            with sqlite3.connect(self.db_path) as conn:
                conn.executescript(SCHEMA)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize autogen store: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(
        self,
        spec: GeneratedCapabilitySpec,
        input_model_code: str,
        output_model_code: str,
        capability_class_code: str,
        man_page_text: str,
        trust_level: TrustLevel = TrustLevel.THIRD_PARTY,
        validation_json: str | None = None,
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

        Returns:
            Capability ID.
        """
        cap_id = str(uuid.uuid4())
        man_page_hash = hashlib.sha256(man_page_text.encode()).hexdigest()[:16]
        spec_json = spec.model_dump_json()

        with self._get_connection() as conn:
            # Check for existing capability
            cursor = conn.execute(
                "SELECT id FROM generated_capabilities WHERE capability_name = ?",
                (spec.name,),
            )
            existing = cursor.fetchone()

            if existing:
                # Update existing
                conn.execute(
                    """
                    UPDATE generated_capabilities SET
                        spec_json = ?,
                        input_model_code = ?,
                        output_model_code = ?,
                        capability_class_code = ?,
                        man_page_hash = ?,
                        generated_at = ?,
                        trust_level = ?,
                        validation_json = ?
                    WHERE capability_name = ?
                    """,
                    (
                        spec_json,
                        input_model_code,
                        output_model_code,
                        capability_class_code,
                        man_page_hash,
                        datetime.utcnow().isoformat(),
                        trust_level.value,
                        validation_json,
                        spec.name,
                    ),
                )
                cap_id = existing["id"]
            else:
                # Insert new
                conn.execute(
                    """
                    INSERT INTO generated_capabilities (
                        id, capability_name, spec_json, input_model_code,
                        output_model_code, capability_class_code, source_command,
                        man_page_hash, generated_at, trust_level, validation_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        datetime.utcnow().isoformat(),
                        trust_level.value,
                        validation_json,
                    ),
                )

            conn.commit()

        return cap_id

    def get(self, capability_name: str) -> StoredCapability | None:
        """Get a stored capability by name.

        Args:
            capability_name: Capability name.

        Returns:
            StoredCapability or None.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM generated_capabilities WHERE capability_name = ?",
                (capability_name,),
            )
            row = cursor.fetchone()

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
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM generated_capabilities WHERE id = ?",
                (cap_id,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            return self._row_to_stored(row)

    def list_all(self) -> list[StoredCapability]:
        """List all stored capabilities.

        Returns:
            List of StoredCapability objects.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM generated_capabilities ORDER BY capability_name"
            )
            return [self._row_to_stored(row) for row in cursor.fetchall()]

    def list_approved_enabled(self) -> list[StoredCapability]:
        """List all approved and enabled capabilities.

        Returns:
            List of StoredCapability objects.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE approved = 1 AND enabled = 1
                ORDER BY capability_name
                """
            )
            return [self._row_to_stored(row) for row in cursor.fetchall()]

    def list_pending_approval(self) -> list[StoredCapability]:
        """List capabilities pending approval.

        Returns:
            List of StoredCapability objects.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE approved = 0 AND trust_level = 'third_party'
                ORDER BY generated_at DESC
                """
            )
            return [self._row_to_stored(row) for row in cursor.fetchall()]

    def list_by_command(self, command: str) -> list[StoredCapability]:
        """List capabilities for a source command.

        Args:
            command: Source command name.

        Returns:
            List of StoredCapability objects.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM generated_capabilities
                WHERE source_command = ?
                ORDER BY capability_name
                """,
                (command,),
            )
            return [self._row_to_stored(row) for row in cursor.fetchall()]

    def approve(self, capability_name: str) -> bool:
        """Approve a capability for use.

        Args:
            capability_name: Capability name.

        Returns:
            True if approved, False if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE generated_capabilities SET approved = 1 WHERE capability_name = ?",
                (capability_name,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def disable(self, capability_name: str) -> bool:
        """Disable a capability.

        Args:
            capability_name: Capability name.

        Returns:
            True if disabled, False if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE generated_capabilities SET enabled = 0 WHERE capability_name = ?",
                (capability_name,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def enable(self, capability_name: str) -> bool:
        """Enable a capability.

        Args:
            capability_name: Capability name.

        Returns:
            True if enabled, False if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE generated_capabilities SET enabled = 1 WHERE capability_name = ?",
                (capability_name,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete(self, capability_name: str) -> bool:
        """Delete a capability.

        Args:
            capability_name: Capability name.

        Returns:
            True if deleted, False if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM generated_capabilities WHERE capability_name = ?",
                (capability_name,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def check_needs_regeneration(
        self,
        capability_name: str,
        new_man_page_text: str,
    ) -> bool:
        """Check if a capability needs regeneration due to man page changes.

        Args:
            capability_name: Capability name.
            new_man_page_text: Current man page text.

        Returns:
            True if regeneration needed.
        """
        stored = self.get(capability_name)
        if not stored:
            return True

        new_hash = hashlib.sha256(new_man_page_text.encode()).hexdigest()[:16]
        return stored.man_page_hash != new_hash

    def _row_to_stored(self, row: sqlite3.Row) -> StoredCapability:
        """Convert database row to StoredCapability.

        Args:
            row: SQLite row.

        Returns:
            StoredCapability object.
        """
        return StoredCapability(
            id=row["id"],
            capability_name=row["capability_name"],
            spec_json=row["spec_json"],
            input_model_code=row["input_model_code"],
            output_model_code=row["output_model_code"],
            capability_class_code=row["capability_class_code"],
            source_command=row["source_command"],
            man_page_hash=row["man_page_hash"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
            trust_level=TrustLevel(row["trust_level"]),
            approved=bool(row["approved"]),
            enabled=bool(row["enabled"]),
        )


# =============================================================================
# Module-level store instance
# =============================================================================

_store: AutogenStore | None = None


def get_store(db_path: Path | str | None = None) -> AutogenStore:
    """Get the shared store instance.

    Args:
        db_path: Optional database path override.

    Returns:
        AutogenStore instance.
    """
    global _store
    if _store is None or (db_path and Path(db_path) != _store.db_path):
        _store = AutogenStore(db_path)
    return _store


def reset_store() -> None:
    """Reset the shared store (for testing)."""
    global _store
    _store = None
