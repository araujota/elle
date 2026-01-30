// SPDX-License-Identifier: MIT
/**
 * auth.h - Authentication failure aggregation probe
 */

#ifndef __PROBED_AUTH_H__
#define __PROBED_AUTH_H__

#include "../probed.h"
#include "../scheduler.h"

/* Auth probe context */
struct auth_probe_ctx {
    int brute_force_threshold;

    /* Tracking state */
    struct auth_failure_entry {
        char source[64];
        char user[32];
        uint64_t ts_ns;
    } *failures;
    int failures_count;
    int failures_capacity;
    int failures_idx;  /* Circular buffer index */

    /* Last aggregation result */
    uint32_t last_failed_logins;
    uint32_t last_failed_sudo;
    uint32_t last_failed_ssh;

    /* Per-source IP tracking for brute force detection */
    struct auth_source_entry {
        char ip[64];
        uint32_t failure_count;
        uint64_t first_seen_ns;
        uint64_t last_seen_ns;
    } source_tracker[256];
    int source_tracker_count;
    bool brute_force_detected;
    char brute_force_source[64];
    uint32_t brute_force_count;
};

/**
 * auth_probe_create_ctx - Create auth probe context
 * @cfg: Configuration
 *
 * Returns: Allocated context on success, NULL on failure
 */
struct auth_probe_ctx *auth_probe_create_ctx(const struct probed_config *cfg);

/**
 * auth_probe_destroy_ctx - Free auth probe context
 * @ctx: Context to free
 */
void auth_probe_destroy_ctx(struct auth_probe_ctx *ctx);

/**
 * auth_probe_run - Run auth probe
 * @sock: Socket for output
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int auth_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_AUTH_H__ */
