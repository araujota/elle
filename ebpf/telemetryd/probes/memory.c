// SPDX-License-Identifier: MIT
/**
 * memory.c - Memory pressure probe
 *
 * Monitors /proc/meminfo for memory and swap pressure.
 * Ported from Python MemoryProbe in src/elle/daemon/telemetry/probes.py
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "memory.h"
#include "../json.h"

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct memory_probe_ctx *memory_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct memory_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    /* Use defaults if not specified in config */
    ctx->warning_pct = 0.85;
    ctx->critical_pct = 0.95;
    ctx->swap_warning = 0.80;

    (void)cfg;  /* Could add config fields for thresholds */
    return ctx;
}

void memory_probe_destroy_ctx(struct memory_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * Memory Info Parsing
 * ========================================================================== */

struct meminfo {
    uint64_t mem_total_kb;
    uint64_t mem_available_kb;
    uint64_t mem_free_kb;
    uint64_t buffers_kb;
    uint64_t cached_kb;
    uint64_t swap_total_kb;
    uint64_t swap_free_kb;
};

static int parse_meminfo(struct meminfo *info)
{
    FILE *f;
    char line[256];

    memset(info, 0, sizeof(*info));

    f = fopen("/proc/meminfo", "r");
    if (!f)
        return -1;

    while (fgets(line, sizeof(line), f)) {
        uint64_t value;
        char key[64];

        /* Parse "Key: value kB" format */
        if (sscanf(line, "%63[^:]: %lu", key, &value) != 2)
            continue;

        if (strcmp(key, "MemTotal") == 0)
            info->mem_total_kb = value;
        else if (strcmp(key, "MemAvailable") == 0)
            info->mem_available_kb = value;
        else if (strcmp(key, "MemFree") == 0)
            info->mem_free_kb = value;
        else if (strcmp(key, "Buffers") == 0)
            info->buffers_kb = value;
        else if (strcmp(key, "Cached") == 0)
            info->cached_kb = value;
        else if (strcmp(key, "SwapTotal") == 0)
            info->swap_total_kb = value;
        else if (strcmp(key, "SwapFree") == 0)
            info->swap_free_kb = value;
    }

    fclose(f);
    return 0;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int memory_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct memory_probe_ctx *ctx = (struct memory_probe_ctx *)ctx_ptr;
    struct meminfo info;
    struct telem_event evt;
    char message[512];
    char *json_str;
    double mem_pressure, swap_pressure;
    bool emit;

    if (parse_meminfo(&info) < 0)
        return -1;

    /* Calculate memory pressure */
    if (info.mem_total_kb == 0)
        return -1;

    mem_pressure = 1.0 - ((double)info.mem_available_kb / (double)info.mem_total_kb);

    /* Check memory threshold */
    if (mem_pressure > ctx->warning_pct) {
        snprintf(message, sizeof(message),
                 "High memory pressure: %.1f%% used (available: %lu MB, total: %lu MB)",
                 mem_pressure * 100.0,
                 info.mem_available_kb / 1024,
                 info.mem_total_kb / 1024);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE,
            mem_pressure > ctx->critical_pct ? TELEM_SEV_CRITICAL : TELEM_SEV_WARNING,
            TELEM_CAT_OOM,
            message,
            NULL,
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

    /* Check swap pressure */
    if (info.swap_total_kb > 0) {
        uint64_t swap_used_kb = info.swap_total_kb - info.swap_free_kb;
        swap_pressure = (double)swap_used_kb / (double)info.swap_total_kb;

        if (swap_pressure > ctx->swap_warning) {
            snprintf(message, sizeof(message),
                     "High swap usage: %.1f%% used (used: %lu MB, total: %lu MB)",
                     swap_pressure * 100.0,
                     swap_used_kb / 1024,
                     info.swap_total_kb / 1024);

            emit = normalizer_process_prenormalized(norm,
                TELEM_SRC_PROBE,
                TELEM_SEV_WARNING,
                TELEM_CAT_OOM,
                message,
                NULL,
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

    return 0;
}
