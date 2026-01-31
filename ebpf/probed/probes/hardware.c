// SPDX-License-Identifier: MIT
/**
 * hardware.c - Hardware error probe
 *
 * Reads from /sys/devices/system/edac/ to detect memory errors.
 * EDAC (Error Detection And Correction) provides visibility into
 * ECC memory errors before they cause system instability.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <dirent.h>
#include <sys/stat.h>

#include "hardware.h"
#include "../socket.h"
#include "../json.h"

/* EDAC paths */
#define EDAC_MC_PATH "/sys/devices/system/edac/mc"

/* Maximum number of tracked memory controllers */
#define MAX_EDAC_STATE 32

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct hardware_probe_ctx *hardware_probe_create_ctx(const struct probed_config *cfg)
{
    struct hardware_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->edac_state = calloc(MAX_EDAC_STATE, sizeof(*ctx->edac_state));
    if (!ctx->edac_state) {
        free(ctx);
        return NULL;
    }
    ctx->edac_state_capacity = MAX_EDAC_STATE;

    (void)cfg;  /* Currently unused */

    return ctx;
}

void hardware_probe_destroy_ctx(struct hardware_probe_ctx *ctx)
{
    if (ctx) {
        free(ctx->edac_state);
        free(ctx);
    }
}

/* ==========================================================================
 * Error State Tracking
 * ========================================================================== */

static struct edac_error_state *find_or_create_state(
    struct hardware_probe_ctx *ctx, const char *mc, const char *csrow)
{
    /* Find existing */
    for (int i = 0; i < ctx->edac_state_count; i++) {
        if (strcmp(ctx->edac_state[i].mc, mc) == 0 &&
            strcmp(ctx->edac_state[i].csrow, csrow) == 0) {
            return &ctx->edac_state[i];
        }
    }

    /* Create new if space available */
    if (ctx->edac_state_count >= ctx->edac_state_capacity) {
        return NULL;
    }

    struct edac_error_state *state = &ctx->edac_state[ctx->edac_state_count++];
    memset(state, 0, sizeof(*state));
    snprintf(state->mc, sizeof(state->mc), "%s", mc);
    snprintf(state->csrow, sizeof(state->csrow), "%s", csrow);

    return state;
}

/* ==========================================================================
 * EDAC Parsing
 * ========================================================================== */

static int read_edac_count(const char *path, uint64_t *value)
{
    FILE *f;
    int ret;

    f = fopen(path, "r");
    if (!f)
        return -1;

    ret = fscanf(f, "%lu", value);
    fclose(f);

    return ret == 1 ? 0 : -1;
}

static int scan_edac_mc(struct hardware_probe_ctx *ctx,
                        struct probed_socket *sock,
                        const char *mc_name)
{
    char mc_path[256];
    DIR *dir;
    struct dirent *entry;

    snprintf(mc_path, sizeof(mc_path), "%s/%s", EDAC_MC_PATH, mc_name);

    dir = opendir(mc_path);
    if (!dir)
        return -1;

    while ((entry = readdir(dir)) != NULL) {
        /* Look for csrowN or rankN directories */
        if (strncmp(entry->d_name, "csrow", 5) != 0 &&
            strncmp(entry->d_name, "rank", 4) != 0) {
            continue;
        }

        /* Read CE count */
        char ce_path[512];
        snprintf(ce_path, sizeof(ce_path), "%s/%s/ce_count",
                 mc_path, entry->d_name);

        uint64_t ce_count = 0;
        if (read_edac_count(ce_path, &ce_count) < 0) {
            /* Try alternate path for newer kernels */
            snprintf(ce_path, sizeof(ce_path), "%s/%s/ce_noinfo_count",
                     mc_path, entry->d_name);
            read_edac_count(ce_path, &ce_count);
        }

        /* Read UE count */
        char ue_path[512];
        snprintf(ue_path, sizeof(ue_path), "%s/%s/ue_count",
                 mc_path, entry->d_name);

        uint64_t ue_count = 0;
        if (read_edac_count(ue_path, &ue_count) < 0) {
            snprintf(ue_path, sizeof(ue_path), "%s/%s/ue_noinfo_count",
                     mc_path, entry->d_name);
            read_edac_count(ue_path, &ue_count);
        }

        /* Get previous state */
        struct edac_error_state *state = find_or_create_state(ctx, mc_name, entry->d_name);
        if (!state)
            continue;

        /* Emit events for new errors */
        if (ce_count > 0 && (ce_count != state->ce_count || state->ce_count == 0)) {
            struct probed_hardware_event evt;

            memset(&evt, 0, sizeof(evt));
            evt.ts_ns = get_timestamp_ns();
            snprintf(evt.subsystem, sizeof(evt.subsystem), "%s", "memory");
            snprintf(evt.error_type, sizeof(evt.error_type), "%s", "correctable");
            snprintf(evt.mc, sizeof(evt.mc), "%s", mc_name);
            snprintf(evt.csrow, sizeof(evt.csrow), "%s", entry->d_name);

            snprintf(evt.device, sizeof(evt.device), "%s/%s", mc_name, entry->d_name);

            evt.error_count = ce_count;
            evt.prev_count = state->ce_count;

            /* Only emit if count increased */
            if (ce_count > state->ce_count) {
                char *json = probed_json_hardware_event(&evt);
                if (json) {
                    probed_socket_write(sock, json, strlen(json));
                    free(json);
                }
            }
        }

        if (ue_count > 0 && (ue_count != state->ue_count || state->ue_count == 0)) {
            struct probed_hardware_event evt;

            memset(&evt, 0, sizeof(evt));
            evt.ts_ns = get_timestamp_ns();
            snprintf(evt.subsystem, sizeof(evt.subsystem), "%s", "memory");
            snprintf(evt.error_type, sizeof(evt.error_type), "%s", "uncorrectable");
            snprintf(evt.mc, sizeof(evt.mc), "%s", mc_name);
            snprintf(evt.csrow, sizeof(evt.csrow), "%s", entry->d_name);

            snprintf(evt.device, sizeof(evt.device), "%s/%s", mc_name, entry->d_name);

            evt.error_count = ue_count;
            evt.prev_count = state->ue_count;

            /* Always emit uncorrectable errors */
            if (ue_count > state->ue_count) {
                char *json = probed_json_hardware_event(&evt);
                if (json) {
                    probed_socket_write(sock, json, strlen(json));
                    free(json);
                }
            }
        }

        /* Update state */
        state->ce_count = ce_count;
        state->ue_count = ue_count;
    }

    closedir(dir);
    return 0;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int hardware_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct hardware_probe_ctx *ctx = (struct hardware_probe_ctx *)ctx_ptr;
    DIR *dir;
    struct dirent *entry;
    int ret = 0;

    /* Scan EDAC memory controllers */
    dir = opendir(EDAC_MC_PATH);
    if (!dir) {
        /* EDAC might not be available */
        return -1;
    }

    while ((entry = readdir(dir)) != NULL) {
        /* Look for mcN directories */
        if (strncmp(entry->d_name, "mc", 2) != 0)
            continue;

        if (scan_edac_mc(ctx, sock, entry->d_name) < 0) {
            ret = -1;
        }
    }

    closedir(dir);
    return ret;
}
