// SPDX-License-Identifier: MIT
/**
 * ioperf.h - Disk I/O performance monitoring
 *
 * Monitors I/O latency, throughput, and utilization from /proc/diskstats.
 * SRE Gap: I/O latency and throughput degradation.
 */

#ifndef __TELEMETRYD_IOPERF_H__
#define __TELEMETRYD_IOPERF_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

#define IOPERF_MAX_DEVICES 32

struct diskstats_snapshot {
    char name[32];
    unsigned long rd_ios;
    unsigned long rd_ticks;
    unsigned long wr_ios;
    unsigned long wr_ticks;
    unsigned long ios_in_progress;
    unsigned long io_ticks;
    int major;
};

/**
 * struct ioperf_probe_ctx - I/O performance probe context
 */
struct ioperf_probe_ctx {
    double latency_threshold_ms;          /* Latency warning (default: 50.0) */
    double util_threshold_pct;            /* Utilization warning (default: 90.0) */
    int queue_depth_threshold;            /* Queue depth warning (default: 32) */
    int interval_sec;                     /* Probe interval for delta calc */
    struct diskstats_snapshot prev[IOPERF_MAX_DEVICES];
    int num_prev;
    uint64_t prev_ts_ns;                  /* Previous poll timestamp */
};

struct ioperf_probe_ctx *ioperf_probe_create_ctx(const struct telemetryd_config *cfg);
void ioperf_probe_destroy_ctx(struct ioperf_probe_ctx *ctx);
int ioperf_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_IOPERF_H__ */
