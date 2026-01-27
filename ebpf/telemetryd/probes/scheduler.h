// SPDX-License-Identifier: MIT
/**
 * scheduler.h - Timer-based probe scheduling for elled-telemetryd
 *
 * Manages probe registration and periodic execution with normalizer integration.
 */

#ifndef __TELEMETRYD_SCHEDULER_H__
#define __TELEMETRYD_SCHEDULER_H__

#include "../telemetryd.h"
#include "../socket.h"
#include "../normalize/normalizer.h"
#include <stdbool.h>
#include <stdint.h>

/* Maximum number of probes */
#define TELEM_MAX_PROBES 20

/**
 * Probe function signature
 *
 * @norm: Normalizer for processing events before emission
 * @sock: Socket to write events to
 * @ctx: Probe-specific context
 *
 * Returns: 0 on success, -1 on error
 */
typedef int (*telem_probe_func_t)(struct normalizer *norm,
                                   struct telem_socket *sock,
                                   void *ctx);

/**
 * struct telem_probe_entry - Registered probe information
 */
struct telem_probe_entry {
    const char *name;           /* Probe name for logging */
    telem_probe_func_t func;    /* Probe function */
    void *ctx;                  /* Probe context */
    int interval_sec;           /* Run interval in seconds */
    uint64_t last_run_ns;       /* Last run timestamp (monotonic) */
    uint64_t run_count;         /* Total runs */
    uint64_t error_count;       /* Error count */
    bool enabled;               /* Whether probe is enabled */
};

/* Opaque scheduler handle */
struct telem_scheduler;

/**
 * telem_scheduler_create - Create a probe scheduler
 * @norm: Normalizer instance
 * @sock: Socket instance
 *
 * Returns: Scheduler handle, or NULL on failure
 */
struct telem_scheduler *telem_scheduler_create(struct normalizer *norm,
                                                struct telem_socket *sock);

/**
 * telem_scheduler_register - Register a probe
 * @sched: Scheduler handle
 * @name: Probe name (will be copied)
 * @func: Probe function
 * @ctx: Probe context (passed to func)
 * @interval_sec: Run interval in seconds
 * @enabled: Initial enabled state
 *
 * Returns: 0 on success, -1 on failure
 */
int telem_scheduler_register(struct telem_scheduler *sched,
                              const char *name,
                              telem_probe_func_t func,
                              void *ctx,
                              int interval_sec,
                              bool enabled);

/**
 * telem_scheduler_run_once - Run all due probes
 * @sched: Scheduler handle
 *
 * Checks each registered probe and runs it if its interval has elapsed.
 *
 * Returns: Number of probes run, or -1 on error
 */
int telem_scheduler_run_once(struct telem_scheduler *sched);

/**
 * telem_scheduler_get_next_deadline - Get time until next probe is due
 * @sched: Scheduler handle
 *
 * Returns: Milliseconds until next probe, or -1 if no probes
 */
int telem_scheduler_get_next_deadline(struct telem_scheduler *sched);

/**
 * telem_scheduler_enable_probe - Enable or disable a probe
 * @sched: Scheduler handle
 * @name: Probe name
 * @enable: New enabled state
 *
 * Returns: 0 on success, -1 if probe not found
 */
int telem_scheduler_enable_probe(struct telem_scheduler *sched,
                                  const char *name,
                                  bool enable);

/**
 * telem_scheduler_get_stats - Get scheduler statistics
 * @sched: Scheduler handle
 * @total_runs: Output for total probe runs (may be NULL)
 * @total_errors: Output for total errors (may be NULL)
 */
void telem_scheduler_get_stats(struct telem_scheduler *sched,
                                uint64_t *total_runs,
                                uint64_t *total_errors);

/**
 * telem_scheduler_destroy - Free scheduler resources
 * @sched: Scheduler handle
 */
void telem_scheduler_destroy(struct telem_scheduler *sched);

#endif /* __TELEMETRYD_SCHEDULER_H__ */
