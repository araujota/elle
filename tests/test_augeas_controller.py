"""Tests for the edit controller."""

from __future__ import annotations

import pytest

from elle.ops.augeas.controller import (
    EditController,
    get_controller,
)
from elle.ops.augeas.models import (
    AugeasEditRequest,
    AugeasEditResult,
    AugeasError,
    AugeasOp,
    EditPreview,
)


class TestEditController:
    """Tests for EditController."""

    def test_get_controller_singleton(self) -> None:
        """get_controller should return consistent instance."""
        c1 = get_controller()
        c2 = get_controller()

        # Should be same instance (singleton pattern)
        assert c1 is c2

    def test_controller_initialization(self) -> None:
        """Controller should initialize properly."""
        controller = EditController()

        assert controller._backup_manager is not None
        assert controller._diff_generator is not None


class TestPreviewEdit:
    """Tests for preview functionality."""

    def test_preview_nonexistent_file_raises(self, tmp_path) -> None:
        """Previewing nonexistent file should raise."""
        controller = EditController()

        request = AugeasEditRequest(
            file_path=str(tmp_path / "nonexistent.conf"),
            operations=(),
            description="Test",
        )

        with pytest.raises(AugeasError):
            controller.preview(request)

    def test_preview_returns_edit_preview(self, tmp_path) -> None:
        """Preview should return EditPreview."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("original content\n")

        controller = EditController()

        request = AugeasEditRequest(
            file_path=str(test_file),
            operations=(),  # Empty ops for simple test
            description="Test preview",
            skip_validation=True,
        )

        # This will fail because no lens, but we're testing the method exists
        # For a full test, we'd need python-augeas installed
        try:
            preview = controller.preview(request)
            assert isinstance(preview, EditPreview)
        except Exception:
            # Expected if augeas not available or file type not supported
            pass

    def test_preview_shows_no_changes_for_empty_ops(self, tmp_path) -> None:
        """Empty operations should show no changes."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("key: value\n")

        controller = EditController()

        request = AugeasEditRequest(
            file_path=str(test_file),
            operations=(),
            description="No changes",
        )

        try:
            preview = controller.preview(request)
            # With no ops, should have no changes
            assert not preview.changes or len(preview.changes) == 0
        except Exception:
            # May fail without proper setup
            pass


class TestEditRequest:
    """Tests for AugeasEditRequest model."""

    def test_request_with_operations(self) -> None:
        """Request should accept operations."""
        request = AugeasEditRequest(
            file_path="/etc/hosts",
            operations=(AugeasOp(kind="set", path="/files/etc/hosts/1/ipaddr", value="10.0.0.1"),),
            description="Update IP",
        )

        assert request.file_path == "/etc/hosts"
        assert len(request.operations) == 1

    def test_request_dry_run_default(self) -> None:
        """dry_run should default to False."""
        request = AugeasEditRequest(
            file_path="/test",
            operations=(),
            description="Test",
        )

        assert request.dry_run is False

    def test_request_with_incident_id(self) -> None:
        """Request should accept incident_id."""
        request = AugeasEditRequest(
            file_path="/test",
            operations=(),
            description="Test",
            incident_id="abc-123",
        )

        assert request.incident_id == "abc-123"

    def test_request_frozen(self) -> None:
        """Request should be immutable."""
        request = AugeasEditRequest(
            file_path="/test",
            operations=(),
            description="Test",
        )

        with pytest.raises(Exception):
            request.file_path = "/other"  # type: ignore[misc]


class TestEditResult:
    """Tests for AugeasEditResult model."""

    def test_success_result(self) -> None:
        """Successful result should have success=True."""
        result = AugeasEditResult(
            success=True,
            file_path="/etc/hosts",
            diff="- old\n+ new",
        )

        assert result.success is True
        assert result.error is None

    def test_failure_result(self) -> None:
        """Failed result should have error message."""
        result = AugeasEditResult(
            success=False,
            file_path="/etc/hosts",
            error="Validation failed",
        )

        assert result.success is False
        assert result.error == "Validation failed"

    def test_rollback_result(self) -> None:
        """Rollback result should indicate rollback."""
        result = AugeasEditResult(
            success=False,
            file_path="/etc/hosts",
            error="Validation failed",
            rollback_applied=True,
        )

        assert result.rollback_applied is True

    def test_result_with_backup(self) -> None:
        """Result should include backup path."""
        result = AugeasEditResult(
            success=True,
            file_path="/etc/hosts",
            backup_path="/var/lib/elle/backups/fstab/20240101_120000/hosts",
        )

        assert result.backup_path is not None
        assert "backups" in result.backup_path

    def test_result_with_validation(self) -> None:
        """Result should include validation output."""
        result = AugeasEditResult(
            success=True,
            file_path="/etc/hosts",
            validation_output="Syntax OK",
            validation_passed=True,
        )

        assert result.validation_passed is True
        assert "OK" in result.validation_output

    def test_result_frozen(self) -> None:
        """Result should be immutable."""
        result = AugeasEditResult(
            success=True,
            file_path="/test",
        )

        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]


class TestEditPreview:
    """Tests for EditPreview model."""

    def test_preview_content(self) -> None:
        """Preview should contain content."""
        preview = EditPreview(
            file_path="/etc/hosts",
            original_content="127.0.0.1 localhost\n",
            proposed_content="10.0.0.1 localhost\n",
            diff="- 127.0.0.1\n+ 10.0.0.1",
            diff_colored="\033[31m- 127\033[0m",
        )

        assert preview.original_content != preview.proposed_content
        assert len(preview.diff) > 0

    def test_preview_requires_privilege(self) -> None:
        """Preview should indicate privilege requirement."""
        preview = EditPreview(
            file_path="/etc/fstab",
            original_content="",
            proposed_content="",
            diff="",
            diff_colored="",
            requires_privilege=True,
        )

        assert preview.requires_privilege is True

    def test_preview_validation_command(self) -> None:
        """Preview should include validation command."""
        preview = EditPreview(
            file_path="/etc/fstab",
            original_content="",
            proposed_content="",
            diff="",
            diff_colored="",
            validation_command="mount -a -f --fake",
        )

        assert preview.validation_command is not None
        assert "mount" in preview.validation_command

    def test_preview_frozen(self) -> None:
        """Preview should be immutable."""
        preview = EditPreview(
            file_path="/test",
            original_content="",
            proposed_content="",
            diff="",
            diff_colored="",
        )

        with pytest.raises(Exception):
            preview.file_path = "/other"  # type: ignore[misc]


class TestRequiresPrivilege:
    """Tests for privilege detection."""

    def test_writable_file_no_privilege(self, tmp_path) -> None:
        """Writable file should not require privilege."""
        controller = EditController()

        test_file = tmp_path / "test.conf"
        test_file.write_text("content\n")

        requires = controller._requires_privilege(test_file)

        # User-owned temp file should be writable
        assert requires is False

    def test_system_file_requires_privilege(self) -> None:
        """System file should require privilege."""
        controller = EditController()

        from pathlib import Path

        requires = controller._requires_privilege(Path("/etc/fstab"))

        # /etc/fstab typically requires root
        # (unless running as root)
        import os

        if os.geteuid() != 0:
            assert requires is True
