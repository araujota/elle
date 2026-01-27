/* SPDX-License-Identifier: GPL-2.0 OR MIT */
/**
 * elled.h - Shared event structures for elled-ebpfd
 *
 * This header defines the event structures shared between BPF programs
 * (kernel side) and userspace (elled-ebpfd). All structures must be
 * packed and use fixed-size types for stable ABI.
 *
 * Event flow:
 *   BPF Program -> Ring Buffer -> Userspace -> JSON -> Unix Socket
 */

#ifndef __ELLED_H__
#define __ELLED_H__

#ifdef __BPF__
/* BPF side uses vmlinux types */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>
#else
/* Userspace side */
#include <stdint.h>
#include <stdbool.h>
typedef uint8_t   __u8;
typedef uint16_t  __u16;
typedef uint32_t  __u32;
typedef uint64_t  __u64;
typedef int8_t    __s8;
typedef int16_t   __s16;
typedef int32_t   __s32;
typedef int64_t   __s64;
#endif

/* ============================================================================
 * Event Type Enumeration
 * ============================================================================ */

enum elled_event_type {
    /* OOM events */
    ELLED_EVENT_OOM_KILL        = 1,

    /* Process events */
    ELLED_EVENT_EXEC_ENTER      = 2,
    ELLED_EVENT_EXEC_EXIT       = 3,
    ELLED_EVENT_PROCESS_EXIT    = 4,

    /* Block I/O events */
    ELLED_EVENT_BLOCK_ISSUE     = 5,
    ELLED_EVENT_BLOCK_COMPLETE  = 6,

    /* Network events */
    ELLED_EVENT_TCP_RETRANS     = 7,
    ELLED_EVENT_NET_DROP        = 8,

    /* Thermal events */
    ELLED_EVENT_THERMAL_TRIP    = 9,

    /* Security/permission events */
    ELLED_EVENT_CAP_DENY        = 10,
    ELLED_EVENT_FILE_DENY       = 11,
    ELLED_EVENT_MOUNT_OP        = 12,
};

/* String mappings for event types (userspace only) */
#ifndef __BPF__
static const char *elled_event_type_str[] = {
    [0]                          = "unknown",
    [ELLED_EVENT_OOM_KILL]       = "oom_kill",
    [ELLED_EVENT_EXEC_ENTER]     = "exec_enter",
    [ELLED_EVENT_EXEC_EXIT]      = "exec_exit",
    [ELLED_EVENT_PROCESS_EXIT]   = "process_exit",
    [ELLED_EVENT_BLOCK_ISSUE]    = "block_rq_issue",
    [ELLED_EVENT_BLOCK_COMPLETE] = "block_rq_complete",
    [ELLED_EVENT_TCP_RETRANS]    = "net_retransmit",
    [ELLED_EVENT_NET_DROP]       = "net_drop",
    [ELLED_EVENT_THERMAL_TRIP]   = "thermal_zone_trip",
    [ELLED_EVENT_CAP_DENY]       = "cap_deny",
    [ELLED_EVENT_FILE_DENY]      = "file_deny",
    [ELLED_EVENT_MOUNT_OP]       = "mount_op",
};

/* Category mappings for event types */
static const char *elled_event_category[] = {
    [0]                          = "other",
    [ELLED_EVENT_OOM_KILL]       = "oom",
    [ELLED_EVENT_EXEC_ENTER]     = "service",
    [ELLED_EVENT_EXEC_EXIT]      = "service",
    [ELLED_EVENT_PROCESS_EXIT]   = "service",
    [ELLED_EVENT_BLOCK_ISSUE]    = "disk",
    [ELLED_EVENT_BLOCK_COMPLETE] = "disk",
    [ELLED_EVENT_TCP_RETRANS]    = "net",
    [ELLED_EVENT_NET_DROP]       = "net",
    [ELLED_EVENT_THERMAL_TRIP]   = "thermal",
    [ELLED_EVENT_CAP_DENY]       = "auth",
    [ELLED_EVENT_FILE_DENY]      = "auth",
    [ELLED_EVENT_MOUNT_OP]       = "fs",
};
#endif

/* ============================================================================
 * Event Header (common to all events)
 * ============================================================================ */

/**
 * struct elled_event_hdr - Common header for all eBPF events
 * @ts_ns: Kernel monotonic timestamp (bpf_ktime_get_ns())
 * @cpu: CPU core where event occurred
 * @type: Event type from elled_event_type enum
 * @_pad: Padding for alignment
 */
struct elled_event_hdr {
    __u64 ts_ns;
    __u32 cpu;
    __u32 type;
};

/* ============================================================================
 * OOM Events
 * ============================================================================ */

/**
 * struct elled_oom_event - OOM kill event
 *
 * Captured from tracepoint:oom:mark_victim when OOM killer selects a victim.
 * Contains process info and memory statistics at kill time.
 */
struct elled_oom_event {
    struct elled_event_hdr hdr;

    /* Process identification */
    __u32 pid;              /* Process ID */
    __u32 tgid;             /* Thread group ID (usually same as pid) */
    __u32 uid;              /* User ID */
    __s16 oom_score_adj;    /* OOM score adjustment (-1000 to 1000) */
    __u8  _pad[2];

    /* Process name */
    char comm[16];          /* Process command name */

    /* Memory statistics (in pages) */
    __u64 total_vm;         /* Total virtual memory */
    __u64 anon_rss;         /* Anonymous RSS */
    __u64 file_rss;         /* File-backed RSS */
    __u64 shmem_rss;        /* Shared memory RSS */
};

/* ============================================================================
 * Exec Events
 * ============================================================================ */

/**
 * struct elled_exec_event - execve enter/exit event
 *
 * Tracks process execution with enter/exit correlation.
 * Enter event captures filename, exit captures return value.
 *
 * For enter events (is_exit=0):
 *   - filename contains the path being executed
 *   - ret is undefined
 *
 * For exit events (is_exit=1):
 *   - ret contains the execve return value (0=success, negative=error)
 *   - filename may be empty (look up via pid correlation)
 */
struct elled_exec_event {
    struct elled_event_hdr hdr;

    /* Process identification */
    __u32 pid;              /* Process ID */
    __u32 tgid;             /* Thread group ID */
    __u32 ppid;             /* Parent process ID */
    __u32 uid;              /* User ID */

    /* Execution result */
    __s32 ret;              /* Return value (exit only, 0=success) */
    __u8  is_exit;          /* 1 if exit event, 0 if enter */
    __u8  _pad[3];

    /* Process name */
    char comm[16];          /* Process command name */

    /* Executed file (enter only, may be truncated) */
    char filename[256];
};

/* ============================================================================
 * Process Exit Events
 * ============================================================================ */

/**
 * struct elled_exit_event - Process exit/signal death event
 *
 * Captured from tracepoint:sched:sched_process_exit.
 * Differentiates between normal exits and signal deaths.
 */
struct elled_exit_event {
    struct elled_event_hdr hdr;

    /* Process identification */
    __u32 pid;              /* Process ID */
    __u32 tgid;             /* Thread group ID */
    __u32 uid;              /* User ID */

    /* Exit information */
    __s32 exit_code;        /* Upper 8 bits: exit code (if exited) */
    __s32 signal;           /* Lower 7 bits: signal number (if signaled) */
    __u32 raw_exit_code;    /* Full exit_code from kernel */

    /* Process name */
    char comm[16];          /* Process command name */
};

/* ============================================================================
 * Block I/O Events
 * ============================================================================ */

/**
 * struct elled_block_event - Block I/O request event
 *
 * Tracks block requests from issue to completion for latency analysis.
 * Issue/complete correlation done via (dev, sector) key in BPF hashmap.
 *
 * Fields populated at issue (is_complete=0):
 *   - sector, dev, nr_sector, bytes, rwbs, start_ts
 *
 * Fields populated at completion (is_complete=1):
 *   - All above plus latency_ns, error
 */
struct elled_block_event {
    struct elled_event_hdr hdr;

    /* Block device */
    __u32 dev;              /* Device major:minor encoded */
    __u32 nr_sector;        /* Number of sectors */
    __u64 sector;           /* Starting sector */
    __u32 bytes;            /* Request size in bytes */

    /* Completion info */
    __s32 error;            /* Error code (0 = success) */
    __u64 latency_ns;       /* Latency from issue to complete (ns) */

    /* Request type (R=read, W=write, D=discard, etc.) */
    __u8  is_complete;      /* 1 if completion event */
    char rwbs[8];           /* Request type string */
    __u8  _pad[3];
};

/* ============================================================================
 * TCP Retransmit Events
 * ============================================================================ */

/**
 * struct elled_tcp_retrans_event - TCP retransmission event
 *
 * Captured from tracepoint:tcp:tcp_retransmit_skb.
 * Indicates network congestion or packet loss.
 */
struct elled_tcp_retrans_event {
    struct elled_event_hdr hdr;

    /* Connection info */
    __u32 saddr;            /* Source IPv4 address (network order) */
    __u32 daddr;            /* Destination IPv4 address */
    __u16 sport;            /* Source port */
    __u16 dport;            /* Destination port */

    /* TCP state */
    __u8  state;            /* TCP connection state */
    __u8  _pad[3];

    /* Process context (if available) */
    __u32 pid;              /* Process ID (may be 0) */
    char comm[16];          /* Process name */
};

/* ============================================================================
 * Network Drop Events
 * ============================================================================ */

/**
 * struct elled_net_drop_event - Network packet drop event
 *
 * Captured from tracepoint:skb:kfree_skb.
 * Provides visibility into why packets are being dropped.
 */
struct elled_net_drop_event {
    struct elled_event_hdr hdr;

    /* Packet info */
    __u16 protocol;         /* Network protocol (ETH_P_*) */
    __u16 len;              /* Packet length */
    __u32 ifindex;          /* Interface index */

    /* Drop reason */
    __u32 reason;           /* SKB drop reason enum */
    __u8  _pad[4];

    /* Process context (if available) */
    __u32 pid;              /* Process ID (may be 0) */
    char comm[16];          /* Process name */
};

/* ============================================================================
 * Capability Denial Events
 * ============================================================================ */

/**
 * struct elled_cap_deny_event - Capability denial event
 *
 * Captured from tracepoint:capability:cap_capable when ret != 0.
 * Helps diagnose "permission denied" errors that require capabilities.
 */
struct elled_cap_deny_event {
    struct elled_event_hdr hdr;

    /* Process identification */
    __u32 pid;              /* Process ID */
    __u32 tgid;             /* Thread group ID */
    __u32 uid;              /* User ID */
    __u32 _pad0;

    /* Capability info */
    __s32 cap;              /* Capability number (CAP_NET_BIND_SERVICE, etc.) */
    __s32 ret;              /* Return code (0=granted, negative=denied) */

    /* Process name */
    char comm[16];          /* Process command name */

    /* Target info (if available) */
    char target[64];        /* Target file/resource (if extractable) */
};

/* Common Linux capabilities */
#define CAP_NET_BIND_SERVICE 10
#define CAP_NET_RAW          13
#define CAP_SYS_ADMIN        21
#define CAP_SYS_PTRACE       19

/* ============================================================================
 * File Permission Denial Events
 * ============================================================================ */

/**
 * struct elled_file_deny_event - File permission denial event
 *
 * Captured from tracepoint:syscalls:sys_exit_openat when ret is EACCES/EPERM.
 * Helps diagnose file access issues (wrong owner, permissions, etc.)
 */
struct elled_file_deny_event {
    struct elled_event_hdr hdr;

    /* Process identification */
    __u32 pid;              /* Process ID */
    __u32 tgid;             /* Thread group ID */
    __u32 uid;              /* User ID */
    __u32 gid;              /* Group ID */

    /* File info */
    __s32 error;            /* Error code (EACCES, EPERM, EROFS) */
    __u32 flags;            /* Open flags (O_RDONLY, O_WRONLY, etc.) */

    /* Process name */
    char comm[16];          /* Process command name */

    /* Filename */
    char filename[256];     /* File path that was denied */
};

/* Error codes for file denial */
#define EACCES_VAL  13      /* Permission denied */
#define EPERM_VAL    1      /* Operation not permitted */
#define EROFS_VAL   30      /* Read-only file system */

/* ============================================================================
 * Mount Operation Events
 * ============================================================================ */

/**
 * struct elled_mount_event - Mount operation event
 *
 * Captured from mount-related tracepoints.
 * Detects remount-ro (filesystem errors) and mount failures.
 */
struct elled_mount_event {
    struct elled_event_hdr hdr;

    /* Process identification */
    __u32 pid;              /* Process ID */
    __u32 tgid;             /* Thread group ID */
    __u32 uid;              /* User ID */
    __u32 _pad0;

    /* Mount info */
    __s32 ret;              /* Return code (0=success) */
    __u32 flags;            /* Mount flags (MS_REMOUNT, MS_RDONLY, etc.) */
    __u8  is_remount_ro;    /* True if remount read-only */
    __u8  _pad1[3];

    /* Process name */
    char comm[16];          /* Process command name */

    /* Mount point and device */
    char source[64];        /* Source device/path */
    char target[128];       /* Mount point */
    char fstype[16];        /* Filesystem type */
};

/* Mount flags */
#define MS_RDONLY       1
#define MS_REMOUNT     32

/* ============================================================================
 * Configuration (runtime tuning via BPF maps)
 * ============================================================================ */

/**
 * struct elled_config - Runtime configuration
 *
 * Stored in a BPF array map for runtime tuning without reload.
 * Userspace updates map values to enable/disable features.
 */
struct elled_config {
    /* Program enable flags */
    __u8 enable_oom;
    __u8 enable_exec;
    __u8 enable_block;
    __u8 enable_tcp_retrans;
    __u8 enable_net_drop;
    __u8 enable_process_exit;
    __u8 enable_cap_deny;
    __u8 enable_file_deny;
    __u8 enable_mount;
    __u8 _pad[3];           /* Padding for alignment */

    /* Filtering thresholds */
    __u32 block_latency_threshold_us;  /* Only report latency above this (0=all) */
    __u32 exec_sample_rate;            /* 1 in N sampling for exec (1=all) */
    __u32 net_drop_sample_rate;        /* 1 in N sampling for drops (1=all) */

    /* Process filtering */
    __u32 filter_pid;                  /* Only trace this PID (0=all) */
    __u32 filter_uid;                  /* Only trace this UID (0xFFFFFFFF=all) */
};

/* Default configuration values */
#define ELLED_DEFAULT_CONFIG {          \
    .enable_oom = 1,                    \
    .enable_exec = 1,                   \
    .enable_block = 1,                  \
    .enable_tcp_retrans = 0,            \
    .enable_net_drop = 0,               \
    .enable_process_exit = 1,           \
    .enable_cap_deny = 1,               \
    .enable_file_deny = 1,              \
    .enable_mount = 1,                  \
    .block_latency_threshold_us = 0,    \
    .exec_sample_rate = 1,              \
    .net_drop_sample_rate = 100,        \
    .filter_pid = 0,                    \
    .filter_uid = 0xFFFFFFFF,           \
}

/* ============================================================================
 * Ring Buffer Configuration
 * ============================================================================ */

/* Ring buffer size (4MB = 4 * 1024 * 1024) */
#define ELLED_RINGBUF_SIZE (1 << 22)

/* Ring buffer map name (must match BPF programs) */
#define ELLED_RINGBUF_NAME "events"

/* ============================================================================
 * Helper Macros (BPF side only)
 * ============================================================================ */

#ifdef __BPF__

/* Read task_struct fields with CO-RE */
#define READ_TASK_FIELD(task, field) \
    BPF_CORE_READ(task, field)

/* Read task comm into buffer */
#define READ_TASK_COMM(buf, task) \
    bpf_probe_read_kernel_str(buf, sizeof(buf), &((task)->comm))

/* Reserve ring buffer space */
#define RINGBUF_RESERVE(rb, size) \
    bpf_ringbuf_reserve(rb, size, 0)

/* Submit ring buffer entry */
#define RINGBUF_SUBMIT(entry) \
    bpf_ringbuf_submit(entry, 0)

/* Discard ring buffer entry */
#define RINGBUF_DISCARD(entry) \
    bpf_ringbuf_discard(entry, 0)

#endif /* __BPF__ */

#endif /* __ELLED_H__ */
