"""Base class for BCC eBPF programs.

Provides common infrastructure for loading, attaching, and
polling eBPF programs via BCC.
"""

import ctypes
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from elle.daemon.telemetry.ebpf.models import EBPFProgramStatus, EBPFRawEvent

logger = logging.getLogger(__name__)


class BCCProgram(ABC):
    """Abstract base class for BCC eBPF programs.

    Subclasses must implement:
    - _get_bpf_program(): Return the BPF C program text
    - _parse_event(): Parse raw event data from ring buffer
    """

    # Program metadata (override in subclasses)
    name: str = "base"
    category: str = "other"
    hook_type: str = "tracepoint"  # tracepoint, kprobe, kretprobe
    hook_name: str = ""  # e.g., "oom:mark_victim"

    def __init__(self) -> None:
        """Initialize the BCC program."""
        self._bpf: Any = None  # BCC BPF object
        self._loaded = False
        self._attached = False
        self._events_count = 0
        self._errors_count = 0
        self._last_event: datetime | None = None
        self._event_callback: Callable[[EBPFRawEvent], None] | None = None

    @property
    def loaded(self) -> bool:
        """Check if program is loaded."""
        return self._loaded

    @property
    def attached(self) -> bool:
        """Check if program is attached."""
        return self._attached

    @property
    def events_count(self) -> int:
        """Get total events produced."""
        return self._events_count

    @property
    def errors_count(self) -> int:
        """Get total errors encountered."""
        return self._errors_count

    def get_status(self) -> EBPFProgramStatus:
        """Get current program status.

        Returns:
            EBPFProgramStatus object.
        """
        return EBPFProgramStatus(
            name=self.name,
            category=self.category,
            loaded=self._loaded,
            attached=self._attached,
            hook_type=self.hook_type,
            hook_name=self.hook_name,
            events_count=self._events_count,
            errors_count=self._errors_count,
            last_event=self._last_event,
        )

    def set_event_callback(self, callback: Callable[[EBPFRawEvent], None] | None) -> None:
        """Set callback for event handling.

        Args:
            callback: Function to call for each event.
        """
        self._event_callback = callback

    @abstractmethod
    def _get_bpf_program(self) -> str:
        """Return the BPF C program text.

        Must define:
        - Ring buffer output named 'events'
        - Event struct
        - Tracepoint or kprobe handler

        Returns:
            BPF C program source code.
        """
        pass

    @abstractmethod
    def _parse_event(self, cpu: int, data: ctypes.Structure, size: int) -> EBPFRawEvent | None:
        """Parse raw event data from ring buffer.

        Args:
            cpu: CPU core where event occurred.
            data: Raw event data structure.
            size: Size of the data.

        Returns:
            Parsed EBPFRawEvent or None if invalid.
        """
        pass

    @abstractmethod
    def _get_ring_buffer_name(self) -> str:
        """Get the name of the ring buffer in the BPF program.

        Returns:
            Ring buffer map name (e.g., 'events').
        """
        pass

    def load(self) -> bool:
        """Load BPF program into kernel.

        Returns:
            True if loaded successfully.
        """
        if self._loaded:
            return True

        try:
            from bcc import BPF

            program_text = self._get_bpf_program()
            self._bpf = BPF(text=program_text)
            self._loaded = True
            logger.info(f"Loaded eBPF program: {self.name}")
            return True

        except ImportError:
            logger.error("BCC library not available")
            self._errors_count += 1
            return False
        except Exception as e:
            logger.error(f"Failed to load eBPF program {self.name}: {e}")
            self._errors_count += 1
            return False

    def attach(self) -> bool:
        """Attach program to hook point.

        Returns:
            True if attached successfully.
        """
        if not self._loaded:
            logger.error(f"Cannot attach {self.name}: not loaded")
            return False

        if self._attached:
            return True

        try:
            if self.hook_type == "tracepoint":
                # Parse tracepoint name (e.g., "oom:mark_victim" -> "oom", "mark_victim")
                if ":" in self.hook_name:
                    category, name = self.hook_name.split(":", 1)
                    self._bpf.attach_tracepoint(
                        tp=self.hook_name,
                        fn_name=f"tracepoint__{category}__{name}",
                    )
                else:
                    logger.error(f"Invalid tracepoint format: {self.hook_name}")
                    return False

            elif self.hook_type == "kprobe":
                self._bpf.attach_kprobe(
                    event=self.hook_name,
                    fn_name=f"kprobe__{self.hook_name}",
                )

            elif self.hook_type == "kretprobe":
                self._bpf.attach_kretprobe(
                    event=self.hook_name,
                    fn_name=f"kretprobe__{self.hook_name}",
                )

            else:
                logger.error(f"Unknown hook type: {self.hook_type}")
                return False

            self._attached = True
            logger.info(f"Attached eBPF program {self.name} to {self.hook_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach eBPF program {self.name}: {e}")
            self._errors_count += 1
            return False

    def setup_ring_buffer(self) -> bool:
        """Setup ring buffer polling.

        Returns:
            True if setup successfully.
        """
        if not self._loaded:
            return False

        try:
            rb_name = self._get_ring_buffer_name()
            self._bpf[rb_name].open_ring_buffer(self._ring_buffer_callback)
            logger.debug(f"Ring buffer setup for {self.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to setup ring buffer for {self.name}: {e}")
            self._errors_count += 1
            return False

    def _ring_buffer_callback(self, cpu: int, data: ctypes.Structure, size: int) -> None:
        """Internal callback for ring buffer events.

        Args:
            cpu: CPU where event occurred.
            data: Raw event data.
            size: Data size.
        """
        try:
            event = self._parse_event(cpu, data, size)
            if event:
                self._events_count += 1
                self._last_event = datetime.now(UTC)

                if self._event_callback:
                    self._event_callback(event)

        except Exception as e:
            logger.debug(f"Failed to parse event in {self.name}: {e}")
            self._errors_count += 1

    def poll(self, timeout_ms: int = 100) -> int:
        """Poll ring buffer for events.

        Args:
            timeout_ms: Timeout in milliseconds.

        Returns:
            Number of events processed, or -1 on error.
        """
        if not self._loaded:
            return -1

        try:
            rb_name = self._get_ring_buffer_name()
            return int(self._bpf[rb_name].ring_buffer_poll(timeout=timeout_ms))
        except Exception as e:
            logger.debug(f"Poll error in {self.name}: {e}")
            self._errors_count += 1
            return -1

    def detach(self) -> None:
        """Detach program from hook point."""
        if not self._attached:
            return

        try:
            # BCC handles detachment automatically when BPF object is deleted
            self._attached = False
            logger.debug(f"Detached eBPF program: {self.name}")
        except Exception as e:
            logger.warning(f"Error detaching {self.name}: {e}")

    def unload(self) -> None:
        """Unload BPF program from kernel."""
        self.detach()

        if self._bpf:
            try:
                # Cleanup is handled by BCC
                self._bpf = None
                self._loaded = False
                logger.debug(f"Unloaded eBPF program: {self.name}")
            except Exception as e:
                logger.warning(f"Error unloading {self.name}: {e}")

    def __del__(self) -> None:
        """Cleanup on deletion."""
        self.unload()


def _bytes_to_string(data: bytes) -> str:
    """Convert null-terminated bytes to string.

    Args:
        data: Byte array (typically from C struct char[]).

    Returns:
        Decoded string.
    """
    try:
        # Find null terminator
        null_idx = data.find(b"\x00")
        if null_idx >= 0:
            data = data[:null_idx]
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""
