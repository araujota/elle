// SPDX-License-Identifier: GPL-2.0 OR MIT
/**
 * tcp_retrans.bpf.c - TCP retransmission tracing for elled-ebpfd
 *
 * Traces TCP retransmissions which indicate network congestion or loss.
 * Useful for detecting network problems affecting applications.
 *
 * Attaches to tracepoint:tcp:tcp_retransmit_skb.
 *
 * CO-RE (Compile Once - Run Everywhere) for kernel portability.
 */

#define __BPF__
#include "elled.h"

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
 * Tracepoint: tcp_retransmit_skb
 *
 * Triggered when a TCP segment is retransmitted.
 *
 * Tracepoint format:
 *   const void *skbaddr, const void *skaddr,
 *   __u16 sport, __u16 dport, __u8 saddr[4], __u8 daddr[4],
 *   __u8 saddr_v6[16], __u8 daddr_v6[16], state
 * ========================================================================== */

SEC("tp/tcp/tcp_retransmit_skb")
int trace_tcp_retransmit(struct trace_event_raw_tcp_event_sk_skb *ctx)
{
    struct elled_tcp_retrans_event *evt;
    struct elled_config *cfg;
    __u32 key = 0;

    /* Check if TCP retransmit tracing is enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_tcp_retrans)
        return 0;

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt)
        return 0;

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_TCP_RETRANS;

    /* Connection info (IPv4 only for now) */
    /* Read 4-byte saddr/daddr arrays and combine into u32 */
    __u8 saddr[4], daddr[4];
    bpf_probe_read_kernel(saddr, sizeof(saddr), ctx->saddr);
    bpf_probe_read_kernel(daddr, sizeof(daddr), ctx->daddr);

    evt->saddr = ((__u32)saddr[0]) |
                 ((__u32)saddr[1] << 8) |
                 ((__u32)saddr[2] << 16) |
                 ((__u32)saddr[3] << 24);
    evt->daddr = ((__u32)daddr[0]) |
                 ((__u32)daddr[1] << 8) |
                 ((__u32)daddr[2] << 16) |
                 ((__u32)daddr[3] << 24);

    evt->sport = BPF_CORE_READ(ctx, sport);
    evt->dport = BPF_CORE_READ(ctx, dport);
    evt->state = BPF_CORE_READ(ctx, state);

    /* Try to get process context */
    evt->pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(evt->comm, sizeof(evt->comm));

    /* Submit event */
    bpf_ringbuf_submit(evt, 0);

    return 0;
}
