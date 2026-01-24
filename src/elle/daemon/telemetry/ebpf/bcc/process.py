"""Process crash detection via eBPF.

Attaches to the sched:sched_process_exit tracepoint to detect
when processes exit, particularly those killed by signals.

Events produced:
- process_exit: A process exited (with signal if killed)
- signal_deliver: A fatal signal was delivered
"""

import ctypes
import logging

from elle.daemon.telemetry.ebpf.bcc.base import BCCProgram, _bytes_to_string
from elle.daemon.telemetry.ebpf.models import EBPFEventType, EBPFRawEvent

logger = logging.getLogger(__name__)

# Fatal signals that indicate crashes
FATAL_SIGNALS = {
    4: "SIGILL",
    6: "SIGABRT",
    7: "SIGBUS",
    8: "SIGFPE",
    9: "SIGKILL",
    11: "SIGSEGV",
    15: "SIGTERM",
}


# Event structure matching the BPF program
class ProcessExitEvent(ctypes.Structure):
    """C struct for process exit events from ring buffer."""

    _fields_ = [
        ("ts_ns", ctypes.c_uint64),       # Kernel timestamp
        ("pid", ctypes.c_uint32),          # Process ID
        ("tgid", ctypes.c_uint32),         # Thread group ID
        ("uid", ctypes.c_uint32),          # User ID
        ("exit_code", ctypes.c_int32),     # Exit code (signal << 8 | code)
        ("comm", ctypes.c_char * 16),      # Process name
    ]


class ProcessProgram(BCCProgram):
    """eBPF program for process crash detection.

    Traces the sched:sched_process_exit tracepoint which fires when
    any process exits. We capture all exits but highlight signal deaths.
    """

    name = "process"
    category = "service"
    hook_type = "tracepoint"
    hook_name = "sched:sched_process_exit"

    def __init__(self, filter_signals_only: bool = True) -> None:
        """Initialize the process program.

        Args:
            filter_signals_only: If True, only emit events for signal deaths.
        """
        super().__init__()
        self._filter_signals_only = filter_signals_only

    def _get_ring_buffer_name(self) -> str:
        """Get ring buffer map name."""
        return "events"

    def _get_bpf_program(self) -> str:
        """Return the BPF C program for process exit detection.

        Returns:
            BPF C program source.
        """
        # Generate filter code based on configuration
        filter_code = ""
        if self._filter_signals_only:
            # Only emit events where signal != 0 (killed by signal)
            filter_code = """
    // Filter: only signal deaths (exit_code & 0x7F != 0)
    int sig = exit_code & 0x7F;
    if (sig == 0) {
        return 0;
    }
"""

        return f"""
#include <uapi/linux/ptrace.h>

// Ring buffer for events
BPF_RINGBUF_OUTPUT(events, 1 << 12);

// Event structure
struct process_exit_event_t {{
    u64 ts_ns;
    u32 pid;
    u32 tgid;
    u32 uid;
    s32 exit_code;
    char comm[16];
}};

// Tracepoint: sched:sched_process_exit
// Fires when a process exits
TRACEPOINT_PROBE(sched, sched_process_exit) {{
    // Get exit code from task struct
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    int exit_code = 0;
    bpf_probe_read(&exit_code, sizeof(exit_code), &task->exit_code);

{filter_code}

    struct process_exit_event_t *e = events.ringbuf_reserve(sizeof(struct process_exit_event_t));
    if (!e) {{
        return 0;
    }}

    e->ts_ns = bpf_ktime_get_ns();

    u64 id = bpf_get_current_pid_tgid();
    e->pid = id & 0xFFFFFFFF;
    e->tgid = id >> 32;

    u64 uid_gid = bpf_get_current_uid_gid();
    e->uid = uid_gid & 0xFFFFFFFF;

    e->exit_code = exit_code;

    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    events.ringbuf_submit(e, 0);
    return 0;
}}
"""

    def _parse_event(
        self, cpu: int, data: ctypes.Structure, size: int
    ) -> EBPFRawEvent | None:
        """Parse process exit event from ring buffer.

        Args:
            cpu: CPU where event occurred.
            data: Raw event data.
            size: Data size.

        Returns:
            Parsed EBPFRawEvent.
        """
        try:
            event = ctypes.cast(data, ctypes.POINTER(ProcessExitEvent)).contents

            # Convert comm to string
            comm = _bytes_to_string(event.comm)

            # Decode exit code
            # Linux exit codes: signal in lower 7 bits, exit status in upper bits
            exit_code = event.exit_code
            signal = exit_code & 0x7F
            exit_status = (exit_code >> 8) & 0xFF

            # Determine event type based on signal
            if signal > 0:
                event_type = EBPFEventType.PROCESS_EXIT
                signal_name = FATAL_SIGNALS.get(signal, f"signal_{signal}")
            else:
                event_type = EBPFEventType.PROCESS_EXIT
                signal_name = None

            return EBPFRawEvent(
                ts_ns=event.ts_ns,
                cpu=cpu,
                event_type=event_type,
                pid=event.pid,
                tgid=event.tgid,
                uid=event.uid,
                comm=comm,
                data={
                    "exit_code": exit_status,
                    "signal": signal,
                    "signal_name": signal_name,
                    "raw_exit_code": exit_code,
                },
            )

        except Exception as e:
            logger.debug(f"Failed to parse process exit event: {e}")
            return None

    def attach(self) -> bool:
        """Attach to sched:sched_process_exit tracepoint.

        Returns:
            True if attached successfully.
        """
        if not self._loaded:
            logger.error("Cannot attach process program: not loaded")
            return False

        if self._attached:
            return True

        try:
            # BCC's TRACEPOINT_PROBE macro auto-attaches
            self._attached = True
            logger.info(f"Attached process program to {self.hook_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach process program: {e}")
            self._errors_count += 1
            return False


def create_process_program(filter_signals_only: bool = True) -> ProcessProgram:
    """Factory function to create a process program.

    Args:
        filter_signals_only: If True, only emit events for signal deaths.

    Returns:
        Configured ProcessProgram instance.
    """
    return ProcessProgram(filter_signals_only=filter_signals_only)
