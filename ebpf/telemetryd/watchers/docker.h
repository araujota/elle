// SPDX-License-Identifier: MIT
/**
 * docker.h - Docker events watcher with crashloop detection
 *
 * Monitors Docker container events via subprocess and detects
 * crashlooping containers (3+ restarts in 5 minutes).
 */

#ifndef __TELEMETRYD_DOCKER_H__
#define __TELEMETRYD_DOCKER_H__

#include "../telemetryd.h"
#include <stdbool.h>

/* Crashloop detection thresholds */
#define DOCKER_CRASHLOOP_THRESHOLD 3
#define DOCKER_CRASHLOOP_WINDOW_SEC 300

/* Opaque docker watcher handle */
struct docker_watcher;

/**
 * docker_watcher_create - Create a Docker events watcher
 *
 * Spawns `docker events --format json` subprocess.
 *
 * Returns: Watcher handle, or NULL on failure
 */
struct docker_watcher *docker_watcher_create(void);

/**
 * docker_watcher_destroy - Free Docker watcher
 * @w: Watcher handle
 */
void docker_watcher_destroy(struct docker_watcher *w);

/**
 * docker_watcher_get_fd - Get file descriptor for poll()
 * @w: Watcher handle
 *
 * Returns: File descriptor for stdout of docker events, or -1 on error
 */
int docker_watcher_get_fd(struct docker_watcher *w);

/**
 * docker_event_callback - Callback for Docker events
 * @action: Event action (start, stop, die, kill, oom, restart, crashloop)
 * @container_name: Container name
 * @container_id: Short container ID (12 chars)
 * @exit_code: Exit code (-1 if not applicable)
 * @is_crashloop: true if this is a synthetic crashloop event
 * @restart_count: Number of restarts in window (for crashloop)
 * @user_data: User-provided context
 */
typedef void (*docker_event_callback)(
    const char *action,
    const char *container_name,
    const char *container_id,
    int exit_code,
    bool is_crashloop,
    int restart_count,
    void *user_data
);

/**
 * docker_watcher_process - Process available Docker events
 * @w: Watcher handle
 * @callback: Function called for each event
 * @user_data: Passed to callback
 *
 * Reads and processes available events from the subprocess.
 *
 * Returns: Number of events processed, or -1 on error
 */
int docker_watcher_process(struct docker_watcher *w,
                           docker_event_callback callback,
                           void *user_data);

/**
 * docker_watcher_is_running - Check if watcher subprocess is running
 * @w: Watcher handle
 *
 * Returns: true if running, false otherwise
 */
bool docker_watcher_is_running(struct docker_watcher *w);

/**
 * docker_is_available - Check if Docker is available
 *
 * Runs `docker info` to check if Docker daemon is accessible.
 *
 * Returns: true if available, false otherwise
 */
bool docker_is_available(void);

#endif /* __TELEMETRYD_DOCKER_H__ */
