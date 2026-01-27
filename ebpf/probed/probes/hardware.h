// SPDX-License-Identifier: MIT
/**
 * hardware.h - Hardware error probe
 */

#ifndef __PROBED_HARDWARE_H__
#define __PROBED_HARDWARE_H__

#include "../probed.h"
#include "../scheduler.h"

/* Hardware probe context */
struct hardware_probe_ctx {
    /* Previous error counts for delta calculation */
    struct edac_error_state {
        char mc[16];
        char csrow[16];
        uint64_t ce_count;
        uint64_t ue_count;
    } *edac_state;
    int edac_state_count;
    int edac_state_capacity;
};

/**
 * hardware_probe_create_ctx - Create hardware probe context
 * @cfg: Configuration
 *
 * Returns: Allocated context on success, NULL on failure
 */
struct hardware_probe_ctx *hardware_probe_create_ctx(const struct probed_config *cfg);

/**
 * hardware_probe_destroy_ctx - Free hardware probe context
 * @ctx: Context to free
 */
void hardware_probe_destroy_ctx(struct hardware_probe_ctx *ctx);

/**
 * hardware_probe_run - Run hardware probe
 * @sock: Socket for output
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int hardware_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_HARDWARE_H__ */
