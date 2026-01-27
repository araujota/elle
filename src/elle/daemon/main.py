"""Main entry point for the elled daemon.

Orchestrates all daemon components:
- Telemetryd watcher (receives pre-normalized events from C daemon)
- Event processor (storage + correlation)
- REST API (optional)

NOTE: This daemon requires elled-telemetryd to be running.
All telemetry collection, normalization, and deduplication is handled
by the C daemon. This Python daemon receives pre-normalized events
via Unix socket and handles storage, correlation, and API.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elle.daemon.config import Config, get_config, load_config, set_config
from elle.daemon.telemetry.models import DaemonStatus, TelemetryEvent
from elle.daemon.telemetry.queue import TelemetryQueue, create_queues
from elle.daemon.telemetry.schema import ensure_schema, get_connection
from elle.daemon.telemetry.state_cache import StateCache
from elle.daemon.telemetry.store import insert_events_batch
from elle.daemon.telemetry.telemetryd_watcher import (
    TelemetrydWatcher,
    is_telemetryd_available,
)

logger = logging.getLogger(__name__)

# Telemetryd socket path
TELEMETRYD_SOCKET = Path("/run/elle/telemetry.sock")


class ElledDaemon:
    """Main daemon orchestrator.

    Coordinates event processing, storage, correlation, and API.
    Requires elled-telemetryd to be running for telemetry collection.
    """

    def __init__(self, config: Config | None = None):
        """Initialize the daemon.

        Args:
            config: Configuration object. Uses default if not provided.
        """
        self.config = config or get_config()
        self.shutdown = asyncio.Event()
        self.started_at: datetime | None = None

        # Event queue (receives pre-normalized events from telemetryd)
        self.event_queue: TelemetryQueue[TelemetryEvent] | None = None

        # Telemetryd watcher (connects to C daemon)
        self._telemetryd_watcher: TelemetrydWatcher | None = None

        # Capability versioning components
        self._capability_versioner: Any = None

        # State cache for CLI queries
        self._state_cache: StateCache | None = None

        # Man Vault service for documentation indexing
        self._manvault_service: Any = None

        # Session token manager for API authentication
        self._session_token_manager: Any = None

        # Tasks
        self._tasks: list[asyncio.Task[Any]] = []

        # Services
        self._notification_service: Any = None

        # Counters
        self._events_total = 0
        self._incidents_total = 0

    @property
    def uptime_sec(self) -> int:
        """Get daemon uptime in seconds."""
        if not self.started_at:
            return 0
        return int((datetime.now(UTC) - self.started_at).total_seconds())

    def get_status(self) -> DaemonStatus:
        """Get current daemon status.

        Returns:
            DaemonStatus object.
        """
        from elle.daemon.telemetry.models import QueueStats

        event_stats = (
            self.event_queue.get_stats() if self.event_queue else QueueStats(name="events", size=0, max_size=0)
        )

        errors: list[str] = []

        # Check telemetryd watcher health
        if self._telemetryd_watcher and not self._telemetryd_watcher.running:
            errors.append("Telemetryd watcher not running - restart elled-telemetryd")

        return DaemonStatus(
            started_at=self.started_at or datetime.now(UTC),
            uptime_sec=self.uptime_sec,
            pid=os.getpid(),
            journal_active=True,  # Handled by telemetryd
            kernel_active=True,  # Handled by telemetryd
            probes_active=True,  # Handled by telemetryd
            api_active=self.config.api.enabled,
            raw_queue=QueueStats(name="raw", size=0, max_size=0),  # Not used
            event_queue=event_stats,
            events_total=self._events_total,
            incidents_total=self._incidents_total,
            healthy=len(errors) == 0,
            errors=tuple(errors),
        )

    async def start(self) -> None:
        """Start all daemon components."""
        logger.info("Starting elled daemon")
        self.started_at = datetime.now(UTC)

        # Check if telemetryd is available
        if not is_telemetryd_available():
            logger.error("elled-telemetryd is not running. Start it with: sudo systemctl start elled-telemetryd")
            logger.error("The Python daemon requires the C telemetry daemon for event collection.")
            raise RuntimeError("elled-telemetryd is not available")

        # Initialize session token for API authentication
        from elle.daemon.api.session_token import get_token_manager

        self._session_token_manager = get_token_manager()
        self._session_token_manager.initialize()
        logger.info(f"Session token written to {self._session_token_manager.token_path}")

        # Initialize database
        self._init_database()

        # Pre-warm LLM models for fast inference
        await self._warmup_models()

        # Start notification service
        await self._start_notification_service()

        # Check for pending reboot recovery
        await self._check_reboot_recovery()

        # Create event queue (only need event queue, telemetryd handles raw events)
        _, self.event_queue = create_queues(
            raw_size=100,  # Not used, but required by create_queues
            event_size=self.config.queues.event_queue_size,
        )

        # Start telemetryd watcher
        logger.info("Connecting to elled-telemetryd")
        self._telemetryd_watcher = TelemetrydWatcher(
            self.event_queue,
            self.shutdown,
        )
        self._tasks.append(
            asyncio.create_task(
                self._telemetryd_watcher.start(),
                name="telemetryd_watcher",
            )
        )

        # Start capability versioning (listens to package events from telemetryd)
        if self.config.capability_versioning_enabled:
            await self._start_capability_versioning()

        # Start state cache for CLI state queries
        self._state_cache = StateCache(
            docker_refresh_sec=30,
            network_refresh_sec=60,
            firewall_refresh_sec=60,
        )
        await self._state_cache.start()

        # Start Man Vault service for documentation indexing
        try:
            from elle.daemon.manvault.service import get_service as get_manvault_service

            self._manvault_service = get_manvault_service()
            await self._manvault_service.start()
            logger.info("Man Vault service started")
        except Exception as e:
            logger.warning(f"Failed to start Man Vault service: {e}")

        # Schedule capability bootstrap in background (after other services ready)
        if self.config.capability_bootstrap_enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._run_capability_bootstrap(),
                    name="capability_bootstrap",
                )
            )

        # Start processor task
        self._tasks.append(
            asyncio.create_task(
                self._processor_loop(),
                name="processor",
            )
        )

        # Start API
        if self.config.api.enabled:
            self._tasks.append(
                asyncio.create_task(
                    self._run_api(),
                    name="api",
                )
            )

        logger.info(
            f"elled started with {len(self._tasks)} tasks "
            f"(telemetryd=connected, "
            f"capability_versioning={self.config.capability_versioning_enabled}, "
            f"api={self.config.api.enabled})"
        )

    async def stop(self) -> None:
        """Graceful shutdown of all components."""
        logger.info("Stopping elled daemon")
        self.shutdown.set()

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks with timeout
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning("Some tasks did not stop in time")

        # Stop watchers explicitly
        if self._telemetryd_watcher:
            await self._telemetryd_watcher.stop()
        if self._state_cache:
            await self._state_cache.stop()
        if self._manvault_service:
            await self._manvault_service.stop()

        # Clean up session token
        if self._session_token_manager:
            self._session_token_manager.cleanup()
            logger.info("Session token cleaned up")

        # Stop notification service
        await self._stop_notification_service()

        logger.info(f"elled stopped (uptime: {self.uptime_sec}s, events: {self._events_total})")

    async def run(self) -> None:
        """Run the daemon until shutdown."""
        await self.start()

        # Wait for shutdown signal
        await self.shutdown.wait()

        await self.stop()

    def _init_database(self) -> None:
        """Initialize database schemas."""
        try:
            # Ensure directory exists
            db_dir = self.config.db_path.parent
            if not str(db_dir).startswith("/var/lib"):
                db_dir.mkdir(parents=True, exist_ok=True)

            # Initialize telemetry schema
            conn = get_connection(self.config.db_path)
            try:
                ensure_schema(conn)
            finally:
                conn.close()

            logger.debug(f"Database initialized at {self.config.db_path}")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")

    async def _warmup_models(self) -> None:
        """Pre-load LLM models into GPU memory for fast inference."""
        try:
            from elle.rag.model_warmup import ModelWarmupService

            warmup = ModelWarmupService()

            # Ensure models are pulled
            slm_ready, llm_ready = await warmup.ensure_models_ready()

            if not slm_ready:
                logger.warning("SLM model not available, classification may be slow")
            else:
                slm_result = await warmup.warm_slm()
                if slm_result.success:
                    logger.info(f"SLM warmed: {slm_result.model} ({slm_result.duration_ms:.0f}ms)")
                else:
                    logger.warning(f"SLM warmup failed: {slm_result.error}")

            if not llm_ready:
                logger.warning("LLM model not available, generation will use fallbacks")
            elif warmup.has_sufficient_vram():
                llm_result = await warmup.warm_llm()
                if llm_result.success:
                    logger.info(f"LLM warmed: {llm_result.model} ({llm_result.duration_ms:.0f}ms)")
                else:
                    logger.warning(f"LLM warmup failed: {llm_result.error}")
            else:
                logger.info("Skipping LLM warmup due to limited VRAM (will load on-demand)")

            await warmup.close()

        except ImportError:
            logger.debug("Model warmup service not available")
        except Exception as e:
            logger.warning(f"Model warmup failed: {e}")

    async def _start_notification_service(self) -> None:
        """Start the notification service."""
        try:
            from elle.daemon.notifications import get_service

            self._notification_service = get_service()
            await self._notification_service.start()
            logger.info("Notification service started")
        except ImportError:
            logger.debug("Notification service not available")
        except Exception as e:
            logger.warning(f"Failed to start notification service: {e}")

    async def _stop_notification_service(self) -> None:
        """Stop the notification service."""
        if self._notification_service:
            try:
                await self._notification_service.stop()
            except Exception as e:
                logger.warning(f"Failed to stop notification service: {e}")

    async def _check_reboot_recovery(self) -> None:
        """Check for and process pending reboot intents."""
        try:
            from elle.daemon.reboot import get_manager

            manager = get_manager()
            intent = await manager.check_pending_reboots()

            if intent:
                logger.info(f"Processed reboot intent: {intent.id} (status={intent.status}, outcome={intent.outcome})")
                if intent.incident_id and intent.outcome != "unknown":
                    await self._update_incident_with_reboot_result(intent)

            cleaned = await manager.cleanup_stale_intents()
            if cleaned > 0:
                logger.warning(f"Cleaned up {cleaned} stale reboot intents")

        except ImportError:
            logger.debug("Reboot module not available")
        except Exception as e:
            logger.error(f"Failed to check reboot recovery: {e}")

    async def _update_incident_with_reboot_result(self, intent: Any) -> None:
        """Update linked incident with reboot outcome."""
        try:
            from elle.daemon.incidents.store import update_incident

            if intent.outcome == "improved":
                update_incident(
                    intent.incident_id,
                    outcome="improved",
                    root_cause=f"Resolved by reboot: {intent.goal}",
                )
            elif intent.outcome in ("failed", "rolled_back"):
                update_incident(
                    intent.incident_id,
                    outcome="partial",
                    verification_steps=[f"Reboot {intent.outcome}: {intent.outcome_detail}"],
                )
        except Exception as e:
            logger.warning(f"Failed to update incident: {e}")

    async def _processor_loop(self) -> None:
        """Process normalized events (storage + correlation)."""
        logger.debug("Processor loop started")

        conn = None
        try:
            conn = get_connection(self.config.db_path)
            ensure_schema(conn)
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")

        while not self.shutdown.is_set():
            try:
                if not self.event_queue:
                    await asyncio.sleep(0.1)
                    continue

                # Get batch of events
                events = await self.event_queue.get_batch(
                    max_items=100,
                    timeout=1.0,
                )

                if not events:
                    continue

                # Store events
                if conn:
                    try:
                        inserted = insert_events_batch(events, conn)
                        self._events_total += inserted
                    except Exception as e:
                        logger.error(f"Failed to store events: {e}")

                # Correlate events (integrate with incident vault)
                try:
                    await self._correlate_events(events)
                except Exception as e:
                    logger.error(f"Failed to correlate events: {e}")

                # Route events to reactive functions
                try:
                    await self._route_to_reactive(events)
                except Exception as e:
                    logger.error(f"Failed to route events: {e}")

                # Handle package upgrade events for capability versioning
                if self.config.capability_versioning_enabled:
                    for event in events:
                        if event.category == "pkg":
                            try:
                                await self._handle_package_event(event)
                            except Exception as e:
                                logger.debug(f"Failed to handle package event: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Processor error: {e}")
                await asyncio.sleep(1)

        if conn:
            conn.close()

        logger.debug("Processor loop stopped")

    async def _correlate_events(self, events: list[Any]) -> None:
        """Correlate events with incident vault."""
        from elle.daemon.incidents.correlator import get_correlator

        correlator = get_correlator()

        for event in events:
            event_dict = {
                "id": event.event_id,
                "ts": event.ts,
                "severity": event.severity,
                "category": event.category,
                "message": event.message,
                "entity": event.entity,
            }

            if event.severity in ("warning", "error", "critical"):
                try:
                    incident_id = correlator.process_event(event_dict)
                    if incident_id:
                        self._incidents_total += 1
                        logger.debug(f"Created/updated incident: {incident_id}")

                        await self._notify_incident(
                            incident_id=incident_id,
                            title=event.message[:80],
                            severity=event.severity,
                            domain=event.category,
                        )
                except Exception as e:
                    logger.debug(f"Correlation failed: {e}")

    async def _route_to_reactive(self, events: list[TelemetryEvent]) -> None:
        """Route events to reactive function engine."""
        try:
            from elle.reactive.router import get_router

            router = get_router()
            for event in events:
                try:
                    await router.route(event)
                except Exception as e:
                    logger.debug(f"Failed to route event to reactive: {e}")
        except ImportError:
            pass

    async def _notify_incident(
        self,
        incident_id: str,
        title: str,
        severity: str,
        domain: str,
    ) -> None:
        """Send notification for an incident."""
        if severity not in ("error", "critical"):
            return

        try:
            from elle.daemon.notifications import notify_incident

            notify_incident(
                title=title,
                severity=severity,
                domain=domain,
                incident_id=incident_id,
            )
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Failed to send incident notification: {e}")

    async def _run_capability_bootstrap(self) -> None:
        """Run capability bootstrap for core packages if needed."""
        await asyncio.sleep(10)

        try:
            from elle.capabilities.autogen.bootstrap import (
                run_bootstrap,
                set_bootstrap_complete,
                should_run_bootstrap,
            )

            if not should_run_bootstrap():
                logger.debug("Capability bootstrap already complete")
                return

            logger.info("Starting capability bootstrap for core packages...")

            def log_progress(current: int, total: int, pkg_name: str) -> None:
                if current % 5 == 0 or current == total:
                    logger.info(f"Bootstrap: {current}/{total} - {pkg_name}")

            result = await run_bootstrap(
                include_core=True,
                include_optional=True,
                include_dependencies=True,
                skip_existing=True,
                progress_callback=log_progress,
            )

            set_bootstrap_complete(result)

            logger.info(
                f"Capability bootstrap complete: "
                f"{result.packages_succeeded}/{result.packages_attempted} packages, "
                f"{result.capabilities_saved} capabilities saved "
                f"({result.duration_seconds:.1f}s)"
            )

            if result.failed_packages:
                logger.warning(f"Bootstrap: {len(result.failed_packages)} packages failed")

        except ImportError as e:
            logger.debug(f"Capability bootstrap not available: {e}")
        except asyncio.CancelledError:
            logger.debug("Capability bootstrap cancelled")
        except Exception as e:
            logger.error(f"Capability bootstrap failed: {e}")

    async def _start_capability_versioning(self) -> None:
        """Initialize capability versioning components.

        Listens to package events from telemetryd and regenerates
        capabilities when packages are upgraded.
        """
        try:
            from elle.capabilities.autogen.store import get_store
            from elle.capabilities.autogen.versioner import CapabilityVersioner

            store = get_store()
            self._capability_versioner = CapabilityVersioner(store)
            package_map = self._capability_versioner.build_package_map()

            logger.info(
                f"Capability versioning enabled: monitoring {len(package_map)} packages "
                "(package events from telemetryd)"
            )

        except ImportError as e:
            logger.debug(f"Capability versioning not available: {e}")
        except Exception as e:
            logger.warning(f"Failed to start capability versioning: {e}")

    async def _handle_package_event(self, event: TelemetryEvent) -> None:
        """Handle package upgrade events from telemetryd."""
        if not self._capability_versioner:
            return

        raw = event.raw
        pkg = raw.get("package_name")
        old_ver = raw.get("old_version")
        new_ver = raw.get("new_version")

        if not pkg or not new_ver:
            return

        try:
            regenerated = await self._capability_versioner.on_package_upgraded(pkg, old_ver, new_ver)
            if regenerated:
                logger.info(f"Regenerated {len(regenerated)} capabilities for {pkg}: {regenerated}")
        except Exception as e:
            logger.error(f"Failed to handle package upgrade for {pkg}: {e}")

    async def _run_api(self) -> None:
        """Run the FastAPI server."""
        logger.info(f"Starting API server on {self.config.api.host}:{self.config.api.port}")

        try:
            import uvicorn

            from elle.daemon.api.app import create_app

            app = create_app(self)

            config = uvicorn.Config(
                app,
                host=self.config.api.host,
                port=self.config.api.port,
                log_level="warning",
                access_log=False,
            )
            server = uvicorn.Server(config)

            await server.serve()

        except ImportError:
            logger.warning("FastAPI not installed, API disabled")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"API server error: {e}")


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the daemon."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


async def run_daemon(config: Config | None = None) -> None:
    """Run the daemon with signal handling."""
    daemon = ElledDaemon(config)

    loop = asyncio.get_event_loop()

    def signal_handler() -> None:
        logger.info("Received shutdown signal")
        daemon.shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await daemon.run()
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> int:
    """Main entry point for elled command."""
    parser = argparse.ArgumentParser(
        description="ELLE Daemon - Local System Intelligence",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Configuration file path",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Disable REST API",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level)

    # Load configuration
    config = load_config(args.config)

    # Apply command-line overrides
    if args.no_api:
        from dataclasses import replace

        from elle.daemon.config import ApiConfig

        config = replace(
            config,
            api=ApiConfig(enabled=False),
        )

    set_config(config)

    # Run daemon
    try:
        asyncio.run(run_daemon(config))
        return 0
    except KeyboardInterrupt:
        return 0
    except RuntimeError as e:
        if "telemetryd" in str(e):
            logger.error("Please start elled-telemetryd first:")
            logger.error("  sudo systemctl start elled-telemetryd")
            return 1
        raise
    except Exception as e:
        logger.error(f"Daemon failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
