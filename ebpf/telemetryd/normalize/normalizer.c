// SPDX-License-Identifier: MIT
/**
 * normalizer.c - Main normalization entry point
 *
 * Combines category detection, entity extraction, fingerprinting,
 * and deduplication into a single normalization pipeline.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "normalizer.h"
#include "category.h"
#include "entity.h"
#include "fingerprint.h"
#include "dedup.h"

/* ==========================================================================
 * Data Structures
 * ========================================================================== */

struct normalizer {
    struct dedup_window *dedup;

    /* Statistics */
    uint64_t processed;
    uint64_t deduplicated;
    uint64_t by_category[TELEM_CAT_OTHER + 1];
};

/* ==========================================================================
 * Initialization
 * ========================================================================== */

struct normalizer *normalizer_create(int dedupe_window_sec)
{
    struct normalizer *n = calloc(1, sizeof(*n));
    if (!n)
        return NULL;

    /* Initialize category patterns */
    if (category_init() < 0) {
        fprintf(stderr, "ERROR: Failed to initialize category patterns\n");
        free(n);
        return NULL;
    }

    /* Initialize entity patterns */
    if (entity_init() < 0) {
        fprintf(stderr, "ERROR: Failed to initialize entity patterns\n");
        category_cleanup();
        free(n);
        return NULL;
    }

    /* Create dedup window */
    n->dedup = dedup_create(dedupe_window_sec, 0);
    if (!n->dedup) {
        fprintf(stderr, "ERROR: Failed to create dedup window\n");
        entity_cleanup();
        category_cleanup();
        free(n);
        return NULL;
    }

    return n;
}

void normalizer_destroy(struct normalizer *n)
{
    if (!n)
        return;

    dedup_destroy(n->dedup);
    entity_cleanup();
    category_cleanup();
    free(n);
}

/* ==========================================================================
 * Normalization
 * ========================================================================== */

bool normalizer_process(struct normalizer *n,
                        telem_source_t source,
                        int priority,
                        const char *message,
                        const char *systemd_unit,
                        const char *unit,
                        uint64_t ts_ns,
                        struct telem_event *out_event)
{
    if (!n || !message || !out_event)
        return false;

    n->processed++;

    /* Initialize output */
    memset(out_event, 0, sizeof(*out_event));

    /* Set timestamp */
    out_event->ts_ns = ts_ns > 0 ? ts_ns : get_timestamp_ns();

    /* Set source */
    out_event->source = source;

    /* Convert priority to severity */
    if (priority >= 0 && priority <= 7) {
        out_event->severity = priority_to_severity(priority);
    } else {
        out_event->severity = TELEM_SEV_INFO;
    }

    /* Detect category */
    out_event->category = category_detect(message);

    /* Kernel panics are always critical */
    if (out_event->category == TELEM_CAT_KERNEL_PANIC) {
        out_event->severity = TELEM_SEV_CRITICAL;
    }

    /* Copy message */
    snprintf(out_event->message, sizeof(out_event->message), "%.*s", (int)(sizeof(out_event->message) - 1), message);

    /* Extract entity */
    out_event->has_entity = entity_extract_from_raw(
        systemd_unit, unit, message, out_event->category,
        out_event->entity, sizeof(out_event->entity)
    );

    /* Compute fingerprint */
    fingerprint_compute(
        category_to_str(out_event->category),
        out_event->has_entity ? out_event->entity : NULL,
        message,
        out_event->fingerprint
    );

    /* Check deduplication */
    if (dedup_check(n->dedup, out_event->fingerprint)) {
        n->deduplicated++;
        return false;  /* Duplicate - don't emit */
    }

    /* Track by category */
    if (out_event->category <= TELEM_CAT_OTHER) {
        n->by_category[out_event->category]++;
    }

    return true;  /* Emit event */
}

bool normalizer_process_prenormalized(struct normalizer *n,
                                      telem_source_t source,
                                      telem_severity_t severity,
                                      telem_category_t category,
                                      const char *message,
                                      const char *entity,
                                      uint64_t ts_ns,
                                      struct telem_event *out_event)
{
    if (!n || !message || !out_event)
        return false;

    n->processed++;

    /* Initialize output */
    memset(out_event, 0, sizeof(*out_event));

    /* Set timestamp */
    out_event->ts_ns = ts_ns > 0 ? ts_ns : get_timestamp_ns();

    /* Set source, severity, category */
    out_event->source = source;
    out_event->severity = severity;
    out_event->category = category;

    /* Copy message */
    snprintf(out_event->message, sizeof(out_event->message), "%.*s", (int)(sizeof(out_event->message) - 1), message);

    /* Copy entity if provided */
    if (entity && entity[0]) {
        snprintf(out_event->entity, sizeof(out_event->entity), "%.*s", (int)(sizeof(out_event->entity) - 1), entity);
        out_event->has_entity = true;
    }

    /* Compute fingerprint */
    fingerprint_compute(
        category_to_str(category),
        out_event->has_entity ? out_event->entity : NULL,
        message,
        out_event->fingerprint
    );

    /* Check deduplication */
    if (dedup_check(n->dedup, out_event->fingerprint)) {
        n->deduplicated++;
        return false;  /* Duplicate - don't emit */
    }

    /* Track by category */
    if (category <= TELEM_CAT_OTHER) {
        n->by_category[category]++;
    }

    return true;  /* Emit event */
}

/* ==========================================================================
 * Statistics
 * ========================================================================== */

void normalizer_get_stats(struct normalizer *n,
                          uint64_t *out_processed,
                          uint64_t *out_deduplicated,
                          uint64_t *out_by_category)
{
    if (!n)
        return;

    if (out_processed)
        *out_processed = n->processed;
    if (out_deduplicated)
        *out_deduplicated = n->deduplicated;
    if (out_by_category) {
        for (int i = 0; i <= TELEM_CAT_OTHER; i++) {
            out_by_category[i] = n->by_category[i];
        }
    }
}

void normalizer_reset_stats(struct normalizer *n)
{
    if (!n)
        return;

    n->processed = 0;
    n->deduplicated = 0;
    memset(n->by_category, 0, sizeof(n->by_category));
    dedup_reset_stats(n->dedup);
}
