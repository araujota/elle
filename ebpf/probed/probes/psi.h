// SPDX-License-Identifier: MIT
/**
 * psi.h - PSI (Pressure Stall Information) probe
 */

#ifndef __PROBED_PSI_H__
#define __PROBED_PSI_H__

#include "../probed.h"
#include "../scheduler.h"

/* PSI probe context */
struct psi_probe_ctx {
    double memory_warning_pct;
    double memory_critical_pct;
    double io_warning_pct;
    double io_critical_pct;
    double cpu_warning_pct;
    double cpu_critical_pct;
};

/**
 * psi_probe_create_ctx - Create PSI probe context
 * @cfg: Configuration
 *
 * Returns: Allocated context on success, NULL on failure
 */
struct psi_probe_ctx *psi_probe_create_ctx(const struct probed_config *cfg);

/**
 * psi_probe_destroy_ctx - Free PSI probe context
 * @ctx: Context to free
 */
void psi_probe_destroy_ctx(struct psi_probe_ctx *ctx);

/**
 * psi_probe_run - Run PSI probe
 * @sock: Socket for output
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int psi_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_PSI_H__ */
