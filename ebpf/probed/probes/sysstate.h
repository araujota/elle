// SPDX-License-Identifier: MIT
/**
 * sysstate.h - System state health monitoring
 *
 * Monitors pending reboots, stale kernels, and security updates.
 * SRE Gap: Pending reboot, stale kernel, security updates.
 */

#ifndef __PROBED_SYSSTATE_H__
#define __PROBED_SYSSTATE_H__

#include "../probed.h"
#include "../socket.h"

/**
 * struct sysstate_probe_ctx - System state probe context
 */
struct sysstate_probe_ctx {
    int security_update_warning;    /* Warning threshold for security updates (default: 0) */
    int security_update_error;      /* Error threshold for security updates (default: 10) */
};

struct sysstate_probe_ctx *sysstate_probe_create_ctx(const struct probed_config *cfg);
void sysstate_probe_destroy_ctx(struct sysstate_probe_ctx *ctx);
int sysstate_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_SYSSTATE_H__ */
