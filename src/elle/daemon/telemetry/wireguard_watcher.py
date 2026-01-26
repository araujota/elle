"""WireGuard telemetry watcher.

Monitors WireGuard interface statistics for:
- Transfer statistics (rx/tx bytes)
- Handshake times (connection health)
- Peer status (online/offline)
- Connection anomalies

Example:
    queue = TelemetryQueue()
    shutdown = asyncio.Event()
    watcher = WireguardWatcher(queue, shutdown)
    await watcher.start()
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime
from typing import Any

from elle.daemon.telemetry.queue import TelemetryQueue

logger = logging.getLogger(__name__)

# Polling interval for WireGuard stats
POLL_INTERVAL = 60  # seconds

# Handshake considered stale after this time
HANDSHAKE_STALE_THRESHOLD = 300  # 5 minutes

# Transfer rate considered high (bytes/interval)
HIGH_TRANSFER_THRESHOLD = 100 * 1024 * 1024  # 100MB per interval


class WireguardWatcher:
    """Async watcher for WireGuard interface statistics.

    Periodically polls `wg show all dump` and generates telemetry
    events for significant changes or anomalies.

    Events generated:
    - wireguard.peer.stale: Peer handshake is too old
    - wireguard.peer.connected: New peer came online
    - wireguard.peer.disconnected: Peer went offline
    - wireguard.transfer.high: Unusually high transfer rate
    - wireguard.interface.up: Interface came online
    - wireguard.interface.down: Interface went offline
    """

    def __init__(
        self,
        queue: TelemetryQueue[dict[str, Any]],
        shutdown: asyncio.Event,
        poll_interval: int = POLL_INTERVAL,
    ):
        """Initialize the WireGuard watcher.

        Args:
            queue: Queue to put telemetry events.
            shutdown: Event to signal shutdown.
            poll_interval: Seconds between polls.
        """
        self._queue = queue
        self._shutdown = shutdown
        self._poll_interval = poll_interval
        self._running = False

        # State tracking
        self._previous_stats: dict[str, dict] = {}  # interface -> stats
        self._peer_states: dict[str, bool] = {}  # peer_pubkey -> is_online

    @property
    def running(self) -> bool:
        """Check if watcher is running."""
        return self._running

    async def start(self) -> None:
        """Start watching WireGuard interfaces.

        Runs until shutdown event is set.
        """
        logger.info("Starting WireGuard watcher")
        self._running = True

        while not self._shutdown.is_set():
            try:
                await self._poll()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WireGuard watcher error: {e}")

            # Sleep with shutdown check
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self._poll_interval,
                )
                break
            except TimeoutError:
                continue

        self._running = False
        logger.info("WireGuard watcher stopped")

    async def stop(self) -> None:
        """Stop the watcher."""
        self._shutdown.set()

    async def _poll(self) -> None:
        """Poll WireGuard stats and generate events."""
        stats = await self._get_wg_stats()
        if not stats:
            return

        now = datetime.utcnow()
        now_ts = int(now.timestamp())

        for interface, interface_stats in stats.items():
            prev_stats = self._previous_stats.get(interface, {})

            # Check each peer
            for peer in interface_stats.get("peers", []):
                peer_key = peer["public_key"][:8]  # Short key for ID
                full_key = peer["public_key"]

                # Check handshake freshness
                last_handshake = peer.get("latest_handshake")
                if last_handshake:
                    handshake_age = now_ts - last_handshake
                    was_online = self._peer_states.get(full_key, None)

                    if handshake_age < HANDSHAKE_STALE_THRESHOLD:
                        # Peer is online
                        if was_online is False:
                            # Peer came back online
                            await self._emit_event(
                                "wireguard.peer.connected",
                                "info",
                                f"WireGuard peer {peer_key}... connected",
                                {
                                    "interface": interface,
                                    "peer_key": peer_key,
                                    "endpoint": peer.get("endpoint"),
                                },
                            )
                        self._peer_states[full_key] = True
                    else:
                        # Peer handshake is stale
                        if was_online is True:
                            # Peer went offline
                            await self._emit_event(
                                "wireguard.peer.stale",
                                "warning",
                                f"WireGuard peer {peer_key}... handshake stale ({handshake_age}s)",
                                {
                                    "interface": interface,
                                    "peer_key": peer_key,
                                    "handshake_age_seconds": handshake_age,
                                    "endpoint": peer.get("endpoint"),
                                },
                            )
                        self._peer_states[full_key] = False

                # Check transfer rates
                prev_peers = prev_stats.get("peers", {})
                prev_peer = prev_peers.get(full_key, {})

                if prev_peer:
                    rx_delta = peer.get("rx_bytes", 0) - prev_peer.get("rx_bytes", 0)
                    tx_delta = peer.get("tx_bytes", 0) - prev_peer.get("tx_bytes", 0)

                    if rx_delta > HIGH_TRANSFER_THRESHOLD or tx_delta > HIGH_TRANSFER_THRESHOLD:
                        await self._emit_event(
                            "wireguard.transfer.high",
                            "info",
                            f"High WireGuard transfer on peer {peer_key}...",
                            {
                                "interface": interface,
                                "peer_key": peer_key,
                                "rx_bytes": rx_delta,
                                "tx_bytes": tx_delta,
                                "interval_seconds": self._poll_interval,
                            },
                        )

            # Store current stats for next comparison
            # Convert peers list to dict for easier lookup
            peers_dict = {p["public_key"]: p for p in interface_stats.get("peers", [])}
            self._previous_stats[interface] = {
                **interface_stats,
                "peers": peers_dict,
            }

    async def _get_wg_stats(self) -> dict[str, dict] | None:
        """Get WireGuard stats via `wg show all dump`.

        Returns:
            Dict mapping interface names to their stats.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "wg",
                "show",
                "all",
                "dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode != 0:
                return None

            return self._parse_wg_dump(stdout.decode())

        except FileNotFoundError:
            logger.debug("WireGuard tools not installed")
            return None
        except TimeoutError:
            logger.warning("WireGuard command timed out")
            return None
        except Exception as e:
            logger.error(f"Failed to get WireGuard stats: {e}")
            return None

    def _parse_wg_dump(self, output: str) -> dict[str, dict]:
        """Parse `wg show all dump` output.

        Format:
        interface_name<TAB>private_key<TAB>public_key<TAB>listen_port<TAB>fwmark
        interface_name<TAB>peer_pubkey<TAB>psk<TAB>endpoint<TAB>allowed_ips<TAB>handshake<TAB>rx<TAB>tx<TAB>keepalive

        Returns:
            Dict mapping interface names to stats.
        """
        stats: dict[str, dict] = {}
        current_interface: str | None = None

        for line in output.strip().split("\n"):
            if not line:
                continue

            parts = line.split("\t")

            # Interface line has 5 fields
            if len(parts) == 5:
                interface_name = parts[0]
                current_interface = interface_name
                stats[interface_name] = {
                    "private_key": parts[1],
                    "public_key": parts[2],
                    "listen_port": int(parts[3]) if parts[3] != "(none)" else None,
                    "fwmark": parts[4] if parts[4] != "off" else None,
                    "peers": [],
                }

            # Peer line has 9 fields
            elif len(parts) == 9 and current_interface:
                peer = {
                    "public_key": parts[1],
                    "preshared_key": parts[2] if parts[2] != "(none)" else None,
                    "endpoint": parts[3] if parts[3] != "(none)" else None,
                    "allowed_ips": parts[4].split(",") if parts[4] else [],
                    "latest_handshake": int(parts[5]) if parts[5] != "0" else None,
                    "rx_bytes": int(parts[6]),
                    "tx_bytes": int(parts[7]),
                    "keepalive": int(parts[8]) if parts[8] != "off" else None,
                }
                stats[current_interface]["peers"].append(peer)

        return stats

    async def _emit_event(
        self,
        category: str,
        severity: str,
        message: str,
        raw: dict[str, Any],
    ) -> None:
        """Emit a telemetry event.

        Args:
            category: Event category (e.g., "wireguard.peer.stale").
            severity: Event severity (info, warning, error, critical).
            message: Human-readable message.
            raw: Raw event data.
        """
        event = {
            "ts": datetime.utcnow().isoformat(),
            "source": "wireguard",
            "category": category,
            "severity": severity,
            "message": message,
            "raw": raw,
            "_SOURCE": "wireguard",
        }
        await self._queue.put(event)
        logger.debug(f"WireGuard event: {category} - {message}")


async def watch_wireguard(
    queue: TelemetryQueue[dict[str, Any]],
    shutdown: asyncio.Event,
) -> None:
    """Watch WireGuard interfaces for telemetry events.

    Convenience function wrapping WireguardWatcher.

    Args:
        queue: Queue to put raw events.
        shutdown: Event to signal shutdown.
    """
    watcher = WireguardWatcher(queue, shutdown)
    await watcher.start()


def get_wireguard_summary() -> dict[str, Any] | None:
    """Get a summary of WireGuard status synchronously.

    Returns:
        Summary dict or None if WireGuard not available.
    """
    try:
        result = subprocess.run(
            ["wg", "show", "all", "dump"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        # Parse output
        watcher = WireguardWatcher(None, None)  # type: ignore
        stats = watcher._parse_wg_dump(result.stdout)

        # Build summary
        total_rx = 0
        total_tx = 0
        peers_online = 0
        peers_total = 0
        now = int(datetime.utcnow().timestamp())

        interfaces = []
        for name, iface_stats in stats.items():
            interface_info = {
                "name": name,
                "listen_port": iface_stats.get("listen_port"),
                "peer_count": len(iface_stats.get("peers", [])),
                "peers_online": 0,
            }

            for peer in iface_stats.get("peers", []):
                peers_total += 1
                total_rx += peer.get("rx_bytes", 0)
                total_tx += peer.get("tx_bytes", 0)

                last_handshake = peer.get("latest_handshake")
                if last_handshake and (now - last_handshake) < HANDSHAKE_STALE_THRESHOLD:
                    peers_online += 1
                    interface_info["peers_online"] += 1

            interfaces.append(interface_info)

        return {
            "interfaces": interfaces,
            "interface_count": len(stats),
            "peers_total": peers_total,
            "peers_online": peers_online,
            "total_rx_bytes": total_rx,
            "total_tx_bytes": total_tx,
        }

    except FileNotFoundError:
        return None
    except Exception as e:
        logger.error(f"Failed to get WireGuard summary: {e}")
        return None
