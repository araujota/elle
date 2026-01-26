"""Tests for DockerEventsWatcher."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.telemetry.docker_watcher import (
    CRASHLOOP_THRESHOLD,
    DockerEventsWatcher,
)


@pytest.fixture
def event_queue():
    """Create an event queue for testing."""
    return asyncio.Queue()


@pytest.fixture
def watcher(event_queue):
    """Create a DockerEventsWatcher for testing."""
    return DockerEventsWatcher(event_queue)


class TestDockerEventsWatcher:
    """Tests for DockerEventsWatcher."""

    def test_init(self, watcher, event_queue):
        """Test watcher initialization."""
        assert watcher._queue is event_queue
        assert watcher._running is False
        assert watcher._stats["events_received"] == 0

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
        assert "crashloops_detected" in stats
        assert "errors" in stats

    @pytest.mark.asyncio
    async def test_process_event_start(self, watcher, event_queue):
        """Test processing container start event."""
        event_json = json.dumps(
            {
                "status": "start",
                "Action": "start",
                "Actor": {
                    "ID": "abc123def456",
                    "Attributes": {"name": "nginx"},
                },
            }
        )

        await watcher._process_event(event_json)

        assert watcher._stats["events_received"] == 1
        assert not event_queue.empty()

        event = event_queue.get_nowait()
        assert event.category == "docker"
        assert "nginx" in event.message
        assert "started" in event.message

    @pytest.mark.asyncio
    async def test_process_event_die(self, watcher, event_queue):
        """Test processing container die event."""
        event_json = json.dumps(
            {
                "status": "die",
                "Action": "die",
                "Actor": {
                    "ID": "abc123def456",
                    "Attributes": {"name": "webapp", "exitCode": "137"},
                },
            }
        )

        await watcher._process_event(event_json)

        assert watcher._stats["events_received"] == 1
        event = event_queue.get_nowait()
        assert event.severity in ("warning", "error")
        assert "died" in event.message
        assert "137" in event.message

    @pytest.mark.asyncio
    async def test_process_event_oom(self, watcher, event_queue):
        """Test processing OOM event."""
        event_json = json.dumps(
            {
                "status": "oom",
                "Action": "oom",
                "Actor": {
                    "ID": "abc123def456",
                    "Attributes": {"name": "memory-hog"},
                },
            }
        )

        await watcher._process_event(event_json)

        event = event_queue.get_nowait()
        assert event.severity == "critical"
        assert "OOM" in event.message

    @pytest.mark.asyncio
    async def test_crashloop_detection(self, watcher, event_queue):
        """Test crashloop detection."""
        # Simulate multiple restarts
        for _i in range(CRASHLOOP_THRESHOLD + 1):
            event_json = json.dumps(
                {
                    "status": "restart",
                    "Action": "restart",
                    "Actor": {
                        "ID": "abc123def456",
                        "Attributes": {"name": "flaky-app"},
                    },
                }
            )
            await watcher._process_event(event_json)

        # Check that crashloop was detected
        assert watcher._stats["crashloops_detected"] >= 1

        # Find crashloop event in queue
        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        crashloop_events = [e for e in events if "crashloop" in e.message.lower()]
        assert len(crashloop_events) >= 1
        assert crashloop_events[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_container_state_tracking(self, watcher):
        """Test container state tracking."""
        # Start event
        await watcher._process_event(
            json.dumps(
                {
                    "status": "start",
                    "Actor": {"ID": "abc", "Attributes": {"name": "test"}},
                }
            )
        )

        assert watcher.get_container_state("test") == "start"

        # Die event
        await watcher._process_event(
            json.dumps(
                {
                    "status": "die",
                    "Actor": {"ID": "abc", "Attributes": {"name": "test"}},
                }
            )
        )

        assert watcher.get_container_state("test") == "die"

    def test_get_restart_count(self, watcher):
        """Test restart count tracking."""
        # Initially zero
        assert watcher.get_restart_count("test-container") == 0

    @pytest.mark.asyncio
    async def test_invalid_json(self, watcher, event_queue):
        """Test handling of invalid JSON."""
        await watcher._process_event("not valid json")

        # Should not crash, stats unchanged
        assert watcher._stats["events_received"] == 0
        assert event_queue.empty()

    @pytest.mark.asyncio
    async def test_message_building(self, watcher, event_queue):
        """Test message building for different actions."""
        actions = [
            ("start", "started"),
            ("stop", "stopped"),
            ("kill", "killed"),
            ("restart", "restarted"),
        ]

        for action, expected_word in actions:
            event_json = json.dumps(
                {
                    "status": action,
                    "Actor": {"ID": "abc", "Attributes": {"name": "test"}},
                }
            )
            await watcher._process_event(event_json)
            event = event_queue.get_nowait()
            assert expected_word in event.message.lower()

    @pytest.mark.asyncio
    async def test_priority_mapping(self, watcher, event_queue):
        """Test priority mapping for events."""
        # OOM should be critical
        await watcher._process_event(
            json.dumps(
                {
                    "status": "oom",
                    "Actor": {"ID": "abc", "Attributes": {"name": "test"}},
                }
            )
        )
        event = event_queue.get_nowait()
        assert event.raw["PRIORITY"] == "2"

        # Die should be error
        await watcher._process_event(
            json.dumps(
                {
                    "status": "die",
                    "Actor": {"ID": "abc", "Attributes": {"name": "test"}},
                }
            )
        )
        event = event_queue.get_nowait()
        assert event.raw["PRIORITY"] == "3"

    @patch("elle.daemon.telemetry.docker_watcher.subprocess.run")
    def test_docker_available_check(self, mock_run, watcher):
        """Test Docker availability check."""
        mock_run.return_value = MagicMock(returncode=0)
        assert watcher._is_docker_available() is True

        mock_run.return_value = MagicMock(returncode=1)
        assert watcher._is_docker_available() is False

    @pytest.mark.asyncio
    async def test_stop(self, watcher):
        """Test stopping the watcher."""
        watcher._running = True
        await watcher.stop()
        assert watcher._running is False
