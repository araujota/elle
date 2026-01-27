// SPDX-License-Identifier: MIT
/**
 * dedup.h - Ring buffer deduplication
 *
 * Implements a fixed-size ring buffer for fingerprint-based event
 * deduplication within a time window.
 */

#ifndef __TELEMETRYD_DEDUP_H__
#define __TELEMETRYD_DEDUP_H__

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* Default ring buffer capacity */
#define DEDUP_DEFAULT_CAPACITY 100000

/* Opaque dedup window handle */
struct dedup_window;

/**
 * dedup_create - Create a deduplication window
 * @window_sec: Time window in seconds (e.g., 60)
 * @capacity: Maximum entries in ring buffer (0 = use default)
 *
 * Returns: Dedup window handle, or NULL on failure
 */
struct dedup_window *dedup_create(int window_sec, size_t capacity);

/**
 * dedup_destroy - Free dedup window
 * @w: Window handle
 */
void dedup_destroy(struct dedup_window *w);

/**
 * dedup_check - Check if fingerprint is a duplicate
 * @w: Window handle
 * @fingerprint: 16-char hex fingerprint string
 *
 * If not a duplicate, records the fingerprint in the window.
 *
 * Returns: true if duplicate, false if new (and recorded)
 */
bool dedup_check(struct dedup_window *w, const char *fingerprint);

/**
 * dedup_get_stats - Get deduplication statistics
 * @w: Window handle
 * @out_total: Total events checked (may be NULL)
 * @out_dupes: Total duplicates found (may be NULL)
 * @out_entries: Current entries in window (may be NULL)
 */
void dedup_get_stats(struct dedup_window *w,
                     uint64_t *out_total,
                     uint64_t *out_dupes,
                     size_t *out_entries);

/**
 * dedup_reset_stats - Reset statistics counters
 * @w: Window handle
 */
void dedup_reset_stats(struct dedup_window *w);

#endif /* __TELEMETRYD_DEDUP_H__ */
