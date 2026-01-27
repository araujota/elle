// SPDX-License-Identifier: GPL-2.0 OR MIT
/**
 * mount.bpf.c - Mount operation tracing
 *
 * Traces mount syscalls to detect remount-ro events (which indicate
 * filesystem errors) and mount failures.
 *
 * Remediation value: "Write failed" -> "filesystem remounted read-only due to errors"
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

/* Map to store mount enter data for exit correlation */
struct mount_enter_data {
    __u64 ts_ns;
    __u32 flags;
    char source[64];
    char target[128];
    char fstype[16];
};

struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, 256);
    __type(key, __u64);  /* pid_tgid */
    __type(value, struct mount_enter_data);
} mount_enter_map SEC(".maps");

/* Mount flags we care about */
#define MS_RDONLY_FLAG       1
#define MS_REMOUNT_FLAG     32

/**
 * Tracepoint: syscalls:sys_enter_mount
 *
 * Captures mount parameters on entry.
 *
 * Args: (char *dev_name, char *dir_name, char *type, unsigned long flags, void *data)
 */
SEC("tp/syscalls/sys_enter_mount")
int trace_mount_enter(struct trace_event_raw_sys_enter *ctx)
{
    struct elled_config *cfg;
    struct mount_enter_data data = {};
    __u64 pid_tgid;
    __u32 key = 0;

    /* Check if enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_mount)
        return 0;

    pid_tgid = bpf_get_current_pid_tgid();

    data.ts_ns = bpf_ktime_get_ns();
    data.flags = (__u32)ctx->args[3];

    /* Only trace remounts or if flags include interesting combinations */
    if (!(data.flags & MS_REMOUNT_FLAG)) {
        /* Skip non-remount unless it's a new mount (we could track these too) */
        /* For now, focus on remount-ro which is most indicative of problems */
        return 0;
    }

    /* Read mount parameters from userspace */
    const char *source = (const char *)ctx->args[0];
    const char *target = (const char *)ctx->args[1];
    const char *fstype = (const char *)ctx->args[2];

    if (source)
        bpf_probe_read_user_str(data.source, sizeof(data.source), source);
    if (target)
        bpf_probe_read_user_str(data.target, sizeof(data.target), target);
    if (fstype)
        bpf_probe_read_user_str(data.fstype, sizeof(data.fstype), fstype);

    /* Store for exit correlation */
    bpf_map_update_elem(&mount_enter_map, &pid_tgid, &data, BPF_ANY);

    return 0;
}

/**
 * Tracepoint: syscalls:sys_exit_mount
 *
 * Emits events for completed mount operations.
 */
SEC("tp/syscalls/sys_exit_mount")
int trace_mount_exit(struct trace_event_raw_sys_exit *ctx)
{
    struct elled_mount_event *evt;
    struct mount_enter_data *enter_data;
    struct elled_config *cfg;
    __u64 pid_tgid;
    __u32 key = 0;
    long ret;

    /* Check if enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_mount)
        return 0;

    pid_tgid = bpf_get_current_pid_tgid();

    /* Look up enter data */
    enter_data = bpf_map_lookup_elem(&mount_enter_map, &pid_tgid);
    if (!enter_data)
        return 0;

    ret = ctx->ret;

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt) {
        bpf_map_delete_elem(&mount_enter_map, &pid_tgid);
        return 0;
    }

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_MOUNT_OP;

    /* Process info */
    evt->pid = pid_tgid >> 32;
    evt->tgid = pid_tgid >> 32;
    evt->uid = bpf_get_current_uid_gid() & 0xFFFFFFFF;

    /* Mount info */
    evt->ret = ret;
    evt->flags = enter_data->flags;

    /* Check for remount read-only */
    evt->is_remount_ro = ((enter_data->flags & MS_REMOUNT_FLAG) &&
                          (enter_data->flags & MS_RDONLY_FLAG)) ? 1 : 0;

    /* Process name */
    bpf_get_current_comm(evt->comm, sizeof(evt->comm));

    /* Copy mount info from enter data */
    __builtin_memcpy(evt->source, enter_data->source, sizeof(evt->source));
    __builtin_memcpy(evt->target, enter_data->target, sizeof(evt->target));
    __builtin_memcpy(evt->fstype, enter_data->fstype, sizeof(evt->fstype));

    /* Cleanup enter map */
    bpf_map_delete_elem(&mount_enter_map, &pid_tgid);

    bpf_ringbuf_submit(evt, 0);
    return 0;
}
