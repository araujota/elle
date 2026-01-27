// SPDX-License-Identifier: MIT
/**
 * json.h - NDJSON serialization for eBPF events
 */

#ifndef __ELLED_JSON_H__
#define __ELLED_JSON_H__

#include "elled.h"

/**
 * elled_json_oom_event - Serialize OOM event to JSON
 * @evt: OOM event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_oom_event(const struct elled_oom_event *evt);

/**
 * elled_json_exec_event - Serialize exec event to JSON
 * @evt: Exec event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_exec_event(const struct elled_exec_event *evt);

/**
 * elled_json_exit_event - Serialize process exit event to JSON
 * @evt: Exit event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_exit_event(const struct elled_exit_event *evt);

/**
 * elled_json_block_event - Serialize block I/O event to JSON
 * @evt: Block event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_block_event(const struct elled_block_event *evt);

/**
 * elled_json_tcp_event - Serialize TCP retransmit event to JSON
 * @evt: TCP event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_tcp_event(const struct elled_tcp_retrans_event *evt);

/**
 * elled_json_cap_deny_event - Serialize capability denial event to JSON
 * @evt: Capability denial event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_cap_deny_event(const struct elled_cap_deny_event *evt);

/**
 * elled_json_file_deny_event - Serialize file permission denial event to JSON
 * @evt: File denial event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_file_deny_event(const struct elled_file_deny_event *evt);

/**
 * elled_json_mount_event - Serialize mount operation event to JSON
 * @evt: Mount event structure
 *
 * Returns: Allocated JSON string (caller must free), NULL on error
 */
char *elled_json_mount_event(const struct elled_mount_event *evt);

#endif /* __ELLED_JSON_H__ */
