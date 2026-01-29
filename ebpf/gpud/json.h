// SPDX-License-Identifier: MIT
/**
 * json.h - NDJSON serialization for GPU events
 */

#ifndef __GPUD_JSON_H__
#define __GPUD_JSON_H__

#include "gpud.h"

/**
 * gpud_json_event - Serialize GPU event to NDJSON
 *
 * @evt: Event to serialize
 *
 * Returns: Allocated JSON string, or NULL on failure.
 *          Caller must free() the returned string.
 */
char *gpud_json_event(const struct gpud_event *evt);

#endif /* __GPUD_JSON_H__ */
