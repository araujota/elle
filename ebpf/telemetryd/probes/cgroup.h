// SPDX-License-Identifier: MIT
/**
 * cgroup.h - Cgroup v2 resource limit monitoring
 *
 * Monitors memory, CPU throttling, and PID limits per cgroup.
 * SRE Gap: Container/service resource exhaustion.
 */

#ifndef __TELEMETRYD_CGROUP_H__
#define __TELEMETRYD_CGROUP_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

#define CGROUP_MAX_TRACKED 20

struct cgroup_snapshot {
    char name[128];
    uint64_t mem_current;
    uint64_t mem_max;
    uint64_t oom_kills;
    uint64_t nr_periods;
    uint64_t nr_throttled;
    uint64_t throttled_usec;
    uint64_t pids_current;
    uint64_t pids_max;
};

/**
 * struct cgroup_probe_ctx - Cgroup probe context
 */
struct cgroup_probe_ctx {
    double mem_warning_pct;         /* Memory warning threshold (default: 80.0) */
    double mem_critical_pct;        /* Memory critical threshold (default: 95.0) */
    double throttle_warning_pct;    /* CPU throttle warning (default: 50.0) */
    struct cgroup_snapshot prev[CGROUP_MAX_TRACKED];
    int num_prev;
};

struct cgroup_probe_ctx *cgroup_probe_create_ctx(const struct telemetryd_config *cfg);
void cgroup_probe_destroy_ctx(struct cgroup_probe_ctx *ctx);
int cgroup_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_CGROUP_H__ */
