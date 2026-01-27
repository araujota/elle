// SPDX-License-Identifier: MIT
/**
 * systemd.h - Systemd unit state probe
 */

#ifndef __PROBED_SYSTEMD_H__
#define __PROBED_SYSTEMD_H__

#include "../probed.h"
#include "../scheduler.h"

/* Systemd probe context */
struct systemd_probe_ctx {
    int crashloop_threshold;
    int crashloop_window_sec;

    /* State tracking for crash loop detection */
    struct unit_restart_state {
        char unit[128];
        uint64_t restart_times[10];  /* Circular buffer of restart timestamps */
        int restart_count;
        int restart_idx;
    } *tracked_units;
    int tracked_units_count;
    int tracked_units_capacity;
};

/**
 * systemd_probe_create_ctx - Create systemd probe context
 * @cfg: Configuration
 *
 * Returns: Allocated context on success, NULL on failure
 */
struct systemd_probe_ctx *systemd_probe_create_ctx(const struct probed_config *cfg);

/**
 * systemd_probe_destroy_ctx - Free systemd probe context
 * @ctx: Context to free
 */
void systemd_probe_destroy_ctx(struct systemd_probe_ctx *ctx);

/**
 * systemd_probe_run - Run systemd probe
 * @sock: Socket for output
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int systemd_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_SYSTEMD_H__ */
