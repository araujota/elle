// SPDX-License-Identifier: MIT
/**
 * cgroup.c - Cgroup v2 resource limit monitoring
 *
 * Scans cgroups under system.slice and user.slice for resource pressure.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>
#include <limits.h>

#include "cgroup.h"
#include "../json.h"

struct cgroup_probe_ctx *cgroup_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct cgroup_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->mem_warning_pct = 80.0;
    ctx->mem_critical_pct = 95.0;
    ctx->throttle_warning_pct = 50.0;
    ctx->num_prev = 0;

    (void)cfg;
    return ctx;
}

void cgroup_probe_destroy_ctx(struct cgroup_probe_ctx *ctx)
{
    free(ctx);
}

static uint64_t read_cgroup_uint(const char *path)
{
    FILE *f;
    char buf[64];
    uint64_t value;

    f = fopen(path, "r");
    if (!f)
        return 0;

    if (!fgets(buf, sizeof(buf), f)) {
        fclose(f);
        return 0;
    }
    fclose(f);

    /* "max" means unlimited */
    if (strncmp(buf, "max", 3) == 0)
        return UINT64_MAX;

    value = strtoull(buf, NULL, 10);
    return value;
}

static uint64_t read_cgroup_key(const char *path, const char *key)
{
    FILE *f;
    char line[256];
    size_t key_len = strlen(key);

    f = fopen(path, "r");
    if (!f)
        return 0;

    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, key, key_len) == 0 && line[key_len] == ' ') {
            uint64_t value = strtoull(line + key_len + 1, NULL, 10);
            fclose(f);
            return value;
        }
    }

    fclose(f);
    return 0;
}

static struct cgroup_snapshot *find_prev_cgroup(struct cgroup_probe_ctx *ctx, const char *name)
{
    for (int i = 0; i < ctx->num_prev; i++) {
        if (strcmp(ctx->prev[i].name, name) == 0)
            return &ctx->prev[i];
    }
    return NULL;
}

static int scan_cgroup_slice(const char *slice_path, struct cgroup_snapshot *snaps,
                              int *count, int max_count)
{
    DIR *dir;
    struct dirent *entry;

    dir = opendir(slice_path);
    if (!dir)
        return 0;

    while ((entry = readdir(dir)) != NULL && *count < max_count) {
        char path[512];
        struct stat st;
        struct cgroup_snapshot *snap;

        if (entry->d_name[0] == '.')
            continue;

        snprintf(path, sizeof(path), "%s/%s", slice_path, entry->d_name);
        if (stat(path, &st) < 0 || !S_ISDIR(st.st_mode))
            continue;

        snap = &snaps[*count];
        strncpy(snap->name, entry->d_name, sizeof(snap->name) - 1);
        snap->name[sizeof(snap->name) - 1] = '\0';

        /* Read memory stats */
        {
            char mem_path[512];
            snprintf(mem_path, sizeof(mem_path), "%s/memory.current", path);
            snap->mem_current = read_cgroup_uint(mem_path);

            snprintf(mem_path, sizeof(mem_path), "%s/memory.max", path);
            snap->mem_max = read_cgroup_uint(mem_path);

            snprintf(mem_path, sizeof(mem_path), "%s/memory.events", path);
            snap->oom_kills = read_cgroup_key(mem_path, "oom_kill");
        }

        /* Read CPU stats */
        {
            char cpu_path[512];
            snprintf(cpu_path, sizeof(cpu_path), "%s/cpu.stat", path);
            snap->nr_periods = read_cgroup_key(cpu_path, "nr_periods");
            snap->nr_throttled = read_cgroup_key(cpu_path, "nr_throttled");
            snap->throttled_usec = read_cgroup_key(cpu_path, "throttled_usec");
        }

        /* Read PID stats */
        {
            char pids_path[512];
            snprintf(pids_path, sizeof(pids_path), "%s/pids.current", path);
            snap->pids_current = read_cgroup_uint(pids_path);

            snprintf(pids_path, sizeof(pids_path), "%s/pids.max", path);
            snap->pids_max = read_cgroup_uint(pids_path);
        }

        /* Only count if memory info was readable */
        if (snap->mem_current > 0)
            (*count)++;
    }

    closedir(dir);
    return 0;
}

int cgroup_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct cgroup_probe_ctx *ctx = (struct cgroup_probe_ctx *)ctx_ptr;
    struct cgroup_snapshot current[CGROUP_MAX_TRACKED];
    int num_current = 0;

    /* Scan system.slice and user.slice */
    scan_cgroup_slice("/sys/fs/cgroup/system.slice", current, &num_current, CGROUP_MAX_TRACKED);
    scan_cgroup_slice("/sys/fs/cgroup/user.slice", current, &num_current, CGROUP_MAX_TRACKED);

    for (int i = 0; i < num_current; i++) {
        struct cgroup_snapshot *snap = &current[i];
        double mem_pct = 0.0;
        double throttle_pct = 0.0;

        /* Calculate memory percentage */
        if (snap->mem_max > 0 && snap->mem_max != UINT64_MAX) {
            mem_pct = (double)snap->mem_current / (double)snap->mem_max * 100.0;
        }

        /* Calculate CPU throttle percentage */
        if (snap->nr_periods > 0) {
            throttle_pct = (double)snap->nr_throttled / (double)snap->nr_periods * 100.0;
        }

        /* Check memory thresholds */
        if (mem_pct > ctx->mem_critical_pct) {
            struct telem_event evt;
            char message[512];
            char entity[128];
            char *json_str;
            bool emit;

            snprintf(message, sizeof(message),
                     "CRITICAL: Cgroup %s memory at %.1f%% (%lu/%lu MB)",
                     snap->name, mem_pct,
                     (unsigned long)(snap->mem_current / (1024UL * 1024UL)),
                     (unsigned long)(snap->mem_max / (1024UL * 1024UL)));
            snprintf(entity, sizeof(entity), "cgroup:%s", snap->name);

            emit = normalizer_process_prenormalized(norm,
                TELEM_SRC_PROBE, TELEM_SEV_CRITICAL, TELEM_CAT_OOM,
                message, entity, 0, &evt);

            if (emit) {
                json_str = telem_json_event(&evt);
                if (json_str) {
                    telem_socket_write(sock, json_str, strlen(json_str));
                    free(json_str);
                }
            }
        } else if (mem_pct > ctx->mem_warning_pct) {
            struct telem_event evt;
            char message[512];
            char entity[128];
            char *json_str;
            bool emit;

            snprintf(message, sizeof(message),
                     "Cgroup %s memory pressure: %.1f%% (%lu/%lu MB)",
                     snap->name, mem_pct,
                     (unsigned long)(snap->mem_current / (1024UL * 1024UL)),
                     (unsigned long)(snap->mem_max / (1024UL * 1024UL)));
            snprintf(entity, sizeof(entity), "cgroup:%s", snap->name);

            emit = normalizer_process_prenormalized(norm,
                TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_OOM,
                message, entity, 0, &evt);

            if (emit) {
                json_str = telem_json_event(&evt);
                if (json_str) {
                    telem_socket_write(sock, json_str, strlen(json_str));
                    free(json_str);
                }
            }
        }

        /* Check OOM kills (delta since last poll) */
        {
            struct cgroup_snapshot *prev = find_prev_cgroup(ctx, snap->name);
            if (prev && snap->oom_kills > prev->oom_kills) {
                struct telem_event evt;
                char message[512];
                char entity[128];
                char *json_str;
                bool emit;

                snprintf(message, sizeof(message),
                         "OOM kills in cgroup %s: %lu new (total: %lu)",
                         snap->name,
                         (unsigned long)(snap->oom_kills - prev->oom_kills),
                         (unsigned long)snap->oom_kills);
                snprintf(entity, sizeof(entity), "cgroup:%s", snap->name);

                emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE, TELEM_SEV_ERROR, TELEM_CAT_OOM,
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

        /* Check CPU throttle */
        if (throttle_pct > ctx->throttle_warning_pct) {
            struct telem_event evt;
            char message[512];
            char entity[128];
            char *json_str;
            bool emit;

            snprintf(message, sizeof(message),
                     "CPU throttling in cgroup %s: %.1f%% periods throttled",
                     snap->name, throttle_pct);
            snprintf(entity, sizeof(entity), "cgroup:%s", snap->name);

            emit = normalizer_process_prenormalized(norm,
                TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_SERVICE,
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

    /* Update previous state */
    memcpy(ctx->prev, current, sizeof(struct cgroup_snapshot) * num_current);
    ctx->num_prev = num_current;

    return 0;
}
