"""Block I/O error detection via eBPF.

Attaches to the block:block_rq_complete tracepoint to detect
disk I/O errors when requests complete with error status.

Events produced:
- block_rq_error: A block I/O request completed with an error
"""

import ctypes
import logging

from elle.daemon.telemetry.ebpf.bcc.base import BCCProgram, _bytes_to_string
from elle.daemon.telemetry.ebpf.models import EBPFEventType, EBPFRawEvent

logger = logging.getLogger(__name__)


# Event structure matching the BPF program
class BlockIOEvent(ctypes.Structure):
    """C struct for block I/O events from ring buffer."""

    _fields_ = [
        ("ts_ns", ctypes.c_uint64),  # Kernel timestamp
        ("dev", ctypes.c_uint32),  # Device (major << 20 | minor)
        ("sector", ctypes.c_uint64),  # Sector number
        ("nr_sector", ctypes.c_uint32),  # Number of sectors
        ("error", ctypes.c_int32),  # Error code (negative on error)
        ("rwbs", ctypes.c_char * 8),  # R/W/S flags
    ]


class BlockIOProgram(BCCProgram):
    """eBPF program for block I/O error detection.

    Traces the block:block_rq_complete tracepoint which fires when
    a block I/O request completes. We filter for errors only.
    """

    name = "block_io"
    category = "disk"
    hook_type = "tracepoint"
    hook_name = "block:block_rq_complete"

    def _get_ring_buffer_name(self) -> str:
        """Get ring buffer map name."""
        return "events"

    def _get_bpf_program(self) -> str:
        """Return the BPF C program for block I/O detection.

        Returns:
            BPF C program source.
        """
        return """
#include <uapi/linux/ptrace.h>

// Ring buffer for events
BPF_RINGBUF_OUTPUT(events, 1 << 12);

// Event structure
struct block_io_event_t {
    u64 ts_ns;
    u32 dev;
    u64 sector;
    u32 nr_sector;
    s32 error;
    char rwbs[8];
};

// Tracepoint: block:block_rq_complete
// Fires when a block I/O request completes
TRACEPOINT_PROBE(block, block_rq_complete) {
    // Only emit events for errors (error != 0)
    if (args->error == 0) {
        return 0;
    }

    struct block_io_event_t *e = events.ringbuf_reserve(sizeof(struct block_io_event_t));
    if (!e) {
        return 0;
    }

    e->ts_ns = bpf_ktime_get_ns();
    e->dev = args->dev;
    e->sector = args->sector;
    e->nr_sector = args->nr_sector;
    e->error = args->error;

    // Copy rwbs flags
    bpf_probe_read_str(&e->rwbs, sizeof(e->rwbs), args->rwbs);

    events.ringbuf_submit(e, 0);
    return 0;
}
"""

    def _parse_event(self, cpu: int, data: ctypes.Structure, size: int) -> EBPFRawEvent | None:
        """Parse block I/O event from ring buffer.

        Args:
            cpu: CPU where event occurred.
            data: Raw event data.
            size: Data size.

        Returns:
            Parsed EBPFRawEvent.
        """
        try:
            event = ctypes.cast(data, ctypes.POINTER(BlockIOEvent)).contents

            # Decode device major/minor
            dev = event.dev
            major = (dev >> 20) & 0xFFF
            minor = dev & 0xFFFFF

            # Convert rwbs to string
            rwbs = _bytes_to_string(event.rwbs)

            return EBPFRawEvent(
                ts_ns=event.ts_ns,
                cpu=cpu,
                event_type=EBPFEventType.BLOCK_RQ_ERROR,
                pid=None,  # Block I/O doesn't have process context
                tgid=None,
                uid=None,
                comm=None,
                data={
                    "dev": dev,
                    "major": major,
                    "minor": minor,
                    "sector": event.sector,
                    "nr_sector": event.nr_sector,
                    "error": event.error,
                    "rwbs": rwbs,
                },
            )

        except Exception as e:
            logger.debug(f"Failed to parse block I/O event: {e}")
            return None

    def attach(self) -> bool:
        """Attach to block:block_rq_complete tracepoint.

        Returns:
            True if attached successfully.
        """
        if not self._loaded:
            logger.error("Cannot attach block I/O program: not loaded")
            return False

        if self._attached:
            return True

        try:
            # BCC's TRACEPOINT_PROBE macro auto-attaches
            self._attached = True
            logger.info(f"Attached block I/O program to {self.hook_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach block I/O program: {e}")
            self._errors_count += 1
            return False


def create_block_io_program() -> BlockIOProgram:
    """Factory function to create a block I/O program.

    Returns:
        Configured BlockIOProgram instance.
    """
    return BlockIOProgram()
