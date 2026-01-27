// SPDX-License-Identifier: MIT
/**
 * collector.h - eBPF program loader and event collector
 */

#ifndef __ELLED_COLLECTOR_H__
#define __ELLED_COLLECTOR_H__

#include "elled.h"
#include "socket.h"

/* Opaque collector handle */
struct elled_collector;

/**
 * elled_collector_create - Create a new collector
 * @cfg: Runtime configuration
 * @sock: Socket for output (NULL for stdout mode)
 * @use_stdout: Output to stdout instead of socket
 * @verbose: Enable verbose logging
 *
 * Returns: Collector handle on success, NULL on failure
 */
struct elled_collector *elled_collector_create(
    struct elled_config *cfg,
    struct elled_socket *sock,
    int use_stdout,
    int verbose
);

/**
 * elled_collector_start - Load and attach BPF programs
 * @collector: Collector handle
 *
 * Returns: 0 on success, -1 on failure
 */
int elled_collector_start(struct elled_collector *collector);

/**
 * elled_collector_poll - Poll for events
 * @collector: Collector handle
 * @timeout_ms: Poll timeout in milliseconds
 *
 * Returns: Number of events processed, -1 on error
 */
int elled_collector_poll(struct elled_collector *collector, int timeout_ms);

/**
 * elled_collector_destroy - Destroy collector and cleanup
 * @collector: Collector handle
 */
void elled_collector_destroy(struct elled_collector *collector);

/**
 * elled_collector_stats - Get collector statistics
 * @collector: Collector handle
 * @events: Output: total events processed
 * @errors: Output: total errors encountered
 */
void elled_collector_stats(
    struct elled_collector *collector,
    uint64_t *events,
    uint64_t *errors
);

#endif /* __ELLED_COLLECTOR_H__ */
