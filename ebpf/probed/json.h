// SPDX-License-Identifier: MIT
/**
 * json.h - NDJSON serialization for probed events
 */

#ifndef __PROBED_JSON_H__
#define __PROBED_JSON_H__

#include "probed.h"

/**
 * probed_json_psi_event - Serialize PSI pressure event to JSON
 * @evt: PSI event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *probed_json_psi_event(const struct probed_psi_event *evt);

/**
 * probed_json_unit_event - Serialize systemd unit event to JSON
 * @evt: Unit event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *probed_json_unit_event(const struct probed_unit_event *evt);

/**
 * probed_json_dns_event - Serialize DNS probe event to JSON
 * @evt: DNS event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *probed_json_dns_event(const struct probed_dns_event *evt);

/**
 * probed_json_conntrack_event - Serialize conntrack stats event to JSON
 * @evt: Conntrack event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *probed_json_conntrack_event(const struct probed_conntrack_event *evt);

/**
 * probed_json_hardware_event - Serialize hardware error event to JSON
 * @evt: Hardware event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *probed_json_hardware_event(const struct probed_hardware_event *evt);

/**
 * probed_json_auth_event - Serialize auth aggregate event to JSON
 * @evt: Auth event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *probed_json_auth_event(const struct probed_auth_event *evt);

#endif /* __PROBED_JSON_H__ */
