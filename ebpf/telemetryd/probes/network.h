// SPDX-License-Identifier: MIT
/**
 * network.h - Network interface probe
 *
 * Monitors network interface state and error counters via sysfs.
 */

#ifndef __TELEMETRYD_NETWORK_H__
#define __TELEMETRYD_NETWORK_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

#define NETWORK_MAX_IFACES 32

/**
 * struct iface_errors - Previous error counts for an interface
 */
struct iface_errors {
    char name[32];
    uint64_t rx_errors;
    uint64_t tx_errors;
};

/**
 * struct network_probe_ctx - Network probe context
 */
struct network_probe_ctx {
    int error_threshold;                        /* Threshold for error delta (default: 10) */
    struct iface_errors prev[NETWORK_MAX_IFACES];  /* Previous error counts */
    int num_prev;
};

/**
 * network_probe_create_ctx - Create network probe context
 * @cfg: Telemetryd configuration
 *
 * Returns: Context pointer, or NULL on failure
 */
struct network_probe_ctx *network_probe_create_ctx(const struct telemetryd_config *cfg);

/**
 * network_probe_destroy_ctx - Free network probe context
 * @ctx: Context to free
 */
void network_probe_destroy_ctx(struct network_probe_ctx *ctx);

/**
 * network_probe_run - Run network probe
 * @norm: Normalizer instance
 * @sock: Socket instance
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int network_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_NETWORK_H__ */
