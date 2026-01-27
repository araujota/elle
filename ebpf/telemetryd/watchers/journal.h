// SPDX-License-Identifier: MIT
/**
 * journal.h - Journal watcher using sd-journal API
 *
 * Streams events from systemd journal, replacing the Python
 * JournalWatcher that spawns journalctl subprocess.
 */

#ifndef __TELEMETRYD_JOURNAL_H__
#define __TELEMETRYD_JOURNAL_H__

#include "../telemetryd.h"
#include <stdbool.h>

/* Opaque journal watcher handle */
struct journal_watcher;

/**
 * journal_watcher_create - Create a journal watcher
 * @kernel_only: If true, only watch kernel messages (_TRANSPORT=kernel)
 *
 * Opens the journal and seeks to the end (tail mode).
 *
 * Returns: Watcher handle, or NULL on failure
 */
struct journal_watcher *journal_watcher_create(bool kernel_only);

/**
 * journal_watcher_destroy - Free journal watcher
 * @w: Watcher handle
 */
void journal_watcher_destroy(struct journal_watcher *w);

/**
 * journal_watcher_get_fd - Get file descriptor for poll()
 * @w: Watcher handle
 *
 * Returns: File descriptor, or -1 on error
 */
int journal_watcher_get_fd(struct journal_watcher *w);

/**
 * journal_watcher_process - Process available journal entries
 * @w: Watcher handle
 * @callback: Function called for each entry
 * @user_data: Passed to callback
 *
 * Processes all available journal entries since last call.
 * For each entry, extracts fields and calls the callback.
 *
 * Returns: Number of entries processed, or -1 on error
 */
typedef void (*journal_entry_callback)(
    uint64_t realtime_us,       /* Timestamp in microseconds */
    int priority,               /* 0-7 syslog priority */
    const char *message,        /* MESSAGE field */
    const char *systemd_unit,   /* _SYSTEMD_UNIT field (may be NULL) */
    const char *unit,           /* UNIT field (may be NULL) */
    const char *transport,      /* _TRANSPORT field (may be NULL) */
    const char *comm,           /* _COMM field (may be NULL) */
    void *user_data
);

int journal_watcher_process(struct journal_watcher *w,
                            journal_entry_callback callback,
                            void *user_data);

/**
 * journal_watcher_wait - Wait for new journal entries
 * @w: Watcher handle
 * @timeout_ms: Timeout in milliseconds (-1 = infinite)
 *
 * Blocks until new entries are available or timeout expires.
 *
 * Returns: 1 if entries available, 0 on timeout, -1 on error
 */
int journal_watcher_wait(struct journal_watcher *w, int timeout_ms);

#endif /* __TELEMETRYD_JOURNAL_H__ */
