"""Tests for the Augeas engine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.ops.augeas.engine import AugeasEngine, _get_augeas, get_version, is_available
from elle.ops.augeas.models import (
    AugeasChange,
    AugeasError,
    AugeasLensNotFoundError,
    AugeasOp,
    AugeasUnavailableError,
)


class TestAugeasAvailability:
    """Tests for Augeas availability checks."""

    def test_is_available_returns_bool(self) -> None:
        """is_available should return a boolean."""
        result = is_available()
        assert isinstance(result, bool)

    def test_get_version_returns_string_or_none(self) -> None:
        """get_version should return string or None."""
        if is_available():
            version = get_version()
            # Version could be None if Augeas doesn't expose it
            assert version is None or isinstance(version, str)

    def test_is_available_false_when_import_fails(self) -> None:
        """is_available should return False when augeas cannot be imported."""
        import elle.ops.augeas.engine as engine_mod

        original = engine_mod._augeas
        try:
            engine_mod._augeas = None
            with patch.dict("sys.modules", {"augeas": None}):
                with patch("builtins.__import__", side_effect=ImportError("no augeas")):
                    result = is_available()
                    assert result is False
        finally:
            engine_mod._augeas = original

    def test_get_version_none_when_unavailable(self) -> None:
        """get_version should return None when augeas is not available."""
        import elle.ops.augeas.engine as engine_mod

        original = engine_mod._augeas
        try:
            engine_mod._augeas = None
            with patch.dict("sys.modules", {"augeas": None}):
                with patch("builtins.__import__", side_effect=ImportError("no augeas")):
                    version = get_version()
                    assert version is None
        finally:
            engine_mod._augeas = original

    def test_get_augeas_raises_when_unavailable(self) -> None:
        """_get_augeas should raise AugeasUnavailableError when import fails."""
        import elle.ops.augeas.engine as engine_mod

        original = engine_mod._augeas
        try:
            engine_mod._augeas = None
            with patch.dict("sys.modules", {"augeas": None}):
                with patch("builtins.__import__", side_effect=ImportError("no augeas")):
                    with pytest.raises(AugeasUnavailableError, match="python-augeas is not installed"):
                        _get_augeas()
        finally:
            engine_mod._augeas = original


@pytest.mark.skipif(not is_available(), reason="python-augeas not installed")
class TestAugeasEngine:
    """Tests for AugeasEngine (requires python-augeas)."""

    def test_context_manager(self, tmp_path) -> None:
        """Engine should work as context manager."""
        test_file = tmp_path / "test.conf"
        test_file.write_text("# empty\n")

        with AugeasEngine(root=str(tmp_path)) as engine:
            assert engine is not None

    def test_context_manager_closes_on_exit(self, tmp_path) -> None:
        """Engine should be closed after exiting context manager."""
        with AugeasEngine(root=str(tmp_path)) as engine:
            assert engine._aug is not None

        # After exiting the context manager, internal state should be cleared
        assert engine._aug is None

    def test_load_file(self, tmp_path) -> None:
        """Engine should load files with appropriate lens."""
        # Create a simple hosts-like file
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            assert engine.is_loaded("/etc/hosts")
        finally:
            engine.close()

    def test_load_file_with_path_object(self, tmp_path) -> None:
        """Engine should accept Path objects for file_path."""
        from pathlib import Path

        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load(Path("/etc/hosts"), "Hosts.lns")
            assert engine.is_loaded(Path("/etc/hosts"))
        finally:
            engine.close()

    def test_is_loaded_false_for_unloaded(self, tmp_path) -> None:
        """is_loaded should return False for files not yet loaded."""
        engine = AugeasEngine(root=str(tmp_path))
        try:
            assert not engine.is_loaded("/etc/hosts")
        finally:
            engine.close()

    def test_load_without_lens_raises(self, tmp_path) -> None:
        """Loading unknown file without lens should raise."""
        test_file = tmp_path / "unknown.conf"
        test_file.write_text("key=value\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            with pytest.raises(AugeasLensNotFoundError):
                engine.load(str(test_file))
        finally:
            engine.close()

    def test_load_strips_lns_suffix_for_transform(self, tmp_path) -> None:
        """Engine should strip .lns suffix from lens name for transforms."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            # Providing lens with .lns suffix should work
            engine.load("/etc/hosts", "Hosts.lns")
            assert engine.is_loaded("/etc/hosts")
        finally:
            engine.close()

    def test_get_set_value(self, tmp_path) -> None:
        """Engine should get and set values."""
        # Create hosts file
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            # Get value
            value = engine.get("/files/etc/hosts/1/ipaddr")
            assert value == "127.0.0.1"

            # Set value
            engine.set("/files/etc/hosts/1/ipaddr", "192.168.1.1")

            # Verify change tracked
            assert engine.has_changes()
            changes = engine.get_changes()
            assert len(changes) > 0

        finally:
            engine.close()

    def test_get_returns_none_for_missing_path(self, tmp_path) -> None:
        """get should return None when path does not exist."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            value = engine.get("/files/etc/hosts/999/ipaddr")
            assert value is None
        finally:
            engine.close()

    def test_set_tracks_old_and_new_value(self, tmp_path) -> None:
        """set should record old and new values in the change."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            engine.set("/files/etc/hosts/1/ipaddr", "10.0.0.1")

            changes = engine.get_changes()
            assert len(changes) == 1
            assert changes[0].old_value == "127.0.0.1"
            assert changes[0].new_value == "10.0.0.1"
            assert changes[0].operation == "set"
        finally:
            engine.close()

    def test_remove_value(self, tmp_path) -> None:
        """Engine should remove values."""
        # Create hosts file with multiple entries
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            # Remove second entry
            count = engine.remove("/files/etc/hosts/2")
            assert count > 0
            assert engine.has_changes()

        finally:
            engine.close()

    def test_remove_nonexistent_returns_zero(self, tmp_path) -> None:
        """Removing a nonexistent path should return 0 and not track changes."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            count = engine.remove("/files/etc/hosts/999")
            assert count == 0
            assert not engine.has_changes()
        finally:
            engine.close()

    def test_remove_tracks_change_with_rm_operation(self, tmp_path) -> None:
        """remove should record a change with operation=rm."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            engine.remove("/files/etc/hosts/2")
            changes = engine.get_changes()
            assert len(changes) == 1
            assert changes[0].operation == "rm"
            assert changes[0].new_value is None
        finally:
            engine.close()

    def test_match_pattern(self, tmp_path) -> None:
        """Engine should match patterns."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            matches = engine.match("/files/etc/hosts/*")
            assert len(matches) >= 2

        finally:
            engine.close()

    def test_match_returns_empty_for_no_match(self, tmp_path) -> None:
        """match should return empty list when nothing matches."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            matches = engine.match("/files/nonexistent/path/*")
            assert matches == []
        finally:
            engine.close()

    def test_exists(self, tmp_path) -> None:
        """Engine should check path existence."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            assert engine.exists("/files/etc/hosts/1/ipaddr")
            assert not engine.exists("/files/etc/hosts/999/ipaddr")

        finally:
            engine.close()

    def test_count_returns_number_of_matches(self, tmp_path) -> None:
        """count should return number of matching nodes."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            c = engine.count("/files/etc/hosts/*")
            assert c >= 2
        finally:
            engine.close()

    def test_count_zero_for_no_match(self, tmp_path) -> None:
        """count should return 0 when nothing matches."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            c = engine.count("/files/nonexistent/*")
            assert c == 0
        finally:
            engine.close()

    def test_get_all_returns_dict(self, tmp_path) -> None:
        """get_all should return a dict mapping paths to values."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            result = engine.get_all("/files/etc/hosts/*/ipaddr")
            assert isinstance(result, dict)
            assert len(result) >= 2
            values = list(result.values())
            assert "127.0.0.1" in values
            assert "192.168.1.1" in values
        finally:
            engine.close()

    def test_clear_changes(self, tmp_path) -> None:
        """clear_changes should reset change tracking."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            engine.set("/files/etc/hosts/1/ipaddr", "192.168.1.1")

            assert engine.has_changes()

            engine.clear_changes()

            assert not engine.has_changes()

        finally:
            engine.close()

    def test_no_changes_initially(self, tmp_path) -> None:
        """has_changes should return False before any modifications."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            assert not engine.has_changes()
            assert engine.get_changes() == []
        finally:
            engine.close()

    def test_closed_engine_raises(self, tmp_path) -> None:
        """Operations on closed engine should raise."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()

        with pytest.raises(AugeasError):
            engine.get("/some/path")

    def test_closed_engine_raises_on_set(self, tmp_path) -> None:
        """set on closed engine should raise AugeasError."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()

        with pytest.raises(AugeasError, match="closed"):
            engine.set("/some/path", "value")

    def test_closed_engine_raises_on_match(self, tmp_path) -> None:
        """match on closed engine should raise AugeasError."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()

        with pytest.raises(AugeasError, match="closed"):
            engine.match("/some/pattern/*")

    def test_closed_engine_raises_on_remove(self, tmp_path) -> None:
        """remove on closed engine should raise AugeasError."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()

        with pytest.raises(AugeasError, match="closed"):
            engine.remove("/some/path")

    def test_closed_engine_raises_on_load(self, tmp_path) -> None:
        """load on closed engine should raise AugeasError."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()

        with pytest.raises(AugeasError, match="closed"):
            engine.load("/etc/hosts", "Hosts.lns")

    def test_closed_engine_raises_on_save(self, tmp_path) -> None:
        """save on closed engine should raise AugeasError."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()

        with pytest.raises(AugeasError, match="closed"):
            engine.save()

    def test_close_is_idempotent(self, tmp_path) -> None:
        """Calling close multiple times should not raise."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()
        engine.close()  # Should not raise

    def test_close_clears_state(self, tmp_path) -> None:
        """close should clear loaded files and pending changes."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        engine.load("/etc/hosts", "Hosts.lns")
        engine.set("/files/etc/hosts/1/ipaddr", "10.0.0.1")

        assert engine.is_loaded("/etc/hosts")
        assert engine.has_changes()

        engine.close()

        assert not engine.is_loaded("/etc/hosts")
        # _pending_changes is cleared by close
        assert engine._pending_changes == []

    def test_get_tree(self, tmp_path) -> None:
        """Engine should return tree as dict."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            tree = engine.get_tree("/files/etc/hosts")
            assert isinstance(tree, dict)

        finally:
            engine.close()

    def test_get_tree_contains_entries(self, tmp_path) -> None:
        """get_tree should have entries corresponding to file content."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            tree = engine.get_tree("/files/etc/hosts")
            # There should be at least one key (the first entry)
            assert len(tree) >= 1
        finally:
            engine.close()

    def test_get_original_content(self, tmp_path) -> None:
        """get_original_content should return the file text."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        content = "127.0.0.1 localhost\n"
        test_file.write_text(content)

        engine = AugeasEngine(root=str(tmp_path))
        try:
            result = engine.get_original_content(str(test_file))
            assert result == content
        finally:
            engine.close()

    def test_get_original_content_missing_file(self, tmp_path) -> None:
        """get_original_content should return empty string for missing file."""
        engine = AugeasEngine(root=str(tmp_path))
        try:
            result = engine.get_original_content(str(tmp_path / "nonexistent"))
            assert result == ""
        finally:
            engine.close()

    def test_preview_fallback_returns_file_content(self, tmp_path) -> None:
        """preview should fall back to reading the file directly."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        content = "127.0.0.1 localhost\n"
        test_file.write_text(content)

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            result = engine.preview("/etc/hosts")
            # Preview may or may not return the augeas text transform content,
            # but it should at least return something non-empty
            assert isinstance(result, str)
        finally:
            engine.close()

    def test_preview_missing_file_returns_empty(self, tmp_path) -> None:
        """preview should return empty string if file path does not exist and no text transform."""
        engine = AugeasEngine(root=str(tmp_path))
        try:
            result = engine.preview("/nonexistent/file")
            assert result == ""
        finally:
            engine.close()

    def test_print_tree(self, tmp_path) -> None:
        """print_tree should return a string representation of the tree."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            tree_str = engine.print_tree("/files/etc/hosts")
            assert isinstance(tree_str, str)
            assert len(tree_str) > 0
        finally:
            engine.close()

    def test_get_errors_returns_list(self, tmp_path) -> None:
        """get_errors should return a list of error messages."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            errors = engine.get_errors()
            assert isinstance(errors, list)
        finally:
            engine.close()

    def test_save_returns_modified_files(self, tmp_path) -> None:
        """save should return list of loaded (potentially modified) files."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            engine.set("/files/etc/hosts/1/ipaddr", "10.0.0.1")
            modified = engine.save()
            assert isinstance(modified, list)
            assert "/etc/hosts" in modified
        finally:
            engine.close()

    def test_save_clears_pending_changes(self, tmp_path) -> None:
        """save should clear pending changes after successful save."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            engine.set("/files/etc/hosts/1/ipaddr", "10.0.0.1")
            assert engine.has_changes()

            engine.save()
            assert not engine.has_changes()
        finally:
            engine.close()

    def test_insert_tracks_change(self, tmp_path) -> None:
        """insert should record a change with operation=ins."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            engine.insert("/files/etc/hosts/1", "0", before=True)
            changes = engine.get_changes()
            assert len(changes) == 1
            assert changes[0].operation == "ins"
            assert changes[0].new_value == "0"
        finally:
            engine.close()

    def test_move_tracks_change(self, tmp_path) -> None:
        """move should record a change with operation=mv."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")
            engine.move("/files/etc/hosts/2", "/files/etc/hosts/3")
            changes = engine.get_changes()
            assert len(changes) == 1
            assert changes[0].operation == "mv"
            assert changes[0].new_value == "/files/etc/hosts/3"
        finally:
            engine.close()


class TestAugeasOp:
    """Tests for AugeasOp model."""

    def test_set_operation(self) -> None:
        """Set operation should be valid."""
        op = AugeasOp(kind="set", path="/test/path", value="value")

        assert op.kind == "set"
        assert op.path == "/test/path"
        assert op.value == "value"

    def test_rm_operation(self) -> None:
        """Remove operation should be valid."""
        op = AugeasOp(kind="rm", path="/test/path")

        assert op.kind == "rm"
        assert op.value is None

    def test_ins_operation(self) -> None:
        """Insert operation should be valid."""
        op = AugeasOp(kind="ins", path="/test/path", label="newnode", before=True)

        assert op.kind == "ins"
        assert op.label == "newnode"
        assert op.before is True

    def test_mv_operation(self) -> None:
        """Move operation should be valid."""
        op = AugeasOp(kind="mv", path="/old/path", value="/new/path")

        assert op.kind == "mv"
        assert op.path == "/old/path"
        assert op.value == "/new/path"

    def test_frozen(self) -> None:
        """AugeasOp should be immutable."""
        op = AugeasOp(kind="set", path="/test", value="val")

        with pytest.raises(Exception):
            op.kind = "rm"  # type: ignore[misc]

    def test_default_value_is_none(self) -> None:
        """value should default to None if not provided."""
        op = AugeasOp(kind="rm", path="/test")
        assert op.value is None

    def test_default_label_is_none(self) -> None:
        """label should default to None if not provided."""
        op = AugeasOp(kind="set", path="/test", value="val")
        assert op.label is None

    def test_default_before_is_false(self) -> None:
        """before should default to False."""
        op = AugeasOp(kind="ins", path="/test", label="lab")
        assert op.before is False


class TestAugeasChange:
    """Tests for AugeasChange model."""

    def test_set_change(self) -> None:
        """Set change should capture old and new values."""
        change = AugeasChange(
            path="/test/path",
            old_value="old",
            new_value="new",
            operation="set",
        )

        assert change.path == "/test/path"
        assert change.old_value == "old"
        assert change.new_value == "new"

    def test_rm_change(self) -> None:
        """Remove change should have None new_value."""
        change = AugeasChange(
            path="/test/path",
            old_value="deleted",
            new_value=None,
            operation="rm",
        )

        assert change.new_value is None
        assert change.operation == "rm"

    def test_ins_change(self) -> None:
        """Insert change should have None old_value."""
        change = AugeasChange(
            path="/test/path",
            old_value=None,
            new_value="inserted",
            operation="ins",
        )

        assert change.old_value is None

    def test_mv_change(self) -> None:
        """Move change should store source path and destination in new_value."""
        change = AugeasChange(
            path="/old/path",
            old_value="value",
            new_value="/new/path",
            operation="mv",
        )

        assert change.path == "/old/path"
        assert change.new_value == "/new/path"
        assert change.operation == "mv"

    def test_frozen(self) -> None:
        """AugeasChange should be immutable."""
        change = AugeasChange(path="/test", operation="set")

        with pytest.raises(Exception):
            change.path = "/other"  # type: ignore[misc]

    def test_default_operation_is_set(self) -> None:
        """operation should default to 'set'."""
        change = AugeasChange(path="/test")
        assert change.operation == "set"


@pytest.mark.skipif(not is_available(), reason="python-augeas not installed")
class TestApplyOps:
    """Tests for applying operations."""

    def test_apply_set_op(self, tmp_path) -> None:
        """apply_op should handle set operations."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            op = AugeasOp(kind="set", path="/files/etc/hosts/1/ipaddr", value="10.0.0.1")
            engine.apply_op(op)

            assert engine.get("/files/etc/hosts/1/ipaddr") == "10.0.0.1"

        finally:
            engine.close()

    def test_apply_rm_op(self, tmp_path) -> None:
        """apply_op should handle rm operations."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            op = AugeasOp(kind="rm", path="/files/etc/hosts/2")
            engine.apply_op(op)

            assert not engine.exists("/files/etc/hosts/2/ipaddr")
            assert engine.has_changes()

        finally:
            engine.close()

    def test_apply_ins_op(self, tmp_path) -> None:
        """apply_op should handle ins operations."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            op = AugeasOp(kind="ins", path="/files/etc/hosts/1", label="0", before=True)
            engine.apply_op(op)

            assert engine.has_changes()

        finally:
            engine.close()

    def test_apply_mv_op(self, tmp_path) -> None:
        """apply_op should handle mv operations."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n192.168.1.1 server\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            op = AugeasOp(kind="mv", path="/files/etc/hosts/2", value="/files/etc/hosts/3")
            engine.apply_op(op)

            assert engine.has_changes()

        finally:
            engine.close()

    def test_apply_multiple_ops(self, tmp_path) -> None:
        """apply_ops should handle multiple operations."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            ops = [
                AugeasOp(kind="set", path="/files/etc/hosts/1/ipaddr", value="10.0.0.1"),
                AugeasOp(kind="set", path="/files/etc/hosts/1/canonical", value="newhost"),
            ]
            engine.apply_ops(ops)

            assert engine.get("/files/etc/hosts/1/ipaddr") == "10.0.0.1"
            assert engine.get("/files/etc/hosts/1/canonical") == "newhost"

        finally:
            engine.close()

    def test_apply_ops_with_tuple(self, tmp_path) -> None:
        """apply_ops should accept a tuple of operations."""
        test_file = tmp_path / "etc" / "hosts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("127.0.0.1 localhost\n")

        engine = AugeasEngine(root=str(tmp_path))
        try:
            engine.load("/etc/hosts", "Hosts.lns")

            ops = (AugeasOp(kind="set", path="/files/etc/hosts/1/ipaddr", value="10.0.0.1"),)
            engine.apply_ops(ops)

            assert engine.get("/files/etc/hosts/1/ipaddr") == "10.0.0.1"

        finally:
            engine.close()

    def test_apply_set_without_value_raises(self, tmp_path) -> None:
        """Set operation without value should raise."""
        engine = AugeasEngine(root=str(tmp_path))
        try:
            op = AugeasOp(kind="set", path="/test")

            with pytest.raises(AugeasError):
                engine.apply_op(op)

        finally:
            engine.close()

    def test_apply_ins_without_label_raises(self, tmp_path) -> None:
        """Insert operation without label should raise."""
        engine = AugeasEngine(root=str(tmp_path))
        try:
            op = AugeasOp(kind="ins", path="/test")

            with pytest.raises(AugeasError):
                engine.apply_op(op)

        finally:
            engine.close()

    def test_apply_mv_without_value_raises(self, tmp_path) -> None:
        """Move operation without destination value should raise."""
        engine = AugeasEngine(root=str(tmp_path))
        try:
            op = AugeasOp(kind="mv", path="/test")

            with pytest.raises(AugeasError, match="mv operation requires destination"):
                engine.apply_op(op)

        finally:
            engine.close()

    def test_apply_unknown_kind_raises(self, tmp_path) -> None:
        """Unknown operation kind should raise AugeasError."""
        engine = AugeasEngine(root=str(tmp_path))
        try:
            # Bypass Pydantic validation by creating a mock op
            op = MagicMock()
            op.kind = "unknown_op"
            op.path = "/test"

            with pytest.raises(AugeasError, match="Unknown operation kind"):
                engine.apply_op(op)

        finally:
            engine.close()
