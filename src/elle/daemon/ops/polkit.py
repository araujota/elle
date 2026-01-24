"""Polkit integration for privileged operations.

Daemon runs unprivileged. A minimal Polkit helper performs atomic operations:
- Edit netplan
- Edit ufw
- Edit fstab
- Manage cron
- Read privileged logs

All operations follow: backup -> apply -> validate -> commit/rollback
Backups stored in: /var/lib/elle/backups/<domain>/<timestamp>/
"""

from pathlib import Path

BACKUP_ROOT = Path("/var/lib/elle/backups")


class PolkitAction:
    """Discrete Polkit action identifiers."""

    EDIT_NETPLAN = "com.elle.edit-netplan"
    EDIT_UFW = "com.elle.edit-ufw"
    EDIT_FSTAB = "com.elle.edit-fstab"
    MANAGE_CRON = "com.elle.manage-cron"
    READ_PRIVILEGED_LOGS = "com.elle.read-privileged-logs"


def check_authorization(action: str) -> bool:
    """Check if the current user is authorized for an action.

    Args:
        action: The Polkit action identifier.

    Returns:
        True if authorized.
    """
    raise NotImplementedError


def execute_privileged(action: str, payload: dict) -> bool:
    """Execute a privileged operation via Polkit helper.

    Args:
        action: The Polkit action identifier.
        payload: Operation-specific data.

    Returns:
        True if operation succeeded.
    """
    raise NotImplementedError


def create_backup(domain: str, file_path: Path) -> Path:
    """Create a backup before modifying a system file.

    Args:
        domain: The operation domain (netplan, ufw, etc.).
        file_path: The file to back up.

    Returns:
        Path to the backup file.
    """
    raise NotImplementedError


def restore_backup(backup_path: Path, target_path: Path) -> bool:
    """Restore a file from backup (rollback).

    Args:
        backup_path: Path to the backup file.
        target_path: Original file location.

    Returns:
        True if restore succeeded.
    """
    raise NotImplementedError
