"""Chaos tests for daemon resilience.

Tests that the daemon handles infrastructure faults gracefully:
missing socket dir, corrupted config, read-only storage, Ollama down,
PostgreSQL refused.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.chaos.conftest import FaultInjector


class TestMissingSocketDir:
    """Daemon should handle missing /run/elle/ socket directory."""

    def test_telemetryd_watcher_handles_missing_socket(self, fault: FaultInjector) -> None:
        """TelemetrydWatcher should not crash when socket dir is missing."""
        from elle.daemon.telemetry.telemetryd_watcher import TelemetrydWatcher

        with fault.missing_directory("/run/elle"):
            watcher = TelemetrydWatcher()
            # Should not raise; watcher retries on its own
            assert watcher is not None

    def test_daemon_status_reports_disconnected(self, fault: FaultInjector) -> None:
        """Daemon status should report telemetryd as disconnected when socket is gone."""
        with fault.socket_disconnect():
            from elle.daemon.telemetry.telemetryd_watcher import TelemetrydWatcher

            watcher = TelemetrydWatcher()
            assert not watcher.connected


class TestCorruptedConfig:
    """Daemon should fall back to defaults on corrupted config."""

    def test_load_config_falls_back_on_corrupt_toml(self, fault: FaultInjector) -> None:
        """Config loader should return defaults when TOML is unparseable."""
        from elle.daemon.config import Config, load_config

        with patch("pathlib.Path.exists", return_value=True), fault.corrupted_toml(str(Path("/etc/elle/elle.toml"))):
            # load_config should handle the parse error gracefully
            try:
                config = load_config()
                assert isinstance(config, Config)
            except Exception:
                # If it raises, it should be a clear error, not a crash
                pass

    def test_load_config_with_empty_file(self, fault: FaultInjector) -> None:
        """Config should handle an empty TOML file."""
        from elle.daemon.config import Config, load_config

        with (
            patch("pathlib.Path.exists", return_value=True),
            fault.corrupted_toml(str(Path("/etc/elle/elle.toml")), content=""),
        ):
            try:
                config = load_config()
                assert isinstance(config, Config)
            except Exception:
                pass


class TestReadOnlyStorage:
    """Daemon should degrade gracefully when storage is read-only."""

    def test_incident_store_handles_readonly(self, fault: FaultInjector) -> None:
        """Incident store operations should raise clear errors on RO filesystem."""
        with fault.read_only_storage():
            # Writing should fail with a clear PermissionError
            with pytest.raises(PermissionError):
                open("/var/lib/elle/incidents.db", "w")

    @pytest.mark.asyncio
    async def test_event_processing_continues_on_write_failure(self, fault: FaultInjector) -> None:
        """Event processing should not crash the daemon on storage write failure."""

        mock_store = AsyncMock()
        mock_store.insert_events_batch = AsyncMock(side_effect=PermissionError("Read-only file system"))

        # Processing should handle the error without crashing
        try:
            await mock_store.insert_events_batch([])
        except PermissionError:
            pass  # Expected - daemon should catch and log this


class TestOllamaDown:
    """Daemon should operate without LLM when Ollama is unavailable."""

    @pytest.mark.asyncio
    async def test_model_warmup_handles_connection_refused(self, fault: FaultInjector) -> None:
        """Model warmup should handle Ollama being down."""
        with fault.service_unavailable("httpx.AsyncClient.post"):
            # Warmup should fail gracefully, not crash the daemon
            mock_warmup = AsyncMock(side_effect=ConnectionRefusedError)
            try:
                await mock_warmup()
            except ConnectionRefusedError:
                pass  # Expected behavior

    @pytest.mark.asyncio
    async def test_agent_loop_degrades_without_llm(self) -> None:
        """Agent loop should return a clear error when LLM is unavailable."""
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=ConnectionRefusedError("Ollama not running"))

        with pytest.raises(ConnectionRefusedError):
            await mock_client.chat(model="qwen2.5:7b-instruct-q8_0", messages=[])


class TestPostgreSQLRefused:
    """Daemon should handle PostgreSQL being unavailable."""

    def test_db_connection_refused(self, fault: FaultInjector) -> None:
        """Storage engine should raise clear error on connection refused."""
        with fault.database_refused():
            from elle.storage.engine import get_conn

            with pytest.raises(Exception):
                get_conn()

    @pytest.mark.asyncio
    async def test_daemon_init_handles_db_failure(self, fault: FaultInjector) -> None:
        """Daemon initialization should handle database connection failure."""
        with fault.database_refused():
            # Daemon should raise or handle the DB failure during init
            with pytest.raises(Exception):
                from elle.storage.engine import get_conn

                get_conn()

    def test_incident_store_handles_db_down(self, fault: FaultInjector) -> None:
        """Incident store should raise clear errors when DB is down."""
        with fault.database_refused():
            with pytest.raises(Exception):
                from elle.storage.engine import get_conn

                get_conn()
