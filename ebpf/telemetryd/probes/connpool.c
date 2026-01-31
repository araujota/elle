// SPDX-License-Identifier: MIT
/**
 * connpool.c - TCP connection pool and socket state monitoring
 *
 * Monitors /proc/net/tcp for TCP state distribution and
 * /proc/net/sockstat for socket resource usage.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "connpool.h"
#include "../json.h"

/* TCP states from /proc/net/tcp (hex) */
#define TCP_ESTABLISHED 0x01
#define TCP_SYN_RECV    0x03
#define TCP_FIN_WAIT2   0x05
#define TCP_TIME_WAIT   0x06
#define TCP_CLOSE_WAIT  0x08

struct connpool_probe_ctx *connpool_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct connpool_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->close_wait_threshold = 50;
    ctx->time_wait_threshold = 5000;
    ctx->orphan_threshold = 100;
    ctx->synrecv_ratio_threshold = 0.8;

    (void)cfg;
    return ctx;
}

void connpool_probe_destroy_ctx(struct connpool_probe_ctx *ctx)
{
    free(ctx);
}

static int read_sysctl_int(const char *path)
{
    FILE *f;
    int value = 0;

    f = fopen(path, "r");
    if (f) {
        if (fscanf(f, "%d", &value) != 1)
            value = 0;
        fclose(f);
    }
    return value;
}

static int parse_sockstat(int *tcp_inuse, int *tcp_orphan, int *tcp_tw, int *tcp_alloc)
{
    FILE *f;
    char line[256];

    *tcp_inuse = 0;
    *tcp_orphan = 0;
    *tcp_tw = 0;
    *tcp_alloc = 0;

    f = fopen("/proc/net/sockstat", "r");
    if (!f)
        return -1;

    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, "TCP:", 4) == 0) {
            sscanf(line, "TCP: inuse %d orphan %d tw %d alloc %d",
                   tcp_inuse, tcp_orphan, tcp_tw, tcp_alloc);
            break;
        }
    }

    fclose(f);
    return 0;
}

int connpool_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct connpool_probe_ctx *ctx = (struct connpool_probe_ctx *)ctx_ptr;
    FILE *f;
    char line[512];
    int tcp_established = 0, tcp_time_wait = 0, tcp_close_wait = 0;
    int tcp_syn_recv = 0, tcp_fin_wait2 = 0;
    int somaxconn, tcp_orphan, tcp_inuse, tcp_tw_sock, tcp_alloc;
    int total_sockets;

    /* Parse /proc/net/tcp for state counts */
    f = fopen("/proc/net/tcp", "r");
    if (!f)
        return -1;

    /* Skip header line */
    if (!fgets(line, sizeof(line), f)) {
        fclose(f);
        return -1;
    }

    while (fgets(line, sizeof(line), f)) {
        unsigned int state;
        /* Format: sl local_address rem_address st ... */
        /* The state is the 4th field (after 3 space-separated hex fields) */
        if (sscanf(line, " %*d: %*s %*s %02X", &state) == 1) {
            switch (state) {
            case TCP_ESTABLISHED: tcp_established++; break;
            case TCP_SYN_RECV:    tcp_syn_recv++;    break;
            case TCP_FIN_WAIT2:   tcp_fin_wait2++;   break;
            case TCP_TIME_WAIT:   tcp_time_wait++;   break;
            case TCP_CLOSE_WAIT:  tcp_close_wait++;  break;
            default: break;
            }
        }
    }
    fclose(f);

    /* Read somaxconn */
    somaxconn = read_sysctl_int("/proc/sys/net/core/somaxconn");
    if (somaxconn <= 0)
        somaxconn = 4096;  /* Default fallback */

    /* Parse sockstat for totals */
    parse_sockstat(&tcp_inuse, &tcp_orphan, &tcp_tw_sock, &tcp_alloc);
    total_sockets = tcp_alloc;

    /* Check emit conditions */
    if (tcp_close_wait > ctx->close_wait_threshold) {
        struct telem_event evt;
        char message[512];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "Connection leak suspected: %d CLOSE_WAIT sockets (threshold: %d)",
                 tcp_close_wait, ctx->close_wait_threshold);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_NET,
            message, "system:tcp_connpool", 0, &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    if (tcp_time_wait > ctx->time_wait_threshold) {
        struct telem_event evt;
        char message[512];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "Connection churn: %d TIME_WAIT sockets (threshold: %d)",
                 tcp_time_wait, ctx->time_wait_threshold);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_NET,
            message, "system:tcp_connpool", 0, &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    if (tcp_orphan > ctx->orphan_threshold) {
        struct telem_event evt;
        char message[512];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "Socket resource leak: %d orphan sockets (threshold: %d)",
                 tcp_orphan, ctx->orphan_threshold);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_NET,
            message, "system:tcp_connpool", 0, &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    if (somaxconn > 0 && tcp_syn_recv > (int)(somaxconn * ctx->synrecv_ratio_threshold)) {
        struct telem_event evt;
        char message[512];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "SYN backlog pressure: %d SYN_RECV vs somaxconn %d (%.0f%%)",
                 tcp_syn_recv, somaxconn,
                 (double)tcp_syn_recv / somaxconn * 100.0);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_NET,
            message, "system:tcp_connpool", 0, &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    (void)tcp_established;
    (void)tcp_fin_wait2;
    (void)total_sockets;

    return 0;
}
