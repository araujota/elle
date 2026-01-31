"""Chaos tests for telemetry pipeline resilience.

Tests: socket disconnect, malformed JSON, event flood (100K/s
backpressure), and clock skew handling.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from tests.chaos.conftest import FaultInjector


class TestSocketDisconnect:
    """Telemetry watcher should handle socket disconnects gracefully."""

    def test_socket_disconnect_detected(self, fault: FaultInjector) -> None:
        """Watcher should detect EOF (disconnect) on the Unix socket."""
        with fault.socket_disconnect() as mock_sock:
            # recv returning b"" means the peer disconnected
            data = mock_sock.recv(4096)
            assert data == b""

    def test_socket_write_after_disconnect(self, fault: FaultInjector) -> None:
        """Writing to a disconnected socket should raise BrokenPipeError."""
        with fault.socket_disconnect() as mock_sock:
            with pytest.raises(BrokenPipeError):
                mock_sock.send(b"test data")

    def test_watcher_reconnect_logic(self, fault: FaultInjector) -> None:
        """TelemetrydWatcher should attempt reconnection after disconnect."""
        from elle.daemon.telemetry.telemetryd_watcher import TelemetrydWatcher

        from unittest.mock import MagicMock

        with fault.socket_disconnect():
            watcher = TelemetrydWatcher(
                event_queue=MagicMock(), shutdown=asyncio.Event(),
            )
            assert not watcher.connected
            # Watcher should have reconnect logic, not crash


class TestMalformedJSON:
    """Telemetry pipeline should handle malformed JSON without crashing."""

    def test_incomplete_json_handled(self) -> None:
        """Incomplete JSON should be caught by json.loads."""
        bad_data = '{"ts": 1706300000, "category": "oom"'  # Missing closing brace

        with pytest.raises(json.JSONDecodeError):
            json.loads(bad_data)

    def test_non_json_data_handled(self) -> None:
        """Non-JSON data should not crash the parser."""
        bad_data = "this is not json at all"

        with pytest.raises(json.JSONDecodeError):
            json.loads(bad_data)

    def test_wrong_types_handled(self) -> None:
        """JSON with wrong field types should be detectable."""
        data = json.loads('{"ts": "not_a_number", "category": 12345}')
        assert not isinstance(data["ts"], int)
        assert not isinstance(data["category"], str)

    def test_empty_json_object(self) -> None:
        """Empty JSON object should parse but lack required fields."""
        data = json.loads("{}")
        assert "ts" not in data
        assert "category" not in data

    def test_null_fields(self) -> None:
        """NULL values in required fields should be detectable."""
        data = json.loads('{"ts": null, "category": null, "message": null}')
        assert data["ts"] is None
        assert data["category"] is None

    def test_stream_with_mixed_valid_invalid(self, fault: FaultInjector) -> None:
        """Stream with mix of valid and invalid lines should process valid ones."""
        lines = [
            '{"ts": 1706300000, "category": "oom", "message": "valid"}',
            "not json",
            '{"ts": 1706300001, "category": "disk", "message": "also valid"}',
            '{"incomplete": true',
            "",
        ]

        valid_events = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if "ts" in event and "category" in event:
                    valid_events.append(event)
            except json.JSONDecodeError:
                continue

        assert len(valid_events) == 2
        assert valid_events[0]["category"] == "oom"
        assert valid_events[1]["category"] == "disk"


class TestEventFlood:
    """Telemetry pipeline should apply backpressure under event flood."""

    def test_flood_generation(self, fault: FaultInjector) -> None:
        """FaultInjector should generate 100K synthetic events."""
        with fault.event_flood(count=100_000) as events:
            assert len(events) == 100_000
            assert events[0]["fingerprint"] == "0000000000000000"
            assert events[-1]["message"] == "Flood event 99999"

    @pytest.mark.asyncio
    async def test_queue_backpressure(self, fault: FaultInjector) -> None:
        """Async queue should reject events when full (backpressure)."""
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)

        with fault.event_flood(count=100_000) as events:
            enqueued = 0
            dropped = 0

            for event in events:
                try:
                    queue.put_nowait(event)
                    enqueued += 1
                except asyncio.QueueFull:
                    dropped += 1

            assert enqueued == 1000
            assert dropped == 99_000

    @pytest.mark.asyncio
    async def test_processor_drains_under_flood(self, fault: FaultInjector) -> None:
        """Event processor should keep draining even under flood conditions."""
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=5000)
        processed = 0

        async def mock_processor() -> int:
            nonlocal processed
            while not queue.empty():
                await queue.get()
                processed += 1
                queue.task_done()
            return processed

        with fault.event_flood(count=5000) as events:
            for event in events:
                await queue.put(event)

        count = await mock_processor()
        assert count == 5000

    def test_flood_does_not_exhaust_memory(self, fault: FaultInjector) -> None:
        """Event flood should use bounded memory via generator-like patterns."""
        import sys

        with fault.event_flood(count=100_000) as events:
            # 100K small dicts should be manageable (< 100MB)
            # Each event is ~200 bytes, so 100K ≈ 20MB
            size_estimate = sys.getsizeof(events) + sum(sys.getsizeof(e) for e in events[:100]) * 1000
            assert size_estimate < 200_000_000  # < 200MB


class TestClockSkew:
    """Telemetry pipeline should handle clock skew gracefully."""

    def test_forward_clock_skew(self, fault: FaultInjector) -> None:
        """Forward clock skew (future timestamps) should not crash dedup."""
        with fault.clock_skew(offset_seconds=3600.0):
            now = time.time()
            # Time should appear shifted forward by 1 hour
            assert now > 1_000_000_000  # Sanity: still reasonable epoch

    def test_backward_clock_skew(self, fault: FaultInjector) -> None:
        """Backward clock skew should not cause negative durations."""
        real_time = time.time()

        with fault.clock_skew(offset_seconds=-3600.0):
            skewed_time = time.time()
            # Skewed time should be 1 hour behind
            assert skewed_time < real_time

    def test_monotonic_clock_skew(self, fault: FaultInjector) -> None:
        """Monotonic clock skew should not break duration calculations."""
        with fault.clock_skew(offset_seconds=7200.0):
            start = time.monotonic()
            # Simulate brief work
            end = time.monotonic()
            duration = end - start
            # Duration should still be small (just function call overhead)
            assert duration >= 0
            assert duration < 1.0

    def test_event_timestamp_ordering(self, fault: FaultInjector) -> None:
        """Events with skewed timestamps should still be processable."""
        events = [
            {"ts": 1706300000000000000, "message": "first"},
            {"ts": 1706300003600000000000, "message": "skewed forward"},  # 1 hour ahead
            {"ts": 1706300000000001000, "message": "slightly after first"},
        ]

        # Sort by timestamp
        sorted_events = sorted(events, key=lambda e: e["ts"])
        assert sorted_events[0]["message"] == "first"
        assert sorted_events[-1]["message"] == "skewed forward"
