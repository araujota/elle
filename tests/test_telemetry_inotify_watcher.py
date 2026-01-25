"""Tests for InotifyWatcher."""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import os

from elle.daemon.telemetry.inotify_watcher import (
    DEFAULT_WATCH_PATHS,
    InotifyWatcher,
)


@pytest.fixture
def event_queue():
    """Create an event queue for testing."""
    return asyncio.Queue()


@pytest.fixture
def watcher(event_queue):
    """Create an InotifyWatcher for testing."""
    return InotifyWatcher(event_queue, watch_paths=["/tmp/test-watch"])


class TestInotifyWatcher:
    """Tests for InotifyWatcher."""

    def test_init(self, event_queue):
        """Test watcher initialization with defaults."""
        watcher = InotifyWatcher(event_queue)
        assert watcher._queue is event_queue
        assert watcher._running is False
        assert len(watcher._watch_paths) > 0

    def test_init_custom_paths(self, event_queue):
        """Test watcher initialization with custom paths."""
        paths = ["/etc/test1", "/etc/test2"]
        watcher = InotifyWatcher(event_queue, watch_paths=paths)
        assert watcher._watch_paths == paths

    def test_is_running(self, watcher):
        """Test is_running property."""
        assert watcher.is_running is False
        watcher._running = True
        assert watcher.is_running is True

    def test_stats(self, watcher):
        """Test stats property."""
        stats = watcher.stats
        assert "events_received" in stats
        assert "events_published" in stats
        assert "errors" in stats

    def test_default_watch_paths(self):
        """Test default watch paths include security-sensitive files."""
        assert "/root/.ssh/authorized_keys" in DEFAULT_WATCH_PATHS
        assert "/etc/ssh/sshd_config" in DEFAULT_WATCH_PATHS

    def test_expand_path_pattern_no_glob(self, watcher):
        """Test path expansion without glob."""
        paths = watcher._expand_path_pattern("/etc/passwd")
        assert paths == ["/etc/passwd"]

    def test_path_matches_pattern_exact(self, watcher):
        """Test exact path matching."""
        assert watcher._path_matches_pattern("/etc/passwd", "/etc/passwd") is True
        assert watcher._path_matches_pattern("/etc/passwd", "/etc/shadow") is False

    def test_path_matches_pattern_glob(self, watcher):
        """Test glob path matching."""
        pattern = "/home/*/.ssh/authorized_keys"
        assert watcher._path_matches_pattern(
            "/home/user/.ssh/authorized_keys", pattern
        ) is True
        assert watcher._path_matches_pattern(
            "/home/user/.ssh/config", pattern
        ) is False

    @pytest.mark.asyncio
    async def test_take_snapshot(self, event_queue):
        """Test taking file snapshot."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test content\nline 2\n")
            path = f.name

        try:
            watcher = InotifyWatcher(event_queue, watch_paths=[path])
            await watcher._take_snapshot(path)

            snapshot = watcher.get_snapshot(path)
            assert snapshot is not None
            assert "test content" in snapshot["content"]
            assert snapshot["hash"] != ""
            assert snapshot["mtime"] > 0
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_generate_diff(self, event_queue):
        """Test diff generation."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("line 1\nline 2\n")
            path = f.name

        try:
            watcher = InotifyWatcher(event_queue, watch_paths=[path])

            # Take initial snapshot
            await watcher._take_snapshot(path)

            # Modify file
            with open(path, "w") as f:
                f.write("line 1\nline 2\nline 3\n")

            # Generate diff
            diff = await watcher._generate_diff(path)

            assert "+line 3" in diff
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_handle_file_event_modify(self, event_queue):
        """Test handling file modify event."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("initial content")
            path = f.name

        try:
            watcher = InotifyWatcher(event_queue, watch_paths=[path])
            await watcher._take_snapshot(path)

            await watcher._handle_file_event(path, "modify")

            assert not event_queue.empty()
            event = event_queue.get_nowait()
            assert "modified" in event.message.lower()
            assert event.category in ("fs", "auth")
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_handle_file_event_create(self, event_queue):
        """Test handling file create event."""
        watcher = InotifyWatcher(event_queue, watch_paths=["/tmp"])
        await watcher._handle_file_event("/tmp/newfile.txt", "create")

        assert not event_queue.empty()
        event = event_queue.get_nowait()
        assert "created" in event.message.lower()

    @pytest.mark.asyncio
    async def test_handle_file_event_delete(self, event_queue):
        """Test handling file delete event."""
        watcher = InotifyWatcher(event_queue, watch_paths=["/tmp"])
        await watcher._handle_file_event("/tmp/oldfile.txt", "delete")

        assert not event_queue.empty()
        event = event_queue.get_nowait()
        assert "deleted" in event.message.lower()

    @pytest.mark.asyncio
    async def test_handle_file_event_attrib(self, event_queue):
        """Test handling file attribute change event."""
        watcher = InotifyWatcher(event_queue, watch_paths=["/tmp"])
        await watcher._handle_file_event("/tmp/file.txt", "attrib")

        assert not event_queue.empty()
        event = event_queue.get_nowait()
        assert "permission" in event.message.lower()

    @pytest.mark.asyncio
    async def test_auth_category_for_ssh_files(self, event_queue):
        """Test auth category assigned to SSH files."""
        watcher = InotifyWatcher(event_queue)
        await watcher._handle_file_event("/root/.ssh/authorized_keys", "modify")

        event = event_queue.get_nowait()
        assert event.category == "auth"
        assert event.severity in ("warning", "error")

    @pytest.mark.asyncio
    async def test_auth_category_for_passwd(self, event_queue):
        """Test auth category for passwd file."""
        watcher = InotifyWatcher(event_queue)
        await watcher._handle_file_event("/etc/passwd", "modify")

        event = event_queue.get_nowait()
        assert event.category == "auth"

    def test_add_watch_path(self, watcher):
        """Test adding watch path."""
        initial_count = len(watcher._watch_paths)
        watcher.add_watch_path("/new/path")
        assert len(watcher._watch_paths) == initial_count + 1
        assert "/new/path" in watcher._watch_paths

    def test_add_watch_path_duplicate(self, watcher):
        """Test adding duplicate watch path."""
        watcher.add_watch_path("/tmp/test-watch")
        watcher.add_watch_path("/tmp/test-watch")
        assert watcher._watch_paths.count("/tmp/test-watch") == 1

    def test_remove_watch_path(self, watcher):
        """Test removing watch path."""
        watcher.add_watch_path("/remove/me")
        watcher.remove_watch_path("/remove/me")
        assert "/remove/me" not in watcher._watch_paths

    @pytest.mark.asyncio
    async def test_stop(self, watcher):
        """Test stopping the watcher."""
        watcher._running = True
        await watcher.stop()
        assert watcher._running is False

    @pytest.mark.asyncio
    async def test_publish_event_queue_full(self, event_queue):
        """Test handling full queue."""
        small_queue = asyncio.Queue(maxsize=1)
        watcher = InotifyWatcher(small_queue)

        # Fill queue
        await small_queue.put(MagicMock())

        # Try to publish (should not raise)
        event = MagicMock()
        event.event_id = "test"
        await watcher._publish_event(event)

        # Error count should increase
        assert watcher._stats["errors"] >= 1
