// SPDX-License-Identifier: MIT
/**
 * conntrack.c - Conntrack statistics probe
 *
 * Reads from /proc/sys/net/netfilter/ and /proc/net/stat/nf_conntrack
 * to monitor connection tracking utilization.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#include "conntrack.h"
#include "../socket.h"
#include "../json.h"

/* Conntrack sysctl paths */
#define CT_COUNT_PATH "/proc/sys/net/netfilter/nf_conntrack_count"
#define CT_MAX_PATH   "/proc/sys/net/netfilter/nf_conntrack_max"
#define CT_STAT_PATH  "/proc/net/stat/nf_conntrack"

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct conntrack_probe_ctx *conntrack_probe_create_ctx(const struct probed_config *cfg)
{
    struct conntrack_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->warning_pct = cfg->conntrack_warning_pct;
    ctx->critical_pct = cfg->conntrack_critical_pct;

    return ctx;
}

void conntrack_probe_destroy_ctx(struct conntrack_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * Sysctl Reading
 * ========================================================================== */

static int read_sysctl_uint(const char *path, uint32_t *value)
{
    FILE *f;
    int ret;

    f = fopen(path, "r");
    if (!f)
        return -1;

    ret = fscanf(f, "%u", value);
    fclose(f);

    return ret == 1 ? 0 : -1;
}

/* ==========================================================================
 * Conntrack Stats Parsing
 * ========================================================================== */

static int parse_conntrack_stats(struct probed_conntrack_event *evt)
{
    FILE *f;
    char line[512];
    int first_line = 1;

    /* Initialize counters */
    evt->searched = 0;
    evt->found = 0;
    evt->new = 0;
    evt->invalid = 0;
    evt->ignore = 0;
    evt->delete = 0;
    evt->delete_list = 0;
    evt->insert = 0;
    evt->insert_failed = 0;
    evt->drop = 0;
    evt->early_drop = 0;

    f = fopen(CT_STAT_PATH, "r");
    if (!f)
        return -1;

    while (fgets(line, sizeof(line), f)) {
        if (first_line) {
            /* Skip header line */
            first_line = 0;
            continue;
        }

        /* Parse per-CPU stats and accumulate */
        unsigned int searched, found, new_val, invalid, ignore;
        unsigned int delete_val, delete_list, insert, insert_failed, drop, early_drop;

        /* Format may vary by kernel version */
        int n = sscanf(line, "%x %x %x %x %x %x %x %x %x %x %x",
                       &searched, &found, &new_val, &invalid, &ignore,
                       &delete_val, &delete_list, &insert, &insert_failed,
                       &drop, &early_drop);

        if (n >= 11) {
            evt->searched += searched;
            evt->found += found;
            evt->new += new_val;
            evt->invalid += invalid;
            evt->ignore += ignore;
            evt->delete += delete_val;
            evt->delete_list += delete_list;
            evt->insert += insert;
            evt->insert_failed += insert_failed;
            evt->drop += drop;
            evt->early_drop += early_drop;
        }
    }

    fclose(f);
    return 0;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int conntrack_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct conntrack_probe_ctx *ctx = (struct conntrack_probe_ctx *)ctx_ptr;
    struct probed_conntrack_event evt;
    uint32_t count, max;

    memset(&evt, 0, sizeof(evt));
    evt.ts_ns = get_timestamp_ns();

    /* Read count and max */
    if (read_sysctl_uint(CT_COUNT_PATH, &count) < 0) {
        /* Conntrack might not be loaded */
        return -1;
    }

    if (read_sysctl_uint(CT_MAX_PATH, &max) < 0) {
        return -1;
    }

    evt.count = count;
    evt.max = max;

    if (max > 0) {
        evt.utilization = (double)count / (double)max * 100.0;
    }

    /* Read detailed stats if available */
    parse_conntrack_stats(&evt);

    /* Check thresholds */
    evt.warning = (evt.utilization >= ctx->warning_pct);
    evt.critical = (evt.utilization >= ctx->critical_pct);

    /* Only emit if above warning or significant utilization */
    if (evt.warning || evt.critical || evt.utilization > 50.0 ||
        evt.drop > 0 || evt.insert_failed > 0) {
        char *json = probed_json_conntrack_event(&evt);
        if (json) {
            probed_socket_write(sock, json, strlen(json));
            free(json);
        }
    }

    return 0;
}
