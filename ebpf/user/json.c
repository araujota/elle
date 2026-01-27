// SPDX-License-Identifier: MIT
/**
 * json.c - NDJSON serialization for eBPF events
 *
 * Serializes eBPF events to NDJSON format compatible with
 * the Python EBPFRawEvent/normalizer pipeline.
 *
 * Output format matches EBPFWatcher._event_to_raw_dict()
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jansson.h>
#include <arpa/inet.h>

#include "json.h"

/* Signal name mapping */
static const char *signal_names[] = {
    [1]  = "SIGHUP",
    [2]  = "SIGINT",
    [3]  = "SIGQUIT",
    [4]  = "SIGILL",
    [5]  = "SIGTRAP",
    [6]  = "SIGABRT",
    [7]  = "SIGBUS",
    [8]  = "SIGFPE",
    [9]  = "SIGKILL",
    [10] = "SIGUSR1",
    [11] = "SIGSEGV",
    [12] = "SIGUSR2",
    [13] = "SIGPIPE",
    [14] = "SIGALRM",
    [15] = "SIGTERM",
};
#define NUM_SIGNALS (sizeof(signal_names) / sizeof(signal_names[0]))

/* Priority mapping (severity to journald priority) */
static int get_priority(const char *severity)
{
    if (strcmp(severity, "critical") == 0) return 2;
    if (strcmp(severity, "error") == 0) return 3;
    if (strcmp(severity, "warning") == 0) return 4;
    if (strcmp(severity, "info") == 0) return 6;
    return 6;  /* default: info */
}

/* ==========================================================================
 * Helper: Add common fields
 * ========================================================================== */

static void add_common_fields(json_t *obj,
                               const struct elled_event_hdr *hdr,
                               const char *event_type,
                               const char *category,
                               const char *message,
                               const char *severity)
{
    char priority_str[8];

    json_object_set_new(obj, "_SOURCE", json_string("ebpf"));
    json_object_set_new(obj, "_EBPF_EVENT_TYPE", json_string(event_type));
    json_object_set_new(obj, "_EBPF_CATEGORY", json_string(category));
    json_object_set_new(obj, "MESSAGE", json_string(message));

    snprintf(priority_str, sizeof(priority_str), "%d", get_priority(severity));
    json_object_set_new(obj, "PRIORITY", json_string(priority_str));

    json_object_set_new(obj, "_EBPF_TS_NS", json_integer(hdr->ts_ns));
    json_object_set_new(obj, "_EBPF_CPU", json_integer(hdr->cpu));
}

static void add_process_context(json_t *obj,
                                 __u32 pid,
                                 __u32 tgid,
                                 __u32 uid,
                                 const char *comm)
{
    char str[32];

    snprintf(str, sizeof(str), "%u", pid);
    json_object_set_new(obj, "_PID", json_string(str));

    snprintf(str, sizeof(str), "%u", tgid);
    json_object_set_new(obj, "_TGID", json_string(str));

    snprintf(str, sizeof(str), "%u", uid);
    json_object_set_new(obj, "_UID", json_string(str));

    if (comm && comm[0])
        json_object_set_new(obj, "_COMM", json_string(comm));
}

/* ==========================================================================
 * OOM Event Serialization
 * ========================================================================== */

char *elled_json_oom_event(const struct elled_oom_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;

    if (!obj)
        return NULL;

    /* Format message */
    snprintf(message, sizeof(message),
             "OOM killer invoked, killed process %.15s (PID %u)",
             evt->comm, evt->pid);

    /* Add common fields */
    add_common_fields(obj, &evt->hdr, "oom_kill", "oom", message, "critical");
    add_process_context(obj, evt->pid, evt->tgid, evt->uid, evt->comm);

    /* Add OOM-specific fields */
    json_object_set_new(obj, "_EBPF_OOM_SCORE_ADJ", json_integer(evt->oom_score_adj));
    json_object_set_new(obj, "_EBPF_TOTAL_VM", json_integer(evt->total_vm));
    json_object_set_new(obj, "_EBPF_ANON_RSS", json_integer(evt->anon_rss));
    json_object_set_new(obj, "_EBPF_FILE_RSS", json_integer(evt->file_rss));
    json_object_set_new(obj, "_EBPF_SHMEM_RSS", json_integer(evt->shmem_rss));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Exec Event Serialization
 * ========================================================================== */

char *elled_json_exec_event(const struct elled_exec_event *evt)
{
    json_t *obj = json_object();
    char message[512];
    char *result;
    const char *event_type;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine success/failure */
    if (evt->ret == 0) {
        event_type = "exec_exit";
        severity = "info";
        snprintf(message, sizeof(message),
                 "Process %.15s (PID %u) executed %.200s",
                 evt->comm, evt->pid, evt->filename);
    } else {
        event_type = "exec_exit";
        severity = "warning";
        snprintf(message, sizeof(message),
                 "Process %.15s (PID %u) failed to execute %.200s (error %d)",
                 evt->comm, evt->pid, evt->filename, evt->ret);
    }

    /* Add common fields */
    add_common_fields(obj, &evt->hdr, event_type, "service", message, severity);
    add_process_context(obj, evt->pid, evt->tgid, evt->uid, evt->comm);

    /* Add exec-specific fields */
    json_object_set_new(obj, "_EBPF_PPID", json_integer(evt->ppid));
    json_object_set_new(obj, "_EBPF_RET", json_integer(evt->ret));
    json_object_set_new(obj, "_EBPF_FILENAME", json_string(evt->filename));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Process Exit Event Serialization
 * ========================================================================== */

char *elled_json_exit_event(const struct elled_exit_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;
    const char *signal_name = NULL;

    if (!obj)
        return NULL;

    /* Get signal name if applicable */
    if (evt->signal > 0 && evt->signal < (int)NUM_SIGNALS)
        signal_name = signal_names[evt->signal];

    /* Format message */
    if (evt->signal > 0) {
        severity = "warning";
        if (evt->signal == 9 || evt->signal == 11 || evt->signal == 6) {
            severity = "error";  /* SIGKILL, SIGSEGV, SIGABRT */
        }

        if (signal_name) {
            snprintf(message, sizeof(message),
                     "Process %.15s (PID %u) killed by signal %s (%d)",
                     evt->comm, evt->pid, signal_name, evt->signal);
        } else {
            snprintf(message, sizeof(message),
                     "Process %.15s (PID %u) killed by signal %d",
                     evt->comm, evt->pid, evt->signal);
        }
    } else {
        severity = "info";
        snprintf(message, sizeof(message),
                 "Process %.15s (PID %u) exited with code %d",
                 evt->comm, evt->pid, evt->exit_code);
    }

    /* Add common fields */
    add_common_fields(obj, &evt->hdr, "process_exit", "service", message, severity);
    add_process_context(obj, evt->pid, evt->tgid, evt->uid, evt->comm);

    /* Add exit-specific fields */
    json_object_set_new(obj, "_EBPF_EXIT_CODE", json_integer(evt->exit_code));
    json_object_set_new(obj, "_EBPF_SIGNAL", json_integer(evt->signal));
    json_object_set_new(obj, "_EBPF_RAW_EXIT_CODE", json_integer(evt->raw_exit_code));

    if (signal_name)
        json_object_set_new(obj, "_EBPF_SIGNAL_NAME", json_string(signal_name));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Block I/O Event Serialization
 * ========================================================================== */

char *elled_json_block_event(const struct elled_block_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;
    __u32 major = (evt->dev >> 20) & 0xfff;
    __u32 minor = evt->dev & 0xfffff;

    if (!obj)
        return NULL;

    /* Format message */
    if (evt->error != 0) {
        severity = "error";
        snprintf(message, sizeof(message),
                 "Block I/O error on device %u:%u (error %d)",
                 major, minor, evt->error);
    } else {
        severity = "info";
        snprintf(message, sizeof(message),
                 "Block I/O completed on device %u:%u (latency %.2fms)",
                 major, minor, evt->latency_ns / 1000000.0);
    }

    /* Add common fields */
    add_common_fields(obj, &evt->hdr,
                      evt->error != 0 ? "block_rq_error" : "block_rq_complete",
                      "disk", message, severity);

    /* Add block-specific fields */
    json_object_set_new(obj, "_EBPF_DEV", json_integer(evt->dev));
    json_object_set_new(obj, "_EBPF_MAJOR", json_integer(major));
    json_object_set_new(obj, "_EBPF_MINOR", json_integer(minor));
    json_object_set_new(obj, "_EBPF_SECTOR", json_integer(evt->sector));
    json_object_set_new(obj, "_EBPF_NR_SECTOR", json_integer(evt->nr_sector));
    json_object_set_new(obj, "_EBPF_BYTES", json_integer(evt->bytes));
    json_object_set_new(obj, "_EBPF_ERROR", json_integer(evt->error));
    json_object_set_new(obj, "_EBPF_LATENCY_NS", json_integer(evt->latency_ns));
    json_object_set_new(obj, "_EBPF_RWBS", json_string(evt->rwbs));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * TCP Retransmit Event Serialization
 * ========================================================================== */

char *elled_json_tcp_event(const struct elled_tcp_retrans_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char saddr_str[INET_ADDRSTRLEN];
    char daddr_str[INET_ADDRSTRLEN];
    char *result;

    if (!obj)
        return NULL;

    /* Convert addresses to string */
    inet_ntop(AF_INET, &evt->saddr, saddr_str, sizeof(saddr_str));
    inet_ntop(AF_INET, &evt->daddr, daddr_str, sizeof(daddr_str));

    /* Format message */
    snprintf(message, sizeof(message),
             "TCP retransmit %s:%u -> %s:%u",
             saddr_str, ntohs(evt->sport),
             daddr_str, ntohs(evt->dport));

    /* Add common fields */
    add_common_fields(obj, &evt->hdr, "net_retransmit", "net", message, "warning");

    /* Add process context if available */
    if (evt->pid > 0)
        add_process_context(obj, evt->pid, evt->pid, 0, evt->comm);

    /* Add TCP-specific fields */
    json_object_set_new(obj, "_EBPF_SADDR", json_string(saddr_str));
    json_object_set_new(obj, "_EBPF_DADDR", json_string(daddr_str));
    json_object_set_new(obj, "_EBPF_SPORT", json_integer(ntohs(evt->sport)));
    json_object_set_new(obj, "_EBPF_DPORT", json_integer(ntohs(evt->dport)));
    json_object_set_new(obj, "_EBPF_STATE", json_integer(evt->state));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Capability Denial Event Serialization
 * ========================================================================== */

/* Capability names */
static const char *capability_names[] = {
    [0]  = "CAP_CHOWN",
    [1]  = "CAP_DAC_OVERRIDE",
    [2]  = "CAP_DAC_READ_SEARCH",
    [3]  = "CAP_FOWNER",
    [4]  = "CAP_FSETID",
    [5]  = "CAP_KILL",
    [6]  = "CAP_SETGID",
    [7]  = "CAP_SETUID",
    [8]  = "CAP_SETPCAP",
    [9]  = "CAP_LINUX_IMMUTABLE",
    [10] = "CAP_NET_BIND_SERVICE",
    [11] = "CAP_NET_BROADCAST",
    [12] = "CAP_NET_ADMIN",
    [13] = "CAP_NET_RAW",
    [14] = "CAP_IPC_LOCK",
    [15] = "CAP_IPC_OWNER",
    [16] = "CAP_SYS_MODULE",
    [17] = "CAP_SYS_RAWIO",
    [18] = "CAP_SYS_CHROOT",
    [19] = "CAP_SYS_PTRACE",
    [20] = "CAP_SYS_PACCT",
    [21] = "CAP_SYS_ADMIN",
    [22] = "CAP_SYS_BOOT",
    [23] = "CAP_SYS_NICE",
    [24] = "CAP_SYS_RESOURCE",
    [25] = "CAP_SYS_TIME",
    [26] = "CAP_SYS_TTY_CONFIG",
    [27] = "CAP_MKNOD",
    [28] = "CAP_LEASE",
    [29] = "CAP_AUDIT_WRITE",
    [30] = "CAP_AUDIT_CONTROL",
    [31] = "CAP_SETFCAP",
    [32] = "CAP_MAC_OVERRIDE",
    [33] = "CAP_MAC_ADMIN",
    [34] = "CAP_SYSLOG",
    [35] = "CAP_WAKE_ALARM",
    [36] = "CAP_BLOCK_SUSPEND",
    [37] = "CAP_AUDIT_READ",
    [38] = "CAP_PERFMON",
    [39] = "CAP_BPF",
    [40] = "CAP_CHECKPOINT_RESTORE",
};
#define NUM_CAPABILITIES (sizeof(capability_names) / sizeof(capability_names[0]))

static const char *get_capability_name(int cap)
{
    if (cap >= 0 && cap < (int)NUM_CAPABILITIES && capability_names[cap])
        return capability_names[cap];
    return "UNKNOWN";
}

char *elled_json_cap_deny_event(const struct elled_cap_deny_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *cap_name;

    if (!obj)
        return NULL;

    cap_name = get_capability_name(evt->cap);

    /* Format message */
    snprintf(message, sizeof(message),
             "Process %.15s (PID %u) denied capability %s (%d)",
             evt->comm, evt->pid, cap_name, evt->cap);

    /* Add common fields */
    add_common_fields(obj, &evt->hdr, "cap_deny", "auth", message, "warning");
    add_process_context(obj, evt->pid, evt->tgid, evt->uid, evt->comm);

    /* Add capability-specific fields */
    json_object_set_new(obj, "_EBPF_CAP", json_integer(evt->cap));
    json_object_set_new(obj, "_EBPF_CAP_NAME", json_string(cap_name));
    json_object_set_new(obj, "_EBPF_RET", json_integer(evt->ret));

    if (evt->target[0])
        json_object_set_new(obj, "_EBPF_TARGET", json_string(evt->target));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * File Permission Denial Event Serialization
 * ========================================================================== */

static const char *get_errno_name(int err)
{
    switch (err) {
    case 1:  return "EPERM";
    case 13: return "EACCES";
    case 30: return "EROFS";
    default: return "UNKNOWN";
    }
}

char *elled_json_file_deny_event(const struct elled_file_deny_event *evt)
{
    json_t *obj = json_object();
    char message[512];
    char *result;
    const char *errno_name;

    if (!obj)
        return NULL;

    errno_name = get_errno_name(evt->error);

    /* Format message */
    snprintf(message, sizeof(message),
             "Process %.15s (PID %u, uid=%u) denied access to %.200s: %s (%d)",
             evt->comm, evt->pid, evt->uid, evt->filename, errno_name, evt->error);

    /* Add common fields */
    add_common_fields(obj, &evt->hdr, "file_deny", "auth", message, "warning");
    add_process_context(obj, evt->pid, evt->tgid, evt->uid, evt->comm);

    /* Add file-specific fields */
    json_object_set_new(obj, "_EBPF_FILENAME", json_string(evt->filename));
    json_object_set_new(obj, "_EBPF_ERROR", json_integer(evt->error));
    json_object_set_new(obj, "_EBPF_ERROR_NAME", json_string(errno_name));
    json_object_set_new(obj, "_EBPF_FLAGS", json_integer(evt->flags));
    json_object_set_new(obj, "_EBPF_GID", json_integer(evt->gid));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Mount Operation Event Serialization
 * ========================================================================== */

char *elled_json_mount_event(const struct elled_mount_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine severity */
    if (evt->is_remount_ro) {
        severity = "critical";
        snprintf(message, sizeof(message),
                 "Filesystem %.64s remounted read-only at %.64s (possible disk errors)",
                 evt->source, evt->target);
    } else if (evt->ret != 0) {
        severity = "error";
        snprintf(message, sizeof(message),
                 "Mount operation failed for %.64s -> %.64s (error %d)",
                 evt->source, evt->target, evt->ret);
    } else {
        severity = "info";
        snprintf(message, sizeof(message),
                 "Mount operation: %.64s -> %.64s (flags=0x%x)",
                 evt->source, evt->target, evt->flags);
    }

    /* Add common fields */
    add_common_fields(obj, &evt->hdr, "mount_op", "fs", message, severity);
    add_process_context(obj, evt->pid, evt->tgid, evt->uid, evt->comm);

    /* Add mount-specific fields */
    json_object_set_new(obj, "_EBPF_MOUNT_SOURCE", json_string(evt->source));
    json_object_set_new(obj, "_EBPF_MOUNT_TARGET", json_string(evt->target));
    json_object_set_new(obj, "_EBPF_MOUNT_FSTYPE", json_string(evt->fstype));
    json_object_set_new(obj, "_EBPF_MOUNT_FLAGS", json_integer(evt->flags));
    json_object_set_new(obj, "_EBPF_MOUNT_RET", json_integer(evt->ret));
    json_object_set_new(obj, "_EBPF_MOUNT_REMOUNT_RO", json_boolean(evt->is_remount_ro));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}
