"""Inotify Watcher - monitors file changes using Linux inotify.

Watches specified files for modifications, creations, and deletions.
Generates telemetry events with diff previews for text files.

Default watched paths:
- /root/.ssh/authorized_keys
- /etc/ssh/sshd_config
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elle.daemon.telemetry.models import TelemetryEvent

logger = logging.getLogger(__name__)

# Default paths to watch
DEFAULT_WATCH_PATHS = [
    "/root/.ssh/authorized_keys",
    "/home/*/.ssh/authorized_keys",
    "/etc/ssh/sshd_config",
    "/etc/sudoers",
    "/etc/passwd",
    "/etc/shadow",
]

# Events we care about
WATCH_EVENTS = ["modify", "create", "delete", "attrib"]


class InotifyWatcher:
    """Watches files for changes using inotify.

    Provides:
    - Real-time file change detection
    - Diff generation for text files
    - Snapshot management for comparison

    Usage:
        watcher = InotifyWatcher(event_queue, ["/etc/ssh/sshd_config"])
        await watcher.start()  # Blocks until stopped
        await watcher.stop()
    """

    def __init__(
        self,
        event_queue: asyncio.Queue[TelemetryEvent],
        watch_paths: list[str] | None = None,
        generate_diffs: bool = True,
    ) -> None:
        """Initialize the inotify watcher.

        Args:
            event_queue: Queue to push normalized events to.
            watch_paths: Paths to watch (uses defaults if None).
            generate_diffs: Whether to generate diffs for text files.
        """
        self._queue = event_queue
        self._watch_paths = watch_paths or DEFAULT_WATCH_PATHS
        self._generate_diffs = generate_diffs
        self._running = False

        # Store file snapshots for diff generation
        # path -> {"content": str, "hash": str, "mtime": float}
        self._snapshots: dict[str, dict[str, Any]] = {}

        # Watch descriptors (for inotify)
        self._watches: dict[int, str] = {}

        # Stats
        self._stats = {
            "events_received": 0,
            "events_published": 0,
            "errors": 0,
        }

        # Inotify file descriptor
        self._inotify_fd: int | None = None

    async def start(self) -> None:
        """Start watching files for changes.

        Blocks until stop() is called.
        """
        if self._running:
            logger.warning("InotifyWatcher already running")
            return

        self._running = True
        logger.info(f"Starting InotifyWatcher for {len(self._watch_paths)} paths")

        # Try native inotify first, fallback to polling
        try:
            await self._run_inotify_loop()
        except (ImportError, OSError) as e:
            logger.warning(f"Native inotify not available ({e}), using polling fallback")
            await self._run_polling_loop()
        except asyncio.CancelledError:
            logger.info("InotifyWatcher cancelled")
        finally:
            self._running = False
            await self._cleanup()

    async def stop(self) -> None:
        """Stop the inotify watcher."""
        self._running = False
        logger.info("InotifyWatcher stopped")

    async def _run_inotify_loop(self) -> None:
        """Main event loop using native inotify."""
        try:
            import inotify_simple
            from inotify_simple import flags as inotify_flags
        except ImportError:
            raise ImportError("inotify_simple package not available")

        # Create inotify instance
        inotify = inotify_simple.INotify()
        self._inotify_fd = inotify.fd

        # Set up watches
        watch_flags = (
            inotify_flags.MODIFY |
            inotify_flags.CREATE |
            inotify_flags.DELETE |
            inotify_flags.ATTRIB |
            inotify_flags.CLOSE_WRITE
        )

        for path_pattern in self._watch_paths:
            # Expand glob patterns
            paths = self._expand_path_pattern(path_pattern)
            for path in paths:
                if Path(path).exists():
                    try:
                        # Watch the directory, not the file
                        watch_dir = str(Path(path).parent)
                        wd = inotify.add_watch(watch_dir, watch_flags)
                        self._watches[wd] = watch_dir
                        logger.debug(f"Watching: {watch_dir}")

                        # Take initial snapshot
                        await self._take_snapshot(path)
                    except Exception as e:
                        logger.warning(f"Failed to watch {path}: {e}")
                else:
                    logger.debug(f"Path does not exist: {path}")

        # Event loop
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # Non-blocking read with timeout
                events = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: inotify.read(timeout=1000)),
                    timeout=2.0,
                )

                for event in events:
                    await self._process_inotify_event(event, inotify_flags)

            except TimeoutError:
                continue
            except Exception as e:
                logger.exception(f"Inotify read error: {e}")
                self._stats["errors"] += 1

        # Cleanup
        inotify.close()

    async def _run_polling_loop(self) -> None:
        """Fallback polling-based file monitoring."""
        # Expand all paths and take initial snapshots
        watched_files: list[str] = []
        for pattern in self._watch_paths:
            paths = self._expand_path_pattern(pattern)
            watched_files.extend(paths)

        for path in watched_files:
            if Path(path).exists():
                await self._take_snapshot(path)

        # Poll for changes
        while self._running:
            for path in watched_files:
                try:
                    await self._check_file_change(path)
                except Exception as e:
                    logger.debug(f"Error checking {path}: {e}")

            await asyncio.sleep(5)  # Poll every 5 seconds

    async def _process_inotify_event(self, event: Any, flags: Any) -> None:
        """Process a single inotify event.

        Args:
            event: inotify event object.
            flags: inotify flags module.
        """
        self._stats["events_received"] += 1

        watch_dir = self._watches.get(event.wd)
        if not watch_dir:
            return

        # Build full path
        filename = event.name
        if filename:
            full_path = str(Path(watch_dir) / filename)
        else:
            full_path = watch_dir

        # Check if this is a watched path
        is_watched = any(
            self._path_matches_pattern(full_path, pattern)
            for pattern in self._watch_paths
        )
        if not is_watched:
            return

        # Determine event type
        event_type = "unknown"
        if event.mask & flags.MODIFY or event.mask & flags.CLOSE_WRITE:
            event_type = "modify"
        elif event.mask & flags.CREATE:
            event_type = "create"
        elif event.mask & flags.DELETE:
            event_type = "delete"
        elif event.mask & flags.ATTRIB:
            event_type = "attrib"

        if event_type == "unknown":
            return

        await self._handle_file_event(full_path, event_type)

    async def _check_file_change(self, path: str) -> None:
        """Check if a file has changed (for polling mode).

        Args:
            path: File path to check.
        """
        p = Path(path)

        if not p.exists():
            # File deleted?
            if path in self._snapshots:
                await self._handle_file_event(path, "delete")
                del self._snapshots[path]
            return

        # Get current state
        try:
            stat = p.stat()
            current_mtime = stat.st_mtime

            snapshot = self._snapshots.get(path)
            if not snapshot:
                # New file
                await self._take_snapshot(path)
                await self._handle_file_event(path, "create")
            elif snapshot.get("mtime") != current_mtime:
                # File modified
                await self._handle_file_event(path, "modify")
                await self._take_snapshot(path)

        except Exception as e:
            logger.debug(f"Error checking file {path}: {e}")

    async def _handle_file_event(self, path: str, event_type: str) -> None:
        """Handle a file change event.

        Args:
            path: Path to the changed file.
            event_type: Type of change (modify, create, delete, attrib).
        """
        # Get file info
        filename = Path(path).name

        # Generate diff for modify events
        diff_preview = ""
        if event_type == "modify" and self._generate_diffs:
            diff_preview = await self._generate_diff(path)

        # Determine category
        category = "auth" if "authorized_keys" in path or "ssh" in path else "fs"
        if "passwd" in path or "shadow" in path or "sudoers" in path:
            category = "auth"

        # Determine severity
        severity = "warning"
        if category == "auth":
            severity = "error" if event_type in ("modify", "create") else "warning"

        # Build message
        if event_type == "modify":
            message = f"File modified: {path}"
        elif event_type == "create":
            message = f"File created: {path}"
        elif event_type == "delete":
            message = f"File deleted: {path}"
        elif event_type == "attrib":
            message = f"File permissions changed: {path}"
        else:
            message = f"File event ({event_type}): {path}"

        # Build raw event
        raw: dict[str, Any] = {
            "_SOURCE": "inotify",
            "_INOTIFY_PATH": path,
            "_INOTIFY_EVENT": event_type,
            "MESSAGE": message,
            "PRIORITY": "3" if severity == "error" else "4",
        }

        if diff_preview:
            raw["diff_preview"] = diff_preview[:2000]  # Truncate

        # Create telemetry event
        event = TelemetryEvent(
            ts=datetime.now(UTC),
            source="probe",
            severity=severity,
            category=category,
            message=message,
            entity=f"file:{filename}",
            fingerprint=TelemetryEvent.compute_fingerprint(
                category, f"file:{path}", f"{event_type}:{path}"
            ),
            raw=raw,
        )

        # Publish
        await self._publish_event(event)

        # Update snapshot after modify
        if event_type == "modify":
            await self._take_snapshot(path)

    async def _take_snapshot(self, path: str) -> None:
        """Take a snapshot of a file.

        Args:
            path: File path.
        """
        try:
            p = Path(path)
            if not p.exists():
                return

            stat = p.stat()

            # Read content for text files
            content = ""
            file_hash = ""
            try:
                if stat.st_size < 1024 * 1024:  # Only for files < 1MB
                    content = p.read_text()
                    file_hash = hashlib.sha256(content.encode()).hexdigest()
            except (UnicodeDecodeError, PermissionError):
                # Binary file or no permission
                pass

            self._snapshots[path] = {
                "content": content,
                "hash": file_hash,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }

        except Exception as e:
            logger.debug(f"Failed to snapshot {path}: {e}")

    async def _generate_diff(self, path: str) -> str:
        """Generate a diff for a modified file.

        Args:
            path: File path.

        Returns:
            Unified diff string.
        """
        try:
            p = Path(path)
            if not p.exists():
                return ""

            snapshot = self._snapshots.get(path)
            if not snapshot or not snapshot.get("content"):
                return ""

            old_content = snapshot["content"]
            new_content = p.read_text()

            # Generate diff
            old_lines = old_content.splitlines(keepends=True)
            new_lines = new_content.splitlines(keepends=True)

            diff = difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{p.name}",
                tofile=f"b/{p.name}",
                n=2,
            )
            return "".join(diff)

        except Exception as e:
            logger.debug(f"Failed to generate diff for {path}: {e}")
            return ""

    async def _publish_event(self, event: TelemetryEvent) -> None:
        """Publish an event to the queue.

        Args:
            event: TelemetryEvent to publish.
        """
        try:
            self._queue.put_nowait(event)
            self._stats["events_published"] += 1
        except asyncio.QueueFull:
            logger.warning("Inotify events queue full, dropping event")
            self._stats["errors"] += 1

    def _expand_path_pattern(self, pattern: str) -> list[str]:
        """Expand a path pattern with globs.

        Args:
            pattern: Path pattern (may contain * wildcards).

        Returns:
            List of matching paths.
        """
        if "*" not in pattern:
            return [pattern]

        try:
            # Handle patterns like /home/*/.ssh/authorized_keys
            import glob
            paths = glob.glob(pattern)
            return paths
        except Exception:
            return [pattern]

    def _path_matches_pattern(self, path: str, pattern: str) -> bool:
        """Check if a path matches a pattern.

        Args:
            path: Actual path.
            pattern: Pattern to match.

        Returns:
            True if path matches pattern.
        """
        if "*" not in pattern:
            return path == pattern

        # Simple glob matching
        import fnmatch
        return fnmatch.fnmatch(path, pattern)

    async def _cleanup(self) -> None:
        """Clean up resources."""
        pass

    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._running

    @property
    def stats(self) -> dict[str, int]:
        """Get watcher statistics."""
        return self._stats.copy()

    def get_snapshot(self, path: str) -> dict[str, Any] | None:
        """Get the current snapshot for a path.

        Args:
            path: File path.

        Returns:
            Snapshot dict or None.
        """
        return self._snapshots.get(path)

    def add_watch_path(self, path: str) -> None:
        """Add a path to watch.

        Note: Takes effect after restart for inotify mode.

        Args:
            path: Path to watch.
        """
        if path not in self._watch_paths:
            self._watch_paths.append(path)

    def remove_watch_path(self, path: str) -> None:
        """Remove a path from watching.

        Note: Takes effect after restart for inotify mode.

        Args:
            path: Path to stop watching.
        """
        if path in self._watch_paths:
            self._watch_paths.remove(path)
