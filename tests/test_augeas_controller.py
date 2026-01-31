from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.ops.augeas.models import (
    AugeasChange,
    AugeasEditRequest,
    AugeasEditResult,
    AugeasError,
    AugeasOp,
    AugeasPermissionError,
    BatchEditRequest,
    BatchEditResult,
    EditPreview,
)

# ---------------------------------------------------------------------------
# EditController
# ---------------------------------------------------------------------------


class TestEditController:
    def _make_controller(self):
        from elle.ops.augeas.controller import EditController

        mock_backup = MagicMock()
        mock_diff = MagicMock()
        return EditController(backup_manager=mock_backup, diff_generator=mock_diff)

    # -- preview --

    def test_preview_file_not_found(self, tmp_path):
        ctrl = self._make_controller()
        req = AugeasEditRequest(
            file_path=str(tmp_path / "nonexistent.conf"),
            description="test",
            operations=(),
        )
        with pytest.raises(AugeasError, match="does not exist"):
            ctrl.preview(req)

    def test_preview_augeas(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("key = value\n")

        mock_changes = [AugeasChange(path="/p", old_value="old", new_value="new", operation="set")]

        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_preview_augeas", return_value=("key = newval\n", mock_changes)),
            patch("elle.ops.augeas.controller.generate_diff") as mock_diff,
            patch("elle.ops.augeas.validators.get_validation_command", return_value=None),
        ):
            mock_diff.return_value = MagicMock(unified="--- a\n+++ b\n", colored="colored diff")
            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
                operations=(AugeasOp(kind="set", path="/p", value="new"),),
            )
            preview = ctrl.preview(req)
            assert isinstance(preview, EditPreview)
            assert preview.diff == "--- a\n+++ b\n"

    def test_preview_yaml(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")

        mock_changes = [AugeasChange(path="key", old_value="value", new_value="new", operation="set")]
        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=True),
            patch.object(ctrl, "_preview_yaml", return_value=("key: new\n", mock_changes)),
            patch("elle.ops.augeas.controller.generate_diff") as mock_diff,
            patch("elle.ops.augeas.validators.get_validation_command", return_value=None),
        ):
            mock_diff.return_value = MagicMock(unified="diff", colored="colored")
            req = AugeasEditRequest(
                file_path=str(f),
                description="test yaml",
                operations=(),
            )
            preview = ctrl.preview(req)
            assert preview.diff == "diff"

    # -- execute --

    @pytest.mark.asyncio
    async def test_execute_file_not_found(self, tmp_path):
        ctrl = self._make_controller()
        req = AugeasEditRequest(
            file_path=str(tmp_path / "missing.conf"),
            description="test",
        )
        result = await ctrl.execute(req)
        assert result.success is False
        assert "does not exist" in result.error

    @pytest.mark.asyncio
    async def test_execute_dry_run(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec

        mock_preview = EditPreview(
            file_path=str(f),
            original_content="content\n",
            proposed_content="new content\n",
            diff="diff text",
            diff_colored="colored",
        )
        with patch.object(ctrl, "preview", return_value=mock_preview):
            req = AugeasEditRequest(
                file_path=str(f),
                description="dry run test",
                dry_run=True,
            )
            result = await ctrl.execute(req)
            assert result.success is True
            assert result.diff == "diff text"

    @pytest.mark.asyncio
    async def test_execute_backup_failure(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")
        ctrl._backup_manager.backup.side_effect = RuntimeError("backup fail")

        req = AugeasEditRequest(
            file_path=str(f),
            description="test",
        )
        result = await ctrl.execute(req)
        assert result.success is False
        assert "Backup failed" in result.error

    @pytest.mark.asyncio
    async def test_execute_skip_backup(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_changes = [AugeasChange(path="/p", new_value="v", operation="set")]
        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, return_value=mock_changes),
            patch("elle.ops.augeas.controller.generate_diff") as mock_diff,
            patch("elle.ops.augeas.controller.validate_config", new_callable=AsyncMock) as mock_val,
        ):
            mock_diff.return_value = MagicMock(unified="diff", colored="colored")
            mock_val.return_value = MagicMock(valid=True, output="ok", error=None)

            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
                skip_backup=True,
                skip_validation=True,
            )
            result = await ctrl.execute(req)
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_validation_fails_with_rollback(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec
        ctrl._backup_manager.restore.return_value = True

        mock_changes = [AugeasChange(path="/p", new_value="v", operation="set")]
        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, return_value=mock_changes),
            patch("elle.ops.augeas.controller.generate_diff") as mock_diff,
            patch("elle.ops.augeas.controller.validate_config", new_callable=AsyncMock) as mock_val,
        ):
            mock_diff.return_value = MagicMock(unified="diff", colored="colored")
            mock_val.return_value = MagicMock(valid=False, output="syntax error", error=None)

            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
            )
            result = await ctrl.execute(req)
            assert result.success is False
            assert result.rollback_applied is True

    @pytest.mark.asyncio
    async def test_execute_validation_fails_rollback_fails(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec
        ctrl._backup_manager.restore.side_effect = RuntimeError("restore fail")

        mock_changes = [AugeasChange(path="/p", new_value="v", operation="set")]
        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, return_value=mock_changes),
            patch("elle.ops.augeas.controller.generate_diff") as mock_diff,
            patch("elle.ops.augeas.controller.validate_config", new_callable=AsyncMock) as mock_val,
        ):
            mock_diff.return_value = MagicMock(unified="diff", colored="colored")
            mock_val.return_value = MagicMock(valid=False, output="syntax error", error=None)

            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
            )
            result = await ctrl.execute(req)
            assert result.success is False
            assert result.rollback_applied is False

    @pytest.mark.asyncio
    async def test_execute_exception_with_rollback(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec
        ctrl._backup_manager.restore.return_value = True

        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        ):
            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
            )
            result = await ctrl.execute(req)
            assert result.success is False
            assert result.rollback_applied is True

    @pytest.mark.asyncio
    async def test_execute_permission_error_re_raised(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec

        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, side_effect=AugeasPermissionError("/etc/test")),
        ):
            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
            )
            with pytest.raises(AugeasPermissionError):
                await ctrl.execute(req)

    @pytest.mark.asyncio
    async def test_execute_yaml(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec

        mock_changes = [AugeasChange(path="key", new_value="new", operation="set")]
        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=True),
            patch.object(ctrl, "_apply_yaml", new_callable=AsyncMock, return_value=mock_changes),
            patch("elle.ops.augeas.controller.generate_diff") as mock_diff,
            patch("elle.ops.augeas.controller.validate_config", new_callable=AsyncMock) as mock_val,
        ):
            mock_diff.return_value = MagicMock(unified="diff", colored="colored")
            mock_val.return_value = MagicMock(valid=True, output="ok", error=None)

            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
            )
            result = await ctrl.execute(req)
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_with_incident_recording(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec

        mock_changes = [AugeasChange(path="/p", new_value="v", operation="set")]
        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, return_value=mock_changes),
            patch("elle.ops.augeas.controller.generate_diff") as mock_diff,
            patch("elle.ops.augeas.controller.validate_config", new_callable=AsyncMock) as mock_val,
        ):
            mock_diff.return_value = MagicMock(unified="diff", colored="colored")
            mock_val.return_value = MagicMock(valid=True, output="ok", error=None)

            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
                incident_id="INC-001",
                skip_validation=True,
            )
            # The incident recording code does import-time checks,
            # we just make sure it doesn't crash
            result = await ctrl.execute(req)
            assert result.success is True

    # -- rollback --

    def test_rollback_with_path(self, tmp_path):
        ctrl = self._make_controller()
        ctrl._backup_manager.restore.return_value = True
        assert ctrl.rollback("/etc/test", "/backup/path") is True

    def test_rollback_latest(self, tmp_path):
        ctrl = self._make_controller()
        ctrl._backup_manager.get_latest_backup.return_value = "/backup/latest"
        ctrl._backup_manager.restore.return_value = True
        assert ctrl.rollback("/etc/test") is True

    def test_rollback_no_backup(self, tmp_path):
        ctrl = self._make_controller()
        ctrl._backup_manager.get_latest_backup.return_value = None
        assert ctrl.rollback("/etc/test") is False

    # -- requires_privilege --

    def test_requires_privilege_writable(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("x")
        assert ctrl._requires_privilege(f) is False

    def test_requires_privilege_nonexistent(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "nonexistent.conf"
        # tmp_path is writable
        assert ctrl._requires_privilege(f) is False

    def test_requires_privilege_no_parent(self):
        ctrl = self._make_controller()
        f = Path("/nonexistent_dir_abc/nonexistent.conf")
        assert ctrl._requires_privilege(f) is True

    # -- batch --

    @pytest.mark.asyncio
    async def test_execute_batch_success(self, tmp_path):
        ctrl = self._make_controller()
        f1 = tmp_path / "a.conf"
        f1.write_text("a\n")
        f2 = tmp_path / "b.conf"
        f2.write_text("b\n")

        async def fake_execute(req):
            return AugeasEditResult(
                success=True,
                file_path=req.file_path,
                backup_path=str(tmp_path / "backup"),
            )

        with patch.object(ctrl, "execute", side_effect=fake_execute):
            batch = BatchEditRequest(
                edits=(
                    AugeasEditRequest(file_path=str(f1), description="a"),
                    AugeasEditRequest(file_path=str(f2), description="b"),
                ),
                description="batch test",
            )
            result = await ctrl.execute_batch(batch)
            assert result.success is True
            assert len(result.results) == 2

    @pytest.mark.asyncio
    async def test_execute_batch_with_incident_id(self, tmp_path):
        ctrl = self._make_controller()
        f = tmp_path / "a.conf"
        f.write_text("a\n")

        async def fake_execute(req):
            return AugeasEditResult(success=True, file_path=req.file_path)

        with patch.object(ctrl, "execute", side_effect=fake_execute):
            batch = BatchEditRequest(
                edits=(AugeasEditRequest(file_path=str(f), description="a"),),
                description="batch",
                incident_id="INC-001",
            )
            result = await ctrl.execute_batch(batch)
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_batch_failure_rollback(self, tmp_path):
        ctrl = self._make_controller()
        f1 = tmp_path / "a.conf"
        f1.write_text("a\n")
        f2 = tmp_path / "b.conf"
        f2.write_text("b\n")

        call_count = [0]

        async def fake_execute(req):
            call_count[0] += 1
            if call_count[0] == 1:
                return AugeasEditResult(
                    success=True,
                    file_path=req.file_path,
                    backup_path=str(tmp_path / "backup1"),
                )
            else:
                return AugeasEditResult(
                    success=False,
                    file_path=req.file_path,
                    error="failed",
                    backup_path=str(tmp_path / "backup2"),
                )

        with patch.object(ctrl, "execute", side_effect=fake_execute):
            batch = BatchEditRequest(
                edits=(
                    AugeasEditRequest(file_path=str(f1), description="a"),
                    AugeasEditRequest(file_path=str(f2), description="b"),
                ),
                description="batch",
            )
            result = await ctrl.execute_batch(batch)
            assert result.success is False
            assert result.rollback_applied is True

    @pytest.mark.asyncio
    async def test_execute_batch_dry_run_failure_no_rollback(self, tmp_path):
        ctrl = self._make_controller()
        f1 = tmp_path / "a.conf"
        f1.write_text("a\n")

        async def fake_execute(req):
            return AugeasEditResult(
                success=False,
                file_path=req.file_path,
                error="failed",
            )

        with patch.object(ctrl, "execute", side_effect=fake_execute):
            batch = BatchEditRequest(
                edits=(AugeasEditRequest(file_path=str(f1), description="a"),),
                description="batch",
                dry_run=True,
            )
            result = await ctrl.execute_batch(batch)
            # When dry_run=True, individual failures do NOT trigger batch rollback
            # so the batch itself succeeds with individual failed results
            assert result.success is True
            assert result.rollback_applied is False

    @pytest.mark.asyncio
    async def test_execute_batch_exception(self, tmp_path):
        ctrl = self._make_controller()

        with patch.object(ctrl, "execute", side_effect=RuntimeError("boom")):
            batch = BatchEditRequest(
                edits=(AugeasEditRequest(file_path="/etc/test", description="a"),),
                description="batch",
            )
            result = await ctrl.execute_batch(batch)
            assert result.success is False
            assert result.rollback_applied is True


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_get_controller_singleton(self):
        import elle.ops.augeas.controller as mod

        old = mod._controller
        mod._controller = None
        try:
            c1 = mod.get_controller()
            c2 = mod.get_controller()
            assert c1 is c2
        finally:
            mod._controller = old

    def test_preview_edit_func(self, tmp_path):
        from elle.ops.augeas.controller import preview_edit

        f = tmp_path / "test.conf"
        f.write_text("content")

        mock_preview = EditPreview(
            file_path=str(f),
            original_content="content",
            proposed_content="content",
            diff="",
            diff_colored="",
        )
        with patch("elle.ops.augeas.controller.get_controller") as mock_ctrl:
            mock_ctrl.return_value.preview.return_value = mock_preview
            result = preview_edit(
                AugeasEditRequest(
                    file_path=str(f),
                    description="test",
                )
            )
            assert isinstance(result, EditPreview)

    @pytest.mark.asyncio
    async def test_execute_edit_func(self, tmp_path):
        from elle.ops.augeas.controller import execute_edit

        with patch("elle.ops.augeas.controller.get_controller") as mock_ctrl:
            mock_ctrl.return_value.execute = AsyncMock(
                return_value=AugeasEditResult(
                    success=True,
                    file_path="/etc/test",
                )
            )
            result = await execute_edit(
                AugeasEditRequest(
                    file_path="/etc/test",
                    description="test",
                )
            )
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_batch_edit_func(self):
        from elle.ops.augeas.controller import execute_batch_edit

        with patch("elle.ops.augeas.controller.get_controller") as mock_ctrl:
            mock_ctrl.return_value.execute_batch = AsyncMock(
                return_value=BatchEditResult(
                    success=True,
                )
            )
            result = await execute_batch_edit(
                BatchEditRequest(
                    edits=(),
                    description="test",
                )
            )
            assert result.success is True

    def test_rollback_edit_func(self):
        from elle.ops.augeas.controller import rollback_edit

        with patch("elle.ops.augeas.controller.get_controller") as mock_ctrl:
            mock_ctrl.return_value.rollback.return_value = True
            assert rollback_edit("/etc/test") is True
