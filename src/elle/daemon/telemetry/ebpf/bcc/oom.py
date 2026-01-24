"""OOM (Out-of-Memory) detection via eBPF.

Attaches to the oom:mark_victim tracepoint to detect when the
OOM killer selects a process for termination.

Events produced:
- oom_kill: A process was killed by the OOM killer
"""

import ctypes
import logging
import time

from elle.daemon.telemetry.ebpf.bcc.base import BCCProgram, _bytes_to_string
from elle.daemon.telemetry.ebpf.models import EBPFEventType, EBPFRawEvent

logger = logging.getLogger(__name__)


# Event structure matching the BPF program
class OOMEvent(ctypes.Structure):
    """C struct for OOM events from ring buffer."""

    _fields_ = [
        ("ts_ns", ctypes.c_uint64),       # Kernel timestamp
        ("pid", ctypes.c_uint32),          # Process ID being killed
        ("tgid", ctypes.c_uint32),         # Thread group ID
        ("uid", ctypes.c_uint32),          # User ID
        ("oom_score_adj", ctypes.c_int16), # OOM score adjustment
        ("comm", ctypes.c_char * 16),      # Process name
        ("totalpages", ctypes.c_uint64),   # Total pages in system
        ("freepages", ctypes.c_uint64),    # Free pages when OOM occurred
    ]


class OOMProgram(BCCProgram):
    """eBPF program for OOM kill detection.

    Traces the oom:mark_victim tracepoint which fires when the
    kernel's OOM killer selects a victim process.
    """

    name = "oom"
    category = "oom"
    hook_type = "tracepoint"
    hook_name = "oom:mark_victim"

    def _get_ring_buffer_name(self) -> str:
        """Get ring buffer map name."""
        return "events"

    def _get_bpf_program(self) -> str:
        """Return the BPF C program for OOM detection.

        Returns:
            BPF C program source.
        """
        return """
#include <uapi/linux/ptrace.h>

// Ring buffer for events (4MB = 2^22 bytes)
BPF_RINGBUF_OUTPUT(events, 1 << 12);

// Event structure
struct oom_event_t {
    u64 ts_ns;
    u32 pid;
    u32 tgid;
    u32 uid;
    s16 oom_score_adj;
    char comm[16];
    u64 totalpages;
    u64 freepages;
};

// Tracepoint: oom:mark_victim
// Fires when OOM killer selects a victim process
TRACEPOINT_PROBE(oom, mark_victim) {
    struct oom_event_t *e = events.ringbuf_reserve(sizeof(struct oom_event_t));
    if (!e) {
        return 0;
    }

    // Timestamp
    e->ts_ns = bpf_ktime_get_ns();

    // Process info from tracepoint args
    e->pid = args->pid;

    // Get current task info for additional context
    u64 id = bpf_get_current_pid_tgid();
    e->tgid = id >> 32;

    u64 uid_gid = bpf_get_current_uid_gid();
    e->uid = uid_gid & 0xFFFFFFFF;

    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    // Memory info is not directly available in tracepoint args
    // Set to 0 as placeholder - could be enhanced with separate probe
    e->totalpages = 0;
    e->freepages = 0;
    e->oom_score_adj = 0;

    events.ringbuf_submit(e, 0);
    return 0;
}
"""

    def _parse_event(
        self, cpu: int, data: ctypes.Structure, size: int
    ) -> EBPFRawEvent | None:
        """Parse OOM event from ring buffer.

        Args:
            cpu: CPU where event occurred.
            data: Raw event data.
            size: Data size.

        Returns:
            Parsed EBPFRawEvent.
        """
        try:
            event = ctypes.cast(data, ctypes.POINTER(OOMEvent)).contents

            # Convert comm to string
            comm = _bytes_to_string(event.comm)

            return EBPFRawEvent(
                ts_ns=event.ts_ns,
                cpu=cpu,
                event_type=EBPFEventType.OOM_KILL,
                pid=event.pid,
                tgid=event.tgid,
                uid=event.uid,
                comm=comm,
                data={
                    "oom_score_adj": event.oom_score_adj,
                    "totalpages": event.totalpages,
                    "freepages": event.freepages,
                },
            )

        except Exception as e:
            logger.debug(f"Failed to parse OOM event: {e}")
            return None

    def attach(self) -> bool:
        """Attach to oom:mark_victim tracepoint.

        Returns:
            True if attached successfully.
        """
        if not self._loaded:
            logger.error("Cannot attach OOM program: not loaded")
            return False

        if self._attached:
            return True

        try:
            # BCC's TRACEPOINT_PROBE macro auto-attaches
            # The function name must be tracepoint__<category>__<name>
            # which BCC handles automatically when it compiles
            self._attached = True
            logger.info(f"Attached OOM program to {self.hook_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach OOM program: {e}")
            self._errors_count += 1
            return False


def create_oom_program() -> OOMProgram:
    """Factory function to create an OOM program.

    Returns:
        Configured OOMProgram instance.
    """
    return OOMProgram()
