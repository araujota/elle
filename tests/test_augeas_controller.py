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

    def test_rollback_edit_with_backup_path(self):
        from elle.ops.augeas.controller import rollback_edit

        with patch("elle.ops.augeas.controller.get_controller") as mock_ctrl:
            mock_ctrl.return_value.rollback.return_value = True
            assert rollback_edit("/etc/test", "/backup/path") is True


# ---------------------------------------------------------------------------
# _preview_yaml
# ---------------------------------------------------------------------------


class TestPreviewYaml:
    def _make_controller(self):
        from elle.ops.augeas.controller import EditController

        mock_backup = MagicMock()
        mock_diff = MagicMock()
        return EditController(backup_manager=mock_backup, diff_generator=mock_diff)

    def test_preview_yaml_set_operation(self, tmp_path):
        ctrl = self._make_controller()
        original = "key: value\n"

        mock_handler = MagicMock()
        mock_handler.get_path.return_value = "value"
        mock_handler.dumps.return_value = "key: new_value\n"

        with (
            patch("elle.ops.augeas.yaml_handler.YAMLHandler", return_value=mock_handler),
            patch("elle.ops.augeas.yaml_handler.is_available", return_value=True),
            patch("ruamel.yaml.YAML") as mock_yaml_cls,
        ):
            mock_yaml_inst = MagicMock()
            mock_yaml_inst.load.return_value = {"key": "value"}
            mock_yaml_cls.return_value = mock_yaml_inst

            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.yaml"),
                description="test",
                operations=(AugeasOp(kind="set", path="key", value="new_value"),),
            )
            modified, changes = ctrl._preview_yaml(req, original)
            assert modified == "key: new_value\n"
            assert len(changes) == 1
            assert changes[0].operation == "set"

    def test_preview_yaml_rm_operation(self, tmp_path):
        ctrl = self._make_controller()
        original = "key: value\nother: keep\n"

        mock_handler = MagicMock()
        mock_handler.get_path.return_value = "value"
        mock_handler.dumps.return_value = "other: keep\n"

        with (
            patch("elle.ops.augeas.yaml_handler.YAMLHandler", return_value=mock_handler),
            patch("elle.ops.augeas.yaml_handler.is_available", return_value=True),
            patch("ruamel.yaml.YAML") as mock_yaml_cls,
        ):
            mock_yaml_inst = MagicMock()
            mock_yaml_inst.load.return_value = {"key": "value", "other": "keep"}
            mock_yaml_cls.return_value = mock_yaml_inst

            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.yaml"),
                description="test",
                operations=(AugeasOp(kind="rm", path="key"),),
            )
            modified, changes = ctrl._preview_yaml(req, original)
            assert len(changes) == 1
            assert changes[0].operation == "rm"

    def test_preview_yaml_not_available(self, tmp_path):
        ctrl = self._make_controller()
        with patch("elle.ops.augeas.yaml_handler.is_available", return_value=False):
            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.yaml"),
                description="test",
                operations=(),
            )
            with pytest.raises(AugeasError, match="ruamel.yaml"):
                ctrl._preview_yaml(req, "key: value\n")

    def test_preview_yaml_null_data(self, tmp_path):
        ctrl = self._make_controller()
        original = ""

        mock_handler = MagicMock()
        mock_handler.get_path.return_value = None
        mock_handler.dumps.return_value = "key: new\n"

        with (
            patch("elle.ops.augeas.yaml_handler.YAMLHandler", return_value=mock_handler),
            patch("elle.ops.augeas.yaml_handler.is_available", return_value=True),
            patch("ruamel.yaml.YAML") as mock_yaml_cls,
        ):
            mock_yaml_inst = MagicMock()
            mock_yaml_inst.load.return_value = None  # Empty YAML
            mock_yaml_cls.return_value = mock_yaml_inst

            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.yaml"),
                description="test",
                operations=(AugeasOp(kind="set", path="key", value="new"),),
            )
            modified, changes = ctrl._preview_yaml(req, original)
            assert len(changes) == 1

    def test_preview_yaml_set_with_none_old_value(self, tmp_path):
        ctrl = self._make_controller()
        original = "key: value\n"

        mock_handler = MagicMock()
        mock_handler.get_path.return_value = None  # Key doesn't exist yet
        mock_handler.dumps.return_value = "key: value\nnew_key: new\n"

        with (
            patch("elle.ops.augeas.yaml_handler.YAMLHandler", return_value=mock_handler),
            patch("elle.ops.augeas.yaml_handler.is_available", return_value=True),
            patch("ruamel.yaml.YAML") as mock_yaml_cls,
        ):
            mock_yaml_inst = MagicMock()
            mock_yaml_inst.load.return_value = {"key": "value"}
            mock_yaml_cls.return_value = mock_yaml_inst

            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.yaml"),
                description="test",
                operations=(AugeasOp(kind="set", path="new_key", value="new"),),
            )
            modified, changes = ctrl._preview_yaml(req, original)
            assert len(changes) == 1
            assert changes[0].old_value is None


# ---------------------------------------------------------------------------
# _apply_yaml
# ---------------------------------------------------------------------------


class TestApplyYaml:
    def _make_controller(self):
        from elle.ops.augeas.controller import EditController

        mock_backup = MagicMock()
        mock_diff = MagicMock()
        return EditController(backup_manager=mock_backup, diff_generator=mock_diff)

    @pytest.mark.asyncio
    async def test_apply_yaml_set_and_rm(self, tmp_path):
        ctrl = self._make_controller()

        mock_handler = MagicMock()
        mock_yaml_change_set = MagicMock()
        mock_yaml_change_set.path = "key"
        mock_yaml_change_set.old_value = "old"
        mock_yaml_change_set.new_value = "new"
        mock_yaml_change_set.operation = "set"

        mock_yaml_change_rm = MagicMock()
        mock_yaml_change_rm.path = "removed"
        mock_yaml_change_rm.old_value = "val"
        mock_yaml_change_rm.new_value = None
        mock_yaml_change_rm.operation = "rm"

        mock_handler.get_changes.return_value = [mock_yaml_change_set, mock_yaml_change_rm]
        mock_handler.load.return_value = {"key": "old", "removed": "val"}

        with patch("elle.ops.augeas.yaml_handler.YAMLHandler", return_value=mock_handler):
            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.yaml"),
                description="test",
                operations=(
                    AugeasOp(kind="set", path="key", value="new"),
                    AugeasOp(kind="rm", path="removed"),
                ),
            )
            changes = await ctrl._apply_yaml(req)
            assert len(changes) == 2
            assert changes[0].operation == "set"
            assert changes[1].operation == "rm"


# ---------------------------------------------------------------------------
# _apply_augeas
# ---------------------------------------------------------------------------


class TestApplyAugeas:
    def _make_controller(self):
        from elle.ops.augeas.controller import EditController

        mock_backup = MagicMock()
        mock_diff = MagicMock()
        return EditController(backup_manager=mock_backup, diff_generator=mock_diff)

    @pytest.mark.asyncio
    async def test_apply_augeas(self, tmp_path):
        ctrl = self._make_controller()
        mock_engine = MagicMock()
        mock_engine.get_changes.return_value = [AugeasChange(path="/p", old_value="o", new_value="n", operation="set")]

        with patch("elle.ops.augeas.engine.AugeasEngine", return_value=mock_engine):
            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.conf"),
                description="test",
                operations=(AugeasOp(kind="set", path="/p", value="n"),),
            )
            changes = await ctrl._apply_augeas(req)
            assert len(changes) == 1
            mock_engine.load.assert_called_once()
            mock_engine.apply_ops.assert_called_once()
            mock_engine.save.assert_called_once()
            mock_engine.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_augeas_engine_close_on_error(self, tmp_path):
        ctrl = self._make_controller()
        mock_engine = MagicMock()
        mock_engine.apply_ops.side_effect = RuntimeError("engine error")

        with patch("elle.ops.augeas.engine.AugeasEngine", return_value=mock_engine):
            req = AugeasEditRequest(
                file_path=str(tmp_path / "test.conf"),
                description="test",
                operations=(AugeasOp(kind="set", path="/p", value="n"),),
            )
            with pytest.raises(RuntimeError, match="engine error"):
                await ctrl._apply_augeas(req)
            # Engine should still be closed in finally block
            mock_engine.close.assert_called_once()


# ---------------------------------------------------------------------------
# Execute with incident recording branches
# ---------------------------------------------------------------------------


class TestExecuteIncidentRecording:
    def _make_controller(self):
        from elle.ops.augeas.controller import EditController

        mock_backup = MagicMock()
        mock_diff = MagicMock()
        return EditController(backup_manager=mock_backup, diff_generator=mock_diff)

    @pytest.mark.asyncio
    async def test_execute_incident_import_error(self, tmp_path):
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

            # Force ImportError for incident store
            with patch.dict(
                "sys.modules",
                {
                    "elle.daemon.incidents.semantic_diff": None,
                    "elle.daemon.incidents.store": None,
                },
            ):
                req = AugeasEditRequest(
                    file_path=str(f),
                    description="test",
                    incident_id="INC-002",
                    skip_validation=True,
                )
                result = await ctrl.execute(req)
                assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_incident_general_exception(self, tmp_path):
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

            # Mock the incident recording modules to raise a general error
            mock_store = MagicMock()
            mock_store.append_action.side_effect = RuntimeError("store error")
            mock_semantic = MagicMock()
            with (
                patch.dict(
                    "sys.modules",
                    {
                        "elle.daemon.incidents.store": mock_store,
                        "elle.daemon.incidents.semantic_diff": mock_semantic,
                    },
                ),
            ):
                req = AugeasEditRequest(
                    file_path=str(f),
                    description="test",
                    incident_id="INC-003",
                    skip_validation=True,
                )
                # Should still succeed (incident recording errors are non-fatal)
                result = await ctrl.execute(req)
                assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_exception_rollback_fails(self, tmp_path):
        """Test that when execution errors and rollback also fails, it still returns."""
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        mock_backup_rec = MagicMock()
        mock_backup_rec.backup_path = str(tmp_path / "backup")
        ctrl._backup_manager.backup.return_value = mock_backup_rec
        ctrl._backup_manager.restore.side_effect = RuntimeError("restore fail")

        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, side_effect=RuntimeError("apply boom")),
        ):
            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
            )
            result = await ctrl.execute(req)
            assert result.success is False
            assert result.rollback_applied is False

    @pytest.mark.asyncio
    async def test_execute_exception_no_backup(self, tmp_path):
        """Test error path when there is no backup to rollback to."""
        ctrl = self._make_controller()
        f = tmp_path / "test.conf"
        f.write_text("content\n")

        with (
            patch("elle.ops.augeas.controller.is_yaml_file", return_value=False),
            patch.object(ctrl, "_apply_augeas", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        ):
            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
                skip_backup=True,
            )
            result = await ctrl.execute(req)
            assert result.success is False
            assert result.rollback_applied is False

    @pytest.mark.asyncio
    async def test_execute_validation_error_output_from_error_field(self, tmp_path):
        """Test validation where output comes from error field instead of output field."""
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
            mock_val.return_value = MagicMock(valid=False, output=None, error="config error")

            req = AugeasEditRequest(
                file_path=str(f),
                description="test",
            )
            result = await ctrl.execute(req)
            assert result.success is False
            assert result.validation_output == "config error"


# ---------------------------------------------------------------------------
# Batch edge cases
# ---------------------------------------------------------------------------


class TestBatchEdgeCases:
    def _make_controller(self):
        from elle.ops.augeas.controller import EditController

        mock_backup = MagicMock()
        mock_diff = MagicMock()
        return EditController(backup_manager=mock_backup, diff_generator=mock_diff)

    @pytest.mark.asyncio
    async def test_batch_rollback_failure_logged(self, tmp_path):
        """Test that batch rollback failures are handled gracefully."""
        ctrl = self._make_controller()
        ctrl._backup_manager.restore.side_effect = RuntimeError("restore fail")

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
    async def test_batch_exception_rollback(self, tmp_path):
        """Test batch exception triggers rollback of all previous backups."""
        ctrl = self._make_controller()

        call_count = [0]

        async def fake_execute(req):
            call_count[0] += 1
            if call_count[0] == 1:
                return AugeasEditResult(
                    success=True,
                    file_path=req.file_path,
                    backup_path=str(tmp_path / "backup1"),
                )
            raise RuntimeError("unexpected error")

        with patch.object(ctrl, "execute", side_effect=fake_execute):
            batch = BatchEditRequest(
                edits=(
                    AugeasEditRequest(file_path="/etc/a", description="a"),
                    AugeasEditRequest(file_path="/etc/b", description="b"),
                ),
                description="batch",
            )
            result = await ctrl.execute_batch(batch)
            assert result.success is False
            assert result.rollback_applied is True
