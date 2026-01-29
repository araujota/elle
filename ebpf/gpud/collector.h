// SPDX-License-Identifier: MIT
/**
 * collector.h - GPU metrics collector
 *
 * Polls NVIDIA GPUs via NVML and generates telemetry events
 * based on thresholds.
 */

#ifndef __GPUD_COLLECTOR_H__
#define __GPUD_COLLECTOR_H__

#include "gpud.h"
#include "nvml.h"
#include "client.h"

/* Forward declaration */
struct gpud_collector;

/**
 * gpud_collector_create - Create GPU collector
 *
 * @api: NVML API handle (must remain valid while collector exists)
 * @config: Configuration
 *
 * Returns: Collector handle, or NULL on failure.
 */
struct gpud_collector *gpud_collector_create(nvml_api_t *api,
                                              const struct gpud_config *config);

/**
 * gpud_collector_destroy - Free collector resources
 */
void gpud_collector_destroy(struct gpud_collector *collector);

/**
 * gpud_collector_poll - Poll all GPUs and emit events
 *
 * @collector: Collector handle
 * @client: Client for writing events
 *
 * Returns: Number of events emitted, or -1 on error.
 */
int gpud_collector_poll(struct gpud_collector *collector, struct gpud_client *client);

/**
 * gpud_collector_get_device_count - Get number of GPUs
 */
unsigned int gpud_collector_get_device_count(struct gpud_collector *collector);

#endif /* __GPUD_COLLECTOR_H__ */
