// SPDX-License-Identifier: MIT
/**
 * topproc.h - Top process and zombie monitoring
 *
 * Monitors process resource usage and zombie accumulation.
 * SRE Gap: Process resource hogs, zombie accumulation.
 */

#ifndef __TELEMETRYD_TOPPROC_H__
#define __TELEMETRYD_TOPPROC_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

#define TOPPROC_MAX_TRACKED 2048
#define TOPPROC_TOP_N       5

struct proc_stats {
    unsigned long utime;
    unsigned long stime;
    long rss_pages;
    char name[64];
    int pid;
    char state;
};

/**
 * struct topproc_probe_ctx - Top process probe context
 */
struct topproc_probe_ctx {
    int zombie_threshold;                   /* Zombie count warning (default: 10) */
    int process_count_threshold;            /* Total process warning (default: 1000) */
    double cpu_hog_threshold;               /* Single process CPU % (default: 80.0) */
    unsigned long prev_total_jiffies;       /* Previous total CPU jiffies */
    struct proc_stats prev[TOPPROC_MAX_TRACKED];
    int num_prev;
    int consecutive_hog_count[TOPPROC_MAX_TRACKED]; /* Consecutive high-CPU polls */
};

struct topproc_probe_ctx *topproc_probe_create_ctx(const struct telemetryd_config *cfg);
void topproc_probe_destroy_ctx(struct topproc_probe_ctx *ctx);
int topproc_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_TOPPROC_H__ */
