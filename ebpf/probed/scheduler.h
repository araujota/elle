// SPDX-License-Identifier: MIT
/**
 * scheduler.h - Timer-based probe scheduling for elled-probed
 */

#ifndef __PROBED_SCHEDULER_H__
#define __PROBED_SCHEDULER_H__

#include <stdint.h>
#include <stdbool.h>

/* Forward declaration */
struct probed_scheduler;
struct probed_socket;

/* Probe callback function type */
typedef int (*probe_func_t)(struct probed_socket *sock, void *ctx);

/**
 * struct probe_entry - Registered probe configuration
 */
struct probe_entry {
    const char *name;        /* Probe name */
    probe_func_t func;       /* Probe function */
    void *ctx;               /* Probe context */
    int interval_sec;        /* Interval between runs */
    uint64_t last_run_ns;    /* Last run timestamp */
    uint64_t run_count;      /* Number of runs */
    uint64_t error_count;    /* Number of errors */
    bool enabled;            /* Is probe enabled */
};

/**
 * probed_scheduler_create - Create a new scheduler
 * @sock: Socket for output
 *
 * Returns: Scheduler handle on success, NULL on failure
 */
struct probed_scheduler *probed_scheduler_create(struct probed_socket *sock);

/**
 * probed_scheduler_register - Register a probe
 * @sched: Scheduler handle
 * @name: Probe name
 * @func: Probe function
 * @ctx: Probe context (passed to func)
 * @interval_sec: Run interval in seconds
 * @enabled: Whether probe starts enabled
 *
 * Returns: 0 on success, -1 on failure
 */
int probed_scheduler_register(struct probed_scheduler *sched,
                               const char *name,
                               probe_func_t func,
                               void *ctx,
                               int interval_sec,
                               bool enabled);

/**
 * probed_scheduler_run_once - Run all due probes once
 * @sched: Scheduler handle
 *
 * Checks all registered probes and runs any that are due.
 * Returns: Number of probes run, -1 on error
 */
int probed_scheduler_run_once(struct probed_scheduler *sched);

/**
 * probed_scheduler_get_next_deadline - Get time until next probe is due
 * @sched: Scheduler handle
 *
 * Returns: Milliseconds until next probe, or -1 if no probes
 */
int probed_scheduler_get_next_deadline(struct probed_scheduler *sched);

/**
 * probed_scheduler_get_stats - Get scheduler statistics
 * @sched: Scheduler handle
 * @total_runs: Output: total probe runs
 * @total_errors: Output: total errors
 */
void probed_scheduler_get_stats(struct probed_scheduler *sched,
                                 uint64_t *total_runs,
                                 uint64_t *total_errors);

/**
 * probed_scheduler_enable_probe - Enable/disable a probe by name
 * @sched: Scheduler handle
 * @name: Probe name
 * @enable: Enable (true) or disable (false)
 *
 * Returns: 0 on success, -1 if probe not found
 */
int probed_scheduler_enable_probe(struct probed_scheduler *sched,
                                   const char *name,
                                   bool enable);

/**
 * probed_scheduler_destroy - Free scheduler resources
 * @sched: Scheduler handle
 */
void probed_scheduler_destroy(struct probed_scheduler *sched);

#endif /* __PROBED_SCHEDULER_H__ */
