// SPDX-License-Identifier: MIT
/**
 * inotify.c - File monitoring with inotify
 *
 * Monitors security-sensitive files using inotify and generates
 * simplified unified diffs for text file changes.
 *
 * Watched events:
 * - IN_MODIFY: File content modified
 * - IN_CREATE: File created
 * - IN_DELETE: File deleted
 * - IN_ATTRIB: File attributes changed (permissions, owner)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/inotify.h>

#include "inotify.h"

/* ==========================================================================
 * Data Structures
 * ========================================================================== */

struct watch_entry {
    int wd;                             /* inotify watch descriptor */
    char path[256];                     /* File path */
    bool cache_content;                 /* Whether to cache for diff */
    char *content;                      /* Cached content (malloc'd) */
    size_t content_len;                 /* Cached content length */
    time_t mtime;                       /* Last modification time */
};

struct inotify_watcher {
    int inotify_fd;
    struct watch_entry watches[INOTIFY_MAX_WATCHES];
    int num_watches;
    uint64_t events_processed;
};

/* ==========================================================================
 * Helpers
 * ========================================================================== */

static char *read_file_content(const char *path, size_t *out_len)
{
    FILE *f = fopen(path, "r");
    if (!f)
        return NULL;

    /* Get file size */
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (size <= 0 || size > INOTIFY_MAX_CONTENT) {
        fclose(f);
        if (out_len)
            *out_len = 0;
        return NULL;
    }

    char *content = malloc(size + 1);
    if (!content) {
        fclose(f);
        return NULL;
    }

    size_t nread = fread(content, 1, size, f);
    content[nread] = '\0';
    fclose(f);

    if (out_len)
        *out_len = nread;

    return content;
}

static time_t get_mtime(const char *path)
{
    struct stat st;
    if (stat(path, &st) < 0)
        return 0;
    return st.st_mtime;
}

/* ==========================================================================
 * Simple Diff Generation
 * ========================================================================== */

/**
 * generate_simple_diff - Generate a simplified diff
 * @old_content: Old file content (may be NULL)
 * @new_content: New file content (may be NULL)
 * @path: File path (for header)
 *
 * Returns: malloc'd diff string, or NULL if no diff
 */
static char *generate_simple_diff(const char *old_content, const char *new_content, const char *path)
{
    /* Handle NULL cases */
    if (!old_content && !new_content)
        return NULL;

    /* Allocate output buffer */
    size_t buf_size = 8192;
    char *diff = malloc(buf_size);
    if (!diff)
        return NULL;

    size_t pos = 0;

    /* Header */
    pos += snprintf(diff + pos, buf_size - pos, "--- %s\n+++ %s\n", path, path);

    if (!old_content) {
        /* New file - show all lines as additions */
        pos += snprintf(diff + pos, buf_size - pos, "@@ -0,0 +1 @@\n");
        const char *line = new_content;
        while (*line && pos < buf_size - 256) {
            const char *eol = strchr(line, '\n');
            if (!eol)
                eol = line + strlen(line);
            pos += snprintf(diff + pos, buf_size - pos, "+%.*s\n", (int)(eol - line), line);
            line = eol;
            if (*line)
                line++;
        }
    } else if (!new_content) {
        /* Deleted file - show all lines as deletions */
        pos += snprintf(diff + pos, buf_size - pos, "@@ -1 +0,0 @@\n");
        const char *line = old_content;
        while (*line && pos < buf_size - 256) {
            const char *eol = strchr(line, '\n');
            if (!eol)
                eol = line + strlen(line);
            pos += snprintf(diff + pos, buf_size - pos, "-%.*s\n", (int)(eol - line), line);
            line = eol;
            if (*line)
                line++;
        }
    } else if (strcmp(old_content, new_content) != 0) {
        /* Content changed - simple before/after */
        pos += snprintf(diff + pos, buf_size - pos, "@@ -1 +1 @@\n");

        /* Show first few changed lines from old */
        const char *line = old_content;
        int count = 0;
        while (*line && count < 5 && pos < buf_size - 256) {
            const char *eol = strchr(line, '\n');
            if (!eol)
                eol = line + strlen(line);
            pos += snprintf(diff + pos, buf_size - pos, "-%.*s\n", (int)(eol - line), line);
            line = eol;
            if (*line)
                line++;
            count++;
        }

        /* Show first few changed lines from new */
        line = new_content;
        count = 0;
        while (*line && count < 5 && pos < buf_size - 256) {
            const char *eol = strchr(line, '\n');
            if (!eol)
                eol = line + strlen(line);
            pos += snprintf(diff + pos, buf_size - pos, "+%.*s\n", (int)(eol - line), line);
            line = eol;
            if (*line)
                line++;
            count++;
        }
    } else {
        /* No change */
        free(diff);
        return NULL;
    }

    return diff;
}

/* ==========================================================================
 * Watch Management
 * ========================================================================== */

static struct watch_entry *find_watch_by_wd(struct inotify_watcher *w, int wd)
{
    for (int i = 0; i < w->num_watches; i++) {
        if (w->watches[i].wd == wd)
            return &w->watches[i];
    }
    return NULL;
}

static void update_cached_content(struct watch_entry *we)
{
    if (!we->cache_content)
        return;

    /* Free old content */
    free(we->content);
    we->content = NULL;
    we->content_len = 0;

    /* Read new content */
    we->content = read_file_content(we->path, &we->content_len);
    we->mtime = get_mtime(we->path);
}

/* ==========================================================================
 * Public API
 * ========================================================================== */

struct inotify_watcher *inotify_watcher_create(void)
{
    struct inotify_watcher *w = calloc(1, sizeof(*w));
    if (!w)
        return NULL;

    w->inotify_fd = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (w->inotify_fd < 0) {
        free(w);
        return NULL;
    }

    return w;
}

void inotify_watcher_destroy(struct inotify_watcher *w)
{
    if (!w)
        return;

    /* Free cached content */
    for (int i = 0; i < w->num_watches; i++) {
        free(w->watches[i].content);
    }

    if (w->inotify_fd >= 0)
        close(w->inotify_fd);

    free(w);
}

int inotify_watcher_add_path(struct inotify_watcher *w, const char *path, bool cache_content)
{
    if (!w || !path || w->num_watches >= INOTIFY_MAX_WATCHES)
        return -1;

    /* Check if file exists */
    struct stat st;
    if (stat(path, &st) < 0 && errno != ENOENT)
        return -1;

    /* For non-existent files, watch the parent directory */
    char watch_path[256];
    bool watch_parent = false;

    if (errno == ENOENT) {
        /* File doesn't exist - watch parent for creation */
        snprintf(watch_path, sizeof(watch_path), "%.*s", (int)(sizeof(watch_path) - 1), path);
        char *slash = strrchr(watch_path, '/');
        if (slash) {
            *slash = '\0';
            watch_parent = true;
        } else {
            return -1;
        }
    } else {
        snprintf(watch_path, sizeof(watch_path), "%.*s", (int)(sizeof(watch_path) - 1), path);
    }

    /* Add inotify watch */
    uint32_t mask = IN_MODIFY | IN_CREATE | IN_DELETE | IN_ATTRIB |
                    IN_MOVE_SELF | IN_DELETE_SELF;

    int wd = inotify_add_watch(w->inotify_fd, watch_path, mask);
    if (wd < 0)
        return -1;

    /* Store watch entry */
    struct watch_entry *we = &w->watches[w->num_watches++];
    we->wd = wd;
    snprintf(we->path, sizeof(we->path), "%.*s", (int)(sizeof(we->path) - 1), path);  /* Store original path */
    we->cache_content = cache_content;

    /* Cache initial content if requested */
    if (cache_content && !watch_parent) {
        update_cached_content(we);
    }

    return 0;
}

int inotify_watcher_add_default_paths(struct inotify_watcher *w)
{
    int count = 0;

    /* Root authorized_keys */
    if (inotify_watcher_add_path(w, "/root/.ssh/authorized_keys", true) == 0)
        count++;

    /* SSH config */
    if (inotify_watcher_add_path(w, "/etc/ssh/sshd_config", true) == 0)
        count++;

    /* Sudoers */
    if (inotify_watcher_add_path(w, "/etc/sudoers", true) == 0)
        count++;

    /* passwd/shadow */
    if (inotify_watcher_add_path(w, "/etc/passwd", true) == 0)
        count++;
    if (inotify_watcher_add_path(w, "/etc/shadow", true) == 0)
        count++;

    /* Scan /home for user authorized_keys */
    DIR *home_dir = opendir("/home");
    if (home_dir) {
        struct dirent *entry;
        while ((entry = readdir(home_dir)) != NULL) {
            if (entry->d_type == DT_DIR &&
                entry->d_name[0] != '.') {
                char path[512];
                snprintf(path, sizeof(path), "/home/%s/.ssh/authorized_keys", entry->d_name);
                if (inotify_watcher_add_path(w, path, true) == 0)
                    count++;
            }
        }
        closedir(home_dir);
    }

    return count;
}

int inotify_watcher_get_fd(struct inotify_watcher *w)
{
    return w ? w->inotify_fd : -1;
}

int inotify_watcher_process(struct inotify_watcher *w,
                            inotify_event_callback callback,
                            void *user_data)
{
    char buf[4096] __attribute__((aligned(__alignof__(struct inotify_event))));
    int events_processed = 0;

    if (!w || w->inotify_fd < 0)
        return -1;

    ssize_t len;
    while ((len = read(w->inotify_fd, buf, sizeof(buf))) > 0) {
        const struct inotify_event *event;

        for (char *ptr = buf; ptr < buf + len;
             ptr += sizeof(struct inotify_event) + event->len) {
            event = (const struct inotify_event *)ptr;

            struct watch_entry *we = find_watch_by_wd(w, event->wd);
            if (!we)
                continue;

            /* Determine event type */
            const char *event_type = NULL;
            if (event->mask & IN_MODIFY)
                event_type = "modified";
            else if (event->mask & IN_CREATE)
                event_type = "created";
            else if (event->mask & IN_DELETE)
                event_type = "deleted";
            else if (event->mask & (IN_ATTRIB | IN_MOVE_SELF | IN_DELETE_SELF))
                event_type = "attrib";

            if (!event_type)
                continue;

            /* Generate diff for modifications */
            char *diff = NULL;
            if (we->cache_content && (event->mask & IN_MODIFY)) {
                /* Read new content */
                size_t new_len = 0;
                char *new_content = read_file_content(we->path, &new_len);

                /* Generate diff */
                diff = generate_simple_diff(we->content, new_content, we->path);

                /* Update cache */
                free(we->content);
                we->content = new_content;
                we->content_len = new_len;
            }

            /* Call callback */
            if (callback) {
                callback(we->path, event_type, diff, user_data);
            }

            free(diff);
            events_processed++;
            w->events_processed++;
        }
    }

    if (len < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
        return -1;
    }

    return events_processed;
}
