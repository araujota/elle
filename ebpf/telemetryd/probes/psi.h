// SPDX-License-Identifier: MIT
/**
 * psi.h - PSI (Pressure Stall Information) probe
 *
 * Reads from /proc/pressure/cpu, /proc/pressure/memory, /proc/pressure/io
 * to detect resource contention.
 *
 * Migrated from elled-probed.
 */

#ifndef __TELEMETRYD_PSI_H__
#define __TELEMETRYD_PSI_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

/**
 * struct psi_probe_ctx - PSI probe context
 */
struct psi_probe_ctx {
    double memory_warning_pct;   /* Default: 10.0 */
    double memory_critical_pct;  /* Default: 25.0 */
    double io_warning_pct;       /* Default: 20.0 */
    double io_critical_pct;      /* Default: 50.0 */
    double cpu_warning_pct;      /* Default: 25.0 */
    double cpu_critical_pct;     /* Default: 50.0 */
};

/**
 * psi_probe_create_ctx - Create PSI probe context
 * @cfg: Telemetryd configuration
 *
 * Returns: Context pointer, or NULL on failure
 */
struct psi_probe_ctx *psi_probe_create_ctx(const struct telemetryd_config *cfg);

/**
 * psi_probe_destroy_ctx - Free PSI probe context
 * @ctx: Context to free
 */
void psi_probe_destroy_ctx(struct psi_probe_ctx *ctx);

/**
 * psi_probe_run - Run PSI probe
 * @norm: Normalizer instance
 * @sock: Socket instance
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int psi_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_PSI_H__ */
