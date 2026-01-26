"""Main entry point for the elled daemon.

Orchestrates all daemon components:
- Journal watcher
- Kernel watcher
- Probe runner
- Event normalizer
- Event processor (storage + correlation)
- REST API (optional)
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
from elle.daemon.telemetry.docker_watcher import DockerEventsWatcher
from elle.daemon.telemetry.ebpf.watcher import EBPFWatcher
from elle.daemon.telemetry.inotify_watcher import InotifyWatcher
from elle.daemon.telemetry.journal import JournalWatcher
from elle.daemon.telemetry.kernel import KernelWatcher
from elle.daemon.telemetry.models import DaemonStatus, TelemetryEvent
from elle.daemon.telemetry.normalizer import Normalizer, get_normalizer
from elle.daemon.telemetry.port_probe import PortListenerProbe
from elle.daemon.telemetry.probes import ProbeRunner, create_default_probes
from elle.daemon.telemetry.queue import TelemetryQueue, create_queues
from elle.daemon.telemetry.schema import ensure_schema, get_connection
from elle.daemon.telemetry.state_cache import StateCache
from elle.daemon.telemetry.store import insert_events_batch

logger = logging.getLogger(__name__)


class ElledDaemon:
    """Main daemon orchestrator.

    Coordinates all telemetry collection, processing, and API
    components with graceful shutdown handling.
    """

    def __init__(self, config: Config | None = None):
        """Initialize the daemon.

        Args:
            config: Configuration object. Uses default if not provided.
        """
        self.config = config or get_config()
        self.shutdown = asyncio.Event()
        self.started_at: datetime | None = None

        # Queues
        self.raw_queue: TelemetryQueue[dict[str, Any]] | None = None
        self.event_queue: TelemetryQueue[Any] | None = None

        # Components
        self._journal_watcher: JournalWatcher | None = None
        self._kernel_watcher: KernelWatcher | None = None
        self._ebpf_watcher: EBPFWatcher | None = None
        self._probe_runner: ProbeRunner | None = None
        self._normalizer: Normalizer | None = None

        # New telemetry watchers
        self._docker_watcher: DockerEventsWatcher | None = None
        self._inotify_watcher: InotifyWatcher | None = None
        self._port_probe: PortListenerProbe | None = None

        # Package versioning components
        self._package_probe: Any = None
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

        raw_stats = (
            self.raw_queue.get_stats()
            if self.raw_queue
            else QueueStats(name="raw", size=0, max_size=0)
        )
        event_stats = (
            self.event_queue.get_stats()
            if self.event_queue
            else QueueStats(name="events", size=0, max_size=0)
        )

        errors: list[str] = []

        # Check component health
        if self.config.journal_enabled and self._journal_watcher:
            if not self._journal_watcher.running:
                errors.append("Journal watcher not running")
        if self.config.kernel_enabled and self._kernel_watcher:
            if not self._kernel_watcher.running:
                errors.append("Kernel watcher not running")
        if self.config.ebpf_enabled and self._ebpf_watcher:
            if not self._ebpf_watcher.running:
                errors.append("eBPF watcher not running")
        if self.config.probes_enabled and self._probe_runner:
            if not self._probe_runner.running:
                errors.append("Probe runner not running")
        if self.config.docker_enabled and self._docker_watcher:
            if not self._docker_watcher.is_running:
                errors.append("Docker watcher not running")
        if self.config.inotify_enabled and self._inotify_watcher:
            if not self._inotify_watcher.is_running:
                errors.append("Inotify watcher not running")
        if self.config.port_probe_enabled and self._port_probe:
            if not self._port_probe.is_running:
                errors.append("Port probe not running")

        return DaemonStatus(
            started_at=self.started_at or datetime.now(UTC),
            uptime_sec=self.uptime_sec,
            pid=os.getpid(),
            journal_active=bool(self._journal_watcher and self._journal_watcher.running),
            kernel_active=bool(self._kernel_watcher and self._kernel_watcher.running),
            probes_active=bool(self._probe_runner and self._probe_runner.running),
            api_active=self.config.api.enabled,
            raw_queue=raw_stats,
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

        # Initialize session token for API authentication
        # This must happen early so the token is available for API startup
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

        # Create queues
        self.raw_queue, self.event_queue = create_queues(
            raw_size=self.config.queues.raw_queue_size,
            event_size=self.config.queues.event_queue_size,
        )

        # Initialize normalizer
        self._normalizer = get_normalizer()

        # Start watchers
        if self.config.journal_enabled:
            self._journal_watcher = JournalWatcher(self.raw_queue, self.shutdown)
            self._tasks.append(
                asyncio.create_task(
                    self._journal_watcher.start(),
                    name="journal_watcher",
                )
            )

        if self.config.kernel_enabled:
            self._kernel_watcher = KernelWatcher(self.raw_queue, self.shutdown)
            self._tasks.append(
                asyncio.create_task(
                    self._kernel_watcher.start(),
                    name="kernel_watcher",
                )
            )

        # Start eBPF watcher
        if self.config.ebpf_enabled:
            self._ebpf_watcher = EBPFWatcher(
                self.raw_queue,
                self.shutdown,
                programs=list(self.config.ebpf.programs),
            )
            self._tasks.append(
                asyncio.create_task(
                    self._ebpf_watcher.start(),
                    name="ebpf_watcher",
                )
            )

        # Start probes
        if self.config.probes_enabled:
            self._probe_runner = ProbeRunner(
                self.raw_queue,
                self.shutdown,
                self.config.thresholds,
            )
            for probe in create_default_probes(self.config.thresholds):
                self._probe_runner.add_probe(probe)
            self._tasks.append(
                asyncio.create_task(
                    self._probe_runner.start(),
                    name="probe_runner",
                )
            )

        # Start Docker events watcher
        if self.config.docker_enabled:
            self._docker_watcher = DockerEventsWatcher(self.event_queue)
            self._tasks.append(
                asyncio.create_task(
                    self._docker_watcher.start(),
                    name="docker_watcher",
                )
            )

        # Start inotify file watcher
        if self.config.inotify_enabled:
            watch_paths = list(self.config.inotify_watch_paths)
            self._inotify_watcher = InotifyWatcher(
                self.event_queue,
                watch_paths=watch_paths,
            )
            self._tasks.append(
                asyncio.create_task(
                    self._inotify_watcher.start(),
                    name="inotify_watcher",
                )
            )

        # Start port listener probe
        if self.config.port_probe_enabled:
            self._port_probe = PortListenerProbe(
                self.event_queue,
                scan_interval_sec=self.config.port_probe_interval,
            )
            self._tasks.append(
                asyncio.create_task(
                    self._port_probe.start(),
                    name="port_probe",
                )
            )

        # Start capability versioning (package probe + versioner)
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

        # Start normalizer task
        self._tasks.append(
            asyncio.create_task(
                self._normalizer_loop(),
                name="normalizer",
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
            f"(journal={self.config.journal_enabled}, "
            f"kernel={self.config.kernel_enabled}, "
            f"ebpf={self.config.ebpf_enabled}, "
            f"probes={self.config.probes_enabled}, "
            f"docker={self.config.docker_enabled}, "
            f"inotify={self.config.inotify_enabled}, "
            f"port_probe={self.config.port_probe_enabled}, "
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
        if self._journal_watcher:
            await self._journal_watcher.stop()
        if self._kernel_watcher:
            await self._kernel_watcher.stop()
        if self._ebpf_watcher:
            await self._ebpf_watcher.stop()
        if self._docker_watcher:
            await self._docker_watcher.stop()
        if self._inotify_watcher:
            await self._inotify_watcher.stop()
        if self._port_probe:
            await self._port_probe.stop()
        if self._package_probe:
            # Package probe uses ProbeRunner pattern, no explicit stop needed
            pass
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
        """Pre-load LLM models into GPU memory for fast inference.

        Warms the SLM (Small Language Model) unconditionally for instant
        classification. Optionally warms the LLM if sufficient VRAM is
        available.
        """
        try:
            from elle.rag.model_warmup import ModelWarmupService

            warmup = ModelWarmupService()

            # Ensure models are pulled
            slm_ready, llm_ready = await warmup.ensure_models_ready()

            if not slm_ready:
                logger.warning("SLM model not available, classification may be slow")
            else:
                # Always warm SLM for instant classification
                slm_result = await warmup.warm_slm()
                if slm_result.success:
                    logger.info(f"SLM warmed: {slm_result.model} ({slm_result.duration_ms:.0f}ms)")
                else:
                    logger.warning(f"SLM warmup failed: {slm_result.error}")

            if not llm_ready:
                logger.warning("LLM model not available, generation will use fallbacks")
            elif warmup.has_sufficient_vram():
                # Warm LLM if we have enough VRAM for both models
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
        """Check for and process pending reboot intents.

        Called on daemon startup to detect post-reboot state and
        run verification if needed.
        """
        try:
            from elle.daemon.reboot import get_manager

            manager = get_manager()

            # Check for pending reboots (status='rebooting')
            intent = await manager.check_pending_reboots()

            if intent:
                logger.info(
                    f"Processed reboot intent: {intent.id} "
                    f"(status={intent.status}, outcome={intent.outcome})"
                )

                # Update incident if linked
                if intent.incident_id and intent.outcome != "unknown":
                    await self._update_incident_with_reboot_result(intent)

            # Also cleanup stale intents
            cleaned = await manager.cleanup_stale_intents()
            if cleaned > 0:
                logger.warning(f"Cleaned up {cleaned} stale reboot intents")

        except ImportError:
            logger.debug("Reboot module not available")
        except Exception as e:
            logger.error(f"Failed to check reboot recovery: {e}")

    async def _update_incident_with_reboot_result(self, intent: Any) -> None:
        """Update linked incident with reboot outcome.

        Args:
            intent: The completed reboot intent.
        """
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

    async def _normalizer_loop(self) -> None:
        """Process raw events and normalize them."""
        logger.debug("Normalizer loop started")

        while not self.shutdown.is_set():
            try:
                if not self.raw_queue or not self.event_queue:
                    await asyncio.sleep(0.1)
                    continue

                # Get batch of raw events
                raw_events = await self.raw_queue.get_batch(
                    max_items=100,
                    timeout=0.5,
                )

                if not raw_events:
                    continue

                # Normalize events
                if self._normalizer:
                    events = self._normalizer.normalize_batch(raw_events)

                    # Queue normalized events
                    for event in events:
                        await self.event_queue.put(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Normalizer error: {e}")
                await asyncio.sleep(1)

        logger.debug("Normalizer loop stopped")

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
        """Correlate events with incident vault.

        Args:
            events: List of TelemetryEvents to correlate.
        """
        from elle.daemon.incidents.correlator import get_correlator

        correlator = get_correlator()

        for event in events:
            # Convert to dict format expected by correlator
            event_dict = {
                "id": event.event_id,
                "ts": event.ts,
                "severity": event.severity,
                "category": event.category,
                "message": event.message,
                "entity": event.entity,
            }

            # Only correlate warning+ severity
            if event.severity in ("warning", "error", "critical"):
                try:
                    incident_id = correlator.process_event(event_dict)
                    if incident_id:
                        self._incidents_total += 1
                        logger.debug(f"Created/updated incident: {incident_id}")

                        # Send notification for new/escalated incidents
                        await self._notify_incident(
                            incident_id=incident_id,
                            title=event.message[:80],
                            severity=event.severity,
                            domain=event.category,
                        )
                except Exception as e:
                    logger.debug(f"Correlation failed: {e}")

    async def _route_to_reactive(self, events: list[TelemetryEvent]) -> None:
        """Route events to reactive function engine.

        Args:
            events: List of TelemetryEvents to route.
        """
        try:
            from elle.reactive.router import get_router

            router = get_router()
            for event in events:
                try:
                    await router.route(event)
                except Exception as e:
                    logger.debug(f"Failed to route event to reactive: {e}")
        except ImportError:
            # Reactive module not available
            pass

    async def _notify_incident(
        self,
        incident_id: str,
        title: str,
        severity: str,
        domain: str,
    ) -> None:
        """Send notification for an incident.

        Args:
            incident_id: The incident ID.
            title: Incident title/summary.
            severity: Severity level.
            domain: Incident domain.
        """
        # Only notify for error/critical severity
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
        """Run capability bootstrap for core packages if needed.

        Runs in the background after daemon startup. Only runs if:
        - Bootstrap hasn't completed yet, or
        - ELLE version changed since last run

        This generates capabilities for core system packages and
        ELLE dependencies so they're available out-of-box.
        """
        # Wait a bit for other services to stabilize
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

        Sets up the package probe and capability versioner to detect
        package upgrades and regenerate affected capabilities.
        Also enables auto-learning for newly installed packages if configured.
        """
        try:
            from elle.capabilities.autogen.store import get_store
            from elle.capabilities.autogen.versioner import CapabilityVersioner
            from elle.daemon.telemetry.package_probe import PackageProbe

            store = get_store()

            # Create versioner and build package map
            self._capability_versioner = CapabilityVersioner(store)
            package_map = self._capability_versioner.build_package_map()

            # Create package probe
            detect_new = self.config.auto_learn_new_packages
            self._package_probe = PackageProbe(detect_new_packages=detect_new)
            self._package_probe.interval = self.config.package_probe_interval

            # Set watched packages for version tracking
            if package_map:
                self._package_probe.set_watched_packages(
                    self._capability_versioner.get_watched_packages()
                )

            # Set callback for auto-learning new packages
            if detect_new:
                self._package_probe.set_new_package_callback(self._on_new_package_installed)

            # Add to probe runner if available
            if self._probe_runner:
                self._probe_runner.add_probe(self._package_probe)
                logger.info(
                    f"Capability versioning enabled: monitoring {len(package_map)} packages"
                    + (", auto-learning new packages" if detect_new else "")
                )
            else:
                logger.warning("Probe runner not available, capability versioning disabled")

        except ImportError as e:
            logger.debug(f"Capability versioning not available: {e}")
        except Exception as e:
            logger.warning(f"Failed to start capability versioning: {e}")

    def _on_new_package_installed(self, package_name: str, version: str) -> None:
        """Callback when a new package is installed.

        Schedules background capability learning for the new package.

        Args:
            package_name: Name of the newly installed package.
            version: Version of the package.
        """
        logger.info(f"New package detected: {package_name} {version}")

        # Schedule learning in background (don't block the probe)
        asyncio.create_task(
            self._auto_learn_package(package_name, version),
            name=f"auto_learn_{package_name}",
        )

    async def _auto_learn_package(self, package_name: str, version: str) -> None:
        """Auto-learn capabilities for a newly installed package.

        Args:
            package_name: Package name.
            version: Package version.
        """
        try:
            # Import here to avoid circular dependencies
            from elle.cli.package_learn_commands import _learn_package

            logger.info(f"Auto-learning package: {package_name}")

            result = await _learn_package(
                package_name,
                force_refresh=False,
                dry_run=False,
            )

            if result.capabilities_saved > 0:
                logger.info(
                    f"Auto-learned {package_name}: {result.capabilities_saved} capabilities saved"
                )

                # Add to versioner's watch list
                if self._capability_versioner and self._package_probe:
                    self._capability_versioner.add_capability_mapping(package_name, package_name)
                    self._package_probe.add_watched_package(package_name)

            elif result.errors:
                logger.warning(f"Auto-learn {package_name} failed: {result.errors[0]}")
            else:
                logger.debug(f"Auto-learn {package_name}: no capabilities generated")

        except Exception as e:
            logger.warning(f"Failed to auto-learn {package_name}: {e}")

    async def _handle_package_event(self, event: TelemetryEvent) -> None:
        """Handle package upgrade events from the package probe.

        Triggers capability regeneration when watched packages are upgraded.

        Args:
            event: TelemetryEvent with category="pkg".
        """
        if not self._capability_versioner:
            return

        raw = event.raw
        pkg = raw.get("package_name")
        old_ver = raw.get("old_version")
        new_ver = raw.get("new_version")

        if not pkg or not new_ver:
            return

        try:
            regenerated = await self._capability_versioner.on_package_upgraded(
                pkg, old_ver, new_ver
            )
            if regenerated:
                logger.info(f"Regenerated {len(regenerated)} capabilities for {pkg}: {regenerated}")
        except Exception as e:
            logger.error(f"Failed to handle package upgrade for {pkg}: {e}")

    async def _run_api(self) -> None:
        """Run the FastAPI server."""
        logger.info(f"Starting API server on {self.config.api.host}:{self.config.api.port}")

        try:
            # Import here to make fastapi optional
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

            # Run until shutdown
            await server.serve()

        except ImportError:
            logger.warning("FastAPI not installed, API disabled")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"API server error: {e}")


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the daemon.

    Args:
        level: Log level name.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise from uvicorn
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


async def run_daemon(config: Config | None = None) -> None:
    """Run the daemon with signal handling.

    Args:
        config: Optional configuration.
    """
    daemon = ElledDaemon(config)

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler() -> None:
        logger.info("Received shutdown signal")
        daemon.shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await daemon.run()
    finally:
        # Remove signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> int:
    """Main entry point for elled command.

    Returns:
        Exit code.
    """
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
        "--no-journal",
        action="store_true",
        help="Disable journal watcher",
    )
    parser.add_argument(
        "--no-kernel",
        action="store_true",
        help="Disable kernel watcher",
    )
    parser.add_argument(
        "--no-probes",
        action="store_true",
        help="Disable periodic probes",
    )
    parser.add_argument(
        "--no-ebpf",
        action="store_true",
        help="Disable eBPF telemetry",
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
    if args.no_journal or args.no_kernel or args.no_probes or args.no_ebpf or args.no_api:
        # Create modified config
        from dataclasses import replace

        from elle.daemon.config import ApiConfig

        api_config = config.api
        if args.no_api:
            api_config = ApiConfig(enabled=False)

        config = replace(
            config,
            journal_enabled=not args.no_journal and config.journal_enabled,
            kernel_enabled=not args.no_kernel and config.kernel_enabled,
            probes_enabled=not args.no_probes and config.probes_enabled,
            ebpf_enabled=not args.no_ebpf and config.ebpf_enabled,
            api=api_config,
        )

    set_config(config)

    # Run daemon
    try:
        asyncio.run(run_daemon(config))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        logger.error(f"Daemon failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
