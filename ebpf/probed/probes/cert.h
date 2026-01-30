// SPDX-License-Identifier: MIT
/**
 * cert.h - TLS certificate expiry monitoring
 *
 * Connects to local TLS endpoints and checks certificate expiry.
 * SRE Gap: Certificate expiration.
 */

#ifndef __PROBED_CERT_H__
#define __PROBED_CERT_H__

#include "../probed.h"
#include "../socket.h"

#define CERT_MAX_ENDPOINTS 16
#define CERT_ENDPOINT_LEN  256

/**
 * struct cert_endpoint - A TLS endpoint to monitor
 */
struct cert_endpoint {
    char host[128];
    int port;
};

/**
 * struct cert_probe_ctx - Certificate probe context
 */
struct cert_probe_ctx {
    struct cert_endpoint endpoints[CERT_MAX_ENDPOINTS];
    int num_endpoints;
    int warning_days;           /* Days until warning (default: 30) */
    int critical_days;          /* Days until critical (default: 7) */
    int connect_timeout_sec;    /* Connection timeout (default: 5) */
};

struct cert_probe_ctx *cert_probe_create_ctx(const struct probed_config *cfg);
void cert_probe_destroy_ctx(struct cert_probe_ctx *ctx);
int cert_probe_run(struct probed_socket *sock, void *ctx);

#endif /* __PROBED_CERT_H__ */
