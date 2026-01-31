// SPDX-License-Identifier: MIT
/**
 * auth.c - Authentication failure aggregation probe
 *
 * Uses sd-journal to read authentication failures from the journal.
 * Aggregates failures to detect brute force attacks.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <systemd/sd-journal.h>
#include <time.h>

#include "auth.h"
#include "../socket.h"
#include "../json.h"

/* Maximum tracked failures for window analysis */
#define MAX_TRACKED_FAILURES 256

/* Window for aggregation (60 seconds) */
#define AGGREGATION_WINDOW_NS (60ULL * 1000000000ULL)

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct auth_probe_ctx *auth_probe_create_ctx(const struct probed_config *cfg)
{
    struct auth_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->brute_force_threshold = cfg->brute_force_threshold;

    ctx->failures = calloc(MAX_TRACKED_FAILURES, sizeof(*ctx->failures));
    if (!ctx->failures) {
        free(ctx);
        return NULL;
    }
    ctx->failures_capacity = MAX_TRACKED_FAILURES;

    return ctx;
}

void auth_probe_destroy_ctx(struct auth_probe_ctx *ctx)
{
    if (ctx) {
        free(ctx->failures);
        free(ctx);
    }
}

/* ==========================================================================
 * Failure Tracking
 * ========================================================================== */

static void add_failure(struct auth_probe_ctx *ctx,
                        const char *source, const char *user, uint64_t ts_ns)
{
    struct auth_failure_entry *entry = &ctx->failures[ctx->failures_idx];

    strncpy(entry->source, source ? source : "unknown", sizeof(entry->source) - 1);
    strncpy(entry->user, user ? user : "unknown", sizeof(entry->user) - 1);
    entry->ts_ns = ts_ns;

    ctx->failures_idx = (ctx->failures_idx + 1) % ctx->failures_capacity;
    if (ctx->failures_count < ctx->failures_capacity)
        ctx->failures_count++;
}

static void aggregate_failures(struct auth_probe_ctx *ctx, uint64_t now_ns,
                               uint32_t *logins, uint32_t *sudo, uint32_t *ssh,
                               uint32_t *unique_sources,
                               char *last_user, char *last_source)
{
    *logins = 0;
    *sudo = 0;
    *ssh = 0;
    *unique_sources = 0;

    /* Track unique sources */
    char sources[64][64];
    int source_count = 0;

    for (int i = 0; i < ctx->failures_count; i++) {
        struct auth_failure_entry *entry = &ctx->failures[i];

        /* Check if within window */
        if ((now_ns - entry->ts_ns) > AGGREGATION_WINDOW_NS)
            continue;

        (*logins)++;

        /* Track unique source */
        int found = 0;
        for (int j = 0; j < source_count; j++) {
            if (strcmp(sources[j], entry->source) == 0) {
                found = 1;
                break;
            }
        }
        if (!found && source_count < 64) {
            strncpy(sources[source_count], entry->source, 63);
            source_count++;
        }

        /* Copy last user/source */
        strncpy(last_user, entry->user, 31);
        strncpy(last_source, entry->source, 63);
    }

    *unique_sources = source_count;
}

/* ==========================================================================
 * Per-Source Brute Force Tracking
 * ========================================================================== */

static void track_source_failure(struct auth_probe_ctx *ctx,
                                  const char *source, uint64_t now_ns)
{
    /* Find existing entry */
    for (int i = 0; i < ctx->source_tracker_count; i++) {
        if (strcmp(ctx->source_tracker[i].ip, source) == 0) {
            ctx->source_tracker[i].failure_count++;
            ctx->source_tracker[i].last_seen_ns = now_ns;
            return;
        }
    }

    /* Add new entry (LRU eviction if full) */
    int idx;
    if (ctx->source_tracker_count < 256) {
        idx = ctx->source_tracker_count++;
    } else {
        /* Evict oldest entry */
        idx = 0;
        uint64_t oldest = ctx->source_tracker[0].last_seen_ns;
        for (int i = 1; i < 256; i++) {
            if (ctx->source_tracker[i].last_seen_ns < oldest) {
                oldest = ctx->source_tracker[i].last_seen_ns;
                idx = i;
            }
        }
    }

    strncpy(ctx->source_tracker[idx].ip, source, sizeof(ctx->source_tracker[0].ip) - 1);
    ctx->source_tracker[idx].ip[sizeof(ctx->source_tracker[0].ip) - 1] = '\0';
    ctx->source_tracker[idx].failure_count = 1;
    ctx->source_tracker[idx].first_seen_ns = now_ns;
    ctx->source_tracker[idx].last_seen_ns = now_ns;
}

static void detect_brute_force(struct auth_probe_ctx *ctx, uint64_t now_ns)
{
    uint64_t window_ns = 60ULL * 1000000000ULL;  /* 60 second window */

    ctx->brute_force_detected = false;
    ctx->brute_force_source[0] = '\0';
    ctx->brute_force_count = 0;

    for (int i = 0; i < ctx->source_tracker_count; i++) {
        struct auth_source_entry *entry = &ctx->source_tracker[i];

        /* Check if within window and exceeds threshold */
        if ((now_ns - entry->first_seen_ns) <= window_ns &&
            entry->failure_count > 5) {
            ctx->brute_force_detected = true;
            strncpy(ctx->brute_force_source, entry->ip,
                    sizeof(ctx->brute_force_source) - 1);
            ctx->brute_force_count = entry->failure_count;
            break;  /* Report first detected */
        }
    }
}

/* ==========================================================================
 * Journal Parsing
 * ========================================================================== */

static int parse_auth_failures(struct auth_probe_ctx *ctx)
{
    sd_journal *j = NULL;
    int r;
    uint64_t now_ns = get_timestamp_ns();
    uint64_t cutoff_us;

    /* Open journal */
    r = sd_journal_open(&j, SD_JOURNAL_SYSTEM);
    if (r < 0) {
        return -1;
    }

    /* Filter to auth-related messages */
    r = sd_journal_add_match(j, "SYSLOG_FACILITY=10", 0);  /* authpriv */
    if (r < 0) {
        sd_journal_close(j);
        return -1;
    }

    /* Seek to 1 minute ago */
    cutoff_us = (now_ns / 1000) - (60ULL * 1000000);
    sd_journal_seek_realtime_usec(j, cutoff_us);

    /* Read entries */
    while (sd_journal_next(j) > 0) {
        const char *message = NULL;
        size_t message_len;
        uint64_t ts_us;

        /* Get timestamp */
        r = sd_journal_get_realtime_usec(j, &ts_us);
        if (r < 0)
            continue;

        /* Get message */
        r = sd_journal_get_data(j, "MESSAGE", (const void **)&message, &message_len);
        if (r < 0 || !message)
            continue;

        /* Skip "MESSAGE=" prefix */
        if (message_len > 8 && strncmp(message, "MESSAGE=", 8) == 0) {
            message += 8;
            message_len -= 8;
        }

        /* Parse failure patterns */
        char source[64] = {0};
        char user[32] = {0};
        int is_failure = 0;

        /* SSH failure: "Failed password for user from IP" */
        if (strstr(message, "Failed password") ||
            strstr(message, "authentication failure") ||
            strstr(message, "pam_unix(sshd:auth): authentication failure")) {

            /* Extract user */
            const char *user_ptr = strstr(message, "user=");
            if (user_ptr) {
                sscanf(user_ptr, "user=%31s", user);
            } else {
                user_ptr = strstr(message, "for ");
                if (user_ptr) {
                    user_ptr += 4;
                    if (strncmp(user_ptr, "invalid user ", 13) == 0)
                        user_ptr += 13;
                    sscanf(user_ptr, "%31s", user);
                }
            }

            /* Extract source IP */
            const char *from_ptr = strstr(message, "from ");
            if (from_ptr) {
                from_ptr += 5;
                sscanf(from_ptr, "%63s", source);
            } else {
                const char *rhost_ptr = strstr(message, "rhost=");
                if (rhost_ptr) {
                    sscanf(rhost_ptr, "rhost=%63s", source);
                }
            }

            is_failure = 1;
        }

        /* sudo failure */
        if (strstr(message, "3 incorrect password attempts") ||
            strstr(message, "authentication failure") ||
            strstr(message, "user NOT in sudoers")) {

            const char *user_ptr = strstr(message, "user=");
            if (user_ptr) {
                sscanf(user_ptr, "user=%31s", user);
            }

            strncpy(source, "local", sizeof(source) - 1);
            is_failure = 1;
        }

        if (is_failure) {
            add_failure(ctx, source, user, ts_us * 1000);
            track_source_failure(ctx, source, ts_us * 1000);
        }
    }

    sd_journal_close(j);
    return 0;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int auth_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct auth_probe_ctx *ctx = (struct auth_probe_ctx *)ctx_ptr;
    struct probed_auth_event evt;
    uint64_t now_ns = get_timestamp_ns();

    /* Parse recent auth failures from journal */
    if (parse_auth_failures(ctx) < 0) {
        return -1;
    }

    /* Aggregate failures */
    memset(&evt, 0, sizeof(evt));
    evt.ts_ns = now_ns;

    aggregate_failures(ctx, now_ns,
                       &evt.failed_logins_1m,
                       &evt.failed_sudo_1m,
                       &evt.failed_ssh_1m,
                       &evt.unique_sources,
                       evt.last_user,
                       evt.last_source);

    /* Detect brute force per-IP pattern */
    detect_brute_force(ctx, now_ns);

    /* Check for brute force */
    evt.likely_brute_force = (evt.failed_logins_1m >= (uint32_t)ctx->brute_force_threshold);

    /* Only emit if there are failures or if previously had failures */
    if (evt.failed_logins_1m > 0 || evt.likely_brute_force ||
        ctx->last_failed_logins > 0) {

        char *json = probed_json_auth_event(&evt);
        if (json) {
            probed_socket_write(sock, json, strlen(json));
            free(json);
        }
    }

    /* Emit brute force event if detected */
    if (ctx->brute_force_detected) {
        char bf_json[512];
        snprintf(bf_json, sizeof(bf_json),
                 "{\"type\":\"auth_brute_force\","
                 "\"brute_force_detected\":true,"
                 "\"brute_force_source\":\"%s\","
                 "\"brute_force_count\":%u,"
                 "\"window_sec\":60}\n",
                 ctx->brute_force_source,
                 ctx->brute_force_count);
        probed_socket_write(sock, bf_json, strlen(bf_json));
    }

    /* Update last state */
    ctx->last_failed_logins = evt.failed_logins_1m;
    ctx->last_failed_sudo = evt.failed_sudo_1m;
    ctx->last_failed_ssh = evt.failed_ssh_1m;

    return 0;
}
