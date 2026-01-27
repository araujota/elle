// SPDX-License-Identifier: MIT
/**
 * conntrack.h - Conntrack statistics probe
 */

#ifndef __PROBED_CONNTRACK_H__
#define __PROBED_CONNTRACK_H__

#include "../probed.h"
#include "../scheduler.h"

/* Conntrack probe context */
struct conntrack_probe_ctx {
    double warning_pct;
    double critical_pct;
};

/**
 * conntrack_probe_create_ctx - Create conntrack probe context
 * @cfg: Configuration
 *
 * Returns: Allocated context on success, NULL on failure
 */
struct conntrack_probe_ctx *conntrack_probe_create_ctx(const struct probed_config *cfg);

/**
 * conntrack_probe_destroy_ctx - Free conntrack probe context
 * @ctx: Context to free
 */
void conntrack_probe_destroy_ctx(struct conntrack_probe_ctx *ctx);

/**
 * conntrack_probe_run - Run conntrack probe
 * @sock: Socket for output
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int conntrack_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_CONNTRACK_H__ */
