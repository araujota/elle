"""Polkit helper for privileged operations.

Executes privileged operations via pkexec with atomic temp file → rename pattern.
All operations follow: backup → apply → validate → commit/rollback
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Polkit Action IDs
# =============================================================================


class PolkitAction:
    """Polkit action identifiers for ELLE operations."""

    # Config file editing
    EDIT_SYSTEM_CONFIG = "com.elle.ops.edit-system-config"
    EDIT_NETPLAN = "com.elle.ops.edit-netplan"
    EDIT_UFW = "com.elle.ops.edit-ufw"
    EDIT_FSTAB = "com.elle.ops.edit-fstab"
    EDIT_SSHD = "com.elle.ops.edit-sshd"
    EDIT_SYSCTL = "com.elle.ops.edit-sysctl"

    # Validation
    VALIDATE_CONFIG = "com.elle.ops.validate-config"

    # Service management
    MANAGE_CRON = "com.elle.ops.manage-cron"
    MANAGE_SERVICES = "com.elle.ops.manage-services"

    # Reading privileged files
    READ_PRIVILEGED_LOGS = "com.elle.ops.read-privileged-logs"

    # Boot and reboot management
    MANAGE_GRUB = "com.elle.ops.manage-grub"
    REBOOT = "com.elle.ops.reboot"


# =============================================================================
# Result Models
# =============================================================================


class PolkitResult(BaseModel):
    """Result of a Polkit-authorized operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the operation succeeded")
    authorized: bool = Field(
        default=True,
        description="Whether authorization was granted",
    )
    output: str = Field(default="", description="Command stdout")
    error: str = Field(default="", description="Command stderr or error message")
    exit_code: int = Field(default=0, description="Command exit code")


# =============================================================================
# Polkit Helper
# =============================================================================


class PolkitHelper:
    """Execute privileged operations via Polkit.

    Uses pkexec for authorization and atomic temp file → rename pattern
    for safe file modifications.

    Usage:
        helper = PolkitHelper()

        # Write to privileged file
        result = await helper.write_privileged(
            "/etc/fstab",
            new_content,
        )

        # Run validation command
        result = await helper.run_validator(["sshd", "-t", "-f", "/etc/ssh/sshd_config"])
    """

    def __init__(self, timeout: float = 60.0) -> None:
        """Initialize the Polkit helper.

        Args:
            timeout: Default timeout for operations in seconds.
        """
        self._timeout = timeout

    async def write_privileged(
        self,
        file_path: str | Path,
        content: str,
        action_id: str = PolkitAction.EDIT_SYSTEM_CONFIG,
        preserve_permissions: bool = True,
    ) -> PolkitResult:
        """Write to a privileged file atomically.

        Process:
        1. Write content to temp file (unprivileged)
        2. Use pkexec to copy temp file to destination atomically
        3. Preserve original permissions if requested

        Args:
            file_path: Destination file path.
            content: Content to write.
            action_id: Polkit action ID for authorization.
            preserve_permissions: Preserve original file permissions.

        Returns:
            PolkitResult with outcome.
        """
        file_path = Path(file_path)

        # Get original permissions if preserving
        original_mode = None
        original_owner = None
        original_group = None

        if preserve_permissions and file_path.exists():
            stat = file_path.stat()
            original_mode = stat.st_mode
            original_owner = stat.st_uid
            original_group = stat.st_gid

        # Write to temp file
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".elle-tmp",
                delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
        except Exception as e:
            return PolkitResult(
                success=False,
                error=f"Failed to create temp file: {e}",
                exit_code=-1,
            )

        try:
            # Copy temp file to destination using pkexec
            result = await self._run_pkexec(
                ["cp", "--preserve=timestamps", tmp_path, str(file_path)],
                action_id,
            )

            if not result.success:
                return result

            # Restore permissions if needed
            if preserve_permissions and original_mode is not None:
                chmod_result = await self._run_pkexec(
                    ["chmod", oct(original_mode)[-3:], str(file_path)],
                    action_id,
                )
                if not chmod_result.success:
                    logger.warning(f"Failed to restore permissions: {chmod_result.error}")

                if original_owner is not None and original_group is not None:
                    chown_result = await self._run_pkexec(
                        ["chown", f"{original_owner}:{original_group}", str(file_path)],
                        action_id,
                    )
                    if not chown_result.success:
                        logger.warning(f"Failed to restore ownership: {chown_result.error}")

            logger.info(f"Wrote privileged file: {file_path}")
            return result

        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    async def run_validator(
        self,
        command: list[str],
        timeout: float | None = None,
    ) -> PolkitResult:
        """Run a validation command with privilege if needed.

        Args:
            command: Command and arguments.
            timeout: Timeout in seconds.

        Returns:
            PolkitResult with validation output.
        """
        # First try without privilege
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
            )

            return PolkitResult(
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr,
                exit_code=result.returncode,
            )

        except PermissionError:
            # Need privilege
            return await self._run_pkexec(
                command,
                PolkitAction.VALIDATE_CONFIG,
                timeout,
            )
        except subprocess.TimeoutExpired:
            return PolkitResult(
                success=False,
                error=f"Validation timed out after {timeout or self._timeout}s",
                exit_code=-1,
            )
        except FileNotFoundError:
            return PolkitResult(
                success=False,
                error=f"Command not found: {command[0]}",
                exit_code=-1,
            )

    async def read_privileged(
        self,
        file_path: str | Path,
    ) -> PolkitResult:
        """Read a privileged file.

        Args:
            file_path: Path to the file.

        Returns:
            PolkitResult with file content in output.
        """
        return await self._run_pkexec(
            ["cat", str(file_path)],
            PolkitAction.READ_PRIVILEGED_LOGS,
        )

    async def delete_privileged(
        self,
        file_path: str | Path,
    ) -> PolkitResult:
        """Delete a privileged file.

        Args:
            file_path: Path to delete.

        Returns:
            PolkitResult with outcome.
        """
        return await self._run_pkexec(
            ["rm", "-f", str(file_path)],
            PolkitAction.EDIT_SYSTEM_CONFIG,
        )

    async def create_directory(
        self,
        path: str | Path,
        mode: str = "755",
    ) -> PolkitResult:
        """Create a privileged directory.

        Args:
            path: Directory path.
            mode: Directory permissions.

        Returns:
            PolkitResult with outcome.
        """
        return await self._run_pkexec(
            ["mkdir", "-p", "-m", mode, str(path)],
            PolkitAction.EDIT_SYSTEM_CONFIG,
        )

    async def create_backup(
        self,
        source: str | Path,
        dest: str | Path,
    ) -> PolkitResult:
        """Create a backup of a privileged file.

        Args:
            source: Source file path.
            dest: Backup destination path.

        Returns:
            PolkitResult with outcome.
        """
        dest_path = Path(dest)

        # Create backup directory if needed
        mkdir_result = await self.create_directory(dest_path.parent)
        if not mkdir_result.success:
            return mkdir_result

        return await self._run_pkexec(
            ["cp", "--preserve=all", str(source), str(dest)],
            PolkitAction.EDIT_SYSTEM_CONFIG,
        )

    async def restore_backup(
        self,
        backup_path: str | Path,
        target_path: str | Path,
    ) -> PolkitResult:
        """Restore a file from backup.

        Args:
            backup_path: Backup file path.
            target_path: Target restore path.

        Returns:
            PolkitResult with outcome.
        """
        return await self._run_pkexec(
            ["cp", "--preserve=all", str(backup_path), str(target_path)],
            PolkitAction.EDIT_SYSTEM_CONFIG,
        )

    def check_authorization(self, action_id: str) -> bool:
        """Check if the current user is authorized for an action.

        Note: This only checks if the action is likely to succeed,
        actual authorization happens at pkexec time.

        Args:
            action_id: Polkit action identifier.

        Returns:
            True if likely authorized.
        """
        # Check if running as root
        if os.geteuid() == 0:
            return True

        # Check if pkexec is available
        try:
            subprocess.run(
                ["which", "pkexec"],
                capture_output=True,
                check=True,
            )
            return True
        except Exception:
            return False

    # =========================================================================
    # Private Methods
    # =========================================================================

    async def _run_pkexec(
        self,
        command: list[str],
        action_id: str,
        timeout: float | None = None,
    ) -> PolkitResult:
        """Run a command via pkexec.

        Args:
            command: Command and arguments.
            action_id: Polkit action ID.
            timeout: Timeout in seconds.

        Returns:
            PolkitResult with outcome.
        """
        # Build pkexec command
        # Note: --disable-internal-agent prevents GUI prompts in headless mode
        pkexec_cmd = ["pkexec", *command]

        try:
            result = subprocess.run(
                pkexec_cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
            )

            # pkexec returns 126 for auth failure, 127 for not found
            if result.returncode == 126:
                return PolkitResult(
                    success=False,
                    authorized=False,
                    error="Authorization denied",
                    exit_code=result.returncode,
                )
            elif result.returncode == 127:
                return PolkitResult(
                    success=False,
                    error=f"Command not found: {command[0]}",
                    exit_code=result.returncode,
                )

            return PolkitResult(
                success=result.returncode == 0,
                authorized=True,
                output=result.stdout,
                error=result.stderr,
                exit_code=result.returncode,
            )

        except subprocess.TimeoutExpired:
            return PolkitResult(
                success=False,
                error=f"Operation timed out after {timeout or self._timeout}s",
                exit_code=-1,
            )
        except FileNotFoundError:
            return PolkitResult(
                success=False,
                error="pkexec not found - Polkit may not be installed",
                exit_code=-1,
            )
        except Exception as e:
            return PolkitResult(
                success=False,
                error=str(e),
                exit_code=-1,
            )


# =============================================================================
# Module-level convenience functions
# =============================================================================

_helper: PolkitHelper | None = None


def get_helper() -> PolkitHelper:
    """Get the global PolkitHelper instance.

    Returns:
        PolkitHelper instance.
    """
    global _helper
    if _helper is None:
        _helper = PolkitHelper()
    return _helper


async def write_privileged(
    file_path: str | Path,
    content: str,
) -> PolkitResult:
    """Write to a privileged file (convenience function).

    Args:
        file_path: Destination file path.
        content: Content to write.

    Returns:
        PolkitResult with outcome.
    """
    return await get_helper().write_privileged(file_path, content)


async def run_validator(command: list[str]) -> PolkitResult:
    """Run a validation command (convenience function).

    Args:
        command: Command and arguments.

    Returns:
        PolkitResult with outcome.
    """
    return await get_helper().run_validator(command)


def is_authorized(action_id: str = PolkitAction.EDIT_SYSTEM_CONFIG) -> bool:
    """Check if likely authorized for an action (convenience function).

    Args:
        action_id: Polkit action identifier.

    Returns:
        True if likely authorized.
    """
    return get_helper().check_authorization(action_id)
