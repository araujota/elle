// SPDX-License-Identifier: MIT
/**
 * memory.h - Memory pressure probe
 *
 * Monitors /proc/meminfo for memory and swap pressure.
 */

#ifndef __TELEMETRYD_MEMORY_H__
#define __TELEMETRYD_MEMORY_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

/**
 * struct memory_probe_ctx - Memory probe context
 */
struct memory_probe_ctx {
    double warning_pct;     /* Memory warning threshold (default: 0.85) */
    double critical_pct;    /* Memory critical threshold (default: 0.95) */
    double swap_warning;    /* Swap warning threshold (default: 0.80) */
};

/**
 * memory_probe_create_ctx - Create memory probe context
 * @cfg: Telemetryd configuration
 *
 * Returns: Context pointer, or NULL on failure
 */
struct memory_probe_ctx *memory_probe_create_ctx(const struct telemetryd_config *cfg);

/**
 * memory_probe_destroy_ctx - Free memory probe context
 * @ctx: Context to free
 */
void memory_probe_destroy_ctx(struct memory_probe_ctx *ctx);

/**
 * memory_probe_run - Run memory probe
 * @norm: Normalizer instance
 * @sock: Socket instance
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int memory_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_MEMORY_H__ */
