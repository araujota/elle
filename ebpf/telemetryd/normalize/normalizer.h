// SPDX-License-Identifier: MIT
/**
 * normalizer.h - Main normalization entry point
 *
 * Combines category detection, entity extraction, fingerprinting,
 * and deduplication into a single normalization pipeline.
 */

#ifndef __TELEMETRYD_NORMALIZER_H__
#define __TELEMETRYD_NORMALIZER_H__

#include "../telemetryd.h"
#include <stdbool.h>

/* Opaque normalizer handle */
struct normalizer;

/**
 * normalizer_create - Create a normalizer instance
 * @dedupe_window_sec: Deduplication window in seconds (e.g., 60)
 *
 * Initializes all pattern matching engines and creates dedup window.
 *
 * Returns: Normalizer handle, or NULL on failure
 */
struct normalizer *normalizer_create(int dedupe_window_sec);

/**
 * normalizer_destroy - Free normalizer instance
 * @n: Normalizer handle
 */
void normalizer_destroy(struct normalizer *n);

/**
 * normalizer_process - Normalize a raw event
 * @n: Normalizer handle
 * @source: Event source type
 * @priority: journald priority (0-7), or -1 if not applicable
 * @message: Event message
 * @systemd_unit: _SYSTEMD_UNIT field (may be NULL)
 * @unit: UNIT field (may be NULL)
 * @ts_ns: Timestamp in nanoseconds (0 = use current time)
 * @out_event: Output event structure
 *
 * Performs full normalization:
 * 1. Detects category from message
 * 2. Extracts entity
 * 3. Computes fingerprint
 * 4. Checks deduplication
 *
 * Returns: true if event should be emitted, false if deduplicated
 */
bool normalizer_process(struct normalizer *n,
                        telem_source_t source,
                        int priority,
                        const char *message,
                        const char *systemd_unit,
                        const char *unit,
                        uint64_t ts_ns,
                        struct telem_event *out_event);

/**
 * normalizer_process_prenormalized - Process a pre-normalized event
 * @n: Normalizer handle
 * @source: Event source type
 * @severity: Pre-determined severity
 * @category: Pre-determined category
 * @message: Event message
 * @entity: Pre-determined entity (may be NULL)
 * @ts_ns: Timestamp in nanoseconds (0 = use current time)
 * @out_event: Output event structure
 *
 * Used for events that are already partially normalized (e.g., eBPF events
 * that come with category already set).
 *
 * Returns: true if event should be emitted, false if deduplicated
 */
bool normalizer_process_prenormalized(struct normalizer *n,
                                      telem_source_t source,
                                      telem_severity_t severity,
                                      telem_category_t category,
                                      const char *message,
                                      const char *entity,
                                      uint64_t ts_ns,
                                      struct telem_event *out_event);

/**
 * normalizer_get_stats - Get normalizer statistics
 * @n: Normalizer handle
 * @out_processed: Total events processed (may be NULL)
 * @out_deduplicated: Total events deduplicated (may be NULL)
 * @out_by_category: Array of counts per category (may be NULL)
 *                   Must be at least TELEM_CAT_OTHER+1 elements
 */
void normalizer_get_stats(struct normalizer *n,
                          uint64_t *out_processed,
                          uint64_t *out_deduplicated,
                          uint64_t *out_by_category);

/**
 * normalizer_reset_stats - Reset statistics counters
 * @n: Normalizer handle
 */
void normalizer_reset_stats(struct normalizer *n);

#endif /* __TELEMETRYD_NORMALIZER_H__ */
