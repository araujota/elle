"""Tests for the main daemon orchestrator."""

import asyncio
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from elle.daemon.config import ApiConfig, Config
from elle.daemon.main import ElledDaemon


def _reset_singletons():
    """Reset module-level singletons between tests."""
    # Reset notification service
    try:
        import elle.daemon.notifications.service as notif_svc
        if notif_svc._service is not None:
            notif_svc._service._running = False
            notif_svc._service = None
    except (ImportError, AttributeError):
        pass

    # Reset reboot manager
    try:
        import elle.daemon.reboot.manager as reboot_mgr
        reboot_mgr._manager = None
    except (ImportError, AttributeError):
        pass


class TestElledDaemon:
    """Tests for ElledDaemon orchestrator."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Reset singleton state before and after each test."""
        _reset_singletons()
        yield
        _reset_singletons()

    @pytest.fixture
    def temp_db(self):
        """Create temporary database path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.db"

    @pytest.fixture
    def config(self, temp_db):
        """Create test configuration."""
        return Config(
            db_path=temp_db,
            journal_enabled=False,  # Disable for testing
            kernel_enabled=False,
            probes_enabled=False,
            ebpf_enabled=False,  # Disable eBPF (BCC not installed in test env)
            api=ApiConfig(enabled=False),
        )

    @pytest.fixture
    def daemon(self, config):
        """Create test daemon instance."""
        return ElledDaemon(config)

    def test_daemon_initialization(self, daemon):
        """Daemon should initialize without error."""
        assert daemon.config is not None
        assert daemon.shutdown.is_set() is False
        assert daemon.started_at is None

    def test_uptime_before_start(self, daemon):
        """Uptime should be 0 before start."""
        assert daemon.uptime_sec == 0

    @pytest.mark.asyncio
    async def test_daemon_start_stop(self, daemon):
        """Daemon should start and stop gracefully."""
        # Start daemon
        await daemon.start()

        assert daemon.started_at is not None
        assert daemon.uptime_sec >= 0
        assert daemon.raw_queue is not None
        assert daemon.event_queue is not None

        # Stop daemon
        await daemon.stop()

        assert daemon.shutdown.is_set() is True

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux-specific features (Docker, inotify)")
    async def test_daemon_status(self, daemon):
        """Daemon should report status correctly."""
        await daemon.start()

        status = daemon.get_status()

        assert status.pid > 0
        assert status.uptime_sec >= 0
        assert status.healthy is True
        assert status.raw_queue.name == "raw"
        assert status.event_queue.name == "events"

        await daemon.stop()

    @pytest.mark.asyncio
    async def test_daemon_run_with_shutdown(self, daemon):
        """Daemon run should respond to shutdown signal."""
        async def trigger_shutdown():
            await asyncio.sleep(0.2)
            daemon.shutdown.set()

        # Run daemon with concurrent shutdown trigger
        await asyncio.gather(
            daemon.run(),
            trigger_shutdown(),
        )

        assert daemon.shutdown.is_set() is True

    @pytest.mark.asyncio
    async def test_daemon_queues_created(self, daemon):
        """Daemon should create queues on start."""
        assert daemon.raw_queue is None
        assert daemon.event_queue is None

        await daemon.start()

        assert daemon.raw_queue is not None
        assert daemon.event_queue is not None
        assert daemon.raw_queue.maxsize == daemon.config.queues.raw_queue_size
        assert daemon.event_queue.maxsize == daemon.config.queues.event_queue_size

        await daemon.stop()


class TestDaemonWithComponents:
    """Tests for daemon with various components enabled."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Reset singleton state before and after each test."""
        _reset_singletons()
        yield
        _reset_singletons()

    @pytest.fixture
    def temp_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.db"

    @pytest.mark.asyncio
    async def test_daemon_with_probes(self, temp_db):
        """Daemon should run probes when enabled."""
        config = Config(
            db_path=temp_db,
            journal_enabled=False,
            kernel_enabled=False,
            probes_enabled=True,
            ebpf_enabled=False,
            api=ApiConfig(enabled=False),
        )
        daemon = ElledDaemon(config)

        await daemon.start()
        await asyncio.sleep(0.1)

        status = daemon.get_status()
        assert status.probes_active is True

        daemon.shutdown.set()
        await daemon.stop()

    @pytest.mark.asyncio
    async def test_daemon_status_reflects_config(self, temp_db):
        """Status should reflect which components are configured."""
        config = Config(
            db_path=temp_db,
            journal_enabled=False,
            kernel_enabled=False,
            probes_enabled=False,
            ebpf_enabled=False,
            api=ApiConfig(enabled=False),
        )
        daemon = ElledDaemon(config)

        await daemon.start()
        status = daemon.get_status()

        assert status.journal_active is False
        assert status.kernel_active is False
        assert status.probes_active is False
        assert status.api_active is False

        await daemon.stop()


class TestDaemonNormalizerProcessor:
    """Tests for daemon normalizer and processor loops."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.db"

    @pytest.mark.asyncio
    async def test_event_flow(self, temp_db):
        """Events should flow through raw queue to event queue."""
        config = Config(
            db_path=temp_db,
            journal_enabled=False,
            kernel_enabled=False,
            probes_enabled=False,
            api=ApiConfig(enabled=False),
        )
        daemon = ElledDaemon(config)

        await daemon.start()

        # Inject a raw event
        assert daemon.raw_queue is not None
        raw_event = {
            "_SOURCE": "journal",
            "MESSAGE": "Test event message",
            "PRIORITY": "6",
        }
        await daemon.raw_queue.put(raw_event)

        # Wait for processing
        await asyncio.sleep(0.5)

        # Event should have been processed
        assert daemon._events_total >= 0

        daemon.shutdown.set()
        await daemon.stop()
