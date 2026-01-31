// SPDX-License-Identifier: MIT
/**
 * json.c - NDJSON serialization for GPU events
 *
 * Serializes gpud_event structures to NDJSON format compatible with
 * telemetryd's event format.
 *
 * Output format:
 * {
 *   "ts": 1706300000000000000,
 *   "source": "gpu",
 *   "severity": "info|warning|...",
 *   "category": "gpu",
 *   "message": "Human-readable message",
 *   "entity": "gpu:0",
 *   "fingerprint": "hash16chars",
 *   "raw": {
 *     "gpu_index": 0,
 *     "gpu_name": "...",
 *     "gpu_uuid": "...",
 *     "mem_used_mb": 1234,
 *     "mem_total_mb": 24576,
 *     "mem_pct": 5,
 *     "util_pct": 87,
 *     "temp_c": 72,
 *     "power_w": 320,
 *     "throttle_reasons": 0,
 *     "ecc_errors": 0,
 *     "processes": [...]
 *   }
 * }
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jansson.h>

#include "json.h"

char *gpud_json_event(const struct gpud_event *evt)
{
    json_t *obj;
    json_t *raw;
    json_t *processes;
    char *result;

    if (!evt)
        return NULL;

    obj = json_object();
    if (!obj)
        return NULL;

    /* Timestamp (nanoseconds) */
    json_object_set_new(obj, "ts", json_integer((json_int_t)evt->ts_ns));

    /* Source - always "gpu" */
    json_object_set_new(obj, "source", json_string("gpu"));

    /* Severity */
    json_object_set_new(obj, "severity", json_string(gpud_severity_to_str(evt->severity)));

    /* Category - always "gpu" */
    json_object_set_new(obj, "category", json_string("gpu"));

    /* Message */
    json_object_set_new(obj, "message", json_string(evt->message));

    /* Entity */
    if (evt->entity[0]) {
        json_object_set_new(obj, "entity", json_string(evt->entity));
    } else {
        json_object_set_new(obj, "entity", json_null());
    }

    /* Fingerprint */
    json_object_set_new(obj, "fingerprint", json_string(evt->fingerprint));

    /* Raw data */
    raw = json_object();
    if (raw && evt->device) {
        struct gpud_device *dev = evt->device;

        json_object_set_new(raw, "gpu_index", json_integer(dev->index));
        json_object_set_new(raw, "gpu_name", json_string(dev->name));
        json_object_set_new(raw, "gpu_uuid", json_string(dev->uuid));

        /* Memory in MB for readability */
        json_object_set_new(raw, "mem_used_mb",
                            json_integer((json_int_t)(dev->mem_used_bytes / (1024ULL * 1024ULL))));
        json_object_set_new(raw, "mem_total_mb",
                            json_integer((json_int_t)(dev->mem_total_bytes / (1024ULL * 1024ULL))));
        json_object_set_new(raw, "mem_pct", json_integer(dev->mem_pct));

        json_object_set_new(raw, "util_pct", json_integer(dev->util_gpu_pct));
        json_object_set_new(raw, "temp_c", json_integer(dev->temp_c));

        /* Power in watts */
        json_object_set_new(raw, "power_w", json_integer((json_int_t)(dev->power_mw / 1000)));

        json_object_set_new(raw, "throttle_reasons", json_integer((json_int_t)dev->throttle_reasons));
        json_object_set_new(raw, "ecc_errors",
                            json_integer((json_int_t)(dev->ecc_errors_corrected + dev->ecc_errors_uncorrected)));

        /* Processes */
        processes = json_array();
        if (processes) {
            for (unsigned int i = 0; i < dev->process_count; i++) {
                json_t *proc = json_object();
                if (proc) {
                    json_object_set_new(proc, "pid", json_integer(dev->processes[i].pid));
                    json_object_set_new(proc, "name", json_string(dev->processes[i].name));
                    json_object_set_new(proc, "mem_mb",
                                        json_integer((json_int_t)(dev->processes[i].mem_bytes / (1024ULL * 1024ULL))));
                    json_array_append_new(processes, proc);
                }
            }
            json_object_set_new(raw, "processes", processes);
        }

        json_object_set_new(obj, "raw", raw);
    } else {
        json_object_set_new(obj, "raw", json_object());
        if (raw)
            json_decref(raw);
    }

    /* Serialize to compact JSON */
    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}
