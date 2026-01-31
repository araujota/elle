// SPDX-License-Identifier: MIT
/**
 * inotify.h - File monitoring with inotify
 *
 * Monitors security-sensitive files using inotify and generates
 * unified diffs for text file changes.
 */

#ifndef __TELEMETRYD_INOTIFY_H__
#define __TELEMETRYD_INOTIFY_H__

#include "../telemetryd.h"
#include <stdbool.h>

/* Maximum watched paths */
#define INOTIFY_MAX_WATCHES 64

/* Maximum content to cache for diff (64KB) */
#define INOTIFY_MAX_CONTENT (64L * 1024)

/* Opaque inotify watcher handle */
struct inotify_watcher;

/**
 * inotify_watcher_create - Create an inotify watcher
 *
 * Creates an inotify instance for file monitoring.
 *
 * Returns: Watcher handle, or NULL on failure
 */
struct inotify_watcher *inotify_watcher_create(void);

/**
 * inotify_watcher_destroy - Free inotify watcher
 * @w: Watcher handle
 */
void inotify_watcher_destroy(struct inotify_watcher *w);

/**
 * inotify_watcher_add_path - Add a path to watch
 * @w: Watcher handle
 * @path: Path to watch
 * @cache_content: Whether to cache content for diff generation
 *
 * Returns: 0 on success, -1 on failure
 */
int inotify_watcher_add_path(struct inotify_watcher *w, const char *path, bool cache_content);

/**
 * inotify_watcher_add_default_paths - Add default security-sensitive paths
 * @w: Watcher handle
 *
 * Adds:
 * - /root/.ssh/authorized_keys
 * - /home/<user>/.ssh/authorized_keys (all users)
 * - /etc/ssh/sshd_config
 * - /etc/sudoers
 * - /etc/passwd
 * - /etc/shadow
 *
 * Returns: Number of paths added
 */
int inotify_watcher_add_default_paths(struct inotify_watcher *w);

/**
 * inotify_watcher_get_fd - Get file descriptor for poll()
 * @w: Watcher handle
 *
 * Returns: inotify file descriptor, or -1 on error
 */
int inotify_watcher_get_fd(struct inotify_watcher *w);

/**
 * inotify_event_callback - Callback for file events
 * @path: Path that changed
 * @event_type: "modified", "created", "deleted", "attrib"
 * @diff: Unified diff (may be NULL if not available)
 * @user_data: User-provided context
 */
typedef void (*inotify_event_callback)(
    const char *path,
    const char *event_type,
    const char *diff,
    void *user_data
);

/**
 * inotify_watcher_process - Process available inotify events
 * @w: Watcher handle
 * @callback: Function called for each event
 * @user_data: Passed to callback
 *
 * Returns: Number of events processed, or -1 on error
 */
int inotify_watcher_process(struct inotify_watcher *w,
                            inotify_event_callback callback,
                            void *user_data);

#endif /* __TELEMETRYD_INOTIFY_H__ */
