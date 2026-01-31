from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

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

# =========================================================================
# Path helper tests
# =========================================================================


class TestPathHelpers:
    def test_is_forbidden_path_exact(self) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/etc/passwd")):
            assert _is_forbidden_path(Path("/etc/passwd")) is True

    def test_is_forbidden_path_subpath(self) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/boot/grub/grub.cfg")):
            assert _is_forbidden_path(Path("/boot/grub/grub.cfg")) is True

    def test_is_forbidden_path_safe(self, tmp_path: Path) -> None:
        safe = tmp_path / "test.txt"
        assert _is_forbidden_path(safe) is False

    def test_is_sensitive_path_etc(self) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/etc/nginx/nginx.conf")):
            assert _is_sensitive_path(Path("/etc/nginx/nginx.conf")) is True

    def test_is_sensitive_path_home(self) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/home/user/file.txt")):
            assert _is_sensitive_path(Path("/home/user/file.txt")) is True

    def test_is_sensitive_path_safe(self, tmp_path: Path) -> None:
        safe = tmp_path / "file.txt"
        assert _is_sensitive_path(safe) is False

    def test_assess_path_risk_forbidden(self) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/etc/passwd")):
            assert _assess_path_risk(Path("/etc/passwd")) == "critical"

    def test_assess_path_risk_sensitive(self) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/etc/nginx/nginx.conf")):
            assert _assess_path_risk(Path("/etc/nginx/nginx.conf")) == "high"

    def test_assess_path_risk_tmp(self) -> None:
        assert _assess_path_risk(Path("/tmp/test.txt")) == "low"

    def test_assess_path_risk_medium(self) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/opt/myapp/test.txt")):
            assert _assess_path_risk(Path("/opt/myapp/test.txt")) == "medium"

    def test_file_hash_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        expected = hashlib.sha256(b"hello").hexdigest()
        assert _file_hash(f) == expected

    def test_file_hash_nonexistent(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.txt"
        assert _file_hash(f) is None


# =========================================================================
# FileReadCapability tests
# =========================================================================


class TestFileReadCapability:
    @pytest.fixture()
    def cap(self) -> FileReadCapability:
        return FileReadCapability()

    def test_spec(self, cap: FileReadCapability) -> None:
        assert cap.spec.name == "file.read"
        assert cap.spec.domain == "file"
        assert cap.spec.risk == "none"

    def test_dry_run_file_exists(self, cap: FileReadCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = cap.dry_run(FileReadInput(path=str(f)))
        assert result.is_valid is True

    def test_dry_run_file_missing(self, cap: FileReadCapability, tmp_path: Path) -> None:
        result = cap.dry_run(FileReadInput(path=str(tmp_path / "missing.txt")))
        assert result.is_valid is False

    def test_dry_run_not_a_file(self, cap: FileReadCapability, tmp_path: Path) -> None:
        result = cap.dry_run(FileReadInput(path=str(tmp_path)))
        assert result.is_valid is False

    def test_dry_run_too_large(self, cap: FileReadCapability, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("x" * 100)
        result = cap.dry_run(FileReadInput(path=str(f), max_size_bytes=10))
        assert result.is_valid is False

    def test_run_success(self, cap: FileReadCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = cap.run(FileReadInput(path=str(f)))
        assert result.success is True
        assert result.output.content == "hello world"
        assert result.output.size_bytes == len(b"hello world")

    def test_run_failure(self, cap: FileReadCapability, tmp_path: Path) -> None:
        result = cap.run(FileReadInput(path=str(tmp_path / "missing.txt")))
        assert result.success is False
        assert result.error is not None


# =========================================================================
# FileWriteCapability tests
# =========================================================================


class TestFileWriteCapability:
    @pytest.fixture()
    def cap(self) -> FileWriteCapability:
        return FileWriteCapability()

    def test_spec(self, cap: FileWriteCapability) -> None:
        assert cap.spec.name == "file.write"
        assert cap.spec.risk == "medium"

    def test_dry_run_forbidden(self, cap: FileWriteCapability) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/etc/passwd")):
            result = cap.dry_run(FileWriteInput(path="/etc/passwd", content="x"))
            assert result.is_valid is False

    def test_dry_run_new_file(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "new.txt"
        result = cap.dry_run(FileWriteInput(path=str(target), content="hello"))
        assert result.is_valid is True

    def test_dry_run_existing_file(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "existing.txt"
        target.write_text("old")
        result = cap.dry_run(FileWriteInput(path=str(target), content="new"))
        assert result.is_valid is True
        assert result.diff is not None

    def test_run_creates_file(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "new.txt"
        result = cap.run(FileWriteInput(path=str(target), content="hello"))
        assert result.success is True
        assert result.output.created is True
        assert target.read_text() == "hello"

    def test_run_creates_backup(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "existing.txt"
        target.write_text("old content")
        result = cap.run(FileWriteInput(path=str(target), content="new content"))
        assert result.success is True
        assert result.output.backup_path is not None
        assert Path(result.output.backup_path).exists()

    def test_run_no_backup(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "existing.txt"
        target.write_text("old")
        result = cap.run(FileWriteInput(path=str(target), content="new", create_backup=False))
        assert result.success is True
        assert result.output.backup_path is None

    def test_run_sets_mode(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "script.sh"
        result = cap.run(FileWriteInput(path=str(target), content="#!/bin/sh", mode=0o755))
        assert result.success is True
        mode = target.stat().st_mode & 0o777
        assert mode == 0o755

    def test_verify_success(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("content")
        result = cap.verify(FileWriteInput(path=str(target), content="content"))
        assert result.passed is True

    def test_verify_hash_mismatch(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("actual")
        result = cap.verify(FileWriteInput(path=str(target), content="expected"))
        assert result.passed is False

    def test_verify_file_missing(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        result = cap.verify(FileWriteInput(path=str(tmp_path / "missing.txt"), content="x"))
        assert result.passed is False

    def test_rollback_with_backup(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("old")
        run_result = cap.run(FileWriteInput(path=str(target), content="new"))
        rollback_result = cap.rollback(
            FileWriteInput(path=str(target), content="new"),
            run_result,
        )
        assert rollback_result.success is True
        assert target.read_text() == "old"

    def test_rollback_new_file(self, cap: FileWriteCapability, tmp_path: Path) -> None:
        target = tmp_path / "newfile.txt"
        run_result = cap.run(FileWriteInput(path=str(target), content="data"))
        rollback_result = cap.rollback(
            FileWriteInput(path=str(target), content="data"),
            run_result,
        )
        assert rollback_result.success is True
        assert not target.exists()

    def test_rollback_no_output(self, cap: FileWriteCapability) -> None:
        from elle.capabilities.models import CapabilityResult

        run_result = CapabilityResult(success=True, output=None)
        rollback_result = cap.rollback(
            FileWriteInput(path="/tmp/x", content="x"),
            run_result,
        )
        assert rollback_result.success is False


# =========================================================================
# FileDeleteCapability tests
# =========================================================================


class TestFileDeleteCapability:
    @pytest.fixture()
    def cap(self) -> FileDeleteCapability:
        return FileDeleteCapability()

    def test_spec(self, cap: FileDeleteCapability) -> None:
        assert cap.spec.name == "file.delete"
        assert cap.spec.risk == "high"

    def test_dry_run_forbidden(self, cap: FileDeleteCapability) -> None:
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/etc/shadow")):
            result = cap.dry_run(FileDeleteInput(path="/etc/shadow"))
            assert result.is_valid is False

    def test_dry_run_nonexistent(self, cap: FileDeleteCapability, tmp_path: Path) -> None:
        result = cap.dry_run(FileDeleteInput(path=str(tmp_path / "nope.txt")))
        assert result.is_valid is True

    def test_dry_run_existing(self, cap: FileDeleteCapability, tmp_path: Path) -> None:
        f = tmp_path / "deleteme.txt"
        f.write_text("bye")
        result = cap.dry_run(FileDeleteInput(path=str(f)))
        assert result.is_valid is True
        assert result.requires_confirmation is True

    def test_run_file_not_exists(self, cap: FileDeleteCapability, tmp_path: Path) -> None:
        result = cap.run(FileDeleteInput(path=str(tmp_path / "gone.txt")))
        assert result.success is True
        assert result.output.size_bytes == 0

    def test_run_with_backup(self, cap: FileDeleteCapability, tmp_path: Path) -> None:
        f = tmp_path / "deleteme.txt"
        f.write_text("data")
        result = cap.run(FileDeleteInput(path=str(f)))
        assert result.success is True
        assert not f.exists()
        assert result.output.backup_path is not None
        assert Path(result.output.backup_path).exists()

    def test_verify_deleted(self, cap: FileDeleteCapability, tmp_path: Path) -> None:
        result = cap.verify(FileDeleteInput(path=str(tmp_path / "gone.txt")))
        assert result.passed is True

    def test_verify_still_exists(self, cap: FileDeleteCapability, tmp_path: Path) -> None:
        f = tmp_path / "still_here.txt"
        f.write_text("x")
        result = cap.verify(FileDeleteInput(path=str(f)))
        assert result.passed is False

    def test_rollback_with_backup(self, cap: FileDeleteCapability, tmp_path: Path) -> None:
        f = tmp_path / "deleteme.txt"
        f.write_text("restore me")
        run_result = cap.run(FileDeleteInput(path=str(f)))
        rollback_result = cap.rollback(FileDeleteInput(path=str(f)), run_result)
        assert rollback_result.success is True
        assert f.exists()
        assert f.read_text() == "restore me"

    def test_rollback_no_backup(self, cap: FileDeleteCapability) -> None:
        from elle.capabilities.models import CapabilityResult

        run_result = CapabilityResult(success=True, output=None)
        rollback_result = cap.rollback(FileDeleteInput(path="/tmp/x"), run_result)
        assert rollback_result.success is False


# =========================================================================
# FileCopyCapability tests
# =========================================================================


class TestFileCopyCapability:
    @pytest.fixture()
    def cap(self) -> FileCopyCapability:
        return FileCopyCapability()

    def test_spec(self, cap: FileCopyCapability) -> None:
        assert cap.spec.name == "file.copy"
        assert cap.spec.risk == "low"

    def test_dry_run_source_missing(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        result = cap.dry_run(
            FileCopyInput(
                source=str(tmp_path / "missing.txt"),
                destination=str(tmp_path / "dest.txt"),
            )
        )
        assert result.is_valid is False

    def test_dry_run_dest_exists_no_overwrite(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("source")
        dst.write_text("existing")
        result = cap.dry_run(FileCopyInput(source=str(src), destination=str(dst), overwrite=False))
        assert result.is_valid is False

    def test_dry_run_forbidden_dest(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        with patch("elle.capabilities.core.file.Path.resolve", return_value=Path("/etc/passwd")):
            result = cap.dry_run(FileCopyInput(source=str(src), destination="/etc/passwd"))
            assert result.is_valid is False

    def test_dry_run_valid(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        result = cap.dry_run(FileCopyInput(source=str(src), destination=str(tmp_path / "dst.txt")))
        assert result.is_valid is True

    def test_run_success(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("content")
        result = cap.run(FileCopyInput(source=str(src), destination=str(dst)))
        assert result.success is True
        assert dst.read_text() == "content"

    def test_run_creates_parent_dirs(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        dst = tmp_path / "sub" / "dir" / "dst.txt"
        src.write_text("data")
        result = cap.run(FileCopyInput(source=str(src), destination=str(dst)))
        assert result.success is True
        assert dst.exists()

    def test_verify_success(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("content")
        dst.write_text("content")
        result = cap.verify(FileCopyInput(source=str(src), destination=str(dst)))
        assert result.passed is True

    def test_verify_dest_missing(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("x")
        result = cap.verify(FileCopyInput(source=str(src), destination=str(tmp_path / "missing.txt")))
        assert result.passed is False

    def test_rollback_deletes_copy(self, cap: FileCopyCapability, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("data")
        run_result = cap.run(FileCopyInput(source=str(src), destination=str(dst)))
        rollback_result = cap.rollback(
            FileCopyInput(source=str(src), destination=str(dst)),
            run_result,
        )
        assert rollback_result.success is True
        assert not dst.exists()


# =========================================================================
# FileDiffCapability tests
# =========================================================================


class TestFileDiffCapability:
    @pytest.fixture()
    def cap(self) -> FileDiffCapability:
        return FileDiffCapability()

    def test_spec(self, cap: FileDiffCapability) -> None:
        assert cap.spec.name == "file.diff"
        assert cap.spec.risk == "none"

    def test_dry_run_file_missing(self, cap: FileDiffCapability, tmp_path: Path) -> None:
        result = cap.dry_run(FileDiffInput(path=str(tmp_path / "missing.txt")))
        assert result.is_valid is False

    def test_dry_run_valid(self, cap: FileDiffCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = cap.dry_run(FileDiffInput(path=str(f)))
        assert result.is_valid is True

    def test_run_no_changes(self, cap: FileDiffCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("same content")
        result = cap.run(FileDiffInput(path=str(f), previous_content="same content"))
        assert result.success is True
        assert result.output.has_changes is False

    def test_run_with_changes(self, cap: FileDiffCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("new line\n")
        result = cap.run(FileDiffInput(path=str(f), previous_content="old line\n"))
        assert result.success is True
        assert result.output.has_changes is True
        assert result.output.lines_added > 0
        assert result.output.lines_removed > 0

    def test_run_file_missing(self, cap: FileDiffCapability, tmp_path: Path) -> None:
        result = cap.run(FileDiffInput(path=str(tmp_path / "missing.txt")))
        assert result.success is False

    def test_run_no_previous_content(self, cap: FileDiffCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("new content\n")
        result = cap.run(FileDiffInput(path=str(f)))
        assert result.success is True
        assert result.output.has_changes is True


# =========================================================================
# FileWatchSnapshotCapability tests
# =========================================================================


class TestFileWatchSnapshotCapability:
    @pytest.fixture()
    def cap(self) -> FileWatchSnapshotCapability:
        return FileWatchSnapshotCapability()

    def test_spec(self, cap: FileWatchSnapshotCapability) -> None:
        assert cap.spec.name == "file.watch_snapshot"

    def test_dry_run_missing(self, cap: FileWatchSnapshotCapability, tmp_path: Path) -> None:
        result = cap.dry_run(FileWatchSnapshotInput(path=str(tmp_path / "nope")))
        assert result.is_valid is False

    def test_dry_run_valid(self, cap: FileWatchSnapshotCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("data")
        result = cap.dry_run(FileWatchSnapshotInput(path=str(f)))
        assert result.is_valid is True

    def test_run_creates_snapshot(self, cap: FileWatchSnapshotCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("snapshot data")
        result = cap.run(FileWatchSnapshotInput(path=str(f), snapshot_id="test-snap"))
        assert result.success is True
        assert result.output.snapshot_id == "test-snap"
        assert "test-snap" in _snapshot_store

    def test_run_file_missing(self, cap: FileWatchSnapshotCapability, tmp_path: Path) -> None:
        result = cap.run(FileWatchSnapshotInput(path=str(tmp_path / "miss")))
        assert result.success is False
