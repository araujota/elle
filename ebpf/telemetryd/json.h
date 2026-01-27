// SPDX-License-Identifier: MIT
/**
 * json.h - NDJSON serialization for telemetry events
 *
 * Serializes telem_event structures to NDJSON format compatible with
 * the Python TelemetryEvent model.
 */

#ifndef __TELEMETRYD_JSON_H__
#define __TELEMETRYD_JSON_H__

#include "telemetryd.h"
#include <jansson.h>

/**
 * telem_json_event - Serialize a telemetry event to JSON
 * @evt: Event to serialize
 *
 * Serializes the event to a compact JSON string.
 *
 * Returns: malloc'd JSON string (caller must free), NULL on error
 */
char *telem_json_event(const struct telem_event *evt);

/**
 * telem_json_event_with_raw - Serialize event with raw data included
 * @evt: Event to serialize
 * @raw_json: Additional raw JSON object to merge (may be NULL)
 *
 * Serializes the event and merges in the raw JSON object.
 *
 * Returns: malloc'd JSON string (caller must free), NULL on error
 */
char *telem_json_event_with_raw(const struct telem_event *evt, json_t *raw_json);

#endif /* __TELEMETRYD_JSON_H__ */
