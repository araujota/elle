// SPDX-License-Identifier: GPL-2.0 OR MIT
/**
 * block_io.bpf.c - Block I/O latency tracing for elled-ebpfd
 *
 * Traces block I/O requests from issue to completion:
 *   - Tracks latency for each request
 *   - Reports errors on completion
 *   - Filters by latency threshold to reduce noise
 *
 * Uses hash map for request correlation (dev, sector) -> start time.
 *
 * CO-RE (Compile Once - Run Everywhere) for kernel portability.
 */

#define __BPF__
#include "elled.h"

char LICENSE[] SEC("license") = "Dual MIT/GPL";

/* Maximum tracked concurrent block requests */
#define MAX_TRACKED_REQUESTS 8192

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

/* Request tracking key */
struct block_req_key {
    __u32 dev;
    __u64 sector;
};

/* Request tracking value */
struct block_req_val {
    __u64 start_ts;
    __u32 nr_sector;
    __u32 bytes;
    char rwbs[8];
};

/* Hash map for issue/complete correlation */
struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, MAX_TRACKED_REQUESTS);
    __type(key, struct block_req_key);
    __type(value, struct block_req_val);
} block_req_map SEC(".maps");

/* ==========================================================================
 * Helper: Parse RWBS flags
 *
 * Convert request flags to RWBS string (Read/Write/Barrier/Sync)
 * ========================================================================== */

static __always_inline void parse_rwbs(char *rwbs, __u32 cmd_flags)
{
    int i = 0;

    /* Check common operation types */
    if (cmd_flags & (1 << 0))  /* REQ_OP_WRITE */
        rwbs[i++] = 'W';
    else
        rwbs[i++] = 'R';

    if (cmd_flags & (1 << 12)) /* REQ_SYNC */
        rwbs[i++] = 'S';

    if (cmd_flags & (1 << 13)) /* REQ_META */
        rwbs[i++] = 'M';

    if (cmd_flags & (1 << 3))  /* REQ_OP_DISCARD */
        rwbs[i++] = 'D';

    if (cmd_flags & (1 << 17)) /* REQ_FUA */
        rwbs[i++] = 'F';

    rwbs[i] = '\0';
}

/* ==========================================================================
 * Tracepoint: block_rq_issue
 *
 * Triggered when a block I/O request is issued to the device.
 * Records start time for latency calculation.
 *
 * Tracepoint format (newer kernels):
 *   dev_t dev, sector_t sector, unsigned int nr_sector, unsigned int bytes,
 *   char rwbs[8]
 * ========================================================================== */

SEC("tp/block/block_rq_issue")
int trace_block_rq_issue(struct trace_event_raw_block_rq *ctx)
{
    struct block_req_key key = {};
    struct block_req_val val = {};
    struct elled_config *cfg;
    __u32 cfg_key = 0;

    /* Check if block tracing is enabled */
    cfg = bpf_map_lookup_elem(&config, &cfg_key);
    if (cfg && !cfg->enable_block)
        return 0;

    /* Build correlation key */
    key.dev = BPF_CORE_READ(ctx, dev);
    key.sector = BPF_CORE_READ(ctx, sector);

    /* Record issue time and request info */
    val.start_ts = bpf_ktime_get_ns();
    val.nr_sector = BPF_CORE_READ(ctx, nr_sector);
    val.bytes = BPF_CORE_READ(ctx, bytes);

    /* Read RWBS string from tracepoint */
    bpf_probe_read_kernel_str(val.rwbs, sizeof(val.rwbs),
                              (void *)&ctx->rwbs);

    /* Store for completion correlation */
    bpf_map_update_elem(&block_req_map, &key, &val, BPF_ANY);

    return 0;
}

/* ==========================================================================
 * Tracepoint: block_rq_complete
 *
 * Triggered when a block I/O request completes.
 * Calculates latency and reports errors.
 *
 * Tracepoint format:
 *   dev_t dev, sector_t sector, unsigned int nr_sector, int error,
 *   char rwbs[8]
 * ========================================================================== */

SEC("tp/block/block_rq_complete")
int trace_block_rq_complete(struct trace_event_raw_block_rq_completion *ctx)
{
    struct elled_block_event *evt;
    struct block_req_key key = {};
    struct block_req_val *start_val;
    struct elled_config *cfg;
    __u32 cfg_key = 0;
    __u64 latency_ns;
    __s32 error;

    /* Check if block tracing is enabled */
    cfg = bpf_map_lookup_elem(&config, &cfg_key);
    if (cfg && !cfg->enable_block)
        return 0;

    /* Build correlation key */
    key.dev = BPF_CORE_READ(ctx, dev);
    key.sector = BPF_CORE_READ(ctx, sector);

    /* Look up start time */
    start_val = bpf_map_lookup_elem(&block_req_map, &key);
    if (!start_val) {
        /* No issue event recorded, might have started before tracing */
        return 0;
    }

    /* Calculate latency */
    latency_ns = bpf_ktime_get_ns() - start_val->start_ts;

    /* Read error code */
    error = BPF_CORE_READ(ctx, error);

    /* Check latency threshold (skip low-latency requests unless error) */
    if (cfg && cfg->block_latency_threshold_us > 0 && error == 0) {
        __u64 threshold_ns = (__u64)cfg->block_latency_threshold_us * 1000;
        if (latency_ns < threshold_ns) {
            bpf_map_delete_elem(&block_req_map, &key);
            return 0;
        }
    }

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt) {
        bpf_map_delete_elem(&block_req_map, &key);
        return 0;
    }

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_BLOCK_COMPLETE;

    /* Block device info */
    evt->dev = key.dev;
    evt->sector = key.sector;
    evt->nr_sector = start_val->nr_sector;
    evt->bytes = start_val->bytes;

    /* Completion info */
    evt->error = error;
    evt->latency_ns = latency_ns;
    evt->is_complete = 1;

    /* Copy RWBS from issue data */
    __builtin_memcpy(evt->rwbs, start_val->rwbs, sizeof(evt->rwbs));

    /* Cleanup tracking entry */
    bpf_map_delete_elem(&block_req_map, &key);

    /* Submit event */
    bpf_ringbuf_submit(evt, 0);

    return 0;
}
