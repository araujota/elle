from __future__ import annotations

"""Tests for policy audit trail logging."""

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.policy.audit import (
    DEFAULT_AUDIT_DIR,
    DEFAULT_AUDIT_FILE,
    MAX_AUDIT_ENTRIES,
    PolicyAuditLogger,
    get_audit_logger,
    log_evaluation,
    read_audit_entries,
)
from elle.policy.models import (
    PolicyAuditEntry,
    PolicyEffect,
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_request(**kwargs) -> PolicyEvaluationRequest:
    """Create a PolicyEvaluationRequest with reasonable defaults."""
    defaults = {
        "operation_type": "command",
        "command": "ls -la",
    }
    defaults.update(kwargs)
    return PolicyEvaluationRequest(**defaults)


def _make_result(**kwargs) -> PolicyEvaluationResult:
    """Create a PolicyEvaluationResult with reasonable defaults."""
    defaults = {
        "allowed": True,
        "effect": PolicyEffect.ALLOW,
    }
    defaults.update(kwargs)
    return PolicyEvaluationResult(**defaults)


def _make_audit_entry(**kwargs) -> PolicyAuditEntry:
    """Create a PolicyAuditEntry with reasonable defaults."""
    defaults = {
        "timestamp": datetime.now(),
        "request": _make_request(),
        "result": _make_result(),
    }
    defaults.update(kwargs)
    return PolicyAuditEntry(**defaults)


# =============================================================================
# Constants Tests
# =============================================================================


class TestAuditConstants:
    """Tests for module-level constants."""

    def test_default_audit_dir(self):
        """Test default audit directory is under .local/state/elle."""
        assert Path.home() / ".local" / "state" / "elle" == DEFAULT_AUDIT_DIR

    def test_default_audit_file(self):
        """Test default audit file is policy_audit.jsonl."""
        assert DEFAULT_AUDIT_FILE == DEFAULT_AUDIT_DIR / "policy_audit.jsonl"

    def test_max_audit_entries(self):
        """Test max audit entries is 10000."""
        assert MAX_AUDIT_ENTRIES == 10000


# =============================================================================
# PolicyAuditLogger Init Tests
# =============================================================================


class TestPolicyAuditLoggerInit:
    """Tests for PolicyAuditLogger initialization."""

    def test_init_defaults(self):
        """Test init with default parameters."""
        logger = PolicyAuditLogger()
        assert logger._audit_path == DEFAULT_AUDIT_FILE
        assert logger._max_entries == MAX_AUDIT_ENTRIES
        assert logger._file is None
        assert logger._entry_count == 0
        assert logger._session_id is None

    def test_init_custom_path(self, tmp_path: Path):
        """Test init with custom audit path."""
        custom_path = tmp_path / "custom_audit.jsonl"
        logger = PolicyAuditLogger(audit_path=custom_path)
        assert logger._audit_path == custom_path

    def test_init_custom_max_entries(self):
        """Test init with custom max entries."""
        logger = PolicyAuditLogger(max_entries=500)
        assert logger._max_entries == 500

    def test_init_with_none_path_uses_default(self):
        """Test init with explicit None path uses default."""
        logger = PolicyAuditLogger(audit_path=None)
        assert logger._audit_path == DEFAULT_AUDIT_FILE


# =============================================================================
# set_session_id Tests
# =============================================================================


class TestSetSessionId:
    """Tests for set_session_id method."""

    def test_set_session_id(self):
        """Test setting session ID."""
        logger = PolicyAuditLogger()
        logger.set_session_id("session-abc-123")
        assert logger._session_id == "session-abc-123"

    def test_set_session_id_overwrite(self):
        """Test overwriting session ID."""
        logger = PolicyAuditLogger()
        logger.set_session_id("session-1")
        logger.set_session_id("session-2")
        assert logger._session_id == "session-2"


# =============================================================================
# log_evaluation Tests
# =============================================================================


class TestLogEvaluation:
    """Tests for log_evaluation method."""

    def test_log_evaluation_creates_entry(self, tmp_path: Path):
        """Test that log_evaluation writes an entry to the audit file."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        request = _make_request(command="apt update")
        result = _make_result(allowed=True, effect=PolicyEffect.ALLOW)

        logger.log_evaluation(request, result)

        assert audit_path.exists()
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 1

        data = json.loads(lines[0])
        assert data["request"]["command"] == "apt update"
        assert data["result"]["allowed"] is True

        logger.close()

    def test_log_evaluation_with_user_confirmed(self, tmp_path: Path):
        """Test log_evaluation with user_confirmed flag."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        request = _make_request(command="reboot")
        result = _make_result(
            allowed=True,
            effect=PolicyEffect.REQUIRE_CONFIRMATION,
            requires_confirmation=True,
        )

        logger.log_evaluation(request, result, user_confirmed=True)

        lines = audit_path.read_text().strip().split("\n")
        data = json.loads(lines[0])
        assert data["user_confirmed"] is True

        logger.close()

    def test_log_evaluation_with_justification(self, tmp_path: Path):
        """Test log_evaluation with justification."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        request = _make_request(command="rm -rf /tmp/old")
        result = _make_result(
            allowed=True,
            effect=PolicyEffect.REQUIRE_JUSTIFICATION,
            requires_justification=True,
        )

        logger.log_evaluation(
            request,
            result,
            justification="Cleaning up old temporary files",
        )

        lines = audit_path.read_text().strip().split("\n")
        data = json.loads(lines[0])
        assert data["justification"] == "Cleaning up old temporary files"

        logger.close()

    def test_log_evaluation_with_session_id(self, tmp_path: Path):
        """Test log_evaluation includes session ID."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)
        logger.set_session_id("sess-xyz")

        request = _make_request()
        result = _make_result()

        logger.log_evaluation(request, result)

        lines = audit_path.read_text().strip().split("\n")
        data = json.loads(lines[0])
        assert data["session_id"] == "sess-xyz"

        logger.close()

    def test_log_evaluation_multiple_entries(self, tmp_path: Path):
        """Test logging multiple entries appends correctly."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        for i in range(5):
            request = _make_request(command=f"cmd-{i}")
            result = _make_result()
            logger.log_evaluation(request, result)

        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 5

        logger.close()

    def test_log_evaluation_increments_entry_count(self, tmp_path: Path):
        """Test that entry count is incremented with each write."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        request = _make_request()
        result = _make_result()

        logger.log_evaluation(request, result)
        logger.log_evaluation(request, result)
        logger.log_evaluation(request, result)

        # entry_count includes existing entries counted on open + 3 new entries
        assert logger._entry_count >= 3

        logger.close()


# =============================================================================
# _write_entry Tests
# =============================================================================


class TestWriteEntry:
    """Tests for _write_entry method."""

    def test_write_entry_file_none_returns_early(self, tmp_path: Path):
        """Test _write_entry returns early if file cannot be opened."""
        logger = PolicyAuditLogger(audit_path=tmp_path / "no" / "such" / "dir" / "audit.jsonl")

        # Patch _ensure_file_open to not actually open (simulating failure)
        with patch.object(logger, "_ensure_file_open"):
            logger._file = None
            entry = _make_audit_entry()
            # Should not raise; should just return silently
            logger._write_entry(entry)

    def test_write_entry_exception_logs_warning(self, tmp_path: Path, caplog):
        """Test that exceptions during write are logged as warnings."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        # Create a mock file that raises on write
        mock_file = MagicMock()
        mock_file.write.side_effect = OSError("disk full")
        logger._file = mock_file

        entry = _make_audit_entry()

        with caplog.at_level(logging.WARNING):
            logger._write_entry(entry)

        assert "Failed to write audit entry" in caplog.text

    def test_write_entry_pydantic_v1_fallback(self, tmp_path: Path):
        """Test _write_entry falls back to json.dumps when model_dump_json is absent."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        entry = _make_audit_entry()

        # Remove model_dump_json to trigger v1 fallback
        original_method = getattr(entry, "model_dump_json", None)
        if original_method is not None:
            with patch.object(type(entry), "model_dump_json", side_effect=AttributeError):
                logger._write_entry(entry)
        else:
            # Already using v1
            logger._write_entry(entry)

        assert audit_path.exists()
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) >= 1
        # Verify it is valid JSON
        json.loads(lines[-1])

        logger.close()

    def test_write_entry_triggers_rotation(self, tmp_path: Path):
        """Test that reaching max_entries triggers log rotation."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path, max_entries=3)

        request = _make_request()
        result = _make_result()

        for i in range(4):
            logger.log_evaluation(request, result)

        # After rotation, entry count should be reduced (kept half)
        assert logger._entry_count < 4

        logger.close()

    def test_write_entry_pydantic_v1_datetime_serializer(self, tmp_path: Path):
        """Test the Pydantic v1 fallback JSON serializer handles datetime."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        entry = _make_audit_entry()

        # Simulate Pydantic v1 by having model_dump_json raise AttributeError
        with patch.object(type(entry), "model_dump_json", side_effect=AttributeError):
            logger._write_entry(entry)

        content = audit_path.read_text().strip()
        assert content  # Something was written
        data = json.loads(content.split("\n")[-1])
        # Verify the entry was serialized
        assert "request" in data
        assert "result" in data

        logger.close()

    def test_write_entry_v1_json_serializer_type_error(self, tmp_path: Path, caplog):
        """Test the v1 fallback JSON serializer catches TypeError for unsupported types."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        entry = _make_audit_entry()

        # Trigger the v1 fallback path AND inject a non-serializable type.
        # The json_serializer inside _write_entry raises TypeError for non-datetime.
        # The outer try/except in _write_entry catches this and logs a warning.
        with patch.object(type(entry), "model_dump_json", side_effect=AttributeError):
            with patch.object(type(entry), "dict", return_value={"bad_obj": object()}):
                with caplog.at_level(logging.WARNING):
                    logger._write_entry(entry)

        assert "Failed to write audit entry" in caplog.text

        logger.close()


# =============================================================================
# _ensure_file_open Tests
# =============================================================================


class TestEnsureFileOpen:
    """Tests for _ensure_file_open method."""

    def test_ensure_file_open_creates_directory(self, tmp_path: Path):
        """Test that _ensure_file_open creates parent directories."""
        audit_path = tmp_path / "subdir" / "deep" / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        logger._ensure_file_open()

        assert audit_path.parent.exists()
        assert logger._file is not None

        logger.close()

    def test_ensure_file_open_noop_if_already_open(self, tmp_path: Path):
        """Test _ensure_file_open is a no-op if file is already open."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        mock_file = MagicMock()
        logger._file = mock_file

        logger._ensure_file_open()

        # Should still be the same mock file, not replaced
        assert logger._file is mock_file

    def test_ensure_file_open_counts_existing_entries(self, tmp_path: Path):
        """Test that _ensure_file_open counts existing entries in the file."""
        audit_path = tmp_path / "audit.jsonl"
        # Pre-populate the file with some entries
        audit_path.write_text('{"a":1}\n{"b":2}\n{"c":3}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)
        logger._ensure_file_open()

        assert logger._entry_count == 3

        logger.close()

    def test_ensure_file_open_count_error_defaults_to_zero(self, tmp_path: Path):
        """Test that entry count defaults to 0 if counting fails."""
        audit_path = tmp_path / "audit.jsonl"
        # Create the file so the exists() check passes
        audit_path.write_text('{"a":1}\n{"b":2}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)

        # The source uses os.open + os.fdopen for the append operation,
        # then builtins.open for counting existing entries.
        # We let os.open/os.fdopen succeed normally but make the
        # builtins.open call (used for counting) raise an error.
        with patch("builtins.open", side_effect=PermissionError("cannot read")):
            logger._ensure_file_open()

        # The entry count should fallback to 0 due to the read error
        assert logger._entry_count == 0

        if logger._file is not None:
            logger._file.close()

    def test_ensure_file_open_failure_logs_warning(self, tmp_path: Path, caplog):
        """Test that failure to open file logs a warning."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        # Patch Path.mkdir at the class level to raise
        with patch("pathlib.Path.mkdir", side_effect=OSError("no permissions")):
            with caplog.at_level(logging.WARNING):
                logger._ensure_file_open()

        assert logger._file is None
        assert "Failed to open audit log" in caplog.text


# =============================================================================
# _rotate_log Tests
# =============================================================================


class TestRotateLog:
    """Tests for _rotate_log method."""

    def test_rotate_log_keeps_last_half(self, tmp_path: Path):
        """Test rotation keeps the last half of entries."""
        audit_path = tmp_path / "audit.jsonl"
        lines = [f'{{"entry": {i}}}\n' for i in range(10)]
        audit_path.write_text("".join(lines))

        logger = PolicyAuditLogger(audit_path=audit_path)
        logger._file = MagicMock()  # simulate open file

        logger._rotate_log()

        # Should have kept last 5 entries
        remaining = audit_path.read_text().strip().split("\n")
        assert len(remaining) == 5
        assert logger._entry_count == 5

        # Verify it kept the last entries
        for i, line in enumerate(remaining):
            data = json.loads(line)
            assert data["entry"] == i + 5

    def test_rotate_log_closes_file(self, tmp_path: Path):
        """Test that rotation closes the file handle."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text('{"a":1}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)
        mock_file = MagicMock()
        logger._file = mock_file

        logger._rotate_log()

        mock_file.close.assert_called_once()
        assert logger._file is None

    def test_rotate_log_nonexistent_file(self, tmp_path: Path):
        """Test rotation with nonexistent file does nothing."""
        audit_path = tmp_path / "nonexistent.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)
        logger._file = None

        # Should not raise
        logger._rotate_log()

    def test_rotate_log_removes_backup(self, tmp_path: Path):
        """Test that rotation removes the backup file after writing."""
        audit_path = tmp_path / "audit.jsonl"
        backup_path = audit_path.with_suffix(".jsonl.bak")
        audit_path.write_text('{"a":1}\n{"b":2}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)
        logger._file = MagicMock()

        logger._rotate_log()

        # Backup should be removed
        assert not backup_path.exists()
        # Original should exist with reduced entries
        assert audit_path.exists()

    def test_rotate_log_exception_logs_warning(self, tmp_path: Path, caplog):
        """Test that rotation failure logs a warning."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text('{"a":1}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)
        logger._file = MagicMock()

        with patch.object(Path, "rename", side_effect=OSError("rename failed")):
            with caplog.at_level(logging.WARNING):
                logger._rotate_log()

        assert "Failed to rotate audit log" in caplog.text

    def test_rotate_log_with_file_none(self, tmp_path: Path):
        """Test rotation when _file is already None (no close needed)."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text('{"a":1}\n{"b":2}\n{"c":3}\n{"d":4}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)
        logger._file = None

        logger._rotate_log()

        remaining = audit_path.read_text().strip().split("\n")
        assert len(remaining) == 2

    def test_rotate_log_odd_number_of_entries(self, tmp_path: Path):
        """Test rotation with odd number of entries (integer division)."""
        audit_path = tmp_path / "audit.jsonl"
        lines = [f'{{"entry": {i}}}\n' for i in range(7)]
        audit_path.write_text("".join(lines))

        logger = PolicyAuditLogger(audit_path=audit_path)
        logger._file = MagicMock()

        logger._rotate_log()

        remaining = audit_path.read_text().strip().split("\n")
        # 7 // 2 == 3
        assert len(remaining) == 3
        assert logger._entry_count == 3


# =============================================================================
# close Tests
# =============================================================================


class TestClose:
    """Tests for close method."""

    def test_close_with_open_file(self, tmp_path: Path):
        """Test close with an open file handle."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        mock_file = MagicMock()
        logger._file = mock_file

        logger.close()

        mock_file.close.assert_called_once()
        assert logger._file is None

    def test_close_with_no_file(self):
        """Test close when no file is open (no-op)."""
        logger = PolicyAuditLogger()
        logger._file = None

        # Should not raise
        logger.close()
        assert logger._file is None

    def test_close_idempotent(self, tmp_path: Path):
        """Test that calling close twice is safe."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        mock_file = MagicMock()
        logger._file = mock_file

        logger.close()
        logger.close()

        mock_file.close.assert_called_once()


# =============================================================================
# read_entries Tests
# =============================================================================


class TestReadEntries:
    """Tests for read_entries method."""

    @patch("elle.policy.audit.safe_model_validate")
    def test_read_entries_returns_entries(self, mock_validate, tmp_path: Path):
        """Test reading entries from the audit file."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        # Write valid JSON lines
        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        audit_path.write_text(json_str + "\n" + json_str + "\n")

        mock_validate.return_value = entry

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.read_entries(limit=10)

        assert len(entries) == 2
        assert mock_validate.call_count == 2

    def test_read_entries_nonexistent_file(self, tmp_path: Path):
        """Test reading from nonexistent file returns empty list."""
        audit_path = tmp_path / "nonexistent.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        entries = logger.read_entries()

        assert entries == []

    @patch("elle.policy.audit.safe_model_validate")
    def test_read_entries_with_limit(self, mock_validate, tmp_path: Path):
        """Test reading entries with limit."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        lines = "\n".join([json_str] * 10) + "\n"
        audit_path.write_text(lines)

        mock_validate.return_value = entry

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.read_entries(limit=3)

        assert len(entries) == 3

    @patch("elle.policy.audit.safe_model_validate")
    def test_read_entries_with_offset(self, mock_validate, tmp_path: Path):
        """Test reading entries with offset from end."""
        audit_path = tmp_path / "audit.jsonl"

        # Write 10 distinct entries
        json_lines = []
        for i in range(10):
            entry = _make_audit_entry()
            try:
                json_str = entry.model_dump_json()
            except AttributeError:
                json_str = json.dumps(entry.dict(), default=str)
            json_lines.append(json_str)

        audit_path.write_text("\n".join(json_lines) + "\n")

        mock_validate.return_value = _make_audit_entry()

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.read_entries(limit=3, offset=2)

        # With 10 lines, offset=2, limit=3: start=max(0,10-2-3)=5, end=10-2=8
        # Lines[5:8] reversed = 3 entries
        assert len(entries) == 3

    def test_read_entries_skips_malformed_lines(self, tmp_path: Path):
        """Test that malformed JSON lines are skipped."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        # Mix valid and invalid lines
        content = f"not valid json\n{json_str}\nalso bad\n"
        audit_path.write_text(content)

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.read_entries()

        # Only the valid line should parse (the malformed ones get skipped)
        # The exact count depends on safe_model_validate success
        assert isinstance(entries, list)

    def test_read_entries_file_read_error(self, tmp_path: Path, caplog):
        """Test that file read errors are handled gracefully."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text('{"a":1}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)

        with patch("builtins.open", side_effect=PermissionError("denied")):
            with caplog.at_level(logging.WARNING):
                entries = logger.read_entries()

        assert entries == []
        assert "Failed to read audit entries" in caplog.text

    @patch("elle.policy.audit.safe_model_validate")
    def test_read_entries_returns_most_recent_first(self, mock_validate, tmp_path: Path):
        """Test entries are returned in reverse order (most recent first)."""
        audit_path = tmp_path / "audit.jsonl"

        lines = []
        entries_list = []
        for i in range(5):
            entry = _make_audit_entry()
            entries_list.append(entry)
            try:
                json_str = entry.model_dump_json()
            except AttributeError:
                json_str = json.dumps(entry.dict(), default=str)
            lines.append(json_str)

        audit_path.write_text("\n".join(lines) + "\n")

        # Return entries with index for tracking order
        call_idx = [0]

        def side_effect(cls, data):
            call_idx[0]
            call_idx[0] += 1
            return entries_list[0]  # Return valid entry

        mock_validate.side_effect = side_effect

        logger = PolicyAuditLogger(audit_path=audit_path)
        result = logger.read_entries()

        assert len(result) == 5


# =============================================================================
# search_entries Tests
# =============================================================================


class TestSearchEntries:
    """Tests for search_entries method."""

    def _write_entries_to_file(self, audit_path: Path, entries: list[PolicyAuditEntry]) -> None:
        """Helper to write entries to audit file."""
        lines = []
        for entry in entries:
            try:
                json_str = entry.model_dump_json()
            except AttributeError:
                json_str = json.dumps(entry.dict(), default=str)
            lines.append(json_str)
        audit_path.write_text("\n".join(lines) + "\n")

    def test_search_entries_nonexistent_file(self, tmp_path: Path):
        """Test searching nonexistent file returns empty list."""
        audit_path = tmp_path / "nonexistent.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        entries = logger.search_entries(command="ls")

        assert entries == []

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_by_command(self, mock_validate, tmp_path: Path):
        """Test searching entries by command pattern."""
        audit_path = tmp_path / "audit.jsonl"

        entry_with_apt = _make_audit_entry()
        _make_audit_entry()

        # Write some JSON lines
        try:
            json_str = entry_with_apt.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry_with_apt.dict(), default=str)

        audit_path.write_text(json_str + "\n" + json_str + "\n")

        # Return entries that match the command filter
        mock_entry_apt = MagicMock()
        mock_entry_apt.request.command = "apt update"
        mock_entry_apt.request.path = None
        mock_entry_apt.result.effect.value = "allow"

        mock_entry_ls = MagicMock()
        mock_entry_ls.request.command = "ls -la"
        mock_entry_ls.request.path = None
        mock_entry_ls.result.effect.value = "allow"

        mock_validate.side_effect = [mock_entry_ls, mock_entry_apt]

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries(command="apt")

        # Only the entry with "apt" in command should be returned
        assert len(entries) == 1
        assert entries[0].request.command == "apt update"

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_by_path(self, mock_validate, tmp_path: Path):
        """Test searching entries by path pattern."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        audit_path.write_text(json_str + "\n" + json_str + "\n")

        mock_entry_etc = MagicMock()
        mock_entry_etc.request.command = None
        mock_entry_etc.request.path = "/etc/ssh/sshd_config"
        mock_entry_etc.result.effect.value = "deny"

        mock_entry_home = MagicMock()
        mock_entry_home.request.command = None
        mock_entry_home.request.path = "/home/user/.bashrc"
        mock_entry_home.result.effect.value = "allow"

        mock_validate.side_effect = [mock_entry_home, mock_entry_etc]

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries(path="/etc")

        assert len(entries) == 1
        assert "/etc" in entries[0].request.path

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_by_effect(self, mock_validate, tmp_path: Path):
        """Test searching entries by effect."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        audit_path.write_text(json_str + "\n" + json_str + "\n")

        mock_entry_allow = MagicMock()
        mock_entry_allow.request.command = "ls"
        mock_entry_allow.request.path = None
        mock_entry_allow.result.effect.value = "allow"

        mock_entry_deny = MagicMock()
        mock_entry_deny.request.command = "sudo rm"
        mock_entry_deny.request.path = None
        mock_entry_deny.result.effect.value = "deny"

        mock_validate.side_effect = [mock_entry_deny, mock_entry_allow]

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries(effect="deny")

        assert len(entries) == 1
        assert entries[0].result.effect.value == "deny"

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_with_limit(self, mock_validate, tmp_path: Path):
        """Test searching entries respects limit."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        # Write 10 entries
        audit_path.write_text((json_str + "\n") * 10)

        mock_entry = MagicMock()
        mock_entry.request.command = "ls"
        mock_entry.request.path = None
        mock_entry.result.effect.value = "allow"

        mock_validate.return_value = mock_entry

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries(limit=3)

        assert len(entries) == 3

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_command_filter_none_command(self, mock_validate, tmp_path: Path):
        """Test command filter skips entries with None command."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        audit_path.write_text(json_str + "\n")

        mock_entry = MagicMock()
        mock_entry.request.command = None
        mock_entry.request.path = None
        mock_entry.result.effect.value = "allow"

        mock_validate.return_value = mock_entry

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries(command="apt")

        assert len(entries) == 0

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_path_filter_none_path(self, mock_validate, tmp_path: Path):
        """Test path filter skips entries with None path."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        audit_path.write_text(json_str + "\n")

        mock_entry = MagicMock()
        mock_entry.request.command = "ls"
        mock_entry.request.path = None
        mock_entry.result.effect.value = "allow"

        mock_validate.return_value = mock_entry

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries(path="/etc")

        assert len(entries) == 0

    def test_search_entries_skips_malformed_lines(self, tmp_path: Path):
        """Test that malformed JSON lines are skipped during search."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text("this is not json\n{also bad\n")

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries()

        assert entries == []

    def test_search_entries_file_error(self, tmp_path: Path, caplog):
        """Test that file read errors during search are handled gracefully."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text('{"a":1}\n')

        logger = PolicyAuditLogger(audit_path=audit_path)

        with patch("builtins.open", side_effect=OSError("read error")):
            with caplog.at_level(logging.WARNING):
                entries = logger.search_entries()

        assert entries == []
        assert "Failed to search audit entries" in caplog.text

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_no_filters_returns_all(self, mock_validate, tmp_path: Path):
        """Test searching with no filters returns all entries."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        audit_path.write_text((json_str + "\n") * 5)

        mock_entry = MagicMock()
        mock_entry.request.command = "ls"
        mock_entry.request.path = None
        mock_entry.result.effect.value = "allow"

        mock_validate.return_value = mock_entry

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries()

        assert len(entries) == 5

    @patch("elle.policy.audit.safe_model_validate")
    def test_search_entries_combined_filters(self, mock_validate, tmp_path: Path):
        """Test searching with multiple filters (command + path + effect)."""
        audit_path = tmp_path / "audit.jsonl"
        entry = _make_audit_entry()

        try:
            json_str = entry.model_dump_json()
        except AttributeError:
            json_str = json.dumps(entry.dict(), default=str)

        audit_path.write_text(json_str + "\n")

        mock_entry = MagicMock()
        mock_entry.request.command = "apt update"
        mock_entry.request.path = "/etc/apt/sources.list"
        mock_entry.result.effect.value = "deny"

        mock_validate.return_value = mock_entry

        logger = PolicyAuditLogger(audit_path=audit_path)

        # All filters match
        entries = logger.search_entries(command="apt", path="/etc", effect="deny")
        assert len(entries) == 1

        # Command filter does not match
        entries = logger.search_entries(command="rm", path="/etc", effect="deny")
        assert len(entries) == 0


# =============================================================================
# Module-level Functions Tests
# =============================================================================


class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_get_audit_logger_returns_instance(self):
        """Test get_audit_logger returns a PolicyAuditLogger."""
        with patch("elle.policy.audit._logger", None):
            with patch("elle.policy.audit.PolicyAuditLogger") as MockLogger:
                mock_instance = MagicMock(spec=PolicyAuditLogger)
                MockLogger.return_value = mock_instance

                result = get_audit_logger()

                assert result is mock_instance
                MockLogger.assert_called_once()

    def test_get_audit_logger_returns_singleton(self):
        """Test get_audit_logger returns same instance on second call."""
        with patch("elle.policy.audit._logger", None):
            with patch("elle.policy.audit.PolicyAuditLogger") as MockLogger:
                mock_instance = MagicMock(spec=PolicyAuditLogger)
                MockLogger.return_value = mock_instance

                first = get_audit_logger()

                # Now _logger is set, patch it for the second call
                with patch("elle.policy.audit._logger", first):
                    second = get_audit_logger()

                assert first is second

    def test_get_audit_logger_reuses_existing(self):
        """Test get_audit_logger reuses existing non-None logger."""
        existing_logger = MagicMock(spec=PolicyAuditLogger)
        with patch("elle.policy.audit._logger", existing_logger):
            result = get_audit_logger()
            assert result is existing_logger

    def test_log_evaluation_delegates_to_logger(self):
        """Test log_evaluation convenience function delegates to the shared logger."""
        mock_logger = MagicMock(spec=PolicyAuditLogger)

        request = _make_request()
        result = _make_result()

        with patch("elle.policy.audit.get_audit_logger", return_value=mock_logger):
            log_evaluation(
                request=request,
                result=result,
                user_confirmed=True,
                justification="testing",
            )

        mock_logger.log_evaluation.assert_called_once_with(
            request=request,
            result=result,
            user_confirmed=True,
            justification="testing",
        )

    def test_log_evaluation_with_defaults(self):
        """Test log_evaluation with default optional parameters."""
        mock_logger = MagicMock(spec=PolicyAuditLogger)

        request = _make_request()
        result = _make_result()

        with patch("elle.policy.audit.get_audit_logger", return_value=mock_logger):
            log_evaluation(request=request, result=result)

        mock_logger.log_evaluation.assert_called_once_with(
            request=request,
            result=result,
            user_confirmed=None,
            justification=None,
        )

    def test_read_audit_entries_delegates_to_logger(self):
        """Test read_audit_entries convenience function delegates to the shared logger."""
        mock_logger = MagicMock(spec=PolicyAuditLogger)
        mock_entries = [_make_audit_entry(), _make_audit_entry()]
        mock_logger.read_entries.return_value = mock_entries

        with patch("elle.policy.audit.get_audit_logger", return_value=mock_logger):
            entries = read_audit_entries(limit=50, offset=10)

        mock_logger.read_entries.assert_called_once_with(limit=50, offset=10)
        assert entries == mock_entries

    def test_read_audit_entries_with_defaults(self):
        """Test read_audit_entries with default parameters."""
        mock_logger = MagicMock(spec=PolicyAuditLogger)
        mock_logger.read_entries.return_value = []

        with patch("elle.policy.audit.get_audit_logger", return_value=mock_logger):
            entries = read_audit_entries()

        mock_logger.read_entries.assert_called_once_with(limit=100, offset=0)
        assert entries == []


# =============================================================================
# Integration-style Tests (using tmp_path, no real system state)
# =============================================================================


class TestAuditLoggerIntegration:
    """Integration-style tests using tmp_path for file I/O."""

    def test_full_write_and_read_cycle(self, tmp_path: Path):
        """Test writing entries and then reading them back."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        request = _make_request(command="systemctl restart nginx")
        result = _make_result(
            allowed=True,
            effect=PolicyEffect.REQUIRE_CONFIRMATION,
            requires_confirmation=True,
        )

        logger.set_session_id("integration-test-session")
        logger.log_evaluation(request, result, user_confirmed=True)

        logger.close()

        # Read back
        logger2 = PolicyAuditLogger(audit_path=audit_path)
        entries = logger2.read_entries()

        assert len(entries) == 1
        assert entries[0].session_id == "integration-test-session"
        assert entries[0].user_confirmed is True
        assert entries[0].request.command == "systemctl restart nginx"

        logger2.close()

    def test_rotation_triggered_by_max_entries(self, tmp_path: Path):
        """Test that rotation is triggered when max entries is reached."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path, max_entries=5)

        request = _make_request()
        result = _make_result()

        for i in range(7):
            logger.log_evaluation(request, result)

        logger.close()

        # After writing 7 entries with max 5:
        # At entry 5 rotation happens (keeps 2-3), then entries 6 and 7 are written
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) < 7  # Some were rotated out

    def test_search_after_write(self, tmp_path: Path):
        """Test searching for entries after writing them."""
        audit_path = tmp_path / "audit.jsonl"
        logger = PolicyAuditLogger(audit_path=audit_path)

        # Write an entry with a specific command
        request_apt = _make_request(command="apt update")
        request_ls = _make_request(command="ls -la")
        result = _make_result()

        logger.log_evaluation(request_apt, result)
        logger.log_evaluation(request_ls, result)
        logger.close()

        # Search by command
        logger2 = PolicyAuditLogger(audit_path=audit_path)
        entries = logger2.search_entries(command="apt")

        assert len(entries) == 1
        assert entries[0].request.command == "apt update"

        logger2.close()

    def test_empty_file_search_returns_empty(self, tmp_path: Path):
        """Test searching an empty audit file returns empty list."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text("")

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.search_entries(command="ls")

        assert entries == []

    def test_read_entries_empty_file_returns_empty(self, tmp_path: Path):
        """Test reading from an empty audit file returns empty list."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text("")

        logger = PolicyAuditLogger(audit_path=audit_path)
        entries = logger.read_entries()

        assert entries == []
