// SPDX-License-Identifier: MIT
/**
 * thermal.h - Thermal monitoring probe
 *
 * Monitors CPU and system temperatures via sysfs thermal zones.
 */

#ifndef __TELEMETRYD_THERMAL_H__
#define __TELEMETRYD_THERMAL_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

/**
 * struct thermal_probe_ctx - Thermal probe context
 */
struct thermal_probe_ctx {
    int warning_celsius;    /* Temperature warning threshold (default: 80) */
    /* CPU frequency tracking */
    uint64_t scaling_cur_freq;
    uint64_t scaling_max_freq;
};

/**
 * thermal_probe_create_ctx - Create thermal probe context
 * @cfg: Telemetryd configuration
 *
 * Returns: Context pointer, or NULL on failure
 */
struct thermal_probe_ctx *thermal_probe_create_ctx(const struct telemetryd_config *cfg);

/**
 * thermal_probe_destroy_ctx - Free thermal probe context
 * @ctx: Context to free
 */
void thermal_probe_destroy_ctx(struct thermal_probe_ctx *ctx);

/**
 * thermal_probe_run - Run thermal probe
 * @norm: Normalizer instance
 * @sock: Socket instance
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int thermal_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_THERMAL_H__ */
