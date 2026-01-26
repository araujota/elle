"""Edit controller - orchestrates the config file edit workflow.

Coordinates the complete edit lifecycle:
1. Backup → 2. Preview → 3. Apply → 4. Validate → 5. Commit/Rollback

Integrates with Incident Vault for audit trail.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from elle.common.pydantic_compat import safe_model_dump
from elle.ops.augeas.backup import BackupManager
from elle.ops.augeas.backup import get_manager as get_backup_manager
from elle.ops.augeas.diff import DiffGenerator, generate_diff
from elle.ops.augeas.lenses import is_yaml_file
from elle.ops.augeas.models import (
    AugeasChange,
    AugeasEditRequest,
    AugeasEditResult,
    AugeasError,
    AugeasPermissionError,
    BatchEditRequest,
    BatchEditResult,
    EditPreview,
)
from elle.ops.augeas.validators import validate_config

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EditController:
    """Orchestrates the config file edit workflow.

    Manages the complete edit lifecycle with safety guarantees:
    - Mandatory preview/diff before any changes
    - Automatic backup creation
    - Validation after apply
    - Automatic rollback on failure
    - Audit trail via Incident Vault

    Usage:
        controller = EditController()

        # Preview changes first
        preview = controller.preview(request)
        print(preview.diff_colored)

        # Apply if approved
        result = controller.execute(request)
        if result.success:
            print("Changes applied successfully")
        else:
            print(f"Failed: {result.error}")
    """

    def __init__(
        self,
        backup_manager: BackupManager | None = None,
        diff_generator: DiffGenerator | None = None,
    ) -> None:
        """Initialize the edit controller.

        Args:
            backup_manager: Custom backup manager (uses default if None).
            diff_generator: Custom diff generator (uses default if None).
        """
        self._backup_manager = backup_manager or get_backup_manager()
        self._diff_generator = diff_generator or DiffGenerator()

    # =========================================================================
    # Preview
    # =========================================================================

    def preview(self, request: AugeasEditRequest) -> EditPreview:
        """Generate a preview of proposed changes.

        Does not modify any files - only shows what would change.

        Args:
            request: Edit request to preview.

        Returns:
            EditPreview with diff and proposed changes.
        """
        file_path = Path(request.file_path)

        # Check if file exists
        if not file_path.exists():
            raise AugeasError(f"File does not exist: {request.file_path}")

        # Read original content
        original_content = file_path.read_text()

        # Generate modified content without applying
        if is_yaml_file(file_path):
            modified_content, changes = self._preview_yaml(request, original_content)
        else:
            modified_content, changes = self._preview_augeas(request, original_content)

        # Generate diff
        diff_result = generate_diff(
            original_content,
            modified_content,
            request.file_path,
        )

        # Check if privileged access required
        requires_privilege = self._requires_privilege(file_path)

        # Get validation command for display
        from elle.ops.augeas.validators import get_validation_command

        validation_cmd = get_validation_command(file_path)

        return EditPreview(
            file_path=request.file_path,
            original_content=original_content,
            proposed_content=modified_content,
            diff=diff_result.unified,
            diff_colored=diff_result.colored,
            changes=tuple(changes),
            requires_privilege=requires_privilege,
            validation_command=validation_cmd,
        )

    def _preview_augeas(
        self,
        request: AugeasEditRequest,
        original: str,
    ) -> tuple[str, list[AugeasChange]]:
        """Preview changes using Augeas without saving.

        Args:
            request: Edit request.
            original: Original file content.

        Returns:
            Tuple of (modified_content, changes).
        """
        import shutil

        # Create engine with a temporary root to avoid modifying real files
        import tempfile

        from elle.ops.augeas.engine import AugeasEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy file to temp location
            tmp_file = Path(tmpdir) / Path(request.file_path).name
            tmp_file.write_text(original)

            # Also need to create the directory structure Augeas expects
            full_tmp_path = Path(tmpdir) / request.file_path.lstrip("/")
            full_tmp_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(tmp_file, full_tmp_path)

            # Load in Augeas with temp root
            try:
                engine = AugeasEngine(root=tmpdir)
                engine.load(request.file_path, request.lens)

                # Apply operations
                engine.apply_ops(list(request.operations))

                # Get changes
                changes = engine.get_changes()

                # Save to temp location
                engine.save()
                engine.close()

                # Read modified content
                modified = full_tmp_path.read_text()

                return modified, changes

            except Exception as e:
                logger.error(f"Preview failed: {e}")
                raise

    def _preview_yaml(
        self,
        request: AugeasEditRequest,
        original: str,
    ) -> tuple[str, list[AugeasChange]]:
        """Preview YAML changes without saving.

        Args:
            request: Edit request.
            original: Original file content.

        Returns:
            Tuple of (modified_content, changes).
        """
        from elle.ops.augeas.yaml_handler import YAMLHandler, is_available

        if not is_available():
            raise AugeasError("ruamel.yaml is not installed. Install with: pip install ruamel.yaml")

        handler = YAMLHandler()

        # Parse original
        import io

        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(io.StringIO(original))

        if data is None:
            data = {}

        # Apply operations
        changes: list[AugeasChange] = []
        for op in request.operations:
            if op.kind == "set" and op.value is not None:
                old_value = handler.get_path(data, op.path)
                handler.set_path(data, op.path, op.value)
                changes.append(
                    AugeasChange(
                        path=op.path,
                        old_value=str(old_value) if old_value is not None else None,
                        new_value=op.value,
                        operation="set",
                    )
                )
            elif op.kind == "rm":
                old_value = handler.get_path(data, op.path)
                handler.delete_path(data, op.path)
                changes.append(
                    AugeasChange(
                        path=op.path,
                        old_value=str(old_value) if old_value is not None else None,
                        new_value=None,
                        operation="rm",
                    )
                )

        # Generate modified content
        modified = handler.dumps(data)

        return modified, changes

    # =========================================================================
    # Execute
    # =========================================================================

    async def execute(self, request: AugeasEditRequest) -> AugeasEditResult:
        """Execute an edit request with full workflow.

        Workflow:
        1. Check permissions
        2. Create backup
        3. Apply changes
        4. Validate configuration
        5. Commit or rollback

        Args:
            request: Edit request to execute.

        Returns:
            AugeasEditResult with outcome and details.
        """
        file_path = Path(request.file_path)
        backup_path: str | None = None
        requires_privilege = self._requires_privilege(file_path)

        # Check if file exists
        if not file_path.exists():
            return AugeasEditResult(
                success=False,
                file_path=request.file_path,
                error=f"File does not exist: {request.file_path}",
                requires_privilege=requires_privilege,
            )

        # Read original content for diff
        original_content = file_path.read_text()

        try:
            # 1. Create backup (unless skipped)
            if not request.skip_backup:
                try:
                    backup_record = self._backup_manager.backup(
                        file_path,
                        metadata={
                            "description": request.description,
                            "incident_id": request.incident_id,
                        },
                    )
                    backup_path = backup_record.backup_path
                    logger.info(f"Created backup: {backup_path}")
                except Exception as e:
                    logger.error(f"Backup failed: {e}")
                    return AugeasEditResult(
                        success=False,
                        file_path=request.file_path,
                        error=f"Backup failed: {e}",
                        requires_privilege=requires_privilege,
                    )

            # 2. Handle dry run
            if request.dry_run:
                preview = self.preview(request)
                return AugeasEditResult(
                    success=True,
                    file_path=request.file_path,
                    diff=preview.diff,
                    diff_colored=preview.diff_colored,
                    backup_path=backup_path,
                    changes=preview.changes,
                    requires_privilege=requires_privilege,
                )

            # 3. Apply changes
            if is_yaml_file(file_path):
                changes = await self._apply_yaml(request)
            else:
                changes = await self._apply_augeas(request)

            # 4. Read modified content for diff
            modified_content = file_path.read_text()
            diff_result = generate_diff(
                original_content,
                modified_content,
                request.file_path,
            )

            # 5. Validate (unless skipped)
            validation_passed = True
            validation_output = ""

            if not request.skip_validation:
                validation_result = await validate_config(file_path)
                validation_passed = validation_result.valid
                validation_output = validation_result.output or validation_result.error or ""

                if not validation_passed:
                    logger.warning(f"Validation failed for {file_path}: {validation_output}")

                    # 6. Rollback on validation failure
                    if backup_path:
                        try:
                            self._backup_manager.restore(backup_path, file_path)
                            logger.info(f"Rolled back {file_path} from {backup_path}")
                            return AugeasEditResult(
                                success=False,
                                file_path=request.file_path,
                                diff=diff_result.unified,
                                diff_colored=diff_result.colored,
                                backup_path=backup_path,
                                validation_output=validation_output,
                                validation_passed=False,
                                error="Validation failed, changes rolled back",
                                rollback_applied=True,
                                changes=tuple(changes),
                                requires_privilege=requires_privilege,
                            )
                        except Exception as e:
                            logger.error(f"Rollback failed: {e}")
                            return AugeasEditResult(
                                success=False,
                                file_path=request.file_path,
                                diff=diff_result.unified,
                                diff_colored=diff_result.colored,
                                backup_path=backup_path,
                                validation_output=validation_output,
                                validation_passed=False,
                                error=f"Validation failed and rollback failed: {e}",
                                rollback_applied=False,
                                changes=tuple(changes),
                                requires_privilege=requires_privilege,
                            )

            # 7. Record action and config state to Incident Vault
            if request.incident_id:
                try:
                    from elle.daemon.incidents.semantic_diff import build_config_state
                    from elle.daemon.incidents.store import append_action, store_config_state

                    # Record the action
                    append_action(
                        request.incident_id,
                        kind="edit",
                        command=f"augeas:{request.file_path}",
                        payload={
                            "file_path": request.file_path,
                            "description": request.description,
                            "changes": [safe_model_dump(c) for c in changes],
                        },
                        stdout=diff_result.unified,
                        success=True,
                    )

                    # Compute pre-edit hash from original content
                    import hashlib

                    pre_hash = hashlib.sha256(original_content.encode()).hexdigest()

                    # Build and store config state with hashes
                    config_state = build_config_state(
                        path=request.file_path,
                        sha256_before=pre_hash,
                        backup_path=backup_path,
                    )
                    store_config_state(
                        incident_id=request.incident_id,
                        which="post",
                        config_state=config_state,
                    )

                except ImportError:
                    logger.debug("Incident store not available")
                except Exception as e:
                    logger.warning(f"Failed to record action to incident vault: {e}")

            return AugeasEditResult(
                success=True,
                file_path=request.file_path,
                diff=diff_result.unified,
                diff_colored=diff_result.colored,
                backup_path=backup_path,
                validation_output=validation_output,
                validation_passed=validation_passed,
                changes=tuple(changes),
                requires_privilege=requires_privilege,
            )

        except AugeasPermissionError:
            raise
        except Exception as e:
            logger.exception(f"Edit failed: {e}")

            # Try to rollback if we have a backup
            rollback_applied = False
            if backup_path:
                try:
                    self._backup_manager.restore(backup_path, file_path)
                    rollback_applied = True
                    logger.info(f"Rolled back {file_path} after error")
                except Exception as restore_error:
                    logger.error(f"Rollback after error failed: {restore_error}")

            return AugeasEditResult(
                success=False,
                file_path=request.file_path,
                backup_path=backup_path,
                error=str(e),
                rollback_applied=rollback_applied,
                requires_privilege=requires_privilege,
            )

    async def _apply_augeas(
        self,
        request: AugeasEditRequest,
    ) -> list[AugeasChange]:
        """Apply changes using Augeas.

        Args:
            request: Edit request.

        Returns:
            List of changes made.
        """
        from elle.ops.augeas.engine import AugeasEngine

        engine = AugeasEngine()
        try:
            engine.load(request.file_path, request.lens)
            engine.apply_ops(list(request.operations))
            changes = engine.get_changes()
            engine.save()
            return changes
        finally:
            engine.close()

    async def _apply_yaml(
        self,
        request: AugeasEditRequest,
    ) -> list[AugeasChange]:
        """Apply changes to YAML file.

        Args:
            request: Edit request.

        Returns:
            List of changes made.
        """
        from elle.ops.augeas.yaml_handler import YAMLHandler

        handler = YAMLHandler()
        data = handler.load(request.file_path)

        for op in request.operations:
            if op.kind == "set" and op.value is not None:
                handler.set_path(data, op.path, op.value)
            elif op.kind == "rm":
                handler.delete_path(data, op.path)

        handler.save(request.file_path, data)

        # Convert YAML changes to Augeas changes
        changes: list[AugeasChange] = []
        for yc in handler.get_changes():
            changes.append(
                AugeasChange(
                    path=yc.path,
                    old_value=str(yc.old_value) if yc.old_value is not None else None,
                    new_value=str(yc.new_value) if yc.new_value is not None else None,
                    operation=yc.operation,  # type: ignore
                )
            )

        return changes

    # =========================================================================
    # Batch Operations
    # =========================================================================

    async def execute_batch(
        self,
        request: BatchEditRequest,
    ) -> BatchEditResult:
        """Execute multiple edits atomically.

        All edits succeed or all are rolled back.

        Args:
            request: Batch edit request.

        Returns:
            BatchEditResult with all results.
        """
        results: list[AugeasEditResult] = []
        backups: list[tuple[str, str]] = []  # (backup_path, original_path)

        try:
            for edit_request in request.edits:
                # Override incident_id from batch
                if request.incident_id and not edit_request.incident_id:
                    edit_request = AugeasEditRequest(
                        file_path=edit_request.file_path,
                        lens=edit_request.lens,
                        operations=edit_request.operations,
                        description=edit_request.description,
                        incident_id=request.incident_id,
                        dry_run=request.dry_run,
                        skip_backup=edit_request.skip_backup,
                        skip_validation=edit_request.skip_validation,
                    )

                result = await self.execute(edit_request)
                results.append(result)

                if result.backup_path:
                    backups.append((result.backup_path, result.file_path))

                if not result.success and not request.dry_run:
                    # Rollback all previous edits
                    for backup_path, original_path in reversed(backups[:-1]):
                        try:
                            self._backup_manager.restore(backup_path, original_path)
                            logger.info(f"Batch rollback: restored {original_path}")
                        except Exception as e:
                            logger.error(f"Batch rollback failed for {original_path}: {e}")

                    return BatchEditResult(
                        success=False,
                        results=tuple(results),
                        rollback_applied=True,
                        error=f"Edit {len(results)} failed: {result.error}",
                    )

            return BatchEditResult(
                success=True,
                results=tuple(results),
            )

        except Exception as e:
            # Rollback on unexpected error
            for backup_path, original_path in reversed(backups):
                with contextlib.suppress(Exception):
                    self._backup_manager.restore(backup_path, original_path)

            return BatchEditResult(
                success=False,
                results=tuple(results),
                rollback_applied=True,
                error=str(e),
            )

    # =========================================================================
    # Rollback
    # =========================================================================

    def rollback(
        self,
        file_path: str | Path,
        backup_path: str | Path | None = None,
    ) -> bool:
        """Rollback a file to its backup.

        Args:
            file_path: Original file path.
            backup_path: Specific backup to restore (uses latest if None).

        Returns:
            True if rollback succeeded.
        """
        file_path = Path(file_path)

        if backup_path:
            return self._backup_manager.restore(backup_path, file_path)

        # Find latest backup
        latest = self._backup_manager.get_latest_backup(file_path)
        if latest:
            return self._backup_manager.restore(latest, file_path)

        logger.warning(f"No backup found for {file_path}")
        return False

    # =========================================================================
    # Helpers
    # =========================================================================

    def _requires_privilege(self, file_path: Path) -> bool:
        """Check if privileged access is required for a file.

        Args:
            file_path: Path to check.

        Returns:
            True if file requires elevated privileges.
        """
        # Check if file is writable
        if file_path.exists():
            return not os.access(file_path, os.W_OK)

        # Check if parent directory is writable
        parent = file_path.parent
        if parent.exists():
            return not os.access(parent, os.W_OK)

        return True


# =============================================================================
# Module-level convenience functions
# =============================================================================

_controller: EditController | None = None


def get_controller() -> EditController:
    """Get the global EditController instance.

    Returns:
        EditController instance.
    """
    global _controller
    if _controller is None:
        _controller = EditController()
    return _controller


def preview_edit(request: AugeasEditRequest) -> EditPreview:
    """Preview an edit request (convenience function).

    Args:
        request: Edit request to preview.

    Returns:
        EditPreview with diff and changes.
    """
    return get_controller().preview(request)


async def execute_edit(request: AugeasEditRequest) -> AugeasEditResult:
    """Execute an edit request (convenience function).

    Args:
        request: Edit request to execute.

    Returns:
        AugeasEditResult with outcome.
    """
    return await get_controller().execute(request)


async def execute_batch_edit(request: BatchEditRequest) -> BatchEditResult:
    """Execute a batch edit request (convenience function).

    Args:
        request: Batch edit request to execute.

    Returns:
        BatchEditResult with all outcomes.
    """
    return await get_controller().execute_batch(request)


def rollback_edit(
    file_path: str | Path,
    backup_path: str | Path | None = None,
) -> bool:
    """Rollback a file to its backup (convenience function).

    Args:
        file_path: Original file path.
        backup_path: Specific backup to restore.

    Returns:
        True if rollback succeeded.
    """
    return get_controller().rollback(file_path, backup_path)
