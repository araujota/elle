// SPDX-License-Identifier: MIT
/**
 * thermal.c - Thermal monitoring probe
 *
 * Monitors CPU and system temperatures via sysfs thermal zones.
 * Ported from Python ThermalProbe in src/elle/daemon/telemetry/probes.py
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <glob.h>

#include "thermal.h"
#include "../json.h"

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct thermal_probe_ctx *thermal_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct thermal_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->warning_celsius = 80;  /* 80C default threshold */

    (void)cfg;
    return ctx;
}

void thermal_probe_destroy_ctx(struct thermal_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * Helpers
 * ========================================================================== */

static int read_sysfs_int(const char *path)
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

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int thermal_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct thermal_probe_ctx *ctx = (struct thermal_probe_ctx *)ctx_ptr;
    glob_t glob_result;
    char path[256];

    /* Find all thermal zones */
    if (glob("/sys/class/thermal/thermal_zone*", GLOB_NOSORT, NULL, &glob_result) != 0)
        return -1;

    for (size_t i = 0; i < glob_result.gl_pathc; i++) {
        const char *zone_path = glob_result.gl_pathv[i];
        char zone_type[64] = {0};
        int temp_mdeg, temp_celsius;

        /* Get zone name from path */
        const char *zone_name = strrchr(zone_path, '/');
        if (zone_name)
            zone_name++;
        else
            zone_name = zone_path;

        /* Read zone type */
        snprintf(path, sizeof(path), "%s/type", zone_path);
        if (read_sysfs_str(path, zone_type, sizeof(zone_type)) < 0)
            strcpy(zone_type, "unknown");

        /* Read temperature (in millidegrees) */
        snprintf(path, sizeof(path), "%s/temp", zone_path);
        temp_mdeg = read_sysfs_int(path);
        temp_celsius = temp_mdeg / 1000;

        /* Check threshold */
        if (temp_celsius > ctx->warning_celsius) {
            struct telem_event evt;
            char message[256];
            char entity[128];
            char *json_str;
            bool emit;

            snprintf(message, sizeof(message),
                     "High temperature on %s (%s): %dC",
                     zone_name, zone_type, temp_celsius);
            snprintf(entity, sizeof(entity), "thermal:%s", zone_name);

            emit = normalizer_process_prenormalized(norm,
                TELEM_SRC_PROBE,
                TELEM_SEV_WARNING,
                TELEM_CAT_THERMAL,
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

    globfree(&glob_result);

    /* ======================================================================
     * CPU Frequency Throttle Detection
     * ====================================================================== */
    {
        uint64_t cur_freq = 0, max_freq = 0;
        FILE *cur_f, *max_f;

        cur_f = fopen("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r");
        if (cur_f) {
            if (fscanf(cur_f, "%lu", &cur_freq) != 1)
                cur_freq = 0;
            fclose(cur_f);
        }

        max_f = fopen("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq", "r");
        if (max_f) {
            if (fscanf(max_f, "%lu", &max_freq) != 1)
                max_freq = 0;
            fclose(max_f);
        }

        ctx->scaling_cur_freq = cur_freq;
        ctx->scaling_max_freq = max_freq;

        if (cur_freq > 0 && max_freq > 0) {
            double freq_ratio = (double)cur_freq / (double)max_freq;

            /* Read fan speed (best-effort, may not exist) */
            uint64_t fan_rpm = 0;
            glob_t fan_glob;

            if (glob("/sys/class/hwmon/*/fan1_input", GLOB_NOSORT, NULL, &fan_glob) == 0) {
                if (fan_glob.gl_pathc > 0) {
                    fan_rpm = (uint64_t)read_sysfs_int(fan_glob.gl_pathv[0]);
                }
                globfree(&fan_glob);
            }

            /* Read load average */
            double loadavg = 0.0;
            FILE *load_f = fopen("/proc/loadavg", "r");
            if (load_f) {
                if (fscanf(load_f, "%lf", &loadavg) != 1)
                    loadavg = 0.0;
                fclose(load_f);
            }

            /* Emit throttle warning if freq < 80% of max AND load > 1.0 */
            if (freq_ratio < 0.80 && loadavg > 1.0) {
                struct telem_event evt;
                char message[256];
                char *json_str;
                bool emit;

                snprintf(message, sizeof(message),
                         "CPU throttled: %.0f%% of max freq (cur=%lu kHz, max=%lu kHz, "
                         "load=%.2f, fan=%lu RPM)",
                         freq_ratio * 100.0, cur_freq, max_freq, loadavg, fan_rpm);

                emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE,
                    TELEM_SEV_WARNING,
                    TELEM_CAT_THERMAL,
                    message,
                    "cpu:throttle",
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

    return 0;
}
