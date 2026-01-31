// SPDX-License-Identifier: MIT
/**
 * systemd.c - Systemd unit state probe
 *
 * Uses sd-bus to query systemd for unit states.
 * Detects failed units, crash loops, and dependency issues.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <systemd/sd-bus.h>

#include "systemd.h"
#include "../socket.h"
#include "../json.h"

/* Maximum number of tracked units for crash loop detection */
#define MAX_TRACKED_UNITS 64

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct systemd_probe_ctx *systemd_probe_create_ctx(const struct probed_config *cfg)
{
    struct systemd_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->crashloop_threshold = cfg->crashloop_threshold;
    ctx->crashloop_window_sec = cfg->crashloop_window_sec;

    ctx->tracked_units = calloc(MAX_TRACKED_UNITS, sizeof(*ctx->tracked_units));
    if (!ctx->tracked_units) {
        free(ctx);
        return NULL;
    }
    ctx->tracked_units_capacity = MAX_TRACKED_UNITS;

    return ctx;
}

void systemd_probe_destroy_ctx(struct systemd_probe_ctx *ctx)
{
    if (ctx) {
        free(ctx->tracked_units);
        free(ctx);
    }
}

/* ==========================================================================
 * Crash Loop Detection
 * ========================================================================== */

static struct unit_restart_state *find_or_create_unit_state(
    struct systemd_probe_ctx *ctx, const char *unit)
{
    /* Find existing */
    for (int i = 0; i < ctx->tracked_units_count; i++) {
        if (strcmp(ctx->tracked_units[i].unit, unit) == 0) {
            return &ctx->tracked_units[i];
        }
    }

    /* Create new if space available */
    if (ctx->tracked_units_count >= ctx->tracked_units_capacity) {
        return NULL;
    }

    struct unit_restart_state *state = &ctx->tracked_units[ctx->tracked_units_count++];
    memset(state, 0, sizeof(*state));
    strncpy(state->unit, unit, sizeof(state->unit) - 1);

    return state;
}

static bool check_crashloop(struct systemd_probe_ctx *ctx,
                            const char *unit, uint64_t ts_ns)
{
    struct unit_restart_state *state = find_or_create_unit_state(ctx, unit);
    if (!state)
        return false;

    /* Record restart time */
    state->restart_times[state->restart_idx] = ts_ns;
    state->restart_idx = (state->restart_idx + 1) % 10;
    if (state->restart_count < 10)
        state->restart_count++;

    /* Count restarts within window */
    uint64_t window_ns = (uint64_t)ctx->crashloop_window_sec * 1000000000ULL;
    int recent_restarts = 0;

    for (int i = 0; i < state->restart_count; i++) {
        if ((ts_ns - state->restart_times[i]) <= window_ns) {
            recent_restarts++;
        }
    }

    return recent_restarts >= ctx->crashloop_threshold;
}

/* ==========================================================================
 * D-Bus Queries
 * ========================================================================== */

static int query_unit_state(sd_bus *bus, const char *unit,
                            struct probed_unit_event *evt)
{
    sd_bus_error error = SD_BUS_ERROR_NULL;
    sd_bus_message *reply = NULL;
    char *path = NULL;
    int r;

    memset(evt, 0, sizeof(*evt));
    evt->ts_ns = get_timestamp_ns();
    strncpy(evt->unit, unit, sizeof(evt->unit) - 1);

    /* Get unit object path */
    r = sd_bus_call_method(
        bus,
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "GetUnit",
        &error,
        &reply,
        "s",
        unit);

    if (r < 0) {
        /* Unit might not exist or not be loaded */
        sd_bus_error_free(&error);
        return -1;
    }

    r = sd_bus_message_read(reply, "o", &path);
    sd_bus_message_unref(reply);

    if (r < 0) {
        sd_bus_error_free(&error);
        return -1;
    }

    /* Query ActiveState */
    char *active_state = NULL;
    r = sd_bus_get_property_string(
        bus,
        "org.freedesktop.systemd1",
        path,
        "org.freedesktop.systemd1.Unit",
        "ActiveState",
        &error,
        &active_state);

    if (r >= 0 && active_state) {
        strncpy(evt->active_state, active_state, sizeof(evt->active_state) - 1);
        free(active_state);
    }
    sd_bus_error_free(&error);

    /* Query SubState */
    char *sub_state = NULL;
    r = sd_bus_get_property_string(
        bus,
        "org.freedesktop.systemd1",
        path,
        "org.freedesktop.systemd1.Unit",
        "SubState",
        &error,
        &sub_state);

    if (r >= 0 && sub_state) {
        strncpy(evt->sub_state, sub_state, sizeof(evt->sub_state) - 1);
        free(sub_state);
    }
    sd_bus_error_free(&error);

    /* For service units, query additional properties */
    if (strstr(unit, ".service")) {
        /* Query Result */
        char *result = NULL;
        r = sd_bus_get_property_string(
            bus,
            "org.freedesktop.systemd1",
            path,
            "org.freedesktop.systemd1.Service",
            "Result",
            &error,
            &result);

        if (r >= 0 && result) {
            strncpy(evt->result, result, sizeof(evt->result) - 1);
            free(result);
        }
        sd_bus_error_free(&error);

        /* Query NRestarts */
        uint32_t n_restarts = 0;
        r = sd_bus_get_property_trivial(
            bus,
            "org.freedesktop.systemd1",
            path,
            "org.freedesktop.systemd1.Service",
            "NRestarts",
            &error,
            'u',
            &n_restarts);

        if (r >= 0) {
            evt->n_restarts = n_restarts;
        }
        sd_bus_error_free(&error);

        /* Query ExecMainStatus (exit code) */
        int32_t exit_status = 0;
        r = sd_bus_get_property_trivial(
            bus,
            "org.freedesktop.systemd1",
            path,
            "org.freedesktop.systemd1.Service",
            "ExecMainStatus",
            &error,
            'i',
            &exit_status);

        if (r >= 0) {
            evt->exit_code = exit_status;
        }
        sd_bus_error_free(&error);
    }

    /* Check if failed */
    evt->failed = (strcmp(evt->active_state, "failed") == 0);

    return 0;
}

static int list_failed_units(sd_bus *bus, char ***units_out, int *count_out)
{
    sd_bus_error error = SD_BUS_ERROR_NULL;
    sd_bus_message *reply = NULL;
    char **units = NULL;
    int count = 0;
    int capacity = 32;
    int r;

    units = (char **)calloc(capacity, sizeof(char *));
    if (!units)
        return -1;

    /* ListUnits returns an array of (name, desc, load, active, sub, following, path, job_id, job_type, job_path) */
    r = sd_bus_call_method(
        bus,
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "ListUnits",
        &error,
        &reply,
        NULL);

    if (r < 0) {
        sd_bus_error_free(&error);
        free((void *)units);
        return -1;
    }

    r = sd_bus_message_enter_container(reply, 'a', "(ssssssouso)");
    if (r < 0) {
        sd_bus_message_unref(reply);
        sd_bus_error_free(&error);
        free((void *)units);
        return -1;
    }

    while (1) {
        const char *name, *desc, *load, *active, *sub, *following, *path, *job_type, *job_path;
        uint32_t job_id;

        r = sd_bus_message_read(reply, "(ssssssouso)",
                                &name, &desc, &load, &active, &sub,
                                &following, &path, &job_id, &job_type, &job_path);
        if (r < 0)
            break;
        if (r == 0)
            break;

        /* Check if failed or activating (interesting states) */
        if (strcmp(active, "failed") == 0 ||
            strcmp(active, "activating") == 0) {
            if (count >= capacity) {
                capacity *= 2;
                char **new_units = (char **)realloc((void *)units, capacity * sizeof(char *));
                if (!new_units)
                    break;
                units = new_units;
            }

            units[count] = strdup(name);
            if (units[count])
                count++;
        }
    }

    sd_bus_message_unref(reply);
    sd_bus_error_free(&error);

    *units_out = units;
    *count_out = count;
    return 0;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int systemd_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct systemd_probe_ctx *ctx = (struct systemd_probe_ctx *)ctx_ptr;
    sd_bus *bus = NULL;
    char **failed_units = NULL;
    int failed_count = 0;
    int r;

    /* Connect to system bus */
    r = sd_bus_open_system(&bus);
    if (r < 0) {
        fprintf(stderr, "ERROR: Failed to connect to system bus: %s\n",
                strerror(-r));
        return -1;
    }

    /* List failed/interesting units */
    r = list_failed_units(bus, &failed_units, &failed_count);
    if (r < 0) {
        sd_bus_unref(bus);
        return -1;
    }

    /* Query and emit state for each failed unit */
    for (int i = 0; i < failed_count; i++) {
        struct probed_unit_event evt;

        if (query_unit_state(bus, failed_units[i], &evt) == 0) {
            /* Check for crash loop */
            if (evt.n_restarts > 0) {
                evt.crashloop = check_crashloop(ctx, evt.unit, evt.ts_ns);
            }

            /* Only emit if failed or in crash loop */
            if (evt.failed || evt.crashloop) {
                char *json = probed_json_unit_event(&evt);
                if (json) {
                    probed_socket_write(sock, json, strlen(json));
                    free(json);
                }
            }
        }

        free(failed_units[i]);
    }

    free((void *)failed_units);
    sd_bus_unref(bus);

    return 0;
}
