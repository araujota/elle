// SPDX-License-Identifier: MIT
/**
 * psi.c - PSI (Pressure Stall Information) probe
 *
 * Reads from /proc/pressure/cpu, /proc/pressure/memory, /proc/pressure/io
 * to detect resource contention.
 *
 * Migrated from elled-probed with adaptations for telemetryd normalizer.
 *
 * PSI provides visibility into time spent waiting for resources:
 * - some: at least one task was stalled
 * - full: all non-idle tasks were stalled
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "psi.h"
#include "../json.h"

/* PSI file paths */
#define PSI_CPU_PATH    "/proc/pressure/cpu"
#define PSI_MEMORY_PATH "/proc/pressure/memory"
#define PSI_IO_PATH     "/proc/pressure/io"

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct psi_probe_ctx *psi_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct psi_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    /* Default thresholds from probed config */
    ctx->memory_warning_pct = 10.0;
    ctx->memory_critical_pct = 25.0;
    ctx->io_warning_pct = 20.0;
    ctx->io_critical_pct = 50.0;
    ctx->cpu_warning_pct = 25.0;
    ctx->cpu_critical_pct = 50.0;

    (void)cfg;
    return ctx;
}

void psi_probe_destroy_ctx(struct psi_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * PSI Event Structure
 * ========================================================================== */

struct psi_reading {
    char resource[8];        /* "cpu", "memory", "io" */
    double some_avg10;
    double some_avg60;
    double some_avg300;
    double full_avg10;
    double full_avg60;
    double full_avg300;
    uint64_t some_total_us;
    uint64_t full_total_us;
    bool warning;
    bool critical;
};

/* ==========================================================================
 * PSI Parsing
 * ========================================================================== */

/**
 * parse_psi_file - Parse a PSI file into a reading structure
 * @path: Path to PSI file
 * @resource: Resource name ("cpu", "memory", "io")
 * @reading: Reading structure to fill
 *
 * PSI format:
 *   some avg10=0.00 avg60=0.00 avg300=0.00 total=12345
 *   full avg10=0.00 avg60=0.00 avg300=0.00 total=12345
 *
 * Note: CPU only has "some" line, not "full"
 */
static int parse_psi_file(const char *path, const char *resource,
                          struct psi_reading *reading)
{
    FILE *f;
    char line[256];
    int found_some = 0;

    f = fopen(path, "r");
    if (!f) {
        /* PSI might not be available (old kernel) */
        return -1;
    }

    memset(reading, 0, sizeof(*reading));
    strncpy(reading->resource, resource, sizeof(reading->resource) - 1);

    while (fgets(line, sizeof(line), f)) {
        double avg10, avg60, avg300;
        unsigned long long total;

        if (sscanf(line, "some avg10=%lf avg60=%lf avg300=%lf total=%llu",
                   &avg10, &avg60, &avg300, &total) == 4) {
            reading->some_avg10 = avg10;
            reading->some_avg60 = avg60;
            reading->some_avg300 = avg300;
            reading->some_total_us = total;
            found_some = 1;
        } else if (sscanf(line, "full avg10=%lf avg60=%lf avg300=%lf total=%llu",
                          &avg10, &avg60, &avg300, &total) == 4) {
            reading->full_avg10 = avg10;
            reading->full_avg60 = avg60;
            reading->full_avg300 = avg300;
            reading->full_total_us = total;
        }
    }

    fclose(f);
    return found_some ? 0 : -1;
}

/* ==========================================================================
 * Threshold Checking
 * ========================================================================== */

static void check_thresholds(struct psi_reading *reading, struct psi_probe_ctx *ctx)
{
    double warning_pct, critical_pct;
    double check_val;

    /* Select thresholds based on resource */
    if (strcmp(reading->resource, "memory") == 0) {
        warning_pct = ctx->memory_warning_pct;
        critical_pct = ctx->memory_critical_pct;
    } else if (strcmp(reading->resource, "io") == 0) {
        warning_pct = ctx->io_warning_pct;
        critical_pct = ctx->io_critical_pct;
    } else {  /* cpu */
        warning_pct = ctx->cpu_warning_pct;
        critical_pct = ctx->cpu_critical_pct;
    }

    /* Check against full_avg10 (or some_avg10 for CPU) */
    check_val = reading->full_avg10 > 0 ? reading->full_avg10 : reading->some_avg10;

    reading->warning = (check_val >= warning_pct);
    reading->critical = (check_val >= critical_pct);
}

/* ==========================================================================
 * Event Emission
 * ========================================================================== */

static void emit_psi_event(struct normalizer *norm, struct telem_socket *sock,
                           struct psi_reading *reading)
{
    struct telem_event evt;
    char message[512];
    char entity[64];
    char *json_str;
    bool emit;
    telem_severity_t severity;

    /* Build message */
    snprintf(message, sizeof(message),
             "PSI %s pressure: some=%.1f%% full=%.1f%% (avg10)",
             reading->resource, reading->some_avg10, reading->full_avg10);

    snprintf(entity, sizeof(entity), "resource:%s", reading->resource);

    /* Determine severity and category */
    if (reading->critical) {
        severity = TELEM_SEV_CRITICAL;
    } else if (reading->warning) {
        severity = TELEM_SEV_WARNING;
    } else {
        severity = TELEM_SEV_INFO;
    }

    /* Use OOM category for memory pressure, other for cpu/io */
    telem_category_t category;
    if (strcmp(reading->resource, "memory") == 0) {
        category = TELEM_CAT_OOM;
    } else if (strcmp(reading->resource, "io") == 0) {
        category = TELEM_CAT_DISK;
    } else {
        category = TELEM_CAT_OTHER;
    }

    emit = normalizer_process_prenormalized(norm,
        TELEM_SRC_PROBE,
        severity,
        category,
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

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int psi_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct psi_probe_ctx *ctx = (struct psi_probe_ctx *)ctx_ptr;
    struct psi_reading reading;
    int ret = 0;

    /* Read and emit CPU PSI */
    if (parse_psi_file(PSI_CPU_PATH, "cpu", &reading) == 0) {
        check_thresholds(&reading, ctx);

        /* Only emit if above warning or periodically for baseline */
        if (reading.warning || reading.critical || reading.some_avg10 > 0) {
            emit_psi_event(norm, sock, &reading);
        }
    } else {
        ret = -1;
    }

    /* Read and emit Memory PSI */
    if (parse_psi_file(PSI_MEMORY_PATH, "memory", &reading) == 0) {
        check_thresholds(&reading, ctx);

        /* Always emit memory pressure if above warning, or if full > 0 */
        if (reading.warning || reading.critical ||
            reading.full_avg10 > 0 || reading.some_avg10 > 1.0) {
            emit_psi_event(norm, sock, &reading);
        }
    } else {
        ret = -1;
    }

    /* Read and emit I/O PSI */
    if (parse_psi_file(PSI_IO_PATH, "io", &reading) == 0) {
        check_thresholds(&reading, ctx);

        /* Emit if above warning or significant pressure */
        if (reading.warning || reading.critical ||
            reading.full_avg10 > 0 || reading.some_avg10 > 5.0) {
            emit_psi_event(norm, sock, &reading);
        }
    } else {
        ret = -1;
    }

    return ret;
}
