// SPDX-License-Identifier: MIT
/**
 * dns.h - DNS health probe
 */

#ifndef __PROBED_DNS_H__
#define __PROBED_DNS_H__

#include "../probed.h"
#include "../scheduler.h"

/* DNS probe context */
struct dns_probe_ctx {
    int timeout_ms;
    char test_domains[4][256];
    int test_domain_count;
    char resolvers[4][64];
    int resolver_count;
    /* Latency percentile tracking */
    uint32_t latency_history[100];
    int history_count;
    int history_idx;
    uint64_t total_query_count;
};

/**
 * dns_probe_create_ctx - Create DNS probe context
 * @cfg: Configuration
 *
 * Returns: Allocated context on success, NULL on failure
 */
struct dns_probe_ctx *dns_probe_create_ctx(const struct probed_config *cfg);

/**
 * dns_probe_destroy_ctx - Free DNS probe context
 * @ctx: Context to free
 */
void dns_probe_destroy_ctx(struct dns_probe_ctx *ctx);

/**
 * dns_probe_run - Run DNS probe
 * @sock: Socket for output
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int dns_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_DNS_H__ */
