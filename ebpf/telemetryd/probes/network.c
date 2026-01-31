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
    snprintf(prev->name, sizeof(prev->name), "%s", name);
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
    char path[512];

    net_dir = opendir("/sys/class/net");
    if (!net_dir)
        return -1;

    while ((entry = readdir(net_dir)) != NULL) {
        char operstate[32] = {0};
        uint64_t rx_errors = 0, tx_errors = 0;
        uint64_t rx_dropped = 0, tx_dropped = 0, rx_packets = 0, tx_packets = 0;
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

        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/rx_dropped", name);
        rx_dropped = read_sysfs_uint(path);

        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/tx_dropped", name);
        tx_dropped = read_sysfs_uint(path);

        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/rx_packets", name);
        rx_packets = read_sysfs_uint(path);

        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/tx_packets", name);
        tx_packets = read_sysfs_uint(path);

        /* Check for link down */
        if (strcmp(operstate, "down") == 0) {
            struct telem_event evt;
            char message[512];
            char entity[280];
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
                char message[512];
                char entity[280];
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

            /* Check for dropped packets */
            if (prev && (rx_dropped > prev->rx_dropped || tx_dropped > prev->tx_dropped)) {
                struct telem_event drop_evt;
                char drop_msg[512];
                char drop_entity[280];
                char *drop_json;
                bool drop_emit;

                snprintf(drop_msg, sizeof(drop_msg),
                         "Dropped packets on %s: rx_dropped=%lu tx_dropped=%lu (rx_pkts=%lu tx_pkts=%lu)",
                         name, rx_dropped, tx_dropped, rx_packets, tx_packets);
                snprintf(drop_entity, sizeof(drop_entity), "interface:%s", name);

                drop_emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE,
                    TELEM_SEV_WARNING,
                    TELEM_CAT_NET,
                    drop_msg,
                    drop_entity,
                    0,
                    &drop_evt);

                if (drop_emit) {
                    drop_json = telem_json_event(&drop_evt);
                    if (drop_json) {
                        telem_socket_write(sock, drop_json, strlen(drop_json));
                        free(drop_json);
                    }
                }
            }

            /* Update previous counters */
            prev->rx_errors = rx_errors;
            prev->tx_errors = tx_errors;
            prev->rx_dropped = rx_dropped;
            prev->tx_dropped = tx_dropped;
            prev->rx_packets = rx_packets;
            prev->tx_packets = tx_packets;
        }
    }

    /* ======================================================================
     * TCP Retransmission Rate from /proc/net/snmp
     * ====================================================================== */
    {
        FILE *snmp_fp;
        char snmp_line[1024];
        uint64_t retrans_segs = 0, out_segs = 0;
        bool found_tcp_data = false;
        int tcp_header_seen = 0;

        snmp_fp = fopen("/proc/net/snmp", "r");
        if (snmp_fp) {
            while (fgets(snmp_line, sizeof(snmp_line), snmp_fp)) {
                if (strncmp(snmp_line, "Tcp:", 4) == 0) {
                    tcp_header_seen++;
                    /* The second "Tcp:" line contains the data values */
                    if (tcp_header_seen == 2) {
                        /* Parse fields: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens
                         * PassiveOpens AttemptFails EstabResets CurrEstab InSegs
                         * OutSegs(11) RetransSegs(12) InErrs OutRsts InCsumErrors */
                        uint64_t fields[15] = {0};
                        int nfields = 0;
                        char *tok = snmp_line + 4; /* Skip "Tcp:" */
                        char *saveptr;

                        tok = strtok_r(tok, " \t\n", &saveptr);
                        while (tok && nfields < 15) {
                            fields[nfields++] = strtoull(tok, NULL, 10);
                            tok = strtok_r(NULL, " \t\n", &saveptr);
                        }

                        if (nfields >= 12) {
                            out_segs = fields[10];      /* OutSegs (field 11, 0-indexed 10) */
                            retrans_segs = fields[11];  /* RetransSegs (field 12, 0-indexed 11) */
                            found_tcp_data = true;
                        }
                        break;
                    }
                }
            }
            fclose(snmp_fp);

            if (found_tcp_data && ctx->has_prev_snmp) {
                uint64_t delta_retrans = retrans_segs - ctx->prev_retrans_segs;
                uint64_t delta_out = out_segs - ctx->prev_out_segs;

                if (delta_out > 0) {
                    double retrans_rate = (double)delta_retrans / (double)delta_out;

                    if (retrans_rate > 0.01) {
                        struct telem_event retrans_evt;
                        char retrans_msg[256];
                        char *retrans_json;
                        bool retrans_emit;

                        snprintf(retrans_msg, sizeof(retrans_msg),
                                 "TCP retransmission rate %.2f%% (retrans=%lu out=%lu)",
                                 retrans_rate * 100.0, delta_retrans, delta_out);

                        retrans_emit = normalizer_process_prenormalized(norm,
                            TELEM_SRC_PROBE,
                            TELEM_SEV_WARNING,
                            TELEM_CAT_NET,
                            retrans_msg,
                            "tcp:retransmit",
                            0,
                            &retrans_evt);

                        if (retrans_emit) {
                            retrans_json = telem_json_event(&retrans_evt);
                            if (retrans_json) {
                                telem_socket_write(sock, retrans_json, strlen(retrans_json));
                                free(retrans_json);
                            }
                        }
                    }
                }
            }

            if (found_tcp_data) {
                ctx->prev_retrans_segs = retrans_segs;
                ctx->prev_out_segs = out_segs;
                ctx->has_prev_snmp = true;
            }
        }
    }

    closedir(net_dir);
    return 0;
}
