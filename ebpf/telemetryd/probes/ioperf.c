// SPDX-License-Identifier: MIT
/**
 * ioperf.c - Disk I/O performance monitoring
 *
 * Monitors /proc/diskstats for I/O latency, throughput, and utilization.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <time.h>

#include "ioperf.h"
#include "../json.h"

struct ioperf_probe_ctx *ioperf_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct ioperf_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->latency_threshold_ms = 50.0;
    ctx->util_threshold_pct = 90.0;
    ctx->queue_depth_threshold = 32;
    ctx->interval_sec = cfg->ioperf_interval > 0 ? cfg->ioperf_interval : 30;
    ctx->num_prev = 0;
    ctx->prev_ts_ns = 0;

    return ctx;
}

void ioperf_probe_destroy_ctx(struct ioperf_probe_ctx *ctx)
{
    free(ctx);
}

static bool is_physical_device(const char *name, int major)
{
    /* Common physical device majors: 8 (sd*), 259 (nvme*) */
    if (major != 8 && major != 259)
        return false;

    /* Skip partitions (ends with digit for sd*, has 'p' + digit for nvme) */
    size_t len = strlen(name);
    if (len == 0)
        return false;

    /* For sd* devices: sda=yes, sda1=no */
    if (name[0] == 's' && name[1] == 'd') {
        return !isdigit((unsigned char)name[len - 1]);
    }

    /* For nvme* devices: nvme0n1=yes, nvme0n1p1=no */
    if (strncmp(name, "nvme", 4) == 0) {
        /* Check if it ends with pN (partition) */
        for (size_t i = 4; i < len; i++) {
            if (name[i] == 'p' && i + 1 < len && isdigit((unsigned char)name[i + 1]))
                return false;
        }
        return true;
    }

    return false;
}

static struct diskstats_snapshot *find_prev_device(struct ioperf_probe_ctx *ctx, const char *name)
{
    for (int i = 0; i < ctx->num_prev; i++) {
        if (strcmp(ctx->prev[i].name, name) == 0)
            return &ctx->prev[i];
    }
    return NULL;
}

int ioperf_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct ioperf_probe_ctx *ctx = (struct ioperf_probe_ctx *)ctx_ptr;
    FILE *f;
    char line[512];
    struct diskstats_snapshot current[IOPERF_MAX_DEVICES];
    int num_current = 0;
    uint64_t now_ns = get_monotonic_ns();

    f = fopen("/proc/diskstats", "r");
    if (!f)
        return -1;

    while (fgets(line, sizeof(line), f) && num_current < IOPERF_MAX_DEVICES) {
        int major, minor;
        char name[32];
        unsigned long rd_ios, rd_merges, rd_sectors, rd_ticks;
        unsigned long wr_ios, wr_merges, wr_sectors, wr_ticks;
        unsigned long ios_in_progress, io_ticks, time_in_queue;

        int n = sscanf(line, " %d %d %31s %lu %lu %lu %lu %lu %lu %lu %lu %lu %lu %lu",
                       &major, &minor, name,
                       &rd_ios, &rd_merges, &rd_sectors, &rd_ticks,
                       &wr_ios, &wr_merges, &wr_sectors, &wr_ticks,
                       &ios_in_progress, &io_ticks, &time_in_queue);

        if (n < 14)
            continue;

        /* Filter to physical devices only */
        if (!is_physical_device(name, major))
            continue;

        struct diskstats_snapshot *snap = &current[num_current];
        snprintf(snap->name, sizeof(snap->name), "%s", name);
        snap->major = major;
        snap->rd_ios = rd_ios;
        snap->rd_ticks = rd_ticks;
        snap->wr_ios = wr_ios;
        snap->wr_ticks = wr_ticks;
        snap->ios_in_progress = ios_in_progress;
        snap->io_ticks = io_ticks;
        num_current++;

        /* Compute deltas if we have previous data */
        if (ctx->prev_ts_ns > 0) {
            struct diskstats_snapshot *prev = find_prev_device(ctx, name);
            if (prev) {
                double interval_ms = (double)(now_ns - ctx->prev_ts_ns) / 1000000.0;
                if (interval_ms <= 0)
                    continue;

                unsigned long d_rd_ios = rd_ios - prev->rd_ios;
                unsigned long d_rd_ticks = rd_ticks - prev->rd_ticks;
                unsigned long d_wr_ios = wr_ios - prev->wr_ios;
                unsigned long d_wr_ticks = wr_ticks - prev->wr_ticks;
                unsigned long d_io_ticks = io_ticks - prev->io_ticks;

                double read_lat = (d_rd_ios > 0) ? (double)d_rd_ticks / (double)d_rd_ios : 0.0;
                double write_lat = (d_wr_ios > 0) ? (double)d_wr_ticks / (double)d_wr_ios : 0.0;
                double io_util = (double)d_io_ticks / interval_ms * 100.0;

                /* Check latency thresholds */
                if (read_lat > ctx->latency_threshold_ms || write_lat > ctx->latency_threshold_ms) {
                    struct telem_event evt;
                    char message[512];
                    char entity[128];
                    char *json_str;
                    bool emit;

                    snprintf(message, sizeof(message),
                             "High I/O latency on %s: read=%.1fms write=%.1fms (threshold: %.0fms)",
                             name, read_lat, write_lat, ctx->latency_threshold_ms);
                    snprintf(entity, sizeof(entity), "disk:%s", name);

                    emit = normalizer_process_prenormalized(norm,
                        TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_DISK,
                        message, entity, 0, &evt);

                    if (emit) {
                        json_str = telem_json_event(&evt);
                        if (json_str) {
                            telem_socket_write(sock, json_str, strlen(json_str));
                            free(json_str);
                        }
                    }
                }

                /* Check utilization threshold */
                if (io_util > ctx->util_threshold_pct) {
                    struct telem_event evt;
                    char message[512];
                    char entity[128];
                    char *json_str;
                    bool emit;

                    snprintf(message, sizeof(message),
                             "High I/O utilization on %s: %.1f%% (threshold: %.0f%%)",
                             name, io_util, ctx->util_threshold_pct);
                    snprintf(entity, sizeof(entity), "disk:%s", name);

                    emit = normalizer_process_prenormalized(norm,
                        TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_DISK,
                        message, entity, 0, &evt);

                    if (emit) {
                        json_str = telem_json_event(&evt);
                        if (json_str) {
                            telem_socket_write(sock, json_str, strlen(json_str));
                            free(json_str);
                        }
                    }
                }

                /* Check queue depth */
                if ((int)ios_in_progress > ctx->queue_depth_threshold) {
                    struct telem_event evt;
                    char message[512];
                    char entity[128];
                    char *json_str;
                    bool emit;

                    snprintf(message, sizeof(message),
                             "High I/O queue depth on %s: %lu (threshold: %d)",
                             name, ios_in_progress, ctx->queue_depth_threshold);
                    snprintf(entity, sizeof(entity), "disk:%s", name);

                    emit = normalizer_process_prenormalized(norm,
                        TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_DISK,
                        message, entity, 0, &evt);

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
    }
    fclose(f);

    /* Save current state */
    memcpy(ctx->prev, current, sizeof(struct diskstats_snapshot) * num_current);
    ctx->num_prev = num_current;
    ctx->prev_ts_ns = now_ns;

    return 0;
}
