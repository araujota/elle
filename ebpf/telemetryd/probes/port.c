// SPDX-License-Identifier: MIT
/**
 * port.c - Port listener probe
 *
 * Monitors /proc/net/tcp and /proc/net/udp for listening ports.
 * Ported from Python PortListenerProbe in src/elle/daemon/telemetry/port_probe.py
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <unistd.h>
#include <netinet/in.h>

#include "port.h"
#include "../json.h"

/* Sensitive ports and their trusted processes */
struct sensitive_port {
    uint16_t port;
    const char *name;
    const char *trusted[8];  /* NULL-terminated */
};

static const struct sensitive_port SENSITIVE_PORTS[] = {
    {22, "ssh", {"sshd", "dropbear", NULL}},
    {80, "http", {"nginx", "apache2", "httpd", "caddy", "traefik", NULL}},
    {443, "https", {"nginx", "apache2", "httpd", "caddy", "traefik", NULL}},
    {3306, "mysql", {"mysqld", "mariadbd", NULL}},
    {5432, "postgres", {"postgres", "postmaster", NULL}},
    {6379, "redis", {"redis-server", "redis", NULL}},
    {27017, "mongodb", {"mongod", NULL}},
    {8080, "http-alt", {"java", "node", "python", "python3", NULL}},
    {0, NULL, {NULL}}  /* Sentinel */
};

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct port_probe_ctx *port_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct port_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->capacity = PORT_MAX_LISTENERS;
    ctx->baseline = calloc(ctx->capacity, sizeof(struct port_listener));
    if (!ctx->baseline) {
        free(ctx);
        return NULL;
    }

    ctx->alert_on_new = false;       /* Don't alert on every new listener */
    ctx->alert_on_sensitive = true;  /* Alert on untrusted sensitive port listeners */
    ctx->initialized = false;

    (void)cfg;
    return ctx;
}

void port_probe_destroy_ctx(struct port_probe_ctx *ctx)
{
    if (ctx) {
        free(ctx->baseline);
        free(ctx);
    }
}

/* ==========================================================================
 * Helpers
 * ========================================================================== */

static const struct sensitive_port *find_sensitive_port(uint16_t port)
{
    for (int i = 0; SENSITIVE_PORTS[i].name; i++) {
        if (SENSITIVE_PORTS[i].port == port)
            return &SENSITIVE_PORTS[i];
    }
    return NULL;
}

static bool is_trusted_process(const struct sensitive_port *sp, const char *comm)
{
    if (!sp || !comm)
        return false;

    for (int i = 0; sp->trusted[i]; i++) {
        if (strcmp(sp->trusted[i], comm) == 0)
            return true;
    }
    return false;
}

static int hex_to_ip(const char *hex, char *out, size_t out_len, bool *is_wildcard)
{
    size_t len = strlen(hex);
    *is_wildcard = false;

    if (len == 8) {
        /* IPv4 */
        unsigned int addr;
        if (sscanf(hex, "%x", &addr) != 1)
            return -1;

        unsigned char *bytes = (unsigned char *)&addr;
        snprintf(out, out_len, "%u.%u.%u.%u",
                 bytes[0], bytes[1], bytes[2], bytes[3]);

        if (addr == 0)
            *is_wildcard = true;
    } else if (len == 32) {
        /* IPv6 - simplified */
        bool all_zero = true;
        for (size_t i = 0; i < 32; i++) {
            if (hex[i] != '0') {
                all_zero = false;
                break;
            }
        }
        if (all_zero) {
            snprintf(out, out_len, "::");
            *is_wildcard = true;
        } else {
            snprintf(out, out_len, "[IPv6]");
        }
    } else {
        return -1;
    }

    return 0;
}

static int get_process_for_inode(unsigned long inode, pid_t *out_pid, char *out_comm, size_t comm_len)
{
    DIR *proc_dir;
    struct dirent *pid_entry;
    char link_target[64];

    *out_pid = 0;
    out_comm[0] = '\0';

    snprintf(link_target, sizeof(link_target), "socket:[%lu]", inode);

    proc_dir = opendir("/proc");
    if (!proc_dir)
        return -1;

    while ((pid_entry = readdir(proc_dir)) != NULL) {
        DIR *fd_dir;
        struct dirent *fd_entry;
        char fd_path[256];
        pid_t pid;

        /* Only look at numeric directories (PIDs) */
        if (pid_entry->d_name[0] < '0' || pid_entry->d_name[0] > '9')
            continue;

        pid = (pid_t)strtol(pid_entry->d_name, NULL, 10);
        snprintf(fd_path, sizeof(fd_path), "/proc/%d/fd", pid);

        fd_dir = opendir(fd_path);
        if (!fd_dir)
            continue;

        while ((fd_entry = readdir(fd_dir)) != NULL) {
            char fd_link[256], resolved[128];
            ssize_t len;

            snprintf(fd_link, sizeof(fd_link), "%s/%s", fd_path, fd_entry->d_name);
            len = readlink(fd_link, resolved, sizeof(resolved) - 1);

            if (len > 0) {
                resolved[len] = '\0';
                if (strcmp(resolved, link_target) == 0) {
                    char comm_path[256];
                    FILE *f;

                    *out_pid = pid;

                    snprintf(comm_path, sizeof(comm_path), "/proc/%d/comm", pid);
                    f = fopen(comm_path, "r");
                    if (f) {
                        if (fgets(out_comm, comm_len, f)) {
                            size_t l = strlen(out_comm);
                            if (l > 0 && out_comm[l-1] == '\n')
                                out_comm[l-1] = '\0';
                        }
                        fclose(f);
                    }

                    closedir(fd_dir);
                    closedir(proc_dir);
                    return 0;
                }
            }
        }

        closedir(fd_dir);
    }

    closedir(proc_dir);
    return -1;
}

/* ==========================================================================
 * /proc/net Parsing
 * ========================================================================== */

static int scan_proc_net(const char *path, int proto,
                         struct port_listener *out, size_t max_out, size_t *count)
{
    FILE *f;
    char line[512];
    int first = 1;

    f = fopen(path, "r");
    if (!f)
        return 0;  /* Not an error if file doesn't exist */

    while (fgets(line, sizeof(line), f) && *count < max_out) {
        int sl;
        char local_addr[64], remote_addr[64];
        unsigned int local_port, state;
        unsigned long inode;
        char addr_str[64];
        bool is_wildcard;

        /* Skip header */
        if (first) {
            first = 0;
            continue;
        }

        /* Parse line:
         * sl local_address:port remote_address:port st tx_queue:rx_queue
         * tr:tm->when retrnsmt uid timeout inode
         */
        if (sscanf(line, "%d: %63[0-9A-Fa-f]:%x %63s %x %*s %*s %*s %*s %lu",
                   &sl, local_addr, &local_port, remote_addr, &state, &inode) != 6)
            continue;

        /* TCP: only LISTEN state (0x0A) */
        if (proto == IPPROTO_TCP && state != 0x0A)
            continue;

        if (local_port == 0)
            continue;

        /* Parse address */
        if (hex_to_ip(local_addr, addr_str, sizeof(addr_str), &is_wildcard) < 0)
            continue;

        /* Skip loopback */
        if (strcmp(addr_str, "127.0.0.1") == 0 || strcmp(addr_str, "::1") == 0)
            continue;

        /* Create listener entry */
        struct port_listener *l = &out[*count];
        l->port = (uint16_t)local_port;
        l->proto = (uint8_t)proto;
        l->inode = (uint32_t)inode;
        l->is_wildcard = is_wildcard;

        /* Get process info */
        get_process_for_inode(inode, &l->pid, l->comm, sizeof(l->comm));
        if (!l->comm[0])
            snprintf(l->comm, sizeof(l->comm), "unknown");

        (*count)++;
    }

    fclose(f);
    return 0;
}

static int get_all_listeners(struct port_listener *out, size_t max_out, size_t *count)
{
    *count = 0;

    scan_proc_net("/proc/net/tcp", IPPROTO_TCP, out, max_out, count);
    scan_proc_net("/proc/net/tcp6", IPPROTO_TCP, out, max_out, count);
    scan_proc_net("/proc/net/udp", IPPROTO_UDP, out, max_out, count);
    scan_proc_net("/proc/net/udp6", IPPROTO_UDP, out, max_out, count);

    return 0;
}

/* ==========================================================================
 * Baseline Management
 * ========================================================================== */

static struct port_listener *find_in_baseline(struct port_probe_ctx *ctx,
                                               uint16_t port, uint8_t proto)
{
    for (size_t i = 0; i < ctx->num_baseline; i++) {
        if (ctx->baseline[i].port == port && ctx->baseline[i].proto == proto)
            return &ctx->baseline[i];
    }
    return NULL;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int port_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct port_probe_ctx *ctx = (struct port_probe_ctx *)ctx_ptr;
    struct port_listener *current;
    size_t current_count;

    current = calloc(PORT_MAX_LISTENERS, sizeof(struct port_listener));
    if (!current)
        return -1;

    if (get_all_listeners(current, PORT_MAX_LISTENERS, &current_count) < 0) {
        free(current);
        return -1;
    }

    if (!ctx->initialized) {
        /* First run - establish baseline */
        memcpy(ctx->baseline, current, current_count * sizeof(struct port_listener));
        ctx->num_baseline = current_count;
        ctx->initialized = true;
        free(current);
        return 0;
    }

    /* Check for new listeners */
    for (size_t i = 0; i < current_count; i++) {
        struct port_listener *l = &current[i];
        struct port_listener *existing = find_in_baseline(ctx, l->port, l->proto);

        if (!existing) {
            /* New listener */
            const struct sensitive_port *sp = find_sensitive_port(l->port);
            bool is_sensitive_alert = false;

            if (sp && !is_trusted_process(sp, l->comm)) {
                is_sensitive_alert = true;
            }

            if (is_sensitive_alert && ctx->alert_on_sensitive) {
                /* Alert: untrusted process on sensitive port */
                struct telem_event evt;
                char message[256];
                char entity[128];
                char *json_str;
                bool emit;

                snprintf(message, sizeof(message),
                         "Unauthorized process on %s port %d: %s (PID %d)",
                         sp->name, l->port, l->comm, l->pid);
                snprintf(entity, sizeof(entity), "port:%d/%s",
                         l->port, l->proto == IPPROTO_TCP ? "tcp" : "udp");

                emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE,
                    TELEM_SEV_ERROR,
                    TELEM_CAT_NET,
                    message,
                    entity,
                    0,
                    &evt);

                if (emit) {
                    json_str = telem_json_event(&evt);
                    if (json_str) {
                        telem_socket_write(sock, json_str, strlen(json_str));
                        free(json_str);
                    }
                }
            } else if (ctx->alert_on_new) {
                /* Alert: new listener */
                struct telem_event evt;
                char message[256];
                char entity[128];
                char *json_str;
                bool emit;

                snprintf(message, sizeof(message),
                         "New listener on port %d/%s: %s (PID %d)",
                         l->port, l->proto == IPPROTO_TCP ? "tcp" : "udp",
                         l->comm, l->pid);
                snprintf(entity, sizeof(entity), "port:%d/%s",
                         l->port, l->proto == IPPROTO_TCP ? "tcp" : "udp");

                emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE,
                    TELEM_SEV_INFO,
                    TELEM_CAT_NET,
                    message,
                    entity,
                    0,
                    &evt);

                if (emit) {
                    json_str = telem_json_event(&evt);
                    if (json_str) {
                        telem_socket_write(sock, json_str, strlen(json_str));
                        free(json_str);
                    }
                }
            }
        }
    }

    /* Update baseline */
    memcpy(ctx->baseline, current, current_count * sizeof(struct port_listener));
    ctx->num_baseline = current_count;

    free(current);
    return 0;
}
