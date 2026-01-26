"""Syscall tracing via eBPF for command explanation.

Provides on-demand syscall tracing for specific PIDs to explain
what a command actually did at the syscall level.

Traces:
- openat: File opens (read/write/create)
- write: File writes
- unlinkat: File deletes
- renameat2: File renames
- connect: Network connections
- execve: Program executions
- clone: Process spawning

Events are aggregated into a SyscallSummary for human-readable explanation.
"""

import ctypes
import logging
from collections.abc import Callable

from elle.daemon.telemetry.ebpf.bcc.base import BCCProgram, _bytes_to_string
from elle.daemon.telemetry.ebpf.models import EBPFEventType, EBPFRawEvent
from elle.daemon.telemetry.ebpf.syscall_models import SyscallEvent

logger = logging.getLogger(__name__)


# Event types for syscall tracing
SYSCALL_TYPE_OPENAT = 0
SYSCALL_TYPE_WRITE = 1
SYSCALL_TYPE_UNLINKAT = 2
SYSCALL_TYPE_RENAMEAT = 3
SYSCALL_TYPE_CONNECT = 4
SYSCALL_TYPE_EXECVE = 5
SYSCALL_TYPE_CLONE = 6


# C struct for syscall events from ring buffer
class SyscallEventStruct(ctypes.Structure):
    """C struct for syscall events from ring buffer."""

    _fields_ = [
        ("ts_ns", ctypes.c_uint64),
        ("pid", ctypes.c_uint32),
        ("tid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("syscall_type", ctypes.c_uint8),
        ("comm", ctypes.c_char * 16),
        ("path", ctypes.c_char * 256),
        ("path2", ctypes.c_char * 256),  # For rename (new path)
        ("flags", ctypes.c_int32),
        ("ret", ctypes.c_int64),
        # Network fields
        ("addr_v4", ctypes.c_uint32),
        ("port", ctypes.c_uint16),
        ("family", ctypes.c_uint16),
    ]


class SyscallProgram(BCCProgram):
    """eBPF program for on-demand syscall tracing.

    Traces syscalls for specific PIDs to explain command behavior.
    Uses kprobes on sys_enter and kretprobes on sys_exit to capture
    both arguments and return values.
    """

    name = "syscall"
    category = "syscall"
    hook_type = "tracepoint"
    hook_name = "raw_syscalls:sys_exit"

    def __init__(self) -> None:
        """Initialize syscall program."""
        super().__init__()
        self._traced_pids: set[int] = set()
        self._syscall_callback: Callable[[SyscallEvent], None] | None = None

    def _get_ring_buffer_name(self) -> str:
        """Get ring buffer map name."""
        return "events"

    def set_syscall_callback(self, callback: Callable[[SyscallEvent], None] | None) -> None:
        """Set callback for syscall events.

        Args:
            callback: Function to call for each syscall event.
        """
        self._syscall_callback = callback

    def add_traced_pid(self, pid: int) -> bool:
        """Add a PID to the trace list.

        Args:
            pid: Process ID to trace.

        Returns:
            True if added successfully.
        """
        if not self._loaded:
            logger.warning("Cannot add PID: program not loaded")
            return False

        try:
            # Add to BPF map
            traced_pids = self._bpf["traced_pids"]
            traced_pids[ctypes.c_uint32(pid)] = ctypes.c_uint8(1)
            self._traced_pids.add(pid)
            logger.debug(f"Added PID {pid} to syscall trace")
            return True
        except Exception as e:
            logger.error(f"Failed to add PID {pid}: {e}")
            return False

    def remove_traced_pid(self, pid: int) -> bool:
        """Remove a PID from the trace list.

        Args:
            pid: Process ID to stop tracing.

        Returns:
            True if removed successfully.
        """
        if not self._loaded:
            return False

        try:
            traced_pids = self._bpf["traced_pids"]
            del traced_pids[ctypes.c_uint32(pid)]
            self._traced_pids.discard(pid)
            logger.debug(f"Removed PID {pid} from syscall trace")
            return True
        except Exception as e:
            logger.debug(f"Failed to remove PID {pid}: {e}")
            return False

    def clear_traced_pids(self) -> None:
        """Remove all PIDs from the trace list."""
        for pid in list(self._traced_pids):
            self.remove_traced_pid(pid)

    @property
    def traced_pids(self) -> set[int]:
        """Get set of currently traced PIDs."""
        return self._traced_pids.copy()

    def _get_bpf_program(self) -> str:
        """Return the BPF C program for syscall tracing.

        Returns:
            BPF C program source.
        """
        return """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>
#include <linux/socket.h>
#include <linux/in.h>
#include <linux/in6.h>

// Ring buffer for events (64KB)
BPF_RINGBUF_OUTPUT(events, 1 << 16);

// Map of PIDs to trace (max 64 concurrent traces)
BPF_HASH(traced_pids, u32, u8, 64);

// Syscall types
#define SYSCALL_OPENAT   0
#define SYSCALL_WRITE    1
#define SYSCALL_UNLINKAT 2
#define SYSCALL_RENAMEAT 3
#define SYSCALL_CONNECT  4
#define SYSCALL_EXECVE   5
#define SYSCALL_CLONE    6

// Event structure
struct syscall_event_t {
    u64 ts_ns;
    u32 pid;
    u32 tid;
    u32 uid;
    u8 syscall_type;
    char comm[16];
    char path[256];
    char path2[256];  // For rename
    s32 flags;
    s64 ret;
    // Network fields
    u32 addr_v4;
    u16 port;
    u16 family;
};

// Temporary storage for syscall args (per-thread)
struct syscall_args_t {
    u64 args[6];
    u8 syscall_type;
};
BPF_HASH(pending_syscalls, u64, struct syscall_args_t, 4096);

// Store syscall entry args
static inline void store_args(u8 syscall_type, struct pt_regs *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;

    // Check if this PID is being traced
    u8 *traced = traced_pids.lookup(&pid);
    if (!traced) {
        return;
    }

    struct syscall_args_t args = {};
    args.syscall_type = syscall_type;
    args.args[0] = PT_REGS_PARM1(ctx);
    args.args[1] = PT_REGS_PARM2(ctx);
    args.args[2] = PT_REGS_PARM3(ctx);
    args.args[3] = PT_REGS_PARM4(ctx);
    args.args[4] = PT_REGS_PARM5(ctx);
    args.args[5] = PT_REGS_PARM6(ctx);

    pending_syscalls.update(&pid_tgid, &args);
}

// Emit event on syscall exit
static inline void emit_event(struct pt_regs *ctx, u8 syscall_type) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = pid_tgid & 0xFFFFFFFF;

    // Look up stored args
    struct syscall_args_t *args = pending_syscalls.lookup(&pid_tgid);
    if (!args) {
        return;
    }

    // Reserve space in ring buffer
    struct syscall_event_t *e = events.ringbuf_reserve(sizeof(struct syscall_event_t));
    if (!e) {
        pending_syscalls.delete(&pid_tgid);
        return;
    }

    // Fill common fields
    e->ts_ns = bpf_ktime_get_ns();
    e->pid = pid;
    e->tid = tid;
    e->uid = bpf_get_current_uid_gid() & 0xFFFFFFFF;
    e->syscall_type = syscall_type;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    e->ret = PT_REGS_RC(ctx);

    // Zero out optional fields
    __builtin_memset(e->path, 0, sizeof(e->path));
    __builtin_memset(e->path2, 0, sizeof(e->path2));
    e->flags = 0;
    e->addr_v4 = 0;
    e->port = 0;
    e->family = 0;

    // Syscall-specific handling
    switch (syscall_type) {
        case SYSCALL_OPENAT: {
            // args[1] = pathname, args[2] = flags
            bpf_probe_read_user_str(e->path, sizeof(e->path), (void *)args->args[1]);
            e->flags = args->args[2];
            break;
        }
        case SYSCALL_WRITE: {
            // args[0] = fd (not much we can do without fd->path mapping)
            break;
        }
        case SYSCALL_UNLINKAT: {
            // args[1] = pathname
            bpf_probe_read_user_str(e->path, sizeof(e->path), (void *)args->args[1]);
            break;
        }
        case SYSCALL_RENAMEAT: {
            // args[1] = oldname, args[3] = newname
            bpf_probe_read_user_str(e->path, sizeof(e->path), (void *)args->args[1]);
            bpf_probe_read_user_str(e->path2, sizeof(e->path2), (void *)args->args[3]);
            break;
        }
        case SYSCALL_CONNECT: {
            // args[1] = sockaddr
            struct sockaddr_in sa = {};
            bpf_probe_read_user(&sa, sizeof(sa), (void *)args->args[1]);
            e->family = sa.sin_family;
            if (sa.sin_family == AF_INET) {
                e->addr_v4 = sa.sin_addr.s_addr;
                e->port = __builtin_bswap16(sa.sin_port);
            }
            break;
        }
        case SYSCALL_EXECVE: {
            // args[0] = pathname
            bpf_probe_read_user_str(e->path, sizeof(e->path), (void *)args->args[0]);
            break;
        }
        case SYSCALL_CLONE: {
            // Clone flags in args[0]
            e->flags = args->args[0];
            break;
        }
    }

    events.ringbuf_submit(e, 0);
    pending_syscalls.delete(&pid_tgid);
}

// Kprobe handlers for syscall entry
int kprobe__sys_openat(struct pt_regs *ctx) {
    store_args(SYSCALL_OPENAT, ctx);
    return 0;
}

int kretprobe__sys_openat(struct pt_regs *ctx) {
    emit_event(ctx, SYSCALL_OPENAT);
    return 0;
}

int kprobe__sys_unlinkat(struct pt_regs *ctx) {
    store_args(SYSCALL_UNLINKAT, ctx);
    return 0;
}

int kretprobe__sys_unlinkat(struct pt_regs *ctx) {
    emit_event(ctx, SYSCALL_UNLINKAT);
    return 0;
}

int kprobe__sys_renameat2(struct pt_regs *ctx) {
    store_args(SYSCALL_RENAMEAT, ctx);
    return 0;
}

int kretprobe__sys_renameat2(struct pt_regs *ctx) {
    emit_event(ctx, SYSCALL_RENAMEAT);
    return 0;
}

int kprobe__sys_connect(struct pt_regs *ctx) {
    store_args(SYSCALL_CONNECT, ctx);
    return 0;
}

int kretprobe__sys_connect(struct pt_regs *ctx) {
    emit_event(ctx, SYSCALL_CONNECT);
    return 0;
}

int kprobe__sys_execve(struct pt_regs *ctx) {
    store_args(SYSCALL_EXECVE, ctx);
    return 0;
}

int kretprobe__sys_execve(struct pt_regs *ctx) {
    emit_event(ctx, SYSCALL_EXECVE);
    return 0;
}

int kprobe__sys_clone(struct pt_regs *ctx) {
    store_args(SYSCALL_CLONE, ctx);
    return 0;
}

int kretprobe__sys_clone(struct pt_regs *ctx) {
    emit_event(ctx, SYSCALL_CLONE);
    return 0;
}
"""

    def _parse_event(self, cpu: int, data: ctypes.Structure, size: int) -> EBPFRawEvent | None:
        """Parse syscall event from ring buffer.

        Args:
            cpu: CPU where event occurred.
            data: Raw event data.
            size: Data size.

        Returns:
            Parsed EBPFRawEvent.
        """
        try:
            event = ctypes.cast(data, ctypes.POINTER(SyscallEventStruct)).contents

            # Convert strings
            comm = _bytes_to_string(event.comm)
            path = _bytes_to_string(event.path)
            path2 = _bytes_to_string(event.path2)

            # Map syscall type to event type
            syscall_type_map = {
                SYSCALL_TYPE_OPENAT: EBPFEventType.SYSCALL_OPENAT,
                SYSCALL_TYPE_WRITE: EBPFEventType.SYSCALL_WRITE,
                SYSCALL_TYPE_UNLINKAT: EBPFEventType.SYSCALL_UNLINKAT,
                SYSCALL_TYPE_RENAMEAT: EBPFEventType.SYSCALL_RENAMEAT,
                SYSCALL_TYPE_CONNECT: EBPFEventType.SYSCALL_CONNECT,
                SYSCALL_TYPE_EXECVE: EBPFEventType.SYSCALL_EXECVE,
                SYSCALL_TYPE_CLONE: EBPFEventType.SYSCALL_CLONE,
            }

            syscall_name_map = {
                SYSCALL_TYPE_OPENAT: "openat",
                SYSCALL_TYPE_WRITE: "write",
                SYSCALL_TYPE_UNLINKAT: "unlinkat",
                SYSCALL_TYPE_RENAMEAT: "renameat2",
                SYSCALL_TYPE_CONNECT: "connect",
                SYSCALL_TYPE_EXECVE: "execve",
                SYSCALL_TYPE_CLONE: "clone",
            }

            event_type = syscall_type_map.get(event.syscall_type, EBPFEventType.SYSCALL_GENERIC)
            syscall_name = syscall_name_map.get(event.syscall_type, "unknown")

            # Build data dict
            data_dict = {
                "syscall": syscall_name,
                "ret": event.ret,
                "flags": event.flags,
            }

            if path:
                data_dict["path"] = path
            if path2:
                data_dict["path2"] = path2
            if event.addr_v4:
                # Convert to dotted quad
                addr_bytes = event.addr_v4.to_bytes(4, "little")
                data_dict["addr"] = ".".join(str(b) for b in addr_bytes)
                data_dict["port"] = event.port
                data_dict["family"] = "ipv4" if event.family == 2 else "other"

            raw_event = EBPFRawEvent(
                ts_ns=event.ts_ns,
                cpu=cpu,
                event_type=event_type,
                pid=event.pid,
                tgid=event.tid,
                uid=event.uid,
                comm=comm,
                data=data_dict,
            )

            # Also call syscall-specific callback if set
            if self._syscall_callback:
                from elle.daemon.telemetry.ebpf.syscall_models import (
                    SYSCALL_CATEGORIES,
                    SyscallEvent,
                )

                category = SYSCALL_CATEGORIES.get(syscall_name, "other")

                # Determine category based on syscall and flags
                if syscall_name == "openat":
                    # Check O_CREAT flag (0x40 on Linux)
                    if event.flags & 0x40:
                        category = "file_create"
                    # Check O_WRONLY or O_RDWR flags
                    elif event.flags & 0x03:
                        category = "file_write"
                    else:
                        category = "file_read"

                syscall_event = SyscallEvent(
                    ts_ns=event.ts_ns,
                    syscall=syscall_name,
                    category=category,
                    pid=event.pid,
                    comm=comm,
                    path=path if path else None,
                    flags=event.flags if event.flags else None,
                    addr=data_dict.get("addr"),
                    port=event.port if event.port else None,
                    ret=event.ret,
                )
                self._syscall_callback(syscall_event)

            return raw_event

        except Exception as e:
            logger.debug(f"Failed to parse syscall event: {e}")
            return None

    def attach(self) -> bool:
        """Attach kprobes and kretprobes for syscall tracing.

        Returns:
            True if attached successfully.
        """
        if not self._loaded:
            logger.error("Cannot attach syscall program: not loaded")
            return False

        if self._attached:
            return True

        try:
            # BCC auto-attaches kprobes/kretprobes based on function naming
            self._attached = True
            logger.info("Attached syscall tracing program")
            return True

        except Exception as e:
            logger.error(f"Failed to attach syscall program: {e}")
            self._errors_count += 1
            return False


def create_syscall_program() -> SyscallProgram:
    """Factory function to create a syscall tracing program.

    Returns:
        Configured SyscallProgram instance.
    """
    return SyscallProgram()
