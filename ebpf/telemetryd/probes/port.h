// SPDX-License-Identifier: MIT
/**
 * port.h - Port listener probe
 *
 * Monitors /proc/net/tcp and /proc/net/udp for listening ports.
 */

#ifndef __TELEMETRYD_PORT_H__
#define __TELEMETRYD_PORT_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

#define PORT_MAX_LISTENERS 512

/**
 * struct port_listener - Information about a listening socket
 */
struct port_listener {
    uint16_t port;
    uint8_t proto;      /* IPPROTO_TCP or IPPROTO_UDP */
    uint32_t inode;
    pid_t pid;
    char comm[16];
    bool is_wildcard;   /* Bound to 0.0.0.0 or :: */
};

/**
 * struct port_probe_ctx - Port probe context
 */
struct port_probe_ctx {
    struct port_listener *baseline;
    size_t num_baseline;
    size_t capacity;
    bool alert_on_new;          /* Alert on any new listener */
    bool alert_on_sensitive;    /* Alert on untrusted process on sensitive port */
    bool initialized;
};

/**
 * port_probe_create_ctx - Create port probe context
 * @cfg: Telemetryd configuration
 *
 * Returns: Context pointer, or NULL on failure
 */
struct port_probe_ctx *port_probe_create_ctx(const struct telemetryd_config *cfg);

/**
 * port_probe_destroy_ctx - Free port probe context
 * @ctx: Context to free
 */
void port_probe_destroy_ctx(struct port_probe_ctx *ctx);

/**
 * port_probe_run - Run port probe
 * @norm: Normalizer instance
 * @sock: Socket instance
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int port_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_PORT_H__ */
