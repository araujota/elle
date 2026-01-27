// SPDX-License-Identifier: MIT
/**
 * fingerprint.h - SHA256-based event fingerprinting
 *
 * Generates fingerprints for event deduplication using the same
 * algorithm as Python TelemetryEvent.compute_fingerprint().
 */

#ifndef __TELEMETRYD_FINGERPRINT_H__
#define __TELEMETRYD_FINGERPRINT_H__

#include <stddef.h>

/* Fingerprint length (16 hex chars + null) */
#define FINGERPRINT_LEN 17

/**
 * fingerprint_compute - Compute fingerprint from event components
 * @category: Event category string
 * @entity: Entity string (may be NULL)
 * @message: Event message
 * @out_fp: Output buffer (must be at least FINGERPRINT_LEN bytes)
 *
 * Computes SHA256(category:entity:normalized_message)[:16]
 * The message is normalized by lowercasing and removing dynamic
 * content (dates, times, numbers).
 */
void fingerprint_compute(const char *category, const char *entity,
                         const char *message, char *out_fp);

/**
 * fingerprint_compute_raw - Compute fingerprint without normalization
 * @category: Event category string
 * @entity: Entity string (may be NULL)
 * @message: Event message (not normalized)
 * @out_fp: Output buffer (must be at least FINGERPRINT_LEN bytes)
 *
 * Same as fingerprint_compute but skips message normalization.
 * Used for pre-normalized content.
 */
void fingerprint_compute_raw(const char *category, const char *entity,
                             const char *message, char *out_fp);

#endif /* __TELEMETRYD_FINGERPRINT_H__ */
