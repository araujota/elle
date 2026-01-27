// SPDX-License-Identifier: GPL-2.0 OR MIT
/**
 * file_deny.bpf.c - File permission denial tracing
 *
 * Traces openat syscall exits to detect EACCES/EPERM/EROFS errors.
 * This helps diagnose file access issues by showing exactly which
 * file was denied and why.
 *
 * Remediation value: "Can't read file" -> "owner is root, process runs as www-data"
 */

#define __BPF__
#include "elled.h"

char LICENSE[] SEC("license") = "Dual MIT/GPL";

/* Ring buffer for events */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, ELLED_RINGBUF_SIZE);
} events SEC(".maps");

/* Configuration map */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct elled_config);
} config SEC(".maps");

/* Map to store enter data for exit correlation */
struct openat_enter_data {
    __u64 ts_ns;
    __u32 flags;
    char filename[256];
};

struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, 4096);
    __type(key, __u64);  /* pid_tgid */
    __type(value, struct openat_enter_data);
} openat_enter_map SEC(".maps");

/* Error codes we care about */
#define EACCES  13
#define EPERM    1
#define EROFS   30

/**
 * Tracepoint: syscalls:sys_enter_openat
 *
 * Captures filename and flags on entry for later correlation.
 */
SEC("tp/syscalls/sys_enter_openat")
int trace_openat_enter(struct trace_event_raw_sys_enter *ctx)
{
    struct elled_config *cfg;
    struct openat_enter_data data = {};
    __u64 pid_tgid;
    __u32 key = 0;
    const char *filename;

    /* Check if enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_file_deny)
        return 0;

    pid_tgid = bpf_get_current_pid_tgid();

    /* Args: (int dfd, const char *filename, int flags, umode_t mode) */
    filename = (const char *)ctx->args[1];
    data.flags = (__u32)ctx->args[2];
    data.ts_ns = bpf_ktime_get_ns();

    /* Read filename from userspace */
    bpf_probe_read_user_str(data.filename, sizeof(data.filename), filename);

    /* Store for exit correlation */
    bpf_map_update_elem(&openat_enter_map, &pid_tgid, &data, BPF_ANY);

    return 0;
}

/**
 * Tracepoint: syscalls:sys_exit_openat
 *
 * Checks return value for permission errors and emits events.
 */
SEC("tp/syscalls/sys_exit_openat")
int trace_openat_exit(struct trace_event_raw_sys_exit *ctx)
{
    struct elled_file_deny_event *evt;
    struct openat_enter_data *enter_data;
    struct elled_config *cfg;
    __u64 pid_tgid;
    __u32 key = 0;
    long ret;

    /* Check if enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_file_deny)
        return 0;

    /* Get return value */
    ret = ctx->ret;

    /* Only care about permission errors (negative errno) */
    if (ret != -EACCES && ret != -EPERM && ret != -EROFS)
        return 0;

    /* Look up enter data */
    pid_tgid = bpf_get_current_pid_tgid();
    enter_data = bpf_map_lookup_elem(&openat_enter_map, &pid_tgid);
    if (!enter_data)
        return 0;

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt) {
        bpf_map_delete_elem(&openat_enter_map, &pid_tgid);
        return 0;
    }

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_FILE_DENY;

    /* Process info */
    evt->pid = pid_tgid >> 32;
    evt->tgid = pid_tgid >> 32;
    evt->uid = bpf_get_current_uid_gid() & 0xFFFFFFFF;
    evt->gid = bpf_get_current_uid_gid() >> 32;

    /* Error info */
    evt->error = -ret;  /* Store positive errno */
    evt->flags = enter_data->flags;

    /* Process name */
    bpf_get_current_comm(evt->comm, sizeof(evt->comm));

    /* Copy filename from enter data */
    __builtin_memcpy(evt->filename, enter_data->filename, sizeof(evt->filename));

    /* Cleanup enter map */
    bpf_map_delete_elem(&openat_enter_map, &pid_tgid);

    bpf_ringbuf_submit(evt, 0);
    return 0;
}
