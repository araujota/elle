"""Audit trail logging for policy decisions.

Logs all policy evaluations to enable transparency, debugging,
and compliance requirements.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TextIO

from elle.policy.models import (
    PolicyAuditEntry,
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Audit Log Location
# =============================================================================

# Default audit log location (XDG state directory)
DEFAULT_AUDIT_DIR = Path.home() / ".local" / "state" / "elle"
DEFAULT_AUDIT_FILE = DEFAULT_AUDIT_DIR / "policy_audit.jsonl"

# Maximum entries to keep (for rotation)
MAX_AUDIT_ENTRIES = 10000


# =============================================================================
# Audit Logger
# =============================================================================


class PolicyAuditLogger:
    """Logs policy evaluations to JSONL file.

    Provides:
    - Append-only audit log
    - JSON Lines format for easy parsing
    - Automatic log rotation
    - Session tracking
    """

    def __init__(
        self,
        audit_path: Path | None = None,
        max_entries: int = MAX_AUDIT_ENTRIES,
    ) -> None:
        """Initialize the audit logger.

        Args:
            audit_path: Path to audit file. Defaults to ~/.local/state/elle/policy_audit.jsonl
            max_entries: Maximum entries before rotation.
        """
        self._audit_path = audit_path or DEFAULT_AUDIT_FILE
        self._max_entries = max_entries
        self._file: TextIO | None = None
        self._entry_count = 0
        self._session_id: str | None = None

    def set_session_id(self, session_id: str) -> None:
        """Set the current session ID for audit entries.

        Args:
            session_id: Session identifier.
        """
        self._session_id = session_id

    def log_evaluation(
        self,
        request: PolicyEvaluationRequest,
        result: PolicyEvaluationResult,
        user_confirmed: bool | None = None,
        justification: str | None = None,
    ) -> None:
        """Log a policy evaluation.

        Args:
            request: The evaluation request.
            result: The evaluation result.
            user_confirmed: Whether user confirmed (if applicable).
            justification: User-provided justification (if applicable).
        """
        entry = PolicyAuditEntry(
            timestamp=datetime.now(),
            request=request,
            result=result,
            user_confirmed=user_confirmed,
            justification=justification,
            session_id=self._session_id,
        )

        self._write_entry(entry)

    def _write_entry(self, entry: PolicyAuditEntry) -> None:
        """Write an entry to the audit log.

        Args:
            entry: Audit entry to write.
        """
        try:
            self._ensure_file_open()

            if self._file is None:
                return

            # Serialize to JSON
            json_str = entry.model_dump_json()
            self._file.write(json_str + "\n")
            self._file.flush()

            self._entry_count += 1

            # Check for rotation
            if self._entry_count >= self._max_entries:
                self._rotate_log()

        except Exception as e:
            logger.warning(f"Failed to write audit entry: {e}")

    def _ensure_file_open(self) -> None:
        """Ensure the audit file is open."""
        if self._file is not None:
            return

        try:
            # Ensure directory exists
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)

            # Open file in append mode
            self._file = open(self._audit_path, "a", encoding="utf-8")

            # Count existing entries
            if self._audit_path.exists():
                try:
                    with open(self._audit_path, encoding="utf-8") as f:
                        self._entry_count = sum(1 for _ in f)
                except Exception:
                    self._entry_count = 0

        except Exception as e:
            logger.warning(f"Failed to open audit log: {e}")
            self._file = None

    def _rotate_log(self) -> None:
        """Rotate the audit log.

        Keeps the last half of entries to maintain some history.
        """
        if self._file is not None:
            self._file.close()
            self._file = None

        try:
            if not self._audit_path.exists():
                return

            # Read all entries
            with open(self._audit_path, encoding="utf-8") as f:
                lines = f.readlines()

            # Keep last half
            keep_count = len(lines) // 2
            kept_lines = lines[-keep_count:]

            # Backup old file
            backup_path = self._audit_path.with_suffix(".jsonl.bak")
            self._audit_path.rename(backup_path)

            # Write kept entries
            with open(self._audit_path, "w", encoding="utf-8") as f:
                f.writelines(kept_lines)

            # Remove backup
            backup_path.unlink()

            self._entry_count = len(kept_lines)
            logger.info(f"Rotated audit log, kept {self._entry_count} entries")

        except Exception as e:
            logger.warning(f"Failed to rotate audit log: {e}")

    def close(self) -> None:
        """Close the audit log file."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def read_entries(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PolicyAuditEntry]:
        """Read audit entries from the log.

        Args:
            limit: Maximum entries to read.
            offset: Number of entries to skip from the end.

        Returns:
            List of audit entries (most recent first).
        """
        entries: list[PolicyAuditEntry] = []

        if not self._audit_path.exists():
            return entries

        try:
            with open(self._audit_path, encoding="utf-8") as f:
                lines = f.readlines()

            # Apply offset and limit (from end)
            start = max(0, len(lines) - offset - limit)
            end = len(lines) - offset

            for line in reversed(lines[start:end]):
                try:
                    data = json.loads(line)
                    entry = PolicyAuditEntry.model_validate(data)
                    entries.append(entry)
                except Exception:
                    continue  # Skip malformed entries

            return entries

        except Exception as e:
            logger.warning(f"Failed to read audit entries: {e}")
            return []

    def search_entries(
        self,
        command: str | None = None,
        path: str | None = None,
        effect: str | None = None,
        limit: int = 100,
    ) -> list[PolicyAuditEntry]:
        """Search audit entries by criteria.

        Args:
            command: Command pattern to search for.
            path: Path pattern to search for.
            effect: Effect to filter by.
            limit: Maximum entries to return.

        Returns:
            Matching audit entries.
        """
        entries: list[PolicyAuditEntry] = []

        if not self._audit_path.exists():
            return entries

        try:
            with open(self._audit_path, encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    if len(entries) >= limit:
                        break

                    try:
                        data = json.loads(line)
                        entry = PolicyAuditEntry.model_validate(data)

                        # Apply filters
                        if command and (
                            entry.request.command is None
                            or command not in entry.request.command
                        ):
                            continue

                        if path and (
                            entry.request.path is None
                            or path not in entry.request.path
                        ):
                            continue

                        if effect and entry.result.effect.value != effect:
                            continue

                        entries.append(entry)

                    except Exception:
                        continue

            return entries

        except Exception as e:
            logger.warning(f"Failed to search audit entries: {e}")
            return []


# =============================================================================
# Module-level Functions
# =============================================================================

_logger: PolicyAuditLogger | None = None


def get_audit_logger() -> PolicyAuditLogger:
    """Get the shared audit logger instance.

    Returns:
        PolicyAuditLogger singleton.
    """
    global _logger
    if _logger is None:
        _logger = PolicyAuditLogger()
    return _logger


def log_evaluation(
    request: PolicyEvaluationRequest,
    result: PolicyEvaluationResult,
    user_confirmed: bool | None = None,
    justification: str | None = None,
) -> None:
    """Log a policy evaluation (convenience function).

    Args:
        request: The evaluation request.
        result: The evaluation result.
        user_confirmed: Whether user confirmed.
        justification: User justification.
    """
    get_audit_logger().log_evaluation(
        request=request,
        result=result,
        user_confirmed=user_confirmed,
        justification=justification,
    )


def read_audit_entries(
    limit: int = 100,
    offset: int = 0,
) -> list[PolicyAuditEntry]:
    """Read audit entries (convenience function).

    Args:
        limit: Maximum entries.
        offset: Entries to skip.

    Returns:
        List of audit entries.
    """
    return get_audit_logger().read_entries(limit=limit, offset=offset)
