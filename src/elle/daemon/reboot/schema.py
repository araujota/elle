"""Reboot Intent SQLite schema.

Defines the database schema for reboot persistence:
- reboot_intents: Core intent records with state machine
- pending_verifications: Post-boot verification checks

Schema version is tracked in the meta table for migrations.
"""

import sqlite3
from pathlib import Path

# Schema version - increment when schema changes
# v1: Initial schema
# v2: Added extended check types (filesystem_writable, mount_exists, etc.)
SCHEMA_VERSION = 2

# Database location
DB_PATH = Path("/var/lib/elle/reboot.db")

# =============================================================================
# Table Definitions
# =============================================================================

# Core reboot intents table
INTENTS_TABLE = """
CREATE TABLE IF NOT EXISTS reboot_intents (
    id TEXT PRIMARY KEY,
    incident_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    -- Intent buffer (what we're trying to achieve)
    goal TEXT NOT NULL,
    task_description TEXT NOT NULL,

    -- Reboot reason
    reason TEXT NOT NULL,
    reason_detail TEXT,

    -- Session trace
    session_history_json TEXT NOT NULL DEFAULT '[]',
    plan_json TEXT NOT NULL DEFAULT '{}',

    -- State machine
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'rebooting', 'verifying', 'completed', 'failed', 'rolled_back', 'cancelled')),

    -- Pre-reboot state
    pre_snapshot_json TEXT NOT NULL DEFAULT '{}',
    boot_id TEXT,

    -- GRUB configuration
    grub_entry TEXT,
    grub_default_saved TEXT,
    grub_state_json TEXT,

    -- Post-reboot results
    post_snapshot_json TEXT NOT NULL DEFAULT '{}',
    new_boot_id TEXT,
    verification_attempts INTEGER NOT NULL DEFAULT 0,
    last_verification_at TEXT,

    -- Outcome
    outcome TEXT NOT NULL DEFAULT 'unknown'
        CHECK (outcome IN ('improved', 'failed', 'rolled_back', 'unknown')),
    outcome_detail TEXT
)
"""

# Verification checks table
# Extended check types for comprehensive coverage:
#   - command: Run shell command, check exit code
#   - service_active: Check systemd service is running
#   - file_exists: Check file/device exists
#   - port_listening: Check TCP port is listening
#   - filesystem_writable: Check path is writable (detects read-only fs)
#   - mount_exists: Check mountpoint is mounted (detects fstab issues)
#   - kernel_module: Check kernel module is loaded
#   - dmesg_no_errors: Check dmesg for error patterns
#   - journal_no_errors: Check journald for error patterns
#   - disk_space: Check minimum free disk space
#   - network_interface: Check network interface is up
VERIFICATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS pending_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reboot_intent_id TEXT NOT NULL REFERENCES reboot_intents(id) ON DELETE CASCADE,

    -- Check definition
    check_type TEXT NOT NULL
        CHECK (check_type IN (
            'command', 'service_active', 'file_exists', 'port_listening',
            'filesystem_writable', 'mount_exists', 'kernel_module',
            'dmesg_no_errors', 'journal_no_errors', 'disk_space', 'network_interface'
        )),
    check_command TEXT NOT NULL,
    expected_exit_code INTEGER NOT NULL DEFAULT 0,
    expected_output_contains TEXT,

    step_index INTEGER NOT NULL,
    required INTEGER NOT NULL DEFAULT 1,

    -- Results (filled post-boot)
    executed_at TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    passed INTEGER,

    UNIQUE(reboot_intent_id, step_index)
)
"""

# Meta table for tracking state
META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# =============================================================================
# Indexes
# =============================================================================

INDEXES = [
    # Intents indexes
    "CREATE INDEX IF NOT EXISTS idx_intents_status ON reboot_intents(status)",
    "CREATE INDEX IF NOT EXISTS idx_intents_outcome ON reboot_intents(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_intents_created ON reboot_intents(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_intents_updated ON reboot_intents(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_intents_boot_id ON reboot_intents(boot_id)",
    "CREATE INDEX IF NOT EXISTS idx_intents_incident ON reboot_intents(incident_id)",

    # Verifications indexes
    "CREATE INDEX IF NOT EXISTS idx_verifications_intent ON pending_verifications(reboot_intent_id)",
    "CREATE INDEX IF NOT EXISTS idx_verifications_passed ON pending_verifications(passed)",
]

# =============================================================================
# Schema Management Functions
# =============================================================================


def init_reboot_schema(conn: sqlite3.Connection) -> None:
    """Initialize the Reboot schema.

    Creates all tables and indexes if they don't exist.
    Safe to call multiple times.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")

    # Create tables
    cursor.execute(INTENTS_TABLE)
    cursor.execute(VERIFICATIONS_TABLE)
    cursor.execute(META_TABLE)

    # Create indexes
    for index_sql in INDEXES:
        cursor.execute(index_sql)

    # Set schema version
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )

    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    """Get the current schema version.

    Args:
        conn: SQLite connection.

    Returns:
        Schema version, or None if not set.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cursor.fetchone()
        if row:
            return int(row[0])
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        pass
    return None


def needs_migration(conn: sqlite3.Connection) -> bool:
    """Check if the schema needs migration.

    Args:
        conn: SQLite connection.

    Returns:
        True if migration is needed.
    """
    version = get_schema_version(conn)
    if version is None:
        return True
    return version < SCHEMA_VERSION


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Migrate the schema to the latest version.

    Args:
        conn: SQLite connection.
    """
    current = get_schema_version(conn) or 0

    if current < 2:
        _migrate_v1_to_v2(conn)

    # Update schema version
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate from v1 to v2: Add extended check types.

    SQLite doesn't support ALTER TABLE for CHECK constraints, so we
    recreate the table with the new constraint. Existing data is preserved
    since all v1 check_types are valid in v2.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Create new table with extended check types
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_verifications_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reboot_intent_id TEXT NOT NULL REFERENCES reboot_intents(id) ON DELETE CASCADE,
            check_type TEXT NOT NULL
                CHECK (check_type IN (
                    'command', 'service_active', 'file_exists', 'port_listening',
                    'filesystem_writable', 'mount_exists', 'kernel_module',
                    'dmesg_no_errors', 'journal_no_errors', 'disk_space', 'network_interface'
                )),
            check_command TEXT NOT NULL,
            expected_exit_code INTEGER NOT NULL DEFAULT 0,
            expected_output_contains TEXT,
            step_index INTEGER NOT NULL,
            required INTEGER NOT NULL DEFAULT 1,
            executed_at TEXT,
            exit_code INTEGER,
            stdout TEXT,
            stderr TEXT,
            passed INTEGER,
            UNIQUE(reboot_intent_id, step_index)
        )
    """)

    # Copy data from old table (if it exists and has data)
    try:
        cursor.execute("""
            INSERT INTO pending_verifications_v2
            SELECT * FROM pending_verifications
        """)
    except sqlite3.OperationalError:
        # Old table doesn't exist or is empty, that's fine
        pass

    # Drop old table and rename new one
    cursor.execute("DROP TABLE IF EXISTS pending_verifications")
    cursor.execute("ALTER TABLE pending_verifications_v2 RENAME TO pending_verifications")

    # Recreate indexes
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_verifications_intent ON pending_verifications(reboot_intent_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_verifications_passed ON pending_verifications(passed)"
    )

    conn.commit()


def drop_all_tables(conn: sqlite3.Connection) -> None:
    """Drop all Reboot tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Drop tables (order matters due to foreign keys)
    cursor.execute("DROP TABLE IF EXISTS pending_verifications")
    cursor.execute("DROP TABLE IF EXISTS reboot_intents")
    cursor.execute("DROP TABLE IF EXISTS meta")

    conn.commit()


def get_db_path() -> Path:
    """Get the Reboot database path.

    Uses a separate database file for reboot intents to keep it
    isolated from other ELLE databases.

    Returns:
        Path to the Reboot database.
    """
    return DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the Reboot database.

    Creates the database directory if it doesn't exist (for non-system paths).

    Args:
        db_path: Override database path (for testing).

    Returns:
        SQLite connection with row factory set.
    """
    path = db_path or get_db_path()

    # For non-system paths, ensure directory exists
    if not str(path).startswith("/var/lib"):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the schema is initialized and up to date.

    Args:
        conn: SQLite connection.
    """
    if needs_migration(conn):
        init_reboot_schema(conn)
