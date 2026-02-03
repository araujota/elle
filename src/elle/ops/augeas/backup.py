"""Backup manager for config file rollback support.

Manages file backups before modifications, enabling rollback on
validation failure or user request.

Backup storage: /var/lib/elle/backups/<domain>/<timestamp>/
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from elle.common.pydantic_compat import safe_model_dump
from elle.ops.augeas.lenses import detect_domain
from elle.ops.augeas.models import (
    AugeasBackupError,
    BackupListResult,
    BackupRecord,
)

logger = logging.getLogger(__name__)

# Default backup root directory
DEFAULT_BACKUP_ROOT = Path("/var/lib/elle/backups")

# Backup retention settings
DEFAULT_RETENTION_DAYS = 30
MAX_BACKUPS_PER_DOMAIN = 100


class BackupManager:
    """Manages file backups for config file rollback.

    Creates timestamped backups before modifications, supports restoration,
    and handles cleanup of old backups.

    Usage:
        manager = BackupManager()
        backup = manager.backup("/etc/fstab")
        # ... make changes ...
        if validation_failed:
            manager.restore(backup)
    """

    def __init__(
        self,
        backup_root: Path | str | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        """Initialize the backup manager.

        Args:
            backup_root: Root directory for backups.
            retention_days: Days to retain backups.
        """
        self.backup_root = Path(backup_root or DEFAULT_BACKUP_ROOT)
        self.retention_days = retention_days

        # Ensure backup root exists
        try:
            self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except PermissionError:
            logger.warning(f"Cannot create backup directory {self.backup_root}, will use temporary directory")
            import tempfile

            self.backup_root = Path(tempfile.gettempdir()) / "elle-backups"
            self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def backup(
        self,
        file_path: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> BackupRecord:
        """Create a backup of a file.

        Args:
            file_path: Path to the file to backup.
            metadata: Optional metadata to store with backup.

        Returns:
            BackupRecord with backup details.

        Raises:
            AugeasBackupError: If backup fails.
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise AugeasBackupError("create", str(file_path), "File does not exist")

        # Determine domain and create backup directory
        domain = detect_domain(file_path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = self.backup_root / domain / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Backup file name preserves original name
        backup_file = backup_dir / file_path.name

        try:
            # Copy file preserving metadata
            shutil.copy2(file_path, backup_file)

            # Calculate hash
            sha256 = self._calculate_hash(file_path)
            size = file_path.stat().st_size

            # Create backup record
            record = BackupRecord(
                backup_path=str(backup_file),
                original_path=str(file_path),
                domain=domain,
                sha256=sha256,
                size_bytes=size,
                created_at=datetime.now(timezone.utc),
                metadata=metadata or {},
            )

            # Save metadata alongside backup
            self._save_metadata(backup_dir, record)

            logger.info(f"Created backup: {backup_file}")
            return record

        except Exception as e:
            # Clean up on failure
            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
            except Exception:
                pass
            raise AugeasBackupError("create", str(file_path), str(e)) from e

    def restore(
        self,
        backup: BackupRecord | str,
        target_path: str | Path | None = None,
    ) -> bool:
        """Restore a file from backup.

        Args:
            backup: BackupRecord or path to backup file.
            target_path: Override target path (defaults to original location).

        Returns:
            True if restore succeeded.

        Raises:
            AugeasBackupError: If restore fails.
        """
        original_path: Path
        if isinstance(backup, BackupRecord):
            backup_path = Path(backup.backup_path)
            original_path = Path(target_path or backup.original_path)
        else:
            backup_path = Path(backup)
            if target_path:
                original_path = Path(target_path)
            else:
                raise AugeasBackupError("restore", str(backup_path), "Target path not specified")

        if not backup_path.exists():
            raise AugeasBackupError("restore", str(backup_path), "Backup file does not exist")

        try:
            # Copy backup to original location
            shutil.copy2(backup_path, original_path)
            logger.info(f"Restored {original_path} from {backup_path}")
            return True

        except Exception as e:
            raise AugeasBackupError("restore", str(backup_path), str(e)) from e

    def get_backup(self, backup_path: str | Path) -> BackupRecord | None:
        """Get backup record by path.

        Args:
            backup_path: Path to the backup file.

        Returns:
            BackupRecord if found, None otherwise.
        """
        backup_path = Path(backup_path)
        if not backup_path.exists():
            return None

        # Try to load metadata
        metadata_file = backup_path.parent / "metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file) as f:
                    data = json.load(f)
                return BackupRecord(**data)
            except Exception:
                pass

        # Create minimal record from file
        return BackupRecord(
            backup_path=str(backup_path),
            original_path="unknown",
            domain="unknown",
            sha256=self._calculate_hash(backup_path),
            size_bytes=backup_path.stat().st_size,
            created_at=datetime.fromtimestamp(backup_path.stat().st_mtime),
        )

    def list_backups(
        self,
        domain: str | None = None,
        original_path: str | Path | None = None,
        limit: int = 100,
    ) -> BackupListResult:
        """List available backups.

        Args:
            domain: Filter by domain.
            original_path: Filter by original file path.
            limit: Maximum number of backups to return.

        Returns:
            BackupListResult with available backups.
        """
        backups: list[BackupRecord] = []
        by_domain: dict[str, int] = {}
        total_size = 0

        # Scan backup directories
        if domain:
            search_dirs = [self.backup_root / domain]
        else:
            search_dirs = [d for d in self.backup_root.iterdir() if d.is_dir()]

        for domain_dir in search_dirs:
            if not domain_dir.exists():
                continue

            domain_name = domain_dir.name
            domain_count = 0

            for timestamp_dir in sorted(domain_dir.iterdir(), reverse=True):
                if not timestamp_dir.is_dir():
                    continue

                # Load metadata if available
                metadata_file = timestamp_dir / "metadata.json"
                if metadata_file.exists():
                    try:
                        with open(metadata_file) as f:
                            data = json.load(f)
                        record = BackupRecord(**data)

                        # Apply filter
                        if original_path and record.original_path != str(original_path):
                            continue

                        backups.append(record)
                        domain_count += 1
                        total_size += record.size_bytes

                        if len(backups) >= limit:
                            break

                    except Exception as e:
                        logger.debug(f"Failed to load backup metadata: {e}")
                        continue
                else:
                    # Scan for backup files without metadata
                    for backup_file in timestamp_dir.iterdir():
                        if backup_file.suffix == ".json":
                            continue

                        record = BackupRecord(
                            backup_path=str(backup_file),
                            original_path="unknown",
                            domain=domain_name,
                            sha256="",
                            size_bytes=backup_file.stat().st_size,
                            created_at=datetime.fromtimestamp(backup_file.stat().st_mtime),
                        )
                        backups.append(record)
                        domain_count += 1
                        total_size += record.size_bytes

                        if len(backups) >= limit:
                            break

            by_domain[domain_name] = domain_count

        return BackupListResult(
            backups=tuple(backups),
            total_size_bytes=total_size,
            by_domain=by_domain,
        )

    def get_latest_backup(
        self,
        original_path: str | Path,
    ) -> BackupRecord | None:
        """Get the most recent backup of a file.

        Args:
            original_path: Original file path.

        Returns:
            Most recent BackupRecord, or None if no backups exist.
        """
        result = self.list_backups(original_path=original_path, limit=1)
        if result.backups:
            return result.backups[0]
        return None

    def delete_backup(self, backup: BackupRecord | str) -> bool:
        """Delete a specific backup.

        Args:
            backup: BackupRecord or path to backup file.

        Returns:
            True if deleted successfully.
        """
        backup_path = Path(backup.backup_path) if isinstance(backup, BackupRecord) else Path(backup)

        if not backup_path.exists():
            return False

        try:
            # Delete the entire timestamp directory
            backup_dir = backup_path.parent
            shutil.rmtree(backup_dir)
            logger.info(f"Deleted backup: {backup_dir}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete backup: {e}")
            return False

    def cleanup_old_backups(
        self,
        domain: str | None = None,
        max_age_days: int | None = None,
        max_count: int | None = None,
    ) -> int:
        """Clean up old backups.

        Args:
            domain: Only clean up specific domain.
            max_age_days: Delete backups older than this (defaults to retention_days).
            max_count: Keep at most this many backups per domain.

        Returns:
            Number of backups deleted.
        """
        max_age = max_age_days or self.retention_days
        max_keep = max_count or MAX_BACKUPS_PER_DOMAIN
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)
        deleted = 0

        domains = [domain] if domain else [d.name for d in self.backup_root.iterdir() if d.is_dir()]

        for domain_name in domains:
            domain_dir = self.backup_root / domain_name
            if not domain_dir.exists():
                continue

            # Get all timestamp directories sorted by age (newest first)
            timestamp_dirs = sorted(
                [d for d in domain_dir.iterdir() if d.is_dir()],
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )

            for i, ts_dir in enumerate(timestamp_dirs):
                should_delete = False

                # Delete if exceeds count limit
                if i >= max_keep:
                    should_delete = True

                # Delete if too old
                try:
                    mtime = datetime.fromtimestamp(ts_dir.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        should_delete = True
                except Exception:
                    pass

                if should_delete:
                    try:
                        shutil.rmtree(ts_dir)
                        deleted += 1
                        logger.debug(f"Cleaned up old backup: {ts_dir}")
                    except Exception as e:
                        logger.warning(f"Failed to clean up backup {ts_dir}: {e}")

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old backups")

        return deleted

    def get_backup_size(self, domain: str | None = None) -> int:
        """Get total size of backups.

        Args:
            domain: Only count specific domain.

        Returns:
            Total size in bytes.
        """
        total = 0

        if domain:
            search_dirs = [self.backup_root / domain]
        else:
            search_dirs = [d for d in self.backup_root.iterdir() if d.is_dir()]

        for domain_dir in search_dirs:
            if not domain_dir.exists():
                continue

            for path in domain_dir.rglob("*"):
                if path.is_file():
                    total += path.stat().st_size

        return total

    def verify_backup(self, backup: BackupRecord) -> bool:
        """Verify backup integrity by checking hash.

        Args:
            backup: BackupRecord to verify.

        Returns:
            True if backup is valid.
        """
        backup_path = Path(backup.backup_path)
        if not backup_path.exists():
            return False

        if not backup.sha256:
            return True  # No hash to verify

        current_hash = self._calculate_hash(backup_path)
        return current_hash == backup.sha256

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _calculate_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file.

        Args:
            file_path: Path to the file.

        Returns:
            Hex-encoded SHA256 hash.
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _save_metadata(self, backup_dir: Path, record: BackupRecord) -> None:
        """Save backup metadata to JSON file.

        Args:
            backup_dir: Backup directory.
            record: BackupRecord to save.
        """
        metadata_file = backup_dir / "metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(safe_model_dump(record), f, indent=2, default=str)


# =============================================================================
# Module-level convenience functions
# =============================================================================

_manager: BackupManager | None = None


def get_manager() -> BackupManager:
    """Get the global BackupManager instance.

    Returns:
        BackupManager instance.
    """
    global _manager
    if _manager is None:
        _manager = BackupManager()
    return _manager


def backup_file(
    file_path: str | Path,
    metadata: dict[str, Any] | None = None,
    domain: str | None = None,
) -> BackupRecord:
    """Create a backup of a file (convenience function).

    Args:
        file_path: Path to the file to backup.
        metadata: Optional metadata.
        domain: Optional domain hint (unused, domain is auto-detected).

    Returns:
        BackupRecord with backup details.
    """
    return get_manager().backup(file_path, metadata)


def restore_file(
    backup: BackupRecord | str,
    target_path: str | Path | None = None,
) -> bool:
    """Restore a file from backup (convenience function).

    Args:
        backup: BackupRecord or path to backup file.
        target_path: Override target path.

    Returns:
        True if restore succeeded.
    """
    return get_manager().restore(backup, target_path)


def list_backups(
    domain: str | None = None,
    original_path: str | Path | None = None,
) -> BackupListResult:
    """List available backups (convenience function).

    Args:
        domain: Filter by domain.
        original_path: Filter by original file path.

    Returns:
        BackupListResult with available backups.
    """
    return get_manager().list_backups(domain, original_path)


def get_latest_backup(original_path: str | Path) -> BackupRecord | None:
    """Get the most recent backup of a file (convenience function).

    Args:
        original_path: Original file path.

    Returns:
        Most recent BackupRecord, or None.
    """
    return get_manager().get_latest_backup(original_path)


def cleanup_backups(max_age_days: int | None = None) -> int:
    """Clean up old backups (convenience function).

    Args:
        max_age_days: Delete backups older than this.

    Returns:
        Number of backups deleted.
    """
    return get_manager().cleanup_old_backups(max_age_days=max_age_days)
