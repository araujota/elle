// SPDX-License-Identifier: MIT
/**
 * journal.c - Journal watcher using sd-journal API
 *
 * Uses libsystemd's sd-journal API to read journal entries directly,
 * avoiding the overhead of spawning a journalctl subprocess.
 *
 * Benefits over subprocess approach:
 * - No subprocess spawn (~100KB memory saved)
 * - Direct access to binary journal data (no JSON parsing)
 * - Cursor-based following (reliable, no missed events)
 * - Can filter efficiently on kernel messages
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <systemd/sd-journal.h>

#include "journal.h"

/* ==========================================================================
 * Data Structures
 * ========================================================================== */

struct journal_watcher {
    sd_journal *journal;
    bool kernel_only;
    uint64_t entries_read;
};

/* ==========================================================================
 * Creation
 * ========================================================================== */

struct journal_watcher *journal_watcher_create(bool kernel_only)
{
    struct journal_watcher *w;
    int r;

    w = calloc(1, sizeof(*w));
    if (!w)
        return NULL;

    w->kernel_only = kernel_only;

    /* Open the journal */
    r = sd_journal_open(&w->journal, SD_JOURNAL_LOCAL_ONLY);
    if (r < 0) {
        fprintf(stderr, "ERROR: Failed to open journal: %s\n", strerror(-r));
        free(w);
        return NULL;
    }

    /* Add filter for kernel messages if requested */
    if (kernel_only) {
        r = sd_journal_add_match(w->journal, "_TRANSPORT=kernel", 0);
        if (r < 0) {
            fprintf(stderr, "ERROR: Failed to add kernel filter: %s\n", strerror(-r));
            sd_journal_close(w->journal);
            free(w);
            return NULL;
        }
    }

    /* Seek to end (tail mode) */
    r = sd_journal_seek_tail(w->journal);
    if (r < 0) {
        fprintf(stderr, "ERROR: Failed to seek to tail: %s\n", strerror(-r));
        sd_journal_close(w->journal);
        free(w);
        return NULL;
    }

    /* Move back one entry and then forward to position at end */
    sd_journal_previous(w->journal);

    return w;
}

void journal_watcher_destroy(struct journal_watcher *w)
{
    if (!w)
        return;

    if (w->journal)
        sd_journal_close(w->journal);

    free(w);
}

/* ==========================================================================
 * File Descriptor for Polling
 * ========================================================================== */

int journal_watcher_get_fd(struct journal_watcher *w)
{
    if (!w || !w->journal)
        return -1;

    return sd_journal_get_fd(w->journal);
}

/* ==========================================================================
 * Wait for New Entries
 * ========================================================================== */

int journal_watcher_wait(struct journal_watcher *w, int timeout_ms)
{
    int r;
    uint64_t timeout_us;

    if (!w || !w->journal)
        return -1;

    /* Convert milliseconds to microseconds */
    if (timeout_ms < 0)
        timeout_us = (uint64_t)-1;  /* Infinite */
    else
        timeout_us = (uint64_t)timeout_ms * 1000;

    r = sd_journal_wait(w->journal, timeout_us);
    if (r < 0) {
        if (r == -EINTR)
            return 0;  /* Interrupted, treat as timeout */
        fprintf(stderr, "ERROR: sd_journal_wait failed: %s\n", strerror(-r));
        return -1;
    }

    /* SD_JOURNAL_NOP = no change, SD_JOURNAL_APPEND = new entries */
    return (r == SD_JOURNAL_APPEND || r == SD_JOURNAL_INVALIDATE) ? 1 : 0;
}

/* ==========================================================================
 * Helper: Get String Field
 * ========================================================================== */

static const char *get_field(sd_journal *j, const char *field, char *buf, size_t buf_len)
{
    const void *data;
    size_t len;
    int r;

    r = sd_journal_get_data(j, field, &data, &len);
    if (r < 0)
        return NULL;

    /* Data is in format "FIELD=value" */
    const char *eq = memchr(data, '=', len);
    if (!eq)
        return NULL;

    eq++;  /* Skip '=' */
    size_t value_len = len - (eq - (const char *)data);

    if (value_len >= buf_len)
        value_len = buf_len - 1;

    memcpy(buf, eq, value_len);
    buf[value_len] = '\0';

    return buf;
}

/* ==========================================================================
 * Process Entries
 * ========================================================================== */

int journal_watcher_process(struct journal_watcher *w,
                            journal_entry_callback callback,
                            void *user_data)
{
    int count = 0;
    int r;

    if (!w || !w->journal || !callback)
        return -1;

    /* Process all available entries */
    while ((r = sd_journal_next(w->journal)) > 0) {
        uint64_t realtime_us;
        char message_buf[4096];
        char unit_buf[256];
        char systemd_unit_buf[256];
        char transport_buf[64];
        char comm_buf[64];
        char priority_buf[8];

        /* Get timestamp */
        r = sd_journal_get_realtime_usec(w->journal, &realtime_us);
        if (r < 0)
            realtime_us = 0;

        /* Get MESSAGE (required) */
        const char *message = get_field(w->journal, "MESSAGE", message_buf, sizeof(message_buf));
        if (!message || !message[0])
            continue;  /* Skip entries without message */

        /* Get PRIORITY */
        const char *priority_str = get_field(w->journal, "PRIORITY", priority_buf, sizeof(priority_buf));
        int priority = priority_str ? (int)strtol(priority_str, NULL, 10) : 6;  /* Default: info */

        /* Get optional fields */
        const char *systemd_unit = get_field(w->journal, "_SYSTEMD_UNIT",
                                             systemd_unit_buf, sizeof(systemd_unit_buf));
        const char *unit = get_field(w->journal, "UNIT", unit_buf, sizeof(unit_buf));
        const char *transport = get_field(w->journal, "_TRANSPORT",
                                          transport_buf, sizeof(transport_buf));
        const char *comm = get_field(w->journal, "_COMM", comm_buf, sizeof(comm_buf));

        /* Call callback */
        callback(realtime_us, priority, message, systemd_unit, unit, transport, comm, user_data);

        count++;
        w->entries_read++;
    }

    if (r < 0 && r != -ENOENT) {
        fprintf(stderr, "ERROR: sd_journal_next failed: %s\n", strerror(-r));
        return -1;
    }

    return count;
}
