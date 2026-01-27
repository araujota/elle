// SPDX-License-Identifier: GPL-2.0 OR MIT
/**
 * cap_deny.bpf.c - Capability denial tracing
 *
 * Traces the cap_capable tracepoint to detect when processes are
 * denied capabilities. This helps diagnose "permission denied" errors
 * that could be resolved by granting specific capabilities.
 *
 * Remediation value: "Permission denied" -> "needs CAP_NET_BIND_SERVICE"
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

/* Capability names for common capabilities (kernel doesn't provide these) */
static const char *cap_names[] = {
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
};

/**
 * Tracepoint: capability:cap_capable
 *
 * Traces capability checks. We only care about denials (ret != 0).
 *
 * Tracepoint format:
 *   field:const struct cred *cred;
 *   field:struct user_namespace *targ_ns;
 *   field:int cap;
 *   field:unsigned int opts;
 *   field:int ret;
 */
SEC("tp/capability/cap_capable")
int trace_cap_capable(void *ctx)
{
    struct elled_cap_deny_event *evt;
    struct task_struct *task;
    __u32 key = 0;
    struct elled_config *cfg;
    int cap, ret;

    /* Check if enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_cap_deny)
        return 0;

    /* Read return value - we only care about denials */
    /* The tracepoint args are at fixed offsets in the context */
    /* For tp/capability/cap_capable:
     *   offset 8: cap
     *   offset 16: ret (after the check)
     * Note: The exact layout depends on kernel version
     */
    ret = 0;
    bpf_probe_read(&cap, sizeof(cap), (void *)ctx + 8);
    bpf_probe_read(&ret, sizeof(ret), (void *)ctx + 16);

    /* Only trace denials */
    if (ret == 0)
        return 0;

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt)
        return 0;

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_CAP_DENY;

    /* Get current task info */
    task = (struct task_struct *)bpf_get_current_task();

    evt->pid = bpf_get_current_pid_tgid() >> 32;
    evt->tgid = bpf_get_current_pid_tgid() >> 32;
    evt->uid = bpf_get_current_uid_gid() & 0xFFFFFFFF;
    evt->cap = cap;
    evt->ret = ret;

    /* Get process name */
    bpf_get_current_comm(evt->comm, sizeof(evt->comm));

    /* Target info is not directly available from tracepoint */
    evt->target[0] = '\0';

    bpf_ringbuf_submit(evt, 0);
    return 0;
}
