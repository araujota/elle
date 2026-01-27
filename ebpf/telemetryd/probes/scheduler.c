// SPDX-License-Identifier: MIT
/**
 * scheduler.c - Timer-based probe scheduling for elled-telemetryd
 *
 * Manages probe registration and periodic execution with normalizer integration.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>
#include <limits.h>

#include "scheduler.h"

/* ==========================================================================
 * Data Structures
 * ========================================================================== */

struct telem_scheduler {
    struct normalizer *norm;
    struct telem_socket *sock;
    struct telem_probe_entry probes[TELEM_MAX_PROBES];
    int num_probes;
    uint64_t total_runs;
    uint64_t total_errors;
};

/* ==========================================================================
 * Helper: Get current timestamp in nanoseconds
 * ========================================================================== */

static uint64_t get_mono_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* ==========================================================================
 * Scheduler Creation
 * ========================================================================== */

struct telem_scheduler *telem_scheduler_create(struct normalizer *norm,
                                                struct telem_socket *sock)
{
    struct telem_scheduler *sched;

    if (!norm || !sock)
        return NULL;

    sched = calloc(1, sizeof(*sched));
    if (!sched)
        return NULL;

    sched->norm = norm;
    sched->sock = sock;
    return sched;
}

/* ==========================================================================
 * Probe Registration
 * ========================================================================== */

int telem_scheduler_register(struct telem_scheduler *sched,
                              const char *name,
                              telem_probe_func_t func,
                              void *ctx,
                              int interval_sec,
                              bool enabled)
{
    struct telem_probe_entry *probe;

    if (!sched || !name || !func)
        return -1;

    if (sched->num_probes >= TELEM_MAX_PROBES) {
        fprintf(stderr, "ERROR: Maximum probes (%d) reached\n", TELEM_MAX_PROBES);
        return -1;
    }

    probe = &sched->probes[sched->num_probes];
    probe->name = strdup(name);
    if (!probe->name)
        return -1;

    probe->func = func;
    probe->ctx = ctx;
    probe->interval_sec = interval_sec;
    probe->enabled = enabled;
    probe->last_run_ns = 0;  /* Run immediately on first iteration */
    probe->run_count = 0;
    probe->error_count = 0;

    sched->num_probes++;
    return 0;
}

/* ==========================================================================
 * Probe Execution
 * ========================================================================== */

int telem_scheduler_run_once(struct telem_scheduler *sched)
{
    int runs = 0;
    uint64_t now_ns;

    if (!sched)
        return -1;

    now_ns = get_mono_ns();

    for (int i = 0; i < sched->num_probes; i++) {
        struct telem_probe_entry *probe = &sched->probes[i];
        uint64_t interval_ns;
        int ret;

        if (!probe->enabled)
            continue;

        /* Check if probe is due */
        interval_ns = (uint64_t)probe->interval_sec * 1000000000ULL;
        if (probe->last_run_ns > 0 && (now_ns - probe->last_run_ns) < interval_ns)
            continue;

        /* Run the probe */
        probe->last_run_ns = now_ns;
        probe->run_count++;
        sched->total_runs++;

        ret = probe->func(sched->norm, sched->sock, probe->ctx);
        if (ret < 0) {
            probe->error_count++;
            sched->total_errors++;
        }

        runs++;
    }

    return runs;
}

/* ==========================================================================
 * Scheduling Helpers
 * ========================================================================== */

int telem_scheduler_get_next_deadline(struct telem_scheduler *sched)
{
    uint64_t now_ns;
    int64_t min_ms = INT64_MAX;

    if (!sched || sched->num_probes == 0)
        return -1;

    now_ns = get_mono_ns();

    for (int i = 0; i < sched->num_probes; i++) {
        struct telem_probe_entry *probe = &sched->probes[i];
        uint64_t interval_ns, next_run_ns;
        int64_t wait_ms;

        if (!probe->enabled)
            continue;

        interval_ns = (uint64_t)probe->interval_sec * 1000000000ULL;

        if (probe->last_run_ns == 0) {
            /* Not yet run, run immediately */
            return 0;
        }

        next_run_ns = probe->last_run_ns + interval_ns;

        if (next_run_ns <= now_ns) {
            /* Already due */
            return 0;
        }

        wait_ms = (int64_t)(next_run_ns - now_ns) / 1000000LL;
        if (wait_ms < min_ms)
            min_ms = wait_ms;
    }

    if (min_ms == INT64_MAX)
        return 1000;  /* Default 1 second if no probes enabled */

    return (int)min_ms;
}

void telem_scheduler_get_stats(struct telem_scheduler *sched,
                                uint64_t *total_runs,
                                uint64_t *total_errors)
{
    if (!sched) {
        if (total_runs) *total_runs = 0;
        if (total_errors) *total_errors = 0;
        return;
    }

    if (total_runs) *total_runs = sched->total_runs;
    if (total_errors) *total_errors = sched->total_errors;
}

int telem_scheduler_enable_probe(struct telem_scheduler *sched,
                                  const char *name,
                                  bool enable)
{
    if (!sched || !name)
        return -1;

    for (int i = 0; i < sched->num_probes; i++) {
        if (strcmp(sched->probes[i].name, name) == 0) {
            sched->probes[i].enabled = enable;
            return 0;
        }
    }

    return -1;  /* Probe not found */
}

/* ==========================================================================
 * Scheduler Destruction
 * ========================================================================== */

void telem_scheduler_destroy(struct telem_scheduler *sched)
{
    if (!sched)
        return;

    for (int i = 0; i < sched->num_probes; i++) {
        free((void *)sched->probes[i].name);
    }

    free(sched);
}
