// SPDX-License-Identifier: MIT
/**
 * network.c - Network interface probe
 *
 * Monitors network interface state and error counters via sysfs.
 * Ported from Python NetworkProbe in src/elle/daemon/telemetry/probes.py
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>

#include "network.h"
#include "../json.h"

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct network_probe_ctx *network_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct network_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->error_threshold = 10;  /* Alert on 10+ new errors */

    (void)cfg;
    return ctx;
}

void network_probe_destroy_ctx(struct network_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * Helpers
 * ========================================================================== */

static uint64_t read_sysfs_uint(const char *path)
{
    FILE *f;
    uint64_t value = 0;

    f = fopen(path, "r");
    if (f) {
        if (fscanf(f, "%lu", &value) != 1)
            value = 0;
        fclose(f);
    }
    return value;
}

static int read_sysfs_str(const char *path, char *buf, size_t len)
{
    FILE *f;
    int ret = -1;

    f = fopen(path, "r");
    if (f) {
        if (fgets(buf, len, f)) {
            /* Remove trailing newline */
            size_t l = strlen(buf);
            if (l > 0 && buf[l-1] == '\n')
                buf[l-1] = '\0';
            ret = 0;
        }
        fclose(f);
    }
    return ret;
}

static struct iface_errors *find_prev_errors(struct network_probe_ctx *ctx, const char *name)
{
    for (int i = 0; i < ctx->num_prev; i++) {
        if (strcmp(ctx->prev[i].name, name) == 0)
            return &ctx->prev[i];
    }
    return NULL;
}

static struct iface_errors *get_or_create_prev(struct network_probe_ctx *ctx, const char *name)
{
    struct iface_errors *prev = find_prev_errors(ctx, name);
    if (prev)
        return prev;

    if (ctx->num_prev >= NETWORK_MAX_IFACES)
        return NULL;

    prev = &ctx->prev[ctx->num_prev++];
    strncpy(prev->name, name, sizeof(prev->name) - 1);
    prev->rx_errors = 0;
    prev->tx_errors = 0;
    return prev;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int network_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct network_probe_ctx *ctx = (struct network_probe_ctx *)ctx_ptr;
    DIR *net_dir;
    struct dirent *entry;
    char path[256];

    net_dir = opendir("/sys/class/net");
    if (!net_dir)
        return -1;

    while ((entry = readdir(net_dir)) != NULL) {
        char operstate[32] = {0};
        uint64_t rx_errors = 0, tx_errors = 0;
        struct iface_errors *prev;
        const char *name = entry->d_name;

        /* Skip . and .. */
        if (name[0] == '.')
            continue;

        /* Skip loopback */
        if (strcmp(name, "lo") == 0)
            continue;

        /* Read operstate */
        snprintf(path, sizeof(path), "/sys/class/net/%s/operstate", name);
        if (read_sysfs_str(path, operstate, sizeof(operstate)) < 0)
            continue;

        /* Read error counters */
        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/rx_errors", name);
        rx_errors = read_sysfs_uint(path);

        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/tx_errors", name);
        tx_errors = read_sysfs_uint(path);

        /* Check for link down */
        if (strcmp(operstate, "down") == 0) {
            struct telem_event evt;
            char message[256];
            char entity[128];
            char *json_str;
            bool emit;

            snprintf(message, sizeof(message), "Network interface %s is down", name);
            snprintf(entity, sizeof(entity), "interface:%s", name);

            emit = normalizer_process_prenormalized(norm,
                TELEM_SRC_PROBE,
                TELEM_SEV_WARNING,
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

        /* Check for new errors */
        prev = get_or_create_prev(ctx, name);
        if (prev) {
            int64_t new_errors = (int64_t)(rx_errors - prev->rx_errors) +
                                 (int64_t)(tx_errors - prev->tx_errors);

            if (new_errors > ctx->error_threshold) {
                struct telem_event evt;
                char message[256];
                char entity[128];
                char *json_str;
                bool emit;

                snprintf(message, sizeof(message),
                         "Network errors on %s: %ld new errors (rx: %lu, tx: %lu)",
                         name, new_errors, rx_errors, tx_errors);
                snprintf(entity, sizeof(entity), "interface:%s", name);

                emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE,
                    TELEM_SEV_WARNING,
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

            /* Update previous counters */
            prev->rx_errors = rx_errors;
            prev->tx_errors = tx_errors;
        }
    }

    closedir(net_dir);
    return 0;
}
