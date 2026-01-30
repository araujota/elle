"""Tests for DB helper utilities."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elle.common.db import (
    DB_PATH,
    EVENTS_INDEX,
    EVENTS_SCHEMA,
    ensure_connection,
    get_connection,
    init_db,
)


# =============================================================================
# Constants
# =============================================================================


class TestConstants:
    """Tests for module-level constants."""

    def test_db_path_is_under_var_lib(self) -> None:
        assert str(DB_PATH).startswith("/var/lib/elle/")
        assert DB_PATH.name == "elle.db"

    def test_events_schema_has_table(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS events" in EVENTS_SCHEMA

    def test_events_schema_has_required_columns(self) -> None:
        for col in ("id", "ts", "source", "severity", "category", "message", "raw"):
            assert col in EVENTS_SCHEMA

    def test_events_index_creates_index(self) -> None:
        assert "CREATE INDEX IF NOT EXISTS" in EVENTS_INDEX
        assert "idx_events_ts" in EVENTS_INDEX
        assert "events(ts)" in EVENTS_INDEX


# =============================================================================
# get_connection
# =============================================================================


class TestGetConnection:
    """Tests for get_connection function."""

    def test_returns_connection(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    def test_row_factory_set(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "nested" / "test.db"
        conn = get_connection(db_path)
        try:
            assert db_path.parent.exists()
        finally:
            conn.close()

    def test_creates_database_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "new.db"
        assert not db_path.exists()
        conn = get_connection(db_path)
        try:
            # Perform a write to ensure the file is created
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
            assert db_path.exists()
        finally:
            conn.close()

    def test_connection_is_functional(self, tmp_path: Path) -> None:
        db_path = tmp_path / "functional.db"
        conn = get_connection(db_path)
        try:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
            conn.execute("INSERT INTO test (val) VALUES (?)", ("hello",))
            conn.commit()
            row = conn.execute("SELECT val FROM test").fetchone()
            assert row["val"] == "hello"
        finally:
            conn.close()

    def test_reopen_persists_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "persist.db"
        conn1 = get_connection(db_path)
        try:
            conn1.execute("CREATE TABLE items (name TEXT)")
            conn1.execute("INSERT INTO items (name) VALUES (?)", ("test_item",))
            conn1.commit()
        finally:
            conn1.close()

        conn2 = get_connection(db_path)
        try:
            row = conn2.execute("SELECT name FROM items").fetchone()
            assert row["name"] == "test_item"
        finally:
            conn2.close()


# =============================================================================
# ensure_connection
# =============================================================================


class TestEnsureConnection:
    """Tests for ensure_connection context manager."""

    def test_creates_new_connection_when_none(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ensure.db"
        with ensure_connection(conn=None, db_path=db_path) as c:
            assert isinstance(c, sqlite3.Connection)
            c.execute("CREATE TABLE t (id INTEGER)")
            c.commit()
        # Connection should be closed after context exits
        # Verify by checking file was created
        assert db_path.exists()

    def test_uses_existing_connection(self, tmp_path: Path) -> None:
        db_path = tmp_path / "existing.db"
        existing_conn = get_connection(db_path)
        try:
            with ensure_connection(conn=existing_conn) as c:
                assert c is existing_conn
        finally:
            existing_conn.close()

    def test_closes_new_connection_on_exit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "close_test.db"
        captured_conn = None
        with ensure_connection(conn=None, db_path=db_path) as c:
            captured_conn = c
            c.execute("CREATE TABLE t (id INTEGER)")
            c.commit()
        # After exiting the context, the new connection should be closed
        # Attempting to use it should raise an error
        with pytest.raises(Exception):
            captured_conn.execute("SELECT 1")

    def test_does_not_close_existing_connection(self, tmp_path: Path) -> None:
        db_path = tmp_path / "no_close.db"
        existing_conn = get_connection(db_path)
        try:
            with ensure_connection(conn=existing_conn) as c:
                c.execute("CREATE TABLE t (id INTEGER)")
                c.commit()
            # Existing connection should still be usable
            existing_conn.execute("SELECT * FROM t")
        finally:
            existing_conn.close()

    def test_functional_with_none_conn(self, tmp_path: Path) -> None:
        db_path = tmp_path / "functional_none.db"
        with ensure_connection(conn=None, db_path=db_path) as c:
            c.execute("CREATE TABLE vals (v TEXT)")
            c.execute("INSERT INTO vals (v) VALUES (?)", ("data",))
            c.commit()
            row = c.execute("SELECT v FROM vals").fetchone()
            assert row["v"] == "data"

    def test_functional_with_existing_conn(self, tmp_path: Path) -> None:
        db_path = tmp_path / "functional_existing.db"
        conn = get_connection(db_path)
        try:
            with ensure_connection(conn=conn) as c:
                c.execute("CREATE TABLE vals (v TEXT)")
                c.execute("INSERT INTO vals (v) VALUES (?)", ("data",))
                c.commit()
            # Verify data persists via the same connection
            row = conn.execute("SELECT v FROM vals").fetchone()
            assert row["v"] == "data"
        finally:
            conn.close()


# =============================================================================
# init_db
# =============================================================================


class TestInitDb:
    """Tests for init_db function."""

    def test_creates_events_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "init.db"
        conn = get_connection(db_path)
        try:
            init_db(conn)
            # Check that the events table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["name"] == "events"
        finally:
            conn.close()

    def test_creates_events_index(self, tmp_path: Path) -> None:
        db_path = tmp_path / "init_idx.db"
        conn = get_connection(db_path)
        try:
            init_db(conn)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_events_ts'"
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["name"] == "idx_events_ts"
        finally:
            conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "idempotent.db"
        conn = get_connection(db_path)
        try:
            init_db(conn)
            init_db(conn)  # Should not raise
            cursor = conn.execute(
                "SELECT count(*) as cnt FROM sqlite_master WHERE type='table' AND name='events'"
            )
            assert cursor.fetchone()["cnt"] == 1
        finally:
            conn.close()

    def test_events_table_has_correct_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "columns.db"
        conn = get_connection(db_path)
        try:
            init_db(conn)
            cursor = conn.execute("PRAGMA table_info(events)")
            columns = {row["name"] for row in cursor.fetchall()}
            expected = {"id", "ts", "source", "severity", "category", "message", "raw"}
            assert columns == expected
        finally:
            conn.close()

    def test_can_insert_event_after_init(self, tmp_path: Path) -> None:
        db_path = tmp_path / "insert.db"
        conn = get_connection(db_path)
        try:
            init_db(conn)
            conn.execute(
                "INSERT INTO events (ts, source, severity, category, message, raw) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("2025-01-15T12:00:00", "journal", "error", "systemd", "service failed", "{}"),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM events WHERE id=1").fetchone()
            assert row is not None
            assert row["source"] == "journal"
            assert row["severity"] == "error"
        finally:
            conn.close()

    def test_autoincrement_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "autoincr.db"
        conn = get_connection(db_path)
        try:
            init_db(conn)
            for i in range(3):
                conn.execute(
                    "INSERT INTO events (ts, source, severity, category, message, raw) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"2025-01-15T12:0{i}:00", "probe", "info", "disk", f"msg {i}", "{}"),
                )
            conn.commit()
            rows = conn.execute("SELECT id FROM events ORDER BY id").fetchall()
            ids = [row["id"] for row in rows]
            assert ids == [1, 2, 3]
        finally:
            conn.close()

    def test_init_db_without_conn(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test init_db creates its own connection when none provided."""
        db_path = tmp_path / "noconn.db"
        monkeypatch.setattr("elle.common.db.DB_PATH", db_path)
        init_db(conn=None)
        # Verify the table was created
        conn = get_connection(db_path)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
            )
            assert cursor.fetchone() is not None
        finally:
            conn.close()
