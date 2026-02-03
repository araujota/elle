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

from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from elle.daemon.config import Config, get_config, load_config, set_config
from elle.daemon.telemetry.models import DaemonStatus, TelemetryEvent
from elle.daemon.telemetry.queue import TelemetryQueue, create_queues
from elle.daemon.telemetry.state_cache import StateCache
from elle.daemon.telemetry.store import insert_events_batch
from elle.daemon.telemetry.telemetryd_watcher import (
    TelemetrydWatcher,
    is_telemetryd_available,
)
from elle.storage.config import PostgresConfig
from elle.storage.engine import close_pools_async, configure_pool

logger = logging.getLogger(__name__)

# Telemetryd socket path
TELEMETRYD_SOCKET = Path("/run/elle/telemetry.sock")

# PID file for daemon management
PID_FILE = Path("/var/run/elle/elled.pid")


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

        # Cloud retry worker
        self._cloud_retry_worker: Any = None

        # Tasks
        self._tasks: list[asyncio.Task[Any]] = []

        # Services
        self._notification_service: Any = None
        self._warmup_service: Any = None

        # Counters
        self._events_total = 0
        self._incidents_total = 0
        self._correlate_semaphore = asyncio.Semaphore(10)

    @property
    def uptime_sec(self) -> int:
        """Get daemon uptime in seconds."""
        if not self.started_at:
            return 0
        return int((datetime.now(timezone.utc) - self.started_at).total_seconds())

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
            started_at=self.started_at or datetime.now(timezone.utc),
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
        self.started_at = datetime.now(timezone.utc)

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

        # Prune old telemetry events on startup
        try:
            from elle.daemon.telemetry.schema import prune_old_events
            from elle.storage.engine import get_conn

            with get_conn(schema="telemetry") as conn:
                prune_old_events(conn, retention_days=30)
                conn.commit()
        except Exception as e:
            logger.debug(f"Event pruning skipped: {e}")

        # Start cloud retry worker
        await self._start_cloud_retry_worker()

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

        # Drain event queue before shutdown (H13)
        if self.event_queue:
            try:
                while not self.event_queue.empty:
                    events = await asyncio.wait_for(self.event_queue.get_batch(max_items=100, timeout=0.5), timeout=1.0)
                    if events:
                        await asyncio.to_thread(insert_events_batch, events)
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("Queue drain timed out, events may be lost")
            except Exception as e:
                logger.warning(f"Queue drain failed: {e}")

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks with timeout (increased from 5s to 15s)
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=15.0,
                )
            except (TimeoutError, asyncio.TimeoutError):
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

        # Stop cloud retry worker
        if self._cloud_retry_worker:
            try:
                await self._cloud_retry_worker.stop()
            except Exception as e:
                logger.warning(f"Error stopping cloud retry worker: {e}")

        # Stop notification service
        await self._stop_notification_service()

        # Stop warmup service (periodic warmup)
        await self._stop_warmup_service()

        # Close database connection pools
        await close_pools_async()

        logger.info(f"elled stopped (uptime: {self.uptime_sec}s, events: {self._events_total})")

    async def run(self) -> None:
        """Run the daemon until shutdown."""
        # Write PID file with advisory flock (H11)
        self._pid_lock_fd: int | None = None
        try:
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = os.open(str(PID_FILE), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                # Lock held by another process
                try:
                    old_pid = os.read(lock_fd, 32).decode().strip()
                    logger.error("elled already running (PID %s, locked)", old_pid)
                except Exception:
                    logger.error("elled already running (PID file locked)")
                os.close(lock_fd)
                sys.exit(1)
            # Lock acquired -- write PID directly to locked FD (M6)
            os.ftruncate(lock_fd, 0)
            os.lseek(lock_fd, 0, os.SEEK_SET)
            os.write(lock_fd, str(os.getpid()).encode())
            os.fsync(lock_fd)
            self._pid_lock_fd = lock_fd
        except SystemExit:
            raise
        except OSError as e:
            logger.warning(f"Could not write PID file: {e}")

        try:
            # Install SIGHUP handler before start() so startup signals are handled
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGHUP, self._handle_sighup)

            await self.start()

            # Wait for shutdown signal
            await self.shutdown.wait()

            await self.stop()
        finally:
            # Release advisory lock and remove PID file
            if self._pid_lock_fd is not None:
                try:
                    fcntl.flock(self._pid_lock_fd, fcntl.LOCK_UN)
                    os.close(self._pid_lock_fd)
                except OSError:
                    pass
                self._pid_lock_fd = None
            PID_FILE.unlink(missing_ok=True)

    def _handle_sighup(self) -> None:
        """Handle SIGHUP for configuration reload."""
        logger.info("Received SIGHUP, reloading configuration")
        try:
            new_config = load_config()
            set_config(new_config)
            self.config = new_config
            logger.info("Configuration reloaded successfully")
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")

    def _init_database(self) -> None:
        """Initialize PostgreSQL connection pool and run all schema migrations."""
        try:
            # Build PostgresConfig from daemon DatabaseConfig
            db_cfg = self.config.database
            pg_config = PostgresConfig(
                host=db_cfg.host,
                port=db_cfg.port,
                dbname=db_cfg.dbname,
                user=db_cfg.user,
                password=db_cfg.password,
                socket_dir=db_cfg.socket_dir,
                min_pool_size=db_cfg.min_pool_size,
                max_pool_size=db_cfg.max_pool_size,
                max_idle_sec=db_cfg.max_idle_sec,
                sslmode=db_cfg.sslmode,
                encryption_key_path=db_cfg.encryption_key_path,
            )

            # Create connection pool
            configure_pool(pg_config)

            # Run all schema migrations
            from elle.common.db import init_all_schemas

            init_all_schemas()

            logger.debug("PostgreSQL database initialized (%s)", pg_config.conninfo_safe)

        except Exception as e:
            logger.critical(f"Database initialization failed: {e}")
            raise  # Fatal — daemon cannot operate without DB

    async def _warmup_models(self) -> None:
        """Pre-load LLM models into GPU memory for fast inference.

        For local inference, the LLM is kept warm via:
        1. keep_alive=-1 in Ollama (never unload)
        2. Periodic warmup pings every 5 minutes (belt-and-suspenders)
        """
        try:
            from elle.rag.model_warmup import ModelWarmupService

            self._warmup_service = ModelWarmupService()

            # Ensure LLM model is available
            llm_ready = await self._warmup_service.ensure_model_ready()

            if not llm_ready:
                logger.warning("LLM model not available, generation will use fallbacks")
            else:
                llm_result = await self._warmup_service.warm_llm()
                if llm_result.success:
                    logger.info(f"LLM warmed: {llm_result.model} ({llm_result.duration_ms:.0f}ms)")

                    # Start periodic warmup to keep model loaded
                    await self._warmup_service.start_periodic_warmup()
                else:
                    logger.warning(f"LLM warmup failed: {llm_result.error}")

        except ImportError:
            logger.debug("Model warmup service not available")
        except Exception as e:
            logger.warning(f"Model warmup failed: {e}")

    async def _stop_warmup_service(self) -> None:
        """Stop the model warmup service."""
        if hasattr(self, "_warmup_service") and self._warmup_service:
            try:
                await self._warmup_service.close()
            except Exception as e:
                logger.warning(f"Failed to stop warmup service: {e}")

    async def _start_cloud_retry_worker(self) -> None:
        """Start the cloud submission retry worker."""
        if not self.config.cloud_sync.enabled:
            logger.info("Cloud sync disabled, skipping retry worker")
            return

        try:
            from elle.daemon.incidents.cloud_queue import configure_cloud_queue
            from elle.daemon.incidents.cloud_retry_worker import CloudRetryWorker

            cs = self.config.cloud_sync
            configure_cloud_queue(
                initial_delay_sec=cs.retry_initial_delay_sec,
                max_delay_sec=cs.retry_max_delay_sec,
                backoff_factor=cs.retry_backoff_factor,
                max_retries=cs.max_retries,
                max_size=cs.queue_max_size,
            )

            # Create and start the worker
            self._cloud_retry_worker = CloudRetryWorker(
                poll_interval_sec=cs.worker_poll_interval_sec,
                batch_size=cs.batch_size,
                health_check_interval_sec=cs.health_check_interval_sec,
                stale_entry_hours=cs.stale_entry_hours,
            )
            await self._cloud_retry_worker.start()
            logger.info("Cloud retry worker started")

        except Exception as e:
            logger.warning(f"Failed to start cloud retry worker: {e}")

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
                    await asyncio.sleep(0.05)
                    continue

                # Store events via thread offload with timeout (C7)
                try:
                    inserted = await asyncio.wait_for(
                        asyncio.to_thread(insert_events_batch, events),
                        timeout=30.0,
                    )
                    self._events_total += inserted
                except (TimeoutError, asyncio.TimeoutError):
                    logger.error(f"DB insert timed out, dropping {len(events)} events")
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
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # basicConfig is idempotent; explicitly set the level to ensure it applies
    # even when the root logger already has handlers from prior calls.
    logging.getLogger().setLevel(log_level)

    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


async def run_daemon(config: Config | None = None) -> None:
    """Run the daemon with signal handling."""
    daemon = ElledDaemon(config)

    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

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
