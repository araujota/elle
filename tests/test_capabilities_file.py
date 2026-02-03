"""Tests for file operation capabilities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pydantic
import pytest

from elle.capabilities.core.file import (
    FileCopyCapability,
    FileCopyInput,
    FileDeleteCapability,
    FileDeleteInput,
    FileDiffCapability,
    FileDiffInput,
    FileReadCapability,
    FileReadInput,
    FileWatchSnapshotCapability,
    FileWatchSnapshotInput,
    FileWriteCapability,
    FileWriteInput,
    _assess_path_risk,
    _file_hash,
    _is_forbidden_path,
    _is_sensitive_path,
    _snapshot_store,
)

# =============================================================================
# Helper utilities
# =============================================================================


def _sha256(content: str) -> str:
    """Return the SHA256 hex digest for a UTF-8 string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# =============================================================================
# TestPathHelpers
# =============================================================================


class TestPathHelpers:
    """Tests for the module-level path helper functions."""

    def test_forbidden_path_exact_match(self):
        """Exact match against FORBIDDEN_PATHS returns True."""
        with patch.object(Path, "resolve", return_value=Path("/etc/shadow")):
            assert _is_forbidden_path(Path("/etc/shadow")) is True

    def test_forbidden_path_child(self):
        """A child of a forbidden directory is also forbidden."""
        with patch.object(Path, "resolve", return_value=Path("/boot/grub/grub.cfg")):
            assert _is_forbidden_path(Path("/boot/grub/grub.cfg")) is True

    def test_forbidden_path_safe_path(self):
        """A safe path is not forbidden."""
        with patch.object(Path, "resolve", return_value=Path("/tmp/safe.txt")):
            assert _is_forbidden_path(Path("/tmp/safe.txt")) is False

    def test_sensitive_path_exact_match(self):
        """Exact match against SENSITIVE_PATHS returns True."""
        with patch.object(Path, "resolve", return_value=Path("/etc")):
            assert _is_sensitive_path(Path("/etc")) is True

    def test_sensitive_path_child(self):
        """A child of a sensitive directory is also sensitive."""
        with patch.object(Path, "resolve", return_value=Path("/etc/nginx/nginx.conf")):
            assert _is_sensitive_path(Path("/etc/nginx/nginx.conf")) is True

    def test_sensitive_path_safe_path(self):
        """A safe path is not sensitive."""
        with patch.object(Path, "resolve", return_value=Path("/tmp/data.txt")):
            assert _is_sensitive_path(Path("/tmp/data.txt")) is False

    def test_assess_path_risk_critical_for_forbidden(self):
        """Forbidden paths are classified as critical risk."""
        with patch("elle.capabilities.core.file._is_forbidden_path", return_value=True):
            assert _assess_path_risk(Path("/etc/shadow")) == "critical"

    def test_assess_path_risk_high_for_sensitive(self):
        """Sensitive paths are classified as high risk."""
        with patch("elle.capabilities.core.file._is_forbidden_path", return_value=False):
            with patch("elle.capabilities.core.file._is_sensitive_path", return_value=True):
                assert _assess_path_risk(Path("/etc/nginx/nginx.conf")) == "high"

    def test_assess_path_risk_low_for_tmp(self):
        """Paths under /tmp are classified as low risk."""
        with patch("elle.capabilities.core.file._is_forbidden_path", return_value=False):
            with patch("elle.capabilities.core.file._is_sensitive_path", return_value=False):
                assert _assess_path_risk(Path("/tmp/myfile.txt")) == "low"

    def test_assess_path_risk_low_for_var_tmp(self):
        """Paths under /var/tmp are classified as low risk."""
        with patch("elle.capabilities.core.file._is_forbidden_path", return_value=False):
            with patch("elle.capabilities.core.file._is_sensitive_path", return_value=False):
                assert _assess_path_risk(Path("/var/tmp/scratch")) == "low"

    def test_assess_path_risk_medium_default(self):
        """Regular paths outside tmp and sensitive areas are medium risk."""
        with patch("elle.capabilities.core.file._is_forbidden_path", return_value=False):
            with patch("elle.capabilities.core.file._is_sensitive_path", return_value=False):
                assert _assess_path_risk(Path("/opt/myapp/config.txt")) == "medium"

    def test_file_hash_returns_sha256(self):
        """_file_hash returns the correct SHA256 digest."""
        content = b"hello world"
        expected = hashlib.sha256(content).hexdigest()
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_bytes.return_value = content
        assert _file_hash(mock_path) == expected

    def test_file_hash_nonexistent(self):
        """_file_hash returns None for a nonexistent path."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False
        assert _file_hash(mock_path) is None

    def test_file_hash_read_error(self):
        """_file_hash returns None when read fails."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_bytes.side_effect = PermissionError("denied")
        assert _file_hash(mock_path) is None


# =============================================================================
# TestFileReadCapability
# =============================================================================


class TestFileReadCapability:
    """Tests for file.read capability."""

    def setup_method(self):
        self.cap = FileReadCapability()

    def test_spec_metadata(self):
        """Spec has correct name, domain, and risk level."""
        spec = self.cap.spec
        assert spec.name == "file.read"
        assert spec.domain == "file"
        assert spec.risk == "none"
        assert spec.idempotent is True
        assert spec.requires_privilege is False

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_file_not_exists(self, mock_path_cls):
        """Dry run returns invalid when file does not exist."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileReadInput(path="/tmp/nonexistent.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "does not exist" in result.validation_errors[0]

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_not_a_file(self, mock_path_cls):
        """Dry run returns invalid when path is not a regular file."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.is_file.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileReadInput(path="/tmp/somedir")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "not a file" in result.validation_errors[0]

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_file_too_large(self, mock_path_cls):
        """Dry run returns invalid when file exceeds max_size_bytes."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.is_file.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 20 * 1024 * 1024  # 20MB
        mock_p.stat.return_value = mock_stat
        mock_path_cls.return_value = mock_p

        inp = FileReadInput(path="/tmp/large.bin", max_size_bytes=10 * 1024 * 1024)
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "too large" in result.validation_errors[0]

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_success(self, mock_path_cls):
        """Dry run succeeds for a valid readable file."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.is_file.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 100
        mock_p.stat.return_value = mock_stat
        mock_path_cls.return_value = mock_p

        inp = FileReadInput(path="/tmp/test.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "none"
        assert "100 bytes" in result.preview_text

    @patch("elle.capabilities.core.file.Path")
    def test_run_success(self, mock_path_cls):
        """Run reads file content and returns correct hash."""
        content = "Hello, world!"
        mock_p = MagicMock()
        mock_p.read_text.return_value = content
        mock_path_cls.return_value = mock_p

        inp = FileReadInput(path="/tmp/test.txt")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.content == content
        assert result.output.sha256 == _sha256(content)
        assert result.output.size_bytes == len(content.encode("utf-8"))
        assert result.output.path == "/tmp/test.txt"

    @patch("elle.capabilities.core.file.Path")
    def test_run_permission_error(self, mock_path_cls):
        """Run returns failure when read raises PermissionError."""
        mock_p = MagicMock()
        mock_p.read_text.side_effect = PermissionError("Permission denied")
        mock_path_cls.return_value = mock_p

        inp = FileReadInput(path="/root/secret.txt")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Permission denied" in result.error
        assert result.evidence.rationale == "Failed to read /root/secret.txt"


# =============================================================================
# TestFileWriteCapability
# =============================================================================


class TestFileWriteCapability:
    """Tests for file.write capability."""

    def setup_method(self):
        self.cap = FileWriteCapability()

    def test_spec_metadata(self):
        """Spec has correct name, domain, and risk level."""
        spec = self.cap.spec
        assert spec.name == "file.write"
        assert spec.domain == "file"
        assert spec.risk == "medium"
        assert spec.trust_level == "core"

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=True)
    def test_dry_run_forbidden_path(self, _):
        """Dry run rejects writes to forbidden paths."""
        inp = FileWriteInput(path="/etc/shadow", content="bad")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "forbidden" in result.validation_errors[0]

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=False)
    @patch("elle.capabilities.core.file._assess_path_risk", return_value="low")
    @patch("elle.capabilities.core.file.os.access", return_value=True)
    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_existing_file_generates_diff(self, mock_path_cls, *_):
        """Dry run generates a unified diff when the file already exists."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = "old content\n"
        mock_p.name = "test.txt"
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/test.txt", content="new content\n")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.diff is not None
        assert "old content" in result.diff or "new content" in result.diff

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=False)
    @patch("elle.capabilities.core.file._assess_path_risk", return_value="low")
    @patch("elle.capabilities.core.file.os.access", return_value=True)
    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_new_file(self, mock_path_cls, *_):
        """Dry run shows new-file preview when file does not exist."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_p.parent = MagicMock()
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/newfile.txt", content="hello")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.diff is not None
        assert "new file" in result.diff

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=False)
    @patch("elle.capabilities.core.file._assess_path_risk", return_value="high")
    @patch("elle.capabilities.core.file.os.access", return_value=True)
    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_high_risk_requires_confirmation(self, mock_path_cls, *_):
        """Dry run requires confirmation for high risk paths."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_p.parent = MagicMock()
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/etc/nginx/nginx.conf", content="config")
        result = self.cap.dry_run(inp)

        assert result.requires_confirmation is True

    @patch("elle.capabilities.core.file.os.rename")
    @patch("elle.capabilities.core.file.os.close")
    @patch("elle.capabilities.core.file.os.write")
    @patch("tempfile.mkstemp", return_value=(99, "/tmp/.tmp123"))
    @patch("elle.capabilities.core.file.os.chmod")
    @patch("elle.capabilities.core.file.shutil.copy2")
    @patch("elle.capabilities.core.file.Path")
    def test_run_success_with_backup(
        self, mock_path_cls, mock_copy2, mock_chmod, mock_mkstemp, mock_write, mock_close, mock_rename
    ):
        """Run creates backup and writes content successfully."""
        content = "new content"
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.parent.mkdir = MagicMock()
        mock_p.parent.exists.return_value = True
        mock_p.parent.is_symlink.return_value = False
        mock_p.__str__ = MagicMock(return_value="/tmp/test.txt")
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/test.txt", content=content, create_backup=True)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.sha256 == _sha256(content)
        assert result.output.backup_path is not None
        assert result.output.created is False
        mock_copy2.assert_called_once()
        mock_write.assert_called_once_with(99, content.encode("utf-8"))
        mock_rename.assert_called_once()

    @patch("elle.capabilities.core.file.os.rename")
    @patch("elle.capabilities.core.file.os.close")
    @patch("elle.capabilities.core.file.os.write")
    @patch("tempfile.mkstemp", return_value=(99, "/tmp/.tmp456"))
    @patch("elle.capabilities.core.file.os.chmod")
    @patch("elle.capabilities.core.file.Path")
    def test_run_new_file_created(self, mock_path_cls, mock_chmod, mock_mkstemp, mock_write, mock_close, mock_rename):
        """Run marks created=True for new files."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_p.parent.mkdir = MagicMock()
        mock_p.parent.is_symlink.return_value = False
        mock_p.parent.exists.return_value = True
        mock_p.__str__ = MagicMock(return_value="/tmp/new.txt")
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/new.txt", content="hello", create_backup=True)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.created is True
        assert result.output.backup_path is None

    @patch("elle.capabilities.core.file.os.rename")
    @patch("elle.capabilities.core.file.os.close")
    @patch("elle.capabilities.core.file.os.write")
    @patch("tempfile.mkstemp", return_value=(99, "/tmp/.tmp789"))
    @patch("elle.capabilities.core.file.os.chmod")
    @patch("elle.capabilities.core.file.Path")
    def test_run_sets_file_mode(self, mock_path_cls, mock_chmod, mock_mkstemp, mock_write, mock_close, mock_rename):
        """Run sets file permissions when mode is specified."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_p.parent.mkdir = MagicMock()
        mock_p.parent.is_symlink.return_value = False
        mock_p.parent.exists.return_value = True
        mock_p.__str__ = MagicMock(return_value="/tmp/script.sh")
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/script.sh", content="#!/bin/bash", mode=0o755)
        result = self.cap.run(inp)

        assert result.success is True
        mock_chmod.assert_called_once_with(mock_p, 0o755)

    @patch("elle.capabilities.core.file.os.close")
    @patch("elle.capabilities.core.file.os.write", side_effect=OSError("No space left on device"))
    @patch("tempfile.mkstemp", return_value=(99, "/tmp/.tmperr"))
    @patch("elle.capabilities.core.file.Path")
    def test_run_write_error(self, mock_path_cls, mock_mkstemp, mock_write, mock_close):
        """Run returns failure when write raises OSError."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_p.parent.mkdir = MagicMock()
        mock_p.parent.is_symlink.return_value = False
        mock_p.parent.exists.return_value = True
        mock_p.__str__ = MagicMock(return_value="/tmp/test.txt")
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/test.txt", content="data")
        result = self.cap.run(inp)

        assert result.success is False
        assert "No space left" in result.error

    # -- verify --

    @patch("elle.capabilities.core.file.Path")
    def test_verify_success(self, mock_path_cls):
        """Verify passes when content hash matches."""
        content = "verified content"
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = content
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/test.txt", content=content)
        result = self.cap.verify(inp)

        assert result.passed is True
        assert "content hash" in result.checks_performed

    @patch("elle.capabilities.core.file.Path")
    def test_verify_file_not_found(self, mock_path_cls):
        """Verify fails when file does not exist after write."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/test.txt", content="something")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "does not exist" in result.discrepancies[0]

    @patch("elle.capabilities.core.file.Path")
    def test_verify_hash_mismatch(self, mock_path_cls):
        """Verify fails when content hash does not match expected."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = "different content"
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/test.txt", content="expected content")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "hash mismatch" in result.discrepancies[0]

    @patch("elle.capabilities.core.file.Path")
    def test_verify_read_exception(self, mock_path_cls):
        """Verify fails gracefully when read raises."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.side_effect = PermissionError("denied")
        mock_path_cls.return_value = mock_p

        inp = FileWriteInput(path="/tmp/test.txt", content="something")
        result = self.cap.verify(inp)

        assert result.passed is False

    # -- rollback --

    @patch("elle.capabilities.core.file.shutil.copy2")
    def test_rollback_from_backup(self, mock_copy2):
        """Rollback restores from backup path."""
        mock_output = MagicMock()
        mock_output.backup_path = "/tmp/test.txt.bak.123"
        mock_output.created = False
        mock_result = MagicMock()
        mock_result.output = mock_output

        inp = FileWriteInput(path="/tmp/test.txt", content="anything")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is True
        assert "/tmp/test.txt" in rb.rolled_back
        mock_copy2.assert_called_once_with("/tmp/test.txt.bak.123", "/tmp/test.txt")

    @patch("elle.capabilities.core.file.Path")
    def test_rollback_newly_created_file(self, mock_path_cls):
        """Rollback deletes a newly created file when no backup exists."""
        mock_p = MagicMock()
        mock_path_cls.return_value = mock_p
        mock_output = MagicMock()
        mock_output.backup_path = None
        mock_output.created = True
        mock_result = MagicMock()
        mock_result.output = mock_output

        inp = FileWriteInput(path="/tmp/new.txt", content="data")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is True
        mock_p.unlink.assert_called_once()

    def test_rollback_no_output(self):
        """Rollback fails when result has no output."""
        mock_result = MagicMock()
        mock_result.output = None

        inp = FileWriteInput(path="/tmp/test.txt", content="data")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is False
        assert "No output" in rb.error

    def test_rollback_no_backup_not_created(self):
        """Rollback fails when no backup and file was not newly created."""
        mock_output = MagicMock()
        mock_output.backup_path = None
        mock_output.created = False
        mock_result = MagicMock()
        mock_result.output = mock_output

        inp = FileWriteInput(path="/tmp/test.txt", content="data")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is False
        assert "No backup" in rb.error

    @patch("elle.capabilities.core.file.shutil.copy2", side_effect=OSError("disk error"))
    def test_rollback_copy_failure(self, _):
        """Rollback fails gracefully when copy2 raises."""
        mock_output = MagicMock()
        mock_output.backup_path = "/tmp/test.txt.bak.123"
        mock_output.created = False
        mock_result = MagicMock()
        mock_result.output = mock_output

        inp = FileWriteInput(path="/tmp/test.txt", content="data")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is False
        assert "/tmp/test.txt" in rb.failed_to_rollback


# =============================================================================
# TestFileDeleteCapability
# =============================================================================


class TestFileDeleteCapability:
    """Tests for file.delete capability."""

    def setup_method(self):
        self.cap = FileDeleteCapability()

    def test_spec_metadata(self):
        """Spec has correct name, domain, and risk level."""
        spec = self.cap.spec
        assert spec.name == "file.delete"
        assert spec.domain == "file"
        assert spec.risk == "high"

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=True)
    def test_dry_run_forbidden_path(self, _):
        """Dry run rejects deletion of forbidden paths."""
        inp = FileDeleteInput(path="/etc/passwd")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "forbidden" in result.validation_errors[0]

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=False)
    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_file_not_exists(self, mock_path_cls, _):
        """Dry run returns valid with no modifications when file is missing."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/tmp/gone.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert "does not exist" in result.preview_text

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=False)
    @patch("elle.capabilities.core.file._assess_path_risk", return_value="medium")
    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_existing_file(self, mock_path_cls, *_):
        """Dry run shows correct preview for an existing file."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 512
        mock_p.stat.return_value = mock_stat
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/tmp/target.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert "512 bytes" in result.preview_text

    @patch("elle.capabilities.core.file.Path")
    def test_run_file_already_deleted(self, mock_path_cls):
        """Run returns success with size 0 when file does not exist."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/tmp/missing.txt")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.size_bytes == 0

    @patch("elle.capabilities.core.file.shutil.copy2")
    @patch("elle.capabilities.core.file.Path")
    def test_run_success_with_backup(self, mock_path_cls, mock_copy2):
        """Run creates backup and deletes file successfully."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 256
        mock_p.stat.return_value = mock_stat
        mock_p.unlink = MagicMock()
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/tmp/delete_me.txt", create_backup=True)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.size_bytes == 256
        assert result.output.backup_path is not None
        mock_copy2.assert_called_once()
        mock_p.unlink.assert_called_once()

    @patch("elle.capabilities.core.file.Path")
    def test_run_no_backup(self, mock_path_cls):
        """Run deletes file without creating a backup when not requested."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 64
        mock_p.stat.return_value = mock_stat
        mock_p.unlink = MagicMock()
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/tmp/no_backup.txt", create_backup=False)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.backup_path is None

    @patch("elle.capabilities.core.file.Path")
    def test_run_delete_error(self, mock_path_cls):
        """Run returns failure when unlink raises."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 10
        mock_p.stat.return_value = mock_stat
        mock_p.unlink.side_effect = PermissionError("permission denied")
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/root/protected.txt", create_backup=False)
        result = self.cap.run(inp)

        assert result.success is False
        assert "permission denied" in result.error

    # -- verify --

    @patch("elle.capabilities.core.file.Path")
    def test_verify_file_deleted(self, mock_path_cls):
        """Verify passes when file no longer exists."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/tmp/gone.txt")
        result = self.cap.verify(inp)

        assert result.passed is True

    @patch("elle.capabilities.core.file.Path")
    def test_verify_file_still_exists(self, mock_path_cls):
        """Verify fails when file still exists after delete."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_path_cls.return_value = mock_p

        inp = FileDeleteInput(path="/tmp/stubborn.txt")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "still exists" in result.discrepancies[0]

    # -- rollback --

    @patch("elle.capabilities.core.file.shutil.copy2")
    def test_rollback_from_backup(self, mock_copy2):
        """Rollback restores deleted file from backup."""
        mock_output = MagicMock()
        mock_output.backup_path = "/tmp/delete_me.txt.deleted.123"
        mock_result = MagicMock()
        mock_result.output = mock_output

        inp = FileDeleteInput(path="/tmp/delete_me.txt")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is True
        mock_copy2.assert_called_once()

    def test_rollback_no_backup(self):
        """Rollback fails when there is no backup available."""
        mock_output = MagicMock()
        mock_output.backup_path = None
        mock_result = MagicMock()
        mock_result.output = mock_output

        inp = FileDeleteInput(path="/tmp/delete_me.txt")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is False
        assert "No backup" in rb.error

    def test_rollback_no_output(self):
        """Rollback fails when result has no output."""
        mock_result = MagicMock()
        mock_result.output = None

        inp = FileDeleteInput(path="/tmp/x.txt")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is False

    @patch("elle.capabilities.core.file.shutil.copy2", side_effect=OSError("error"))
    def test_rollback_restore_failure(self, _):
        """Rollback fails gracefully when copy2 raises during restore."""
        mock_output = MagicMock()
        mock_output.backup_path = "/tmp/file.deleted.999"
        mock_result = MagicMock()
        mock_result.output = mock_output

        inp = FileDeleteInput(path="/tmp/file.txt")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is False
        assert "/tmp/file.txt" in rb.failed_to_rollback


# =============================================================================
# TestFileCopyCapability
# =============================================================================


class TestFileCopyCapability:
    """Tests for file.copy capability."""

    def setup_method(self):
        self.cap = FileCopyCapability()

    def test_spec_metadata(self):
        """Spec has correct name, domain, and risk level."""
        spec = self.cap.spec
        assert spec.name == "file.copy"
        assert spec.domain == "file"
        assert spec.risk == "low"

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_source_not_exists(self, mock_path_cls):
        """Dry run fails when source does not exist."""
        mock_src = MagicMock()
        mock_src.exists.return_value = False
        mock_dst = MagicMock()
        # First call is for source, second for destination
        mock_path_cls.side_effect = [mock_src, mock_dst]

        inp = FileCopyInput(source="/tmp/missing.txt", destination="/tmp/copy.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "Source does not exist" in result.validation_errors[0]

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_destination_exists_no_overwrite(self, mock_path_cls):
        """Dry run fails when destination exists and overwrite is False."""
        mock_src = MagicMock()
        mock_src.exists.return_value = True
        mock_dst = MagicMock()
        mock_dst.exists.return_value = True
        mock_path_cls.side_effect = [mock_src, mock_dst]

        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt", overwrite=False)
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "overwrite=False" in result.validation_errors[0]

    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=True)
    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_forbidden_destination(self, mock_path_cls, _):
        """Dry run fails when destination is a forbidden path."""
        mock_src = MagicMock()
        mock_src.exists.return_value = True
        mock_dst = MagicMock()
        mock_dst.exists.return_value = False
        mock_path_cls.side_effect = [mock_src, mock_dst]

        inp = FileCopyInput(source="/tmp/a.txt", destination="/etc/shadow")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "forbidden" in result.validation_errors[0]

    @patch("elle.capabilities.core.file._is_sensitive_path", return_value=False)
    @patch("elle.capabilities.core.file._assess_path_risk", return_value="low")
    @patch("elle.capabilities.core.file._is_forbidden_path", return_value=False)
    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_success(self, mock_path_cls, *_):
        """Dry run succeeds for a valid copy operation."""
        mock_src = MagicMock()
        mock_src.exists.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 1024
        mock_src.stat.return_value = mock_stat

        mock_dst = MagicMock()
        mock_dst.exists.return_value = False
        mock_path_cls.side_effect = [mock_src, mock_dst]

        inp = FileCopyInput(source="/tmp/src.txt", destination="/tmp/dst.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert "1024 bytes" in result.preview_text

    @patch("elle.capabilities.core.file._file_hash", return_value="abc123hash")
    @patch("elle.capabilities.core.file.shutil.copy2")
    @patch("elle.capabilities.core.file.Path")
    def test_run_success(self, mock_path_cls, mock_copy2, _):
        """Run copies file successfully."""
        mock_src = MagicMock()
        mock_dst = MagicMock()
        mock_dst.parent.mkdir = MagicMock()
        mock_dst.parent.is_symlink.return_value = False
        mock_dst.parent.exists.return_value = True
        mock_stat = MagicMock()
        mock_stat.st_size = 512
        mock_dst.stat.return_value = mock_stat
        mock_path_cls.side_effect = [mock_src, mock_dst]

        inp = FileCopyInput(source="/tmp/src.txt", destination="/tmp/dst.txt")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.size_bytes == 512
        assert result.output.sha256 == "abc123hash"
        mock_copy2.assert_called_once_with(mock_src, mock_dst)

    @patch("elle.capabilities.core.file.shutil.copy2", side_effect=OSError("copy failed"))
    @patch("elle.capabilities.core.file.Path")
    def test_run_copy_error(self, mock_path_cls, _):
        """Run returns failure when copy2 raises."""
        mock_src = MagicMock()
        mock_dst = MagicMock()
        mock_dst.parent.mkdir = MagicMock()
        mock_dst.parent.is_symlink.return_value = False
        mock_dst.parent.exists.return_value = True
        mock_path_cls.side_effect = [mock_src, mock_dst]

        inp = FileCopyInput(source="/tmp/src.txt", destination="/tmp/dst.txt")
        result = self.cap.run(inp)

        assert result.success is False
        assert "copy failed" in result.error

    # -- verify --

    @patch("elle.capabilities.core.file._file_hash")
    @patch("elle.capabilities.core.file.Path")
    def test_verify_copy_success(self, mock_path_cls, mock_hash):
        """Verify passes when source and destination hashes match."""
        mock_src = MagicMock()
        mock_dst = MagicMock()
        mock_dst.exists.return_value = True
        mock_path_cls.side_effect = [mock_src, mock_dst]
        mock_hash.side_effect = ["samehash", "samehash"]

        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt")
        result = self.cap.verify(inp)

        assert result.passed is True

    @patch("elle.capabilities.core.file.Path")
    def test_verify_destination_missing(self, mock_path_cls):
        """Verify fails when destination does not exist after copy."""
        mock_src = MagicMock()
        mock_dst = MagicMock()
        mock_dst.exists.return_value = False
        mock_path_cls.side_effect = [mock_src, mock_dst]

        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "does not exist" in result.discrepancies[0]

    @patch("elle.capabilities.core.file._file_hash")
    @patch("elle.capabilities.core.file.Path")
    def test_verify_hash_mismatch(self, mock_path_cls, mock_hash):
        """Verify fails when source and destination hashes differ."""
        mock_src = MagicMock()
        mock_dst = MagicMock()
        mock_dst.exists.return_value = True
        mock_path_cls.side_effect = [mock_src, mock_dst]
        mock_hash.side_effect = ["hash_a", "hash_b"]

        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "hash mismatch" in result.discrepancies[0]

    # -- rollback --

    @patch("elle.capabilities.core.file.Path")
    def test_rollback_deletes_copy(self, mock_path_cls):
        """Rollback removes the copied file."""
        mock_dst = MagicMock()
        mock_dst.exists.return_value = True
        mock_path_cls.return_value = mock_dst

        mock_result = MagicMock()
        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is True
        mock_dst.unlink.assert_called_once()

    @patch("elle.capabilities.core.file.Path")
    def test_rollback_destination_already_gone(self, mock_path_cls):
        """Rollback succeeds even when destination is already gone."""
        mock_dst = MagicMock()
        mock_dst.exists.return_value = False
        mock_path_cls.return_value = mock_dst

        mock_result = MagicMock()
        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is True
        mock_dst.unlink.assert_not_called()

    @patch("elle.capabilities.core.file.Path")
    def test_rollback_unlink_error(self, mock_path_cls):
        """Rollback fails when unlink raises."""
        mock_dst = MagicMock()
        mock_dst.exists.return_value = True
        mock_dst.unlink.side_effect = OSError("busy")
        mock_path_cls.return_value = mock_dst

        mock_result = MagicMock()
        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt")
        rb = self.cap.rollback(inp, mock_result)

        assert rb.success is False
        assert "/tmp/b.txt" in rb.failed_to_rollback


# =============================================================================
# TestFileDiffCapability
# =============================================================================


class TestFileDiffCapability:
    """Tests for file.diff capability."""

    def setup_method(self):
        self.cap = FileDiffCapability()

    def test_spec_metadata(self):
        """Spec has correct name and risk level."""
        spec = self.cap.spec
        assert spec.name == "file.diff"
        assert spec.risk == "none"
        assert spec.domain == "file"

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_file_not_exists(self, mock_path_cls):
        """Dry run returns invalid when file does not exist."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileDiffInput(path="/tmp/missing.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_success(self, mock_path_cls):
        """Dry run succeeds for an existing file."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_path_cls.return_value = mock_p

        inp = FileDiffInput(path="/tmp/test.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "none"

    @patch("elle.capabilities.core.file.Path")
    def test_run_file_not_exists(self, mock_path_cls):
        """Run returns failure when file does not exist."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileDiffInput(path="/tmp/gone.txt")
        result = self.cap.run(inp)

        assert result.success is False
        assert "does not exist" in result.error

    @patch("elle.capabilities.core.file.Path")
    def test_run_no_changes(self, mock_path_cls):
        """Run returns has_changes=False when content is identical."""
        content = "same content"
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = content
        mock_p.name = "test.txt"
        mock_path_cls.return_value = mock_p

        inp = FileDiffInput(path="/tmp/test.txt", previous_content=content)
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.has_changes is False
        assert result.output.lines_added == 0

    @patch("elle.capabilities.core.file.Path")
    def test_run_with_changes(self, mock_path_cls):
        """Run detects changes and counts added/removed lines."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = "line1\nline2\nline3\n"
        mock_p.name = "test.txt"
        mock_path_cls.return_value = mock_p

        inp = FileDiffInput(path="/tmp/test.txt", previous_content="line1\nold_line\n")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.has_changes is True
        assert result.output.lines_added > 0 or result.output.lines_removed > 0
        assert len(result.output.diff) > 0

    @patch("elle.capabilities.core.file.Path")
    def test_run_empty_previous_content(self, mock_path_cls):
        """Run treats missing previous_content as empty string (all new)."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = "new content\n"
        mock_p.name = "test.txt"
        mock_path_cls.return_value = mock_p

        # No previous_content and no snapshot_id - defaults to empty string
        inp = FileDiffInput(path="/tmp/test.txt")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.has_changes is True
        assert result.output.lines_added > 0

    @patch("elle.capabilities.core.file.Path")
    def test_run_snapshot_not_found(self, mock_path_cls):
        """Run fails when snapshot_id is specified but not found."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = "content"
        mock_path_cls.return_value = mock_p

        # Ensure snapshot store is empty for this test
        _snapshot_store.clear()

        inp = FileDiffInput(path="/tmp/test.txt", snapshot_id="nonexistent_snap")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Snapshot not found" in result.error

    @patch("elle.capabilities.core.file.Path")
    def test_run_snapshot_found(self, mock_path_cls):
        """Run succeeds using content from a snapshot."""
        old_content = "old snapshot content\n"
        new_content = "new file content\n"

        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = new_content
        mock_p.name = "test.txt"
        mock_path_cls.return_value = mock_p

        # Pre-populate snapshot store (entries are (timestamp, data) tuples)
        import time as _time

        _snapshot_store["test_snap"] = (
            _time.time(),
            {
                "path": "/tmp/test.txt",
                "content": old_content,
                "sha256": _sha256(old_content),
            },
        )

        try:
            inp = FileDiffInput(path="/tmp/test.txt", snapshot_id="test_snap")
            result = self.cap.run(inp)

            assert result.success is True
            assert result.output.has_changes is True
        finally:
            _snapshot_store.pop("test_snap", None)

    @patch("elle.capabilities.core.file.Path")
    def test_run_read_exception(self, mock_path_cls):
        """Run handles read exceptions gracefully."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
        mock_path_cls.return_value = mock_p

        inp = FileDiffInput(path="/tmp/binary.bin", previous_content="old")
        result = self.cap.run(inp)

        assert result.success is False


# =============================================================================
# TestFileWatchSnapshotCapability
# =============================================================================


class TestFileWatchSnapshotCapability:
    """Tests for file.watch_snapshot capability."""

    def setup_method(self):
        self.cap = FileWatchSnapshotCapability()

    def test_spec_metadata(self):
        """Spec has correct name and risk level."""
        spec = self.cap.spec
        assert spec.name == "file.watch_snapshot"
        assert spec.risk == "none"

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_file_not_exists(self, mock_path_cls):
        """Dry run fails when the file does not exist."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileWatchSnapshotInput(path="/tmp/missing.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "does not exist" in result.validation_errors[0]

    @patch("elle.capabilities.core.file.Path")
    def test_dry_run_success(self, mock_path_cls):
        """Dry run succeeds for an existing file."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_path_cls.return_value = mock_p

        inp = FileWatchSnapshotInput(path="/tmp/test.txt")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert "snapshot" in result.preview_text.lower()

    @patch("elle.capabilities.core.file.Path")
    def test_run_file_not_exists(self, mock_path_cls):
        """Run fails when file does not exist."""
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_path_cls.return_value = mock_p

        inp = FileWatchSnapshotInput(path="/tmp/nope.txt")
        result = self.cap.run(inp)

        assert result.success is False
        assert "does not exist" in result.error

    @patch("elle.capabilities.core.file.Path")
    def test_run_success_auto_id(self, mock_path_cls):
        """Run creates snapshot with auto-generated ID."""
        content = "snapshot content"
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = content
        mock_p.name = "watch.txt"
        mock_path_cls.return_value = mock_p

        inp = FileWatchSnapshotInput(path="/tmp/watch.txt")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.sha256 == _sha256(content)
        assert result.output.path == "/tmp/watch.txt"
        assert result.output.snapshot_id.startswith("watch.txt_")
        # Verify it was stored
        assert result.output.snapshot_id in _snapshot_store

        # Clean up
        _snapshot_store.pop(result.output.snapshot_id, None)

    @patch("elle.capabilities.core.file.Path")
    def test_run_success_custom_id(self, mock_path_cls):
        """Run creates snapshot with a user-provided custom ID."""
        content = "custom id snapshot"
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.return_value = content
        mock_p.name = "custom.txt"
        mock_path_cls.return_value = mock_p

        inp = FileWatchSnapshotInput(path="/tmp/custom.txt", snapshot_id="my_snap_id")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.snapshot_id == "my_snap_id"
        assert _snapshot_store["my_snap_id"][1]["content"] == content

        # Clean up
        _snapshot_store.pop("my_snap_id", None)

    @patch("elle.capabilities.core.file.Path")
    def test_run_read_error(self, mock_path_cls):
        """Run fails gracefully when reading the file raises an exception."""
        mock_p = MagicMock()
        mock_p.exists.return_value = True
        mock_p.read_text.side_effect = PermissionError("denied")
        mock_path_cls.return_value = mock_p

        inp = FileWatchSnapshotInput(path="/root/secret.txt")
        result = self.cap.run(inp)

        assert result.success is False
        assert "denied" in result.error


# =============================================================================
# TestInputValidation
# =============================================================================


class TestInputValidation:
    """Pydantic validation tests for file capability input models."""

    def test_file_read_input_max_size_positive(self):
        """FileReadInput rejects max_size_bytes < 1."""
        with pytest.raises(pydantic.ValidationError):
            FileReadInput(path="/tmp/file.txt", max_size_bytes=0)

    def test_file_read_input_default_max_size(self):
        """FileReadInput has a sensible default max_size_bytes."""
        inp = FileReadInput(path="/tmp/file.txt")
        assert inp.max_size_bytes == 10 * 1024 * 1024

    def test_file_write_input_frozen(self):
        """FileWriteInput is frozen (immutable)."""
        inp = FileWriteInput(path="/tmp/test.txt", content="hello")
        with pytest.raises(pydantic.ValidationError):
            inp.path = "/tmp/other.txt"

    def test_file_copy_input_overwrite_default(self):
        """FileCopyInput defaults to overwrite=False."""
        inp = FileCopyInput(source="/tmp/a.txt", destination="/tmp/b.txt")
        assert inp.overwrite is False

    def test_file_diff_input_context_lines_range(self):
        """FileDiffInput rejects context_lines outside 0-10 range."""
        with pytest.raises(pydantic.ValidationError):
            FileDiffInput(path="/tmp/f.txt", context_lines=11)
        with pytest.raises(pydantic.ValidationError):
            FileDiffInput(path="/tmp/f.txt", context_lines=-1)

    def test_file_diff_input_defaults(self):
        """FileDiffInput has correct defaults."""
        inp = FileDiffInput(path="/tmp/f.txt")
        assert inp.context_lines == 3
        assert inp.previous_content is None
        assert inp.snapshot_id is None

    def test_file_delete_input_backup_default(self):
        """FileDeleteInput defaults create_backup to True."""
        inp = FileDeleteInput(path="/tmp/f.txt")
        assert inp.create_backup is True


# =============================================================================
# TestFileCapabilitiesExport
# =============================================================================


class TestFileCapabilitiesExport:
    """Tests for the FILE_CAPABILITIES export list."""

    def test_all_capabilities_exported(self):
        """FILE_CAPABILITIES contains all six capability classes."""
        from elle.capabilities.core.file import FILE_CAPABILITIES

        names = [cls().spec.name for cls in FILE_CAPABILITIES]
        assert "file.read" in names
        assert "file.write" in names
        assert "file.delete" in names
        assert "file.copy" in names
        assert "file.diff" in names
        assert "file.watch_snapshot" in names
        assert len(FILE_CAPABILITIES) == 6

    def test_all_capabilities_have_core_trust(self):
        """All file capabilities declare core trust level."""
        from elle.capabilities.core.file import FILE_CAPABILITIES

        for cls in FILE_CAPABILITIES:
            assert cls().spec.trust_level == "core"
