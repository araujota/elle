// SPDX-License-Identifier: MIT
/**
 * connpool.h - TCP connection pool and socket state monitoring
 *
 * Monitors TCP state distribution and socket resource usage.
 * SRE Gap: Connection pool exhaustion detection.
 */

#ifndef __TELEMETRYD_CONNPOOL_H__
#define __TELEMETRYD_CONNPOOL_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

/**
 * struct connpool_probe_ctx - Connection pool probe context
 */
struct connpool_probe_ctx {
    int close_wait_threshold;       /* CLOSE_WAIT count threshold (default: 50) */
    int time_wait_threshold;        /* TIME_WAIT count threshold (default: 5000) */
    int orphan_threshold;           /* Orphan socket threshold (default: 100) */
    double synrecv_ratio_threshold; /* SYN_RECV/somaxconn ratio threshold (default: 0.8) */
};

struct connpool_probe_ctx *connpool_probe_create_ctx(const struct telemetryd_config *cfg);
void connpool_probe_destroy_ctx(struct connpool_probe_ctx *ctx);
int connpool_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_CONNPOOL_H__ */
