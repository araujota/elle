from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.telemetry.wireguard_watcher import (
    HIGH_TRANSFER_THRESHOLD,
    POLL_INTERVAL,
    WireguardWatcher,
    get_wireguard_summary,
    watch_wireguard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_queue():
    q = MagicMock()
    q.put = AsyncMock()
    return q


def _make_shutdown():
    return asyncio.Event()


WG_DUMP_OUTPUT = (
    "wg0\tPRIVKEY\tPUBKEY\t51820\toff\n"
    "wg0\tPEERPUBKEY1\t(none)\t1.2.3.4:51820\t10.0.0.0/24\t{handshake}\t1000\t2000\toff\n"
)


# ---------------------------------------------------------------------------
# WireguardWatcher init & properties
# ---------------------------------------------------------------------------


class TestWireguardWatcherInit:
    def test_init_defaults(self):
        q = _make_queue()
        shutdown = _make_shutdown()
        w = WireguardWatcher(q, shutdown)
        assert w.running is False
        assert w._poll_interval == POLL_INTERVAL

    def test_init_custom_interval(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown(), poll_interval=10)
        assert w._poll_interval == 10


# ---------------------------------------------------------------------------
# _parse_wg_dump
# ---------------------------------------------------------------------------


class TestParseWgDump:
    def test_parse_interface_and_peer(self):
        q = _make_queue()
        w = WireguardWatcher(q, _make_shutdown())
        output = (
            "wg0\tPRIV\tPUB\t51820\toff\nwg0\tPEER1\t(none)\t1.2.3.4:51820\t10.0.0.0/24\t1700000000\t1000\t2000\toff\n"
        )
        stats = w._parse_wg_dump(output)
        assert "wg0" in stats
        assert stats["wg0"]["listen_port"] == 51820
        assert stats["wg0"]["fwmark"] is None
        assert len(stats["wg0"]["peers"]) == 1

        peer = stats["wg0"]["peers"][0]
        assert peer["public_key"] == "PEER1"
        assert peer["rx_bytes"] == 1000
        assert peer["tx_bytes"] == 2000
        assert peer["latest_handshake"] == 1700000000

    def test_parse_none_values(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        output = "wg0\tPRIV\tPUB\t(none)\toff\n"
        stats = w._parse_wg_dump(output)
        assert stats["wg0"]["listen_port"] is None

    def test_parse_empty(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        stats = w._parse_wg_dump("")
        assert stats == {}

    def test_parse_peer_with_psk_and_keepalive(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        output = "wg0\tPRIV\tPUB\t51820\toff\nwg0\tPEER1\tPSK123\t5.6.7.8:51820\t10.0.0.0/24\t0\t500\t600\t25\n"
        stats = w._parse_wg_dump(output)
        peer = stats["wg0"]["peers"][0]
        assert peer["preshared_key"] == "PSK123"
        assert peer["keepalive"] == 25
        assert peer["latest_handshake"] is None  # 0 means none

    def test_parse_malformed_line(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        output = "wg0\tPRIV\tPUB\t51820\toff\nBAD LINE\n"
        stats = w._parse_wg_dump(output)
        assert "wg0" in stats
        assert len(stats["wg0"]["peers"]) == 0


# ---------------------------------------------------------------------------
# _get_wg_stats
# ---------------------------------------------------------------------------


class TestGetWgStats:
    @pytest.mark.asyncio
    async def test_success(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"wg0\tPRIV\tPUB\t51820\toff\n", b"")
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", return_value=(b"wg0\tPRIV\tPUB\t51820\toff\n", b"")),
        ):
            mock_proc.communicate = AsyncMock(return_value=(b"wg0\tPRIV\tPUB\t51820\toff\n", b""))
            stats = await w._get_wg_stats()
        assert stats is not None

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            stats = await w._get_wg_stats()
        assert stats is None

    @pytest.mark.asyncio
    async def test_timeout(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        mock_proc = AsyncMock()
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=TimeoutError),
        ):
            stats = await w._get_wg_stats()
        assert stats is None

    @pytest.mark.asyncio
    async def test_nonzero_return(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"err"))
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", return_value=(b"", b"err")),
        ):
            stats = await w._get_wg_stats()
        assert stats is None

    @pytest.mark.asyncio
    async def test_general_exception(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("fail")):
            stats = await w._get_wg_stats()
        assert stats is None


# ---------------------------------------------------------------------------
# _emit_event
# ---------------------------------------------------------------------------


class TestEmitEvent:
    @pytest.mark.asyncio
    async def test_emit_event(self):
        q = _make_queue()
        w = WireguardWatcher(q, _make_shutdown())
        await w._emit_event("test.category", "info", "msg", {"key": "val"})
        q.put.assert_called_once()
        event = q.put.call_args[0][0]
        assert event["category"] == "test.category"
        assert event["severity"] == "info"
        assert event["message"] == "msg"


# ---------------------------------------------------------------------------
# _poll
# ---------------------------------------------------------------------------


class TestPoll:
    @pytest.mark.asyncio
    async def test_poll_no_stats(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        with patch.object(w, "_get_wg_stats", new_callable=AsyncMock, return_value=None):
            await w._poll()
        # Should return early without error

    @pytest.mark.asyncio
    async def test_poll_stale_handshake(self):
        q = _make_queue()
        w = WireguardWatcher(q, _make_shutdown())
        # First poll: peer is online
        now_ts = int(datetime.utcnow().timestamp())
        stats = {
            "wg0": {
                "peers": [
                    {
                        "public_key": "ABCDEFGH12345678",
                        "latest_handshake": now_ts - 10,  # fresh
                        "rx_bytes": 100,
                        "tx_bytes": 200,
                        "endpoint": "1.2.3.4:51820",
                    }
                ]
            }
        }
        with patch.object(w, "_get_wg_stats", new_callable=AsyncMock, return_value=stats):
            await w._poll()

        # Second poll: peer handshake is stale
        stats2 = {
            "wg0": {
                "peers": [
                    {
                        "public_key": "ABCDEFGH12345678",
                        "latest_handshake": now_ts - 600,  # stale
                        "rx_bytes": 100,
                        "tx_bytes": 200,
                        "endpoint": "1.2.3.4:51820",
                    }
                ]
            }
        }
        with patch.object(w, "_get_wg_stats", new_callable=AsyncMock, return_value=stats2):
            await w._poll()

        # Should have emitted stale event
        calls = [c[0][0] for c in q.put.call_args_list]
        categories = [c["category"] for c in calls]
        assert "wireguard.peer.stale" in categories

    @pytest.mark.asyncio
    async def test_poll_peer_reconnected(self):
        q = _make_queue()
        w = WireguardWatcher(q, _make_shutdown())
        now_ts = int(datetime.utcnow().timestamp())

        # Set peer as offline
        w._peer_states["ABCDEFGH12345678"] = False

        stats = {
            "wg0": {
                "peers": [
                    {
                        "public_key": "ABCDEFGH12345678",
                        "latest_handshake": now_ts - 10,
                        "rx_bytes": 100,
                        "tx_bytes": 200,
                        "endpoint": "1.2.3.4:51820",
                    }
                ]
            }
        }
        with patch.object(w, "_get_wg_stats", new_callable=AsyncMock, return_value=stats):
            await w._poll()

        calls = [c[0][0] for c in q.put.call_args_list]
        categories = [c["category"] for c in calls]
        assert "wireguard.peer.connected" in categories

    @pytest.mark.asyncio
    async def test_poll_high_transfer(self):
        q = _make_queue()
        w = WireguardWatcher(q, _make_shutdown())
        now_ts = int(datetime.utcnow().timestamp())

        # Set previous stats
        w._previous_stats["wg0"] = {
            "peers": {
                "PEERKEY1": {"rx_bytes": 0, "tx_bytes": 0},
            }
        }

        stats = {
            "wg0": {
                "peers": [
                    {
                        "public_key": "PEERKEY1",
                        "latest_handshake": now_ts - 10,
                        "rx_bytes": HIGH_TRANSFER_THRESHOLD + 1,
                        "tx_bytes": 0,
                        "endpoint": "1.2.3.4:51820",
                    }
                ]
            }
        }
        with patch.object(w, "_get_wg_stats", new_callable=AsyncMock, return_value=stats):
            await w._poll()

        calls = [c[0][0] for c in q.put.call_args_list]
        categories = [c["category"] for c in calls]
        assert "wireguard.transfer.high" in categories


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_stop(self):
        w = WireguardWatcher(_make_queue(), _make_shutdown())
        await w.stop()
        assert w._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_start_shutdown_immediately(self):
        q = _make_queue()
        shutdown = _make_shutdown()
        shutdown.set()
        w = WireguardWatcher(q, shutdown, poll_interval=1)
        await w.start()
        assert w.running is False


# ---------------------------------------------------------------------------
# watch_wireguard
# ---------------------------------------------------------------------------


class TestWatchWireguard:
    @pytest.mark.asyncio
    async def test_watch_function(self):
        q = _make_queue()
        shutdown = _make_shutdown()
        shutdown.set()
        await watch_wireguard(q, shutdown)


# ---------------------------------------------------------------------------
# get_wireguard_summary
# ---------------------------------------------------------------------------


class TestGetWireguardSummary:
    def test_success(self):
        now_ts = int(datetime.utcnow().timestamp())
        dump = (
            f"wg0\tPRIV\tPUB\t51820\toff\n"
            f"wg0\tPEER1\t(none)\t1.2.3.4:51820\t10.0.0.0/24\t{now_ts - 10}\t1000\t2000\toff\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = dump

        with patch("elle.daemon.telemetry.wireguard_watcher.subprocess.run", return_value=mock_result):
            summary = get_wireguard_summary()

        assert summary is not None
        assert summary["interface_count"] == 1
        assert summary["peers_total"] == 1
        assert summary["peers_online"] == 1

    def test_nonzero_return(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("elle.daemon.telemetry.wireguard_watcher.subprocess.run", return_value=mock_result):
            summary = get_wireguard_summary()
        assert summary is None

    def test_file_not_found(self):
        with patch("elle.daemon.telemetry.wireguard_watcher.subprocess.run", side_effect=FileNotFoundError):
            summary = get_wireguard_summary()
        assert summary is None

    def test_general_exception(self):
        with patch("elle.daemon.telemetry.wireguard_watcher.subprocess.run", side_effect=RuntimeError):
            summary = get_wireguard_summary()
        assert summary is None

    def test_stale_peer(self):
        old_ts = int(datetime.utcnow().timestamp()) - 600
        dump = (
            f"wg0\tPRIV\tPUB\t51820\toff\nwg0\tPEER1\t(none)\t1.2.3.4:51820\t10.0.0.0/24\t{old_ts}\t1000\t2000\toff\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = dump

        with patch("elle.daemon.telemetry.wireguard_watcher.subprocess.run", return_value=mock_result):
            summary = get_wireguard_summary()
        assert summary is not None
        assert summary["peers_online"] == 0
