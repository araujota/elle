// SPDX-License-Identifier: MIT
/**
 * psi.c - PSI (Pressure Stall Information) probe
 *
 * Reads from /proc/pressure/cpu, /proc/pressure/memory, /proc/pressure/io
 * to detect resource contention.
 *
 * PSI provides visibility into time spent waiting for resources:
 * - some: at least one task was stalled
 * - full: all non-idle tasks were stalled
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#include "psi.h"
#include "../socket.h"
#include "../json.h"

/* PSI file paths */
#define PSI_CPU_PATH    "/proc/pressure/cpu"
#define PSI_MEMORY_PATH "/proc/pressure/memory"
#define PSI_IO_PATH     "/proc/pressure/io"

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct psi_probe_ctx *psi_probe_create_ctx(const struct probed_config *cfg)
{
    struct psi_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->memory_warning_pct = cfg->memory_warning_pct;
    ctx->memory_critical_pct = cfg->memory_critical_pct;
    ctx->io_warning_pct = cfg->io_warning_pct;
    ctx->io_critical_pct = cfg->io_critical_pct;
    ctx->cpu_warning_pct = cfg->cpu_warning_pct;
    ctx->cpu_critical_pct = cfg->cpu_critical_pct;

    return ctx;
}

void psi_probe_destroy_ctx(struct psi_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * PSI Parsing
 * ========================================================================== */

/**
 * parse_psi_file - Parse a PSI file into an event structure
 * @path: Path to PSI file
 * @resource: Resource name ("cpu", "memory", "io")
 * @evt: Event structure to fill
 *
 * PSI format:
 *   some avg10=0.00 avg60=0.00 avg300=0.00 total=12345
 *   full avg10=0.00 avg60=0.00 avg300=0.00 total=12345
 *
 * Note: CPU only has "some" line, not "full"
 */
static int parse_psi_file(const char *path, const char *resource,
                          struct probed_psi_event *evt)
{
    FILE *f;
    char line[256];
    int found_some = 0;

    f = fopen(path, "r");
    if (!f) {
        /* PSI might not be available (old kernel) */
        return -1;
    }

    memset(evt, 0, sizeof(*evt));
    evt->ts_ns = get_timestamp_ns();
    snprintf(evt->resource, sizeof(evt->resource), "%s", resource);

    while (fgets(line, sizeof(line), f)) {
        double avg10, avg60, avg300;
        unsigned long long total;

        if (sscanf(line, "some avg10=%lf avg60=%lf avg300=%lf total=%llu",
                   &avg10, &avg60, &avg300, &total) == 4) {
            evt->some_avg10 = avg10;
            evt->some_avg60 = avg60;
            evt->some_avg300 = avg300;
            evt->some_total_us = total;
            found_some = 1;
        } else if (sscanf(line, "full avg10=%lf avg60=%lf avg300=%lf total=%llu",
                          &avg10, &avg60, &avg300, &total) == 4) {
            evt->full_avg10 = avg10;
            evt->full_avg60 = avg60;
            evt->full_avg300 = avg300;
            evt->full_total_us = total;
        }
    }

    fclose(f);

    /* CPU only has "some" line, memory/io have both */
    return found_some ? 0 : -1;
}

/* ==========================================================================
 * Threshold Checking
 * ========================================================================== */

static void check_thresholds(struct probed_psi_event *evt,
                              struct psi_probe_ctx *ctx)
{
    double warning_pct, critical_pct;

    /* Select thresholds based on resource */
    if (strcmp(evt->resource, "memory") == 0) {
        warning_pct = ctx->memory_warning_pct;
        critical_pct = ctx->memory_critical_pct;
    } else if (strcmp(evt->resource, "io") == 0) {
        warning_pct = ctx->io_warning_pct;
        critical_pct = ctx->io_critical_pct;
    } else {  /* cpu */
        warning_pct = ctx->cpu_warning_pct;
        critical_pct = ctx->cpu_critical_pct;
    }

    /* Check against full_avg10 (or some_avg10 for CPU) */
    double check_val = evt->full_avg10 > 0 ? evt->full_avg10 : evt->some_avg10;

    evt->warning = (check_val >= warning_pct);
    evt->critical = (check_val >= critical_pct);
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int psi_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct psi_probe_ctx *ctx = (struct psi_probe_ctx *)ctx_ptr;
    struct probed_psi_event evt;
    int ret = 0;

    /* Read and emit CPU PSI */
    if (parse_psi_file(PSI_CPU_PATH, "cpu", &evt) == 0) {
        check_thresholds(&evt, ctx);

        /* Only emit if above warning or periodically for baseline */
        if (evt.warning || evt.critical || evt.some_avg10 > 0) {
            char *json = probed_json_psi_event(&evt);
            if (json) {
                probed_socket_write(sock, json, strlen(json));
                free(json);
            }
        }
    } else {
        ret = -1;
    }

    /* Read and emit Memory PSI */
    if (parse_psi_file(PSI_MEMORY_PATH, "memory", &evt) == 0) {
        check_thresholds(&evt, ctx);

        /* Always emit memory pressure if above warning, or if full > 0 */
        if (evt.warning || evt.critical || evt.full_avg10 > 0 || evt.some_avg10 > 1.0) {
            char *json = probed_json_psi_event(&evt);
            if (json) {
                probed_socket_write(sock, json, strlen(json));
                free(json);
            }
        }
    } else {
        ret = -1;
    }

    /* Read and emit I/O PSI */
    if (parse_psi_file(PSI_IO_PATH, "io", &evt) == 0) {
        check_thresholds(&evt, ctx);

        /* Emit if above warning or significant pressure */
        if (evt.warning || evt.critical || evt.full_avg10 > 0 || evt.some_avg10 > 5.0) {
            char *json = probed_json_psi_event(&evt);
            if (json) {
                probed_socket_write(sock, json, strlen(json));
                free(json);
            }
        }
    } else {
        ret = -1;
    }

    return ret;
}
