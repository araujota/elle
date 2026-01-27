// SPDX-License-Identifier: GPL-2.0 OR MIT
/**
 * oom.bpf.c - OOM kill tracing for elled-ebpfd
 *
 * Attaches to tracepoint:oom:mark_victim to capture OOM kill events.
 * Reports the victim process, its memory usage, and OOM score.
 *
 * CO-RE (Compile Once - Run Everywhere) for kernel portability.
 */

#define __BPF__
#include "elled.h"

/* License declaration required for BPF programs */
char LICENSE[] SEC("license") = "Dual MIT/GPL";

/* ==========================================================================
 * Maps
 * ========================================================================== */

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

/* ==========================================================================
 * Tracepoint: oom:mark_victim
 *
 * Triggered when OOM killer selects a victim process.
 *
 * Tracepoint args (from /sys/kernel/debug/tracing/events/oom/mark_victim/format):
 *   pid_t pid
 *   const char *comm
 *   unsigned long total_vm
 *   unsigned long anon_rss
 *   unsigned long file_rss
 *   unsigned long shmem_rss
 *   uid_t uid
 *   short oom_score_adj
 * ========================================================================== */

SEC("tp/oom/mark_victim")
int trace_oom_mark_victim(struct trace_event_raw_mark_victim *ctx)
{
    struct elled_oom_event *evt;
    __u32 key = 0;
    struct elled_config *cfg;

    /* Check if OOM tracing is enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_oom)
        return 0;

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt)
        return 0;

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_OOM_KILL;

    /* Read tracepoint fields via CO-RE */
    evt->pid = BPF_CORE_READ(ctx, pid);
    evt->tgid = evt->pid;  /* For OOM, pid == tgid typically */
    evt->uid = BPF_CORE_READ(ctx, uid);
    evt->oom_score_adj = BPF_CORE_READ(ctx, oom_score_adj);

    /* Read comm string */
    bpf_probe_read_kernel_str(evt->comm, sizeof(evt->comm),
                              (void *)BPF_CORE_READ(ctx, comm));

    /* Memory stats (in pages, convert to bytes in userspace) */
    evt->total_vm = BPF_CORE_READ(ctx, total_vm);
    evt->anon_rss = BPF_CORE_READ(ctx, anon_rss);
    evt->file_rss = BPF_CORE_READ(ctx, file_rss);
    evt->shmem_rss = BPF_CORE_READ(ctx, shmem_rss);

    /* Submit event */
    bpf_ringbuf_submit(evt, 0);

    return 0;
}
