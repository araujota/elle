"""Tests for the Augeas engine."""

from __future__ import annotations

import pytest

from elle.ops.augeas.engine import AugeasEngine, get_version, is_available
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


@pytest.mark.skipif(not is_available(), reason="python-augeas not installed")
class TestAugeasEngine:
    """Tests for AugeasEngine (requires python-augeas)."""

    def test_context_manager(self, tmp_path) -> None:
        """Engine should work as context manager."""
        test_file = tmp_path / "test.conf"
        test_file.write_text("# empty\n")

        with AugeasEngine(root=str(tmp_path)) as engine:
            assert engine is not None

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

    def test_closed_engine_raises(self, tmp_path) -> None:
        """Operations on closed engine should raise."""
        engine = AugeasEngine(root=str(tmp_path))
        engine.close()

        with pytest.raises(AugeasError):
            engine.get("/some/path")

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

    def test_frozen(self) -> None:
        """AugeasChange should be immutable."""
        change = AugeasChange(path="/test", operation="set")

        with pytest.raises(Exception):
            change.path = "/other"  # type: ignore[misc]


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
