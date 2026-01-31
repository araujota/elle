from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.ops.augeas.models import (
    AugeasError,
    AugeasLensNotFoundError,
    AugeasOp,
    AugeasParseError,
    AugeasUnavailableError,
)

# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_is_available_true(self):
        with patch("elle.ops.augeas.engine._get_augeas") as mock_get:
            mock_get.return_value = MagicMock()
            from elle.ops.augeas.engine import is_available

            assert is_available() is True

    def test_is_available_false(self):
        with patch("elle.ops.augeas.engine._get_augeas", side_effect=AugeasUnavailableError("no")):
            from elle.ops.augeas.engine import is_available

            assert is_available() is False

    def test_get_augeas_cached(self):
        import elle.ops.augeas.engine as eng

        old = eng._augeas
        eng._augeas = "cached_module"
        try:
            result = eng._get_augeas()
            assert result == "cached_module"
        finally:
            eng._augeas = old

    def test_get_augeas_import_error(self):
        import elle.ops.augeas.engine as eng

        old = eng._augeas
        eng._augeas = None
        try:
            with patch.dict("sys.modules", {"augeas": None}):
                with pytest.raises(AugeasUnavailableError):
                    eng._get_augeas()
        finally:
            eng._augeas = old

    def test_get_version_success(self):
        mock_aug_mod = MagicMock()
        mock_instance = MagicMock()
        mock_instance.get.return_value = "1.14.0"
        mock_aug_mod.Augeas.return_value = mock_instance
        mock_aug_mod.Augeas.NO_LOAD = 0
        mock_aug_mod.Augeas.NO_MODL_AUTOLOAD = 0

        with patch("elle.ops.augeas.engine._get_augeas", return_value=mock_aug_mod):
            from elle.ops.augeas.engine import get_version

            result = get_version()
            assert result == "1.14.0"

    def test_get_version_none(self):
        mock_aug_mod = MagicMock()
        mock_instance = MagicMock()
        mock_instance.get.return_value = None
        mock_aug_mod.Augeas.return_value = mock_instance
        mock_aug_mod.Augeas.NO_LOAD = 0
        mock_aug_mod.Augeas.NO_MODL_AUTOLOAD = 0

        with patch("elle.ops.augeas.engine._get_augeas", return_value=mock_aug_mod):
            from elle.ops.augeas.engine import get_version

            result = get_version()
            assert result is None

    def test_get_version_exception(self):
        with patch("elle.ops.augeas.engine._get_augeas", side_effect=RuntimeError("nope")):
            from elle.ops.augeas.engine import get_version

            result = get_version()
            assert result is None


# ---------------------------------------------------------------------------
# AugeasEngine (all mocked)
# ---------------------------------------------------------------------------


class TestAugeasEngine:
    def _make_engine(self):
        mock_aug_mod = MagicMock()
        mock_instance = MagicMock()
        mock_aug_mod.Augeas.return_value = mock_instance
        mock_aug_mod.Augeas.SAVE_BACKUP = 1

        with patch("elle.ops.augeas.engine._get_augeas", return_value=mock_aug_mod):
            from elle.ops.augeas.engine import AugeasEngine

            engine = AugeasEngine()
        return engine, mock_instance

    def test_init(self):
        engine, mock_aug = self._make_engine()
        assert engine._aug is not None

    def test_context_manager(self):
        engine, mock_aug = self._make_engine()
        with engine:
            pass
        assert engine._aug is None

    def test_close(self):
        engine, mock_aug = self._make_engine()
        engine._loaded_files.add("/etc/test")
        engine._pending_changes.append(MagicMock())
        engine.close()
        assert engine._aug is None
        assert len(engine._loaded_files) == 0
        assert len(engine._pending_changes) == 0
        mock_aug.close.assert_called_once()

    def test_close_already_closed(self):
        engine, _ = self._make_engine()
        engine._aug = None
        engine.close()  # Should not raise

    def test_ensure_open_raises(self):
        engine, _ = self._make_engine()
        engine._aug = None
        with pytest.raises(AugeasError, match="closed"):
            engine._ensure_open()

    # -- Loading --

    def test_load_auto_detect(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = []  # No errors
        with patch("elle.ops.augeas.engine.detect_lens", return_value="Fstab.lns"):
            engine.load("/etc/fstab")
        assert "/etc/fstab" in engine._loaded_files

    def test_load_no_lens(self):
        engine, _ = self._make_engine()
        with patch("elle.ops.augeas.engine.detect_lens", return_value=None):
            with pytest.raises(AugeasLensNotFoundError):
                engine.load("/unknown/file")

    def test_load_parse_error(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = ["/augeas/files/etc/test/error/line"]
        mock_aug.get.return_value = "parse error at line 5"
        with patch("elle.ops.augeas.engine.detect_lens", return_value="Test.lns"):
            with pytest.raises(AugeasParseError):
                engine.load("/etc/test")

    def test_load_explicit_lens(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = []
        engine.load("/etc/fstab", lens="Fstab.lns")
        assert "/etc/fstab" in engine._loaded_files

    def test_load_lens_with_lns_suffix(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = []
        engine.load("/etc/fstab", lens="Fstab.lns")
        mock_aug.set.assert_any_call("/augeas/load/Fstab/lens", "Fstab.lns")

    def test_is_loaded(self):
        engine, _ = self._make_engine()
        assert engine.is_loaded("/etc/fstab") is False
        engine._loaded_files.add("/etc/fstab")
        assert engine.is_loaded("/etc/fstab") is True

    # -- Reading --

    def test_get(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = "value"
        assert engine.get("/some/path") == "value"

    def test_get_none(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None
        assert engine.get("/some/path") is None

    def test_match(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = ["/a", "/b"]
        assert engine.match("/pattern/*") == ["/a", "/b"]

    def test_exists_true(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = ["/a"]
        assert engine.exists("/a") is True

    def test_exists_false(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = []
        assert engine.exists("/a") is False

    def test_count(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = ["/a", "/b", "/c"]
        assert engine.count("/pattern/*") == 3

    def test_get_all(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = ["/a", "/b"]
        mock_aug.get.side_effect = ["v1", "v2"]
        result = engine.get_all("/pattern/*")
        assert result == {"/a": "v1", "/b": "v2"}

    def test_get_tree(self):
        engine, mock_aug = self._make_engine()

        def mock_match(pattern):
            if pattern == "/base/*":
                return ["/base/child1", "/base/child2"]
            elif pattern == "/base/child1/*":
                return ["/base/child1/grandchild"]
            elif pattern == "/base/child1/grandchild/*" or pattern == "/base/child2/*":
                return []
            return []

        def mock_get(path):
            return {
                "/base/child1": None,
                "/base/child1/grandchild": "gval",
                "/base/child2": "c2val",
            }.get(path)

        mock_aug.match.side_effect = mock_match
        mock_aug.get.side_effect = mock_get
        result = engine.get_tree("/base")
        assert "child1" in result
        assert "child2" in result
        assert result["child2"] == "c2val"

    def test_get_tree_with_value_on_parent(self):
        engine, mock_aug = self._make_engine()

        def mock_match(pattern):
            return {
                "/base/*": ["/base/node"],
                "/base/node/*": ["/base/node/sub"],
                "/base/node/sub/*": [],
            }.get(pattern, [])

        def mock_get(path):
            if path == "/base/node":
                return "parent_val"
            elif path == "/base/node/sub":
                return "sub_val"
            return None

        mock_aug.match.side_effect = mock_match
        mock_aug.get.side_effect = mock_get
        result = engine.get_tree("/base")
        assert result["node"]["#value"] == "parent_val"

    # -- Writing --

    def test_set(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = "old"
        engine.set("/path", "new")
        mock_aug.set.assert_called_with("/path", "new")
        assert len(engine._pending_changes) == 1
        assert engine._pending_changes[0].old_value == "old"
        assert engine._pending_changes[0].new_value == "new"

    def test_remove(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = "val"
        mock_aug.remove.return_value = 1
        count = engine.remove("/path")
        assert count == 1
        assert len(engine._pending_changes) == 1
        assert engine._pending_changes[0].operation == "rm"

    def test_remove_nothing(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None
        mock_aug.remove.return_value = 0
        count = engine.remove("/path")
        assert count == 0
        assert len(engine._pending_changes) == 0

    def test_insert(self):
        engine, mock_aug = self._make_engine()
        engine.insert("/path", "label", before=True)
        mock_aug.insert.assert_called_once_with("/path", "label", True)
        assert len(engine._pending_changes) == 1
        assert engine._pending_changes[0].operation == "ins"

    def test_move(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = "val"
        engine.move("/src", "/dst")
        mock_aug.move.assert_called_once_with("/src", "/dst")
        assert len(engine._pending_changes) == 1
        assert engine._pending_changes[0].operation == "mv"

    # -- apply_op --

    def test_apply_op_set(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None
        op = AugeasOp(kind="set", path="/p", value="v")
        engine.apply_op(op)
        mock_aug.set.assert_called_with("/p", "v")

    def test_apply_op_set_no_value(self):
        engine, _ = self._make_engine()
        op = AugeasOp(kind="set", path="/p")
        with pytest.raises(AugeasError, match="value"):
            engine.apply_op(op)

    def test_apply_op_rm(self):
        engine, mock_aug = self._make_engine()
        mock_aug.remove.return_value = 1
        mock_aug.get.return_value = None
        op = AugeasOp(kind="rm", path="/p")
        engine.apply_op(op)
        mock_aug.remove.assert_called()

    def test_apply_op_ins(self):
        engine, mock_aug = self._make_engine()
        op = AugeasOp(kind="ins", path="/p", label="lbl", before=True)
        engine.apply_op(op)
        mock_aug.insert.assert_called_once()

    def test_apply_op_ins_no_label(self):
        engine, _ = self._make_engine()
        op = AugeasOp(kind="ins", path="/p")
        with pytest.raises(AugeasError, match="label"):
            engine.apply_op(op)

    def test_apply_op_mv(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None
        op = AugeasOp(kind="mv", path="/src", value="/dst")
        engine.apply_op(op)
        mock_aug.move.assert_called()

    def test_apply_op_mv_no_value(self):
        engine, _ = self._make_engine()
        op = AugeasOp(kind="mv", path="/src")
        with pytest.raises(AugeasError, match="destination"):
            engine.apply_op(op)

    def test_apply_op_unknown(self):
        engine, _ = self._make_engine()
        # Need to bypass frozen model validation
        op = MagicMock()
        op.kind = "unknown"
        with pytest.raises(AugeasError, match="Unknown"):
            engine.apply_op(op)

    def test_apply_ops(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None
        ops = [
            AugeasOp(kind="set", path="/a", value="1"),
            AugeasOp(kind="set", path="/b", value="2"),
        ]
        engine.apply_ops(ops)
        assert len(engine._pending_changes) == 2

    # -- Change tracking --

    def test_get_changes(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None
        engine.set("/path", "val")
        changes = engine.get_changes()
        assert len(changes) == 1
        assert changes is not engine._pending_changes  # is a copy

    def test_has_changes(self):
        engine, _ = self._make_engine()
        assert engine.has_changes() is False
        engine._pending_changes.append(MagicMock())
        assert engine.has_changes() is True

    def test_clear_changes(self):
        engine, _ = self._make_engine()
        engine._pending_changes.append(MagicMock())
        engine.clear_changes()
        assert len(engine._pending_changes) == 0

    # -- Preview / content --

    def test_preview_with_content(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = "file content here"
        result = engine.preview("/etc/test")
        assert result == "file content here"

    def test_preview_fallback(self, tmp_path):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None

        test_file = tmp_path / "test.conf"
        test_file.write_text("original")
        result = engine.preview(str(test_file))
        assert result == "original"

    def test_preview_fallback_missing(self):
        engine, mock_aug = self._make_engine()
        mock_aug.get.return_value = None
        result = engine.preview("/nonexistent/path")
        assert result == ""

    def test_get_original_content(self, tmp_path):
        engine, _ = self._make_engine()
        f = tmp_path / "test.conf"
        f.write_text("content")
        assert engine.get_original_content(str(f)) == "content"

    def test_get_original_content_missing(self):
        engine, _ = self._make_engine()
        assert engine.get_original_content("/nonexistent") == ""

    # -- Save --

    def test_save_success(self):
        engine, mock_aug = self._make_engine()
        engine._loaded_files.add("/etc/test")
        engine._pending_changes.append(MagicMock())
        files = engine.save()
        mock_aug.save.assert_called_once()
        assert "/etc/test" in files
        assert len(engine._pending_changes) == 0

    def test_save_error_with_messages(self):
        engine, mock_aug = self._make_engine()
        mock_aug.save.side_effect = RuntimeError("save error")
        mock_aug.match.return_value = ["/augeas/files//error"]
        mock_aug.get.return_value = "Write failed"
        with pytest.raises(AugeasError, match="Write failed"):
            engine.save()

    def test_save_error_no_messages(self):
        engine, mock_aug = self._make_engine()
        mock_aug.save.side_effect = RuntimeError("save error")
        mock_aug.match.return_value = []
        with pytest.raises(AugeasError, match="save error"):
            engine.save()

    # -- Utility --

    def test_print_tree(self):
        engine, mock_aug = self._make_engine()

        def mock_match(pattern):
            if pattern == "/files/*":
                return ["/files/child"]
            return []

        def mock_get(path):
            if path == "/files":
                return None
            if path == "/files/child":
                return "val"
            return None

        mock_aug.match.side_effect = mock_match
        mock_aug.get.side_effect = mock_get
        result = engine.print_tree("/files")
        assert "child" in result
        assert "val" in result

    def test_print_tree_no_value(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = []
        mock_aug.get.return_value = None
        result = engine.print_tree("/files")
        assert "files/" in result

    def test_get_errors(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = ["/augeas//error"]
        mock_aug.get.return_value = "some error"
        errors = engine.get_errors()
        assert "some error" in errors

    def test_get_errors_empty(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = []
        errors = engine.get_errors()
        assert errors == []

    def test_get_errors_no_message(self):
        engine, mock_aug = self._make_engine()
        mock_aug.match.return_value = ["/augeas//error"]
        mock_aug.get.return_value = None
        errors = engine.get_errors()
        assert errors == []
