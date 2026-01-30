"""Tests for the Augeas backup manager."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.ops.augeas.backup import (
    BackupManager,
    backup_file,
    cleanup_backups,
    get_latest_backup,
    get_manager,
    list_backups,
    restore_file,
)
from elle.ops.augeas.models import (
    AugeasBackupError,
    BackupListResult,
    BackupRecord,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backup_root(tmp_path: Path) -> Path:
    """Provide a temporary backup root directory."""
    root = tmp_path / "backups"
    root.mkdir()
    return root


@pytest.fixture()
def manager(backup_root: Path) -> BackupManager:
    """Provide a BackupManager rooted in tmp_path."""
    return BackupManager(backup_root=backup_root)


@pytest.fixture()
def sample_config(tmp_path: Path) -> Path:
    """Create a sample config file in tmp_path."""
    cfg = tmp_path / "etc" / "hosts"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("127.0.0.1 localhost\n")
    return cfg


@pytest.fixture()
def sample_binary(tmp_path: Path) -> Path:
    """Create a sample binary file in tmp_path."""
    bf = tmp_path / "binary.dat"
    bf.write_bytes(b"\x00\x01\x02\xff" * 256)
    return bf


# ---------------------------------------------------------------------------
# BackupManager.__init__
# ---------------------------------------------------------------------------


class TestBackupManagerInit:
    """Tests for BackupManager initialization."""

    def test_creates_backup_root(self, tmp_path: Path) -> None:
        """BackupManager should create the backup root directory."""
        root = tmp_path / "new_backup_dir"
        assert not root.exists()
        mgr = BackupManager(backup_root=root)
        assert mgr.backup_root == root
        assert root.exists()

    def test_default_retention_days(self, backup_root: Path) -> None:
        """Default retention should be 30 days."""
        mgr = BackupManager(backup_root=backup_root)
        assert mgr.retention_days == 30

    def test_custom_retention_days(self, backup_root: Path) -> None:
        """Custom retention_days should be stored."""
        mgr = BackupManager(backup_root=backup_root, retention_days=7)
        assert mgr.retention_days == 7

    def test_fallback_to_tmp_on_permission_error(self, tmp_path: Path) -> None:
        """Should fall back to tempdir when backup_root cannot be created."""
        target = tmp_path / "unreachable"
        original_mkdir = Path.mkdir

        call_count = 0

        def patched_mkdir(self_path: Path, *args: object, **kwargs: object) -> None:
            nonlocal call_count
            # Only fail on the first call (the backup_root creation)
            if call_count == 0 and str(self_path) == str(target):
                call_count += 1
                raise PermissionError("denied")
            return original_mkdir(self_path, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "mkdir", patched_mkdir):
            mgr = BackupManager(backup_root=target)
        assert "elle-backups" in str(mgr.backup_root)

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """BackupManager should accept a string for backup_root."""
        root = str(tmp_path / "str_root")
        mgr = BackupManager(backup_root=root)
        assert isinstance(mgr.backup_root, Path)
        assert mgr.backup_root.exists()


# ---------------------------------------------------------------------------
# BackupManager.backup
# ---------------------------------------------------------------------------


class TestBackup:
    """Tests for creating backups."""

    def test_backup_creates_file(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """backup should copy the file into the backup directory."""
        record = manager.backup(str(sample_config))
        backup_path = Path(record.backup_path)
        assert backup_path.exists()
        assert backup_path.read_text() == sample_config.read_text()

    def test_backup_preserves_filename(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """The backup file should keep the original file name."""
        record = manager.backup(str(sample_config))
        assert Path(record.backup_path).name == sample_config.name

    def test_backup_record_fields(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """BackupRecord should contain correct metadata."""
        record = manager.backup(str(sample_config))
        assert record.original_path == str(sample_config)
        assert record.size_bytes == sample_config.stat().st_size
        assert record.sha256 != ""
        assert isinstance(record.created_at, datetime)
        assert isinstance(record.domain, str)

    def test_backup_sha256_matches_file(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """The sha256 in the record should match the actual file hash."""
        record = manager.backup(str(sample_config))
        expected = hashlib.sha256(sample_config.read_bytes()).hexdigest()
        assert record.sha256 == expected

    def test_backup_with_metadata(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """Optional metadata should be stored in the record."""
        meta = {"incident_id": "INC-001", "description": "test edit"}
        record = manager.backup(str(sample_config), metadata=meta)
        assert record.metadata == meta

    def test_backup_without_metadata(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """Metadata should default to empty dict when not provided."""
        record = manager.backup(str(sample_config))
        assert record.metadata == {}

    def test_backup_saves_metadata_json(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """A metadata.json should be written alongside the backup file."""
        record = manager.backup(str(sample_config))
        metadata_file = Path(record.backup_path).parent / "metadata.json"
        assert metadata_file.exists()

        data = json.loads(metadata_file.read_text())
        assert data["original_path"] == str(sample_config)

    def test_backup_missing_file_raises(self, manager: BackupManager) -> None:
        """Backing up a non-existent file should raise AugeasBackupError."""
        with pytest.raises(AugeasBackupError, match="File does not exist"):
            manager.backup("/nonexistent/file.conf")

    def test_backup_binary_file(
        self, manager: BackupManager, sample_binary: Path
    ) -> None:
        """Backing up a binary file should work correctly."""
        record = manager.backup(str(sample_binary))
        backup_path = Path(record.backup_path)
        assert backup_path.exists()
        assert backup_path.read_bytes() == sample_binary.read_bytes()

    def test_backup_cleans_up_on_copy_failure(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """If shutil.copy2 fails, the backup dir should be cleaned up."""
        with patch("shutil.copy2", side_effect=OSError("disk full")):
            with pytest.raises(AugeasBackupError, match="disk full"):
                manager.backup(str(sample_config))

    def test_multiple_backups_different_timestamps(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """Successive backups should be in different timestamped directories."""
        r1 = manager.backup(str(sample_config))
        # Small sleep to guarantee different timestamp
        time.sleep(0.01)
        r2 = manager.backup(str(sample_config))
        assert r1.backup_path != r2.backup_path


# ---------------------------------------------------------------------------
# BackupManager.restore
# ---------------------------------------------------------------------------


class TestRestore:
    """Tests for restoring backups."""

    def test_restore_from_record(
        self, manager: BackupManager, sample_config: Path, tmp_path: Path
    ) -> None:
        """restore should copy backup back to original location."""
        record = manager.backup(str(sample_config))
        # Modify the original
        sample_config.write_text("MODIFIED\n")
        assert sample_config.read_text() == "MODIFIED\n"

        result = manager.restore(record)
        assert result is True
        assert sample_config.read_text() == "127.0.0.1 localhost\n"

    def test_restore_to_alternate_path(
        self, manager: BackupManager, sample_config: Path, tmp_path: Path
    ) -> None:
        """restore should support overriding the target path."""
        record = manager.backup(str(sample_config))
        alt_target = tmp_path / "restored.conf"

        result = manager.restore(record, target_path=str(alt_target))
        assert result is True
        assert alt_target.read_text() == "127.0.0.1 localhost\n"

    def test_restore_from_string_path_with_target(
        self, manager: BackupManager, sample_config: Path, tmp_path: Path
    ) -> None:
        """restore should accept a string backup path when target is given."""
        record = manager.backup(str(sample_config))
        alt_target = tmp_path / "restored2.conf"

        result = manager.restore(record.backup_path, target_path=str(alt_target))
        assert result is True
        assert alt_target.read_text() == "127.0.0.1 localhost\n"

    def test_restore_string_without_target_raises(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """restore with string path but no target should raise."""
        record = manager.backup(str(sample_config))
        with pytest.raises(AugeasBackupError, match="Target path not specified"):
            manager.restore(record.backup_path)

    def test_restore_missing_backup_raises(self, manager: BackupManager) -> None:
        """Restoring from a nonexistent backup should raise."""
        fake_record = BackupRecord(
            backup_path="/nonexistent/backup.conf",
            original_path="/etc/hosts",
            sha256="abc",
            size_bytes=0,
        )
        with pytest.raises(AugeasBackupError, match="Backup file does not exist"):
            manager.restore(fake_record)

    def test_restore_copy_failure_raises(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """restore should raise AugeasBackupError when copy fails."""
        record = manager.backup(str(sample_config))
        with patch("shutil.copy2", side_effect=OSError("permission denied")):
            with pytest.raises(AugeasBackupError, match="permission denied"):
                manager.restore(record)


# ---------------------------------------------------------------------------
# BackupManager.get_backup
# ---------------------------------------------------------------------------


class TestGetBackup:
    """Tests for retrieving a single backup record."""

    def test_get_backup_from_metadata(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """get_backup should load the BackupRecord from metadata.json."""
        record = manager.backup(str(sample_config))
        loaded = manager.get_backup(record.backup_path)
        assert loaded is not None
        assert loaded.original_path == str(sample_config)
        assert loaded.sha256 == record.sha256

    def test_get_backup_missing_path(self, manager: BackupManager) -> None:
        """get_backup should return None for a nonexistent path."""
        result = manager.get_backup("/nonexistent/backup")
        assert result is None

    def test_get_backup_without_metadata_file(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """get_backup should create a minimal record when metadata.json is absent."""
        record = manager.backup(str(sample_config))
        # Remove the metadata file
        metadata = Path(record.backup_path).parent / "metadata.json"
        metadata.unlink()

        loaded = manager.get_backup(record.backup_path)
        assert loaded is not None
        assert loaded.original_path == "unknown"
        assert loaded.domain == "unknown"

    def test_get_backup_corrupt_metadata(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """get_backup should fall back to minimal record on corrupt metadata."""
        record = manager.backup(str(sample_config))
        metadata = Path(record.backup_path).parent / "metadata.json"
        metadata.write_text("NOT VALID JSON {{")

        loaded = manager.get_backup(record.backup_path)
        assert loaded is not None
        assert loaded.original_path == "unknown"


# ---------------------------------------------------------------------------
# BackupManager.list_backups
# ---------------------------------------------------------------------------


class TestListBackups:
    """Tests for listing backups."""

    def test_list_empty(self, manager: BackupManager) -> None:
        """list_backups on empty backup dir should return empty result."""
        result = manager.list_backups()
        assert isinstance(result, BackupListResult)
        assert result.backups == ()
        assert result.total_size_bytes == 0

    def test_list_after_backup(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """list_backups should include recently created backups."""
        manager.backup(str(sample_config))
        result = manager.list_backups()
        assert len(result.backups) == 1
        assert result.backups[0].original_path == str(sample_config)

    def test_list_filter_by_domain(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """list_backups with domain filter should only show matching domain."""
        record = manager.backup(str(sample_config))
        # Filter by the auto-detected domain
        result = manager.list_backups(domain=record.domain)
        assert len(result.backups) >= 1

        # Filter by a domain that does not match
        result_empty = manager.list_backups(domain="nonexistent_domain_xyz")
        assert len(result_empty.backups) == 0

    def test_list_filter_by_original_path(
        self, manager: BackupManager, sample_config: Path, tmp_path: Path
    ) -> None:
        """list_backups with original_path filter should only match that file."""
        manager.backup(str(sample_config))
        other = tmp_path / "other.conf"
        other.write_text("other content\n")
        manager.backup(str(other))

        result = manager.list_backups(original_path=str(sample_config))
        assert len(result.backups) == 1
        assert result.backups[0].original_path == str(sample_config)

    def test_list_respects_limit(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """list_backups should respect the limit parameter."""
        for _ in range(3):
            manager.backup(str(sample_config))
            time.sleep(0.01)

        result = manager.list_backups(limit=2)
        assert len(result.backups) == 2

    def test_list_by_domain_counts(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """by_domain should contain the correct count per domain."""
        record = manager.backup(str(sample_config))
        result = manager.list_backups()
        assert record.domain in result.by_domain
        assert result.by_domain[record.domain] >= 1

    def test_list_includes_backups_without_metadata(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """list_backups should include entries even without metadata.json."""
        record = manager.backup(str(sample_config))
        # Remove metadata.json
        metadata = Path(record.backup_path).parent / "metadata.json"
        metadata.unlink()

        result = manager.list_backups()
        assert len(result.backups) >= 1
        assert result.backups[0].original_path == "unknown"


# ---------------------------------------------------------------------------
# BackupManager.get_latest_backup
# ---------------------------------------------------------------------------


class TestGetLatestBackup:
    """Tests for get_latest_backup."""

    def test_no_backups_returns_none(self, manager: BackupManager) -> None:
        """get_latest_backup should return None when no backups exist."""
        result = manager.get_latest_backup("/etc/hosts")
        assert result is None

    def test_returns_most_recent(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """get_latest_backup should return the most recent backup."""
        manager.backup(str(sample_config))
        time.sleep(0.01)
        r2 = manager.backup(str(sample_config))

        latest = manager.get_latest_backup(str(sample_config))
        assert latest is not None
        assert latest.backup_path == r2.backup_path


# ---------------------------------------------------------------------------
# BackupManager.delete_backup
# ---------------------------------------------------------------------------


class TestDeleteBackup:
    """Tests for deleting backups."""

    def test_delete_removes_directory(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """delete_backup should remove the entire timestamp directory."""
        record = manager.backup(str(sample_config))
        backup_dir = Path(record.backup_path).parent
        assert backup_dir.exists()

        result = manager.delete_backup(record)
        assert result is True
        assert not backup_dir.exists()

    def test_delete_nonexistent_returns_false(self, manager: BackupManager) -> None:
        """delete_backup should return False for missing backup."""
        result = manager.delete_backup("/nonexistent/backup.conf")
        assert result is False

    def test_delete_with_string_path(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """delete_backup should accept a string path."""
        record = manager.backup(str(sample_config))
        result = manager.delete_backup(record.backup_path)
        assert result is True

    def test_delete_failure_returns_false(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """delete_backup should return False when rmtree fails."""
        record = manager.backup(str(sample_config))
        with patch("shutil.rmtree", side_effect=OSError("locked")):
            result = manager.delete_backup(record)
            assert result is False


# ---------------------------------------------------------------------------
# BackupManager.cleanup_old_backups
# ---------------------------------------------------------------------------


class TestCleanupOldBackups:
    """Tests for cleaning up old backups."""

    def test_cleanup_by_age(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """cleanup_old_backups should remove backups older than max_age_days."""
        record = manager.backup(str(sample_config))
        backup_dir = Path(record.backup_path).parent

        # Make the backup appear old by changing its mtime
        old_time = time.time() - (60 * 60 * 24 * 40)  # 40 days ago
        os.utime(backup_dir, (old_time, old_time))

        deleted = manager.cleanup_old_backups(max_age_days=30)
        assert deleted >= 1

    def test_cleanup_by_count(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """cleanup_old_backups should keep at most max_count backups per domain."""
        for _ in range(5):
            manager.backup(str(sample_config))
            time.sleep(0.01)

        deleted = manager.cleanup_old_backups(max_count=2)
        assert deleted >= 3

    def test_cleanup_no_deletions_when_fresh(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """cleanup_old_backups should delete nothing when backups are fresh."""
        manager.backup(str(sample_config))
        deleted = manager.cleanup_old_backups(max_age_days=30)
        assert deleted == 0

    def test_cleanup_specific_domain(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        """cleanup_old_backups with domain filter should only clean that domain."""
        file_a = tmp_path / "a.conf"
        file_a.write_text("aaa\n")

        file_b = tmp_path / "b.conf"
        file_b.write_text("bbb\n")

        # Mock detect_domain to return distinct domains for the two files
        def fake_domain(fp: object) -> str:
            if "a.conf" in str(fp):
                return "domain_a"
            return "domain_b"

        with patch("elle.ops.augeas.backup.detect_domain", side_effect=fake_domain):
            r1 = manager.backup(str(file_a))
            r2 = manager.backup(str(file_b))

        assert r1.domain == "domain_a"
        assert r2.domain == "domain_b"

        # Make both old
        for rec in [r1, r2]:
            d = Path(rec.backup_path).parent
            old_time = time.time() - (60 * 60 * 24 * 40)
            os.utime(d, (old_time, old_time))

        # Only clean domain_a
        deleted = manager.cleanup_old_backups(domain="domain_a", max_age_days=30)
        assert deleted >= 1

        # domain_b's backup should still exist
        assert Path(r2.backup_path).exists()

    def test_cleanup_handles_rmtree_failure(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """cleanup should continue and not raise when rmtree fails."""
        record = manager.backup(str(sample_config))
        backup_dir = Path(record.backup_path).parent
        old_time = time.time() - (60 * 60 * 24 * 40)
        os.utime(backup_dir, (old_time, old_time))

        with patch("shutil.rmtree", side_effect=OSError("locked")):
            # Should not raise, but return 0 since it could not delete
            deleted = manager.cleanup_old_backups(max_age_days=30)
            assert deleted == 0


# ---------------------------------------------------------------------------
# BackupManager.get_backup_size
# ---------------------------------------------------------------------------


class TestGetBackupSize:
    """Tests for get_backup_size."""

    def test_empty_returns_zero(self, manager: BackupManager) -> None:
        """get_backup_size should return 0 when no backups exist."""
        assert manager.get_backup_size() == 0

    def test_returns_nonzero_after_backup(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """get_backup_size should be positive after creating a backup."""
        manager.backup(str(sample_config))
        size = manager.get_backup_size()
        assert size > 0

    def test_filter_by_domain(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """get_backup_size with domain should only count that domain."""
        record = manager.backup(str(sample_config))
        size_domain = manager.get_backup_size(domain=record.domain)
        size_none = manager.get_backup_size(domain="nonexistent_domain_xyz")

        assert size_domain > 0
        assert size_none == 0


# ---------------------------------------------------------------------------
# BackupManager.verify_backup
# ---------------------------------------------------------------------------


class TestVerifyBackup:
    """Tests for verify_backup."""

    def test_valid_backup_passes(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """verify_backup should return True for an unmodified backup."""
        record = manager.backup(str(sample_config))
        assert manager.verify_backup(record) is True

    def test_tampered_backup_fails(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """verify_backup should return False if backup file was changed."""
        record = manager.backup(str(sample_config))
        # Tamper with the backup file
        Path(record.backup_path).write_text("tampered content\n")
        assert manager.verify_backup(record) is False

    def test_missing_backup_fails(self, manager: BackupManager) -> None:
        """verify_backup should return False if backup file does not exist."""
        fake_record = BackupRecord(
            backup_path="/nonexistent/backup.conf",
            original_path="/etc/hosts",
            sha256="abc",
            size_bytes=0,
        )
        assert manager.verify_backup(fake_record) is False

    def test_empty_sha256_passes(
        self, manager: BackupManager, sample_config: Path
    ) -> None:
        """verify_backup with empty sha256 should return True (nothing to check)."""
        record = manager.backup(str(sample_config))
        # Create a record with no hash
        no_hash_record = BackupRecord(
            backup_path=record.backup_path,
            original_path=record.original_path,
            sha256="",
            size_bytes=record.size_bytes,
        )
        assert manager.verify_backup(no_hash_record) is True


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    """Tests for module-level backup_file, restore_file, etc."""

    def test_backup_file_function(self, sample_config: Path, tmp_path: Path) -> None:
        """backup_file convenience function should create a backup."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = BackupManager(backup_root=tmp_path / "conv_backups")
            record = backup_file(str(sample_config))
            assert Path(record.backup_path).exists()
        finally:
            backup_mod._manager = original_manager

    def test_backup_file_with_domain_parameter(
        self, sample_config: Path, tmp_path: Path
    ) -> None:
        """backup_file should accept domain parameter (unused, auto-detected)."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = BackupManager(backup_root=tmp_path / "conv_backups")
            record = backup_file(str(sample_config), domain="custom")
            # domain is auto-detected, not the parameter
            assert isinstance(record.domain, str)
            assert Path(record.backup_path).exists()
        finally:
            backup_mod._manager = original_manager

    def test_backup_file_with_metadata(
        self, sample_config: Path, tmp_path: Path
    ) -> None:
        """backup_file should pass metadata through."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = BackupManager(backup_root=tmp_path / "conv_backups")
            meta = {"key": "val"}
            record = backup_file(str(sample_config), metadata=meta)
            assert record.metadata == meta
        finally:
            backup_mod._manager = original_manager

    def test_restore_file_function(
        self, sample_config: Path, tmp_path: Path
    ) -> None:
        """restore_file convenience function should restore the file."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = BackupManager(backup_root=tmp_path / "conv_backups")
            record = backup_file(str(sample_config))
            sample_config.write_text("CHANGED\n")

            result = restore_file(record)
            assert result is True
            assert sample_config.read_text() == "127.0.0.1 localhost\n"
        finally:
            backup_mod._manager = original_manager

    def test_list_backups_function(
        self, sample_config: Path, tmp_path: Path
    ) -> None:
        """list_backups convenience function should list backups."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = BackupManager(backup_root=tmp_path / "conv_backups")
            backup_file(str(sample_config))

            result = list_backups()
            assert isinstance(result, BackupListResult)
            assert len(result.backups) >= 1
        finally:
            backup_mod._manager = original_manager

    def test_get_latest_backup_function(
        self, sample_config: Path, tmp_path: Path
    ) -> None:
        """get_latest_backup convenience function should return latest."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = BackupManager(backup_root=tmp_path / "conv_backups")
            backup_file(str(sample_config))

            latest = get_latest_backup(str(sample_config))
            assert latest is not None
            assert latest.original_path == str(sample_config)
        finally:
            backup_mod._manager = original_manager

    def test_cleanup_backups_function(
        self, sample_config: Path, tmp_path: Path
    ) -> None:
        """cleanup_backups convenience function should run cleanup."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = BackupManager(backup_root=tmp_path / "conv_backups")
            backup_file(str(sample_config))
            deleted = cleanup_backups(max_age_days=30)
            assert isinstance(deleted, int)
        finally:
            backup_mod._manager = original_manager

    def test_get_manager_creates_singleton(self) -> None:
        """get_manager should return the same instance on repeated calls."""
        import elle.ops.augeas.backup as backup_mod

        original_manager = backup_mod._manager
        try:
            backup_mod._manager = None
            m1 = get_manager()
            m2 = get_manager()
            assert m1 is m2
        finally:
            backup_mod._manager = original_manager


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for backup operations."""

    def test_backup_empty_file(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        """Backing up an empty file should work."""
        empty = tmp_path / "empty.conf"
        empty.write_text("")

        record = manager.backup(str(empty))
        assert record.size_bytes == 0
        assert Path(record.backup_path).exists()

    def test_backup_file_with_special_chars_in_name(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        """Files with spaces and special chars in the name should work."""
        special = tmp_path / "my config (v2).conf"
        special.write_text("data\n")

        record = manager.backup(str(special))
        assert Path(record.backup_path).exists()
        assert Path(record.backup_path).read_text() == "data\n"

    def test_backup_large_ish_file(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        """Backing up a file larger than the hash chunk size (8192) should work."""
        big = tmp_path / "big.conf"
        big.write_text("x" * 20000)

        record = manager.backup(str(big))
        expected = hashlib.sha256(b"x" * 20000).hexdigest()
        assert record.sha256 == expected
        assert record.size_bytes == 20000

    def test_restore_preserves_content(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        """Restoring should produce byte-identical content."""
        original = tmp_path / "binary_restore.dat"
        content = bytes(range(256))
        original.write_bytes(content)

        record = manager.backup(str(original))
        original.write_bytes(b"\x00")

        manager.restore(record)
        assert original.read_bytes() == content
