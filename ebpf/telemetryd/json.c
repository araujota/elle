// SPDX-License-Identifier: MIT
/**
 * json.c - NDJSON serialization for telemetry events
 *
 * Serializes telem_event structures to NDJSON format compatible with
 * the Python TelemetryEvent model.
 *
 * Output format:
 * {
 *   "ts": 1706300000000000,           // nanoseconds since epoch
 *   "source": "journal|kernel|...",
 *   "severity": "info|warning|...",
 *   "category": "oom|disk|...",
 *   "message": "Human-readable message",
 *   "entity": "service:nginx|null",
 *   "fingerprint": "sha256_16char",
 *   "raw": { ... optional raw data ... }
 * }
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jansson.h>

#include "json.h"

/* ==========================================================================
 * Serialization
 * ========================================================================== */

char *telem_json_event(const struct telem_event *evt)
{
    return telem_json_event_with_raw(evt, NULL);
}

char *telem_json_event_with_raw(const struct telem_event *evt, json_t *raw_json)
{
    json_t *obj;
    char *result;

    if (!evt)
        return NULL;

    obj = json_object();
    if (!obj)
        return NULL;

    /* Timestamp (nanoseconds) */
    json_object_set_new(obj, "ts", json_integer(evt->ts_ns));

    /* Source */
    json_object_set_new(obj, "source", json_string(source_to_str(evt->source)));

    /* Severity */
    json_object_set_new(obj, "severity", json_string(severity_to_str(evt->severity)));

    /* Category */
    json_object_set_new(obj, "category", json_string(category_to_str(evt->category)));

    /* Message */
    json_object_set_new(obj, "message", json_string(evt->message));

    /* Entity (null if not present) */
    if (evt->has_entity && evt->entity[0]) {
        json_object_set_new(obj, "entity", json_string(evt->entity));
    } else {
        json_object_set_new(obj, "entity", json_null());
    }

    /* Fingerprint */
    json_object_set_new(obj, "fingerprint", json_string(evt->fingerprint));

    /* Raw data (if provided) */
    if (raw_json) {
        json_object_set(obj, "raw", raw_json);  /* Not _new, don't steal ref */
    } else {
        json_object_set_new(obj, "raw", json_object());
    }

    /* Serialize to compact JSON */
    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}
