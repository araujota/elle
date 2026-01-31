// SPDX-License-Identifier: MIT
/**
 * docker.c - Docker events watcher with crashloop detection
 *
 * Monitors Docker container events by spawning `docker events --format json`
 * and detecting crashlooping containers.
 *
 * Crashloop detection logic:
 * - Track restart timestamps per container in a ring buffer
 * - On restart/start (after die): record timestamp
 * - Clean entries older than 300 seconds
 * - If count >= 3: emit synthetic crashloop event, reset count to 1
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <time.h>
#include <jansson.h>

#include "docker.h"

/* ==========================================================================
 * Constants
 * ========================================================================== */

#define MAX_CONTAINERS 256
#define MAX_RESTART_HISTORY 8
#define LINE_BUFFER_SIZE 4096

/* ==========================================================================
 * Data Structures
 * ========================================================================== */

struct container_state {
    char name[64];
    char id[13];            /* Short ID (12 chars + null) */
    char last_action[16];   /* Last action (start, die, etc.) */
    double restart_times[MAX_RESTART_HISTORY];  /* Ring buffer of timestamps */
    int restart_idx;
    int restart_count;      /* Count in current window */
};

struct docker_watcher {
    pid_t child_pid;
    int stdout_fd;
    bool running;

    /* Container state tracking */
    struct container_state containers[MAX_CONTAINERS];
    int num_containers;

    /* Line buffer for partial reads */
    char line_buffer[LINE_BUFFER_SIZE];
    size_t line_pos;

    /* Stats */
    uint64_t events_received;
    uint64_t crashloops_detected;
};

/* ==========================================================================
 * Helpers
 * ========================================================================== */

static double get_time_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1000000000.0;
}

static int set_nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0)
        return -1;
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

/* ==========================================================================
 * Container State Management
 * ========================================================================== */

static struct container_state *find_container(struct docker_watcher *w, const char *name)
{
    for (int i = 0; i < w->num_containers; i++) {
        if (strcmp(w->containers[i].name, name) == 0)
            return &w->containers[i];
    }
    return NULL;
}

static struct container_state *get_or_create_container(struct docker_watcher *w, const char *name, const char *id)
{
    struct container_state *c = find_container(w, name);
    if (c)
        return c;

    if (w->num_containers >= MAX_CONTAINERS) {
        /* Evict oldest */
        memmove(&w->containers[0], &w->containers[1],
                (MAX_CONTAINERS - 1) * sizeof(struct container_state));
        w->num_containers = MAX_CONTAINERS - 1;
    }

    c = &w->containers[w->num_containers++];
    memset(c, 0, sizeof(*c));
    snprintf(c->name, sizeof(c->name), "%.*s", (int)(sizeof(c->name) - 1), name);
    if (id)
        snprintf(c->id, sizeof(c->id), "%.*s", (int)(sizeof(c->id) - 1), id);

    return c;
}

/* ==========================================================================
 * Crashloop Detection
 * ========================================================================== */

static int check_crashloop(struct docker_watcher *w, struct container_state *c,
                           const char *action, docker_event_callback callback,
                           void *user_data)
{
    double now = get_time_sec();
    bool is_crashloop = false;
    int count = 0;

    /* Track restart on certain actions */
    if (strcmp(action, "restart") == 0 ||
        (strcmp(action, "start") == 0 && strcmp(c->last_action, "die") == 0)) {
        /* Record restart time */
        c->restart_times[c->restart_idx] = now;
        c->restart_idx = (c->restart_idx + 1) % MAX_RESTART_HISTORY;
        if (c->restart_count < MAX_RESTART_HISTORY)
            c->restart_count++;
    }

    /* Count restarts within window */
    double cutoff = now - DOCKER_CRASHLOOP_WINDOW_SEC;
    for (int i = 0; i < c->restart_count; i++) {
        if (c->restart_times[i] > cutoff)
            count++;
    }

    /* Check for crashloop */
    if (count >= DOCKER_CRASHLOOP_THRESHOLD) {
        is_crashloop = true;
        w->crashloops_detected++;

        /* Emit crashloop event via callback */
        if (callback) {
            callback("crashloop", c->name, c->id, -1, true, count, user_data);
        }

        /* Reset to prevent repeated alerts */
        c->restart_times[0] = now;
        c->restart_idx = 1;
        c->restart_count = 1;
    }

    /* Update last action */
    snprintf(c->last_action, sizeof(c->last_action), "%.*s", (int)(sizeof(c->last_action) - 1), action);

    return is_crashloop ? 1 : 0;
}

/* ==========================================================================
 * Event Processing
 * ========================================================================== */

static int process_event_json(struct docker_watcher *w, const char *json_str,
                              docker_event_callback callback, void *user_data)
{
    json_error_t error;
    json_t *root = json_loads(json_str, 0, &error);
    if (!root)
        return 0;  /* Invalid JSON, skip */

    w->events_received++;

    /* Extract fields */
    const char *status = json_string_value(json_object_get(root, "status"));
    const char *action = json_string_value(json_object_get(root, "Action"));

    if (!action)
        action = status;
    if (!action) {
        json_decref(root);
        return 0;
    }

    /* Extract Actor fields */
    json_t *actor = json_object_get(root, "Actor");
    const char *container_id = NULL;
    const char *container_name = "unknown";
    int exit_code = -1;

    if (actor) {
        container_id = json_string_value(json_object_get(actor, "ID"));
        json_t *attrs = json_object_get(actor, "Attributes");
        if (attrs) {
            const char *name = json_string_value(json_object_get(attrs, "name"));
            if (name)
                container_name = name;

            json_t *ec = json_object_get(attrs, "exitCode");
            if (ec) {
                if (json_is_integer(ec))
                    exit_code = (int)json_integer_value(ec);
                else if (json_is_string(ec))
                    exit_code = (int)strtol(json_string_value(ec), NULL, 10);
            }
        }
    }

    /* Truncate container ID to 12 chars */
    char short_id[13] = {0};
    if (container_id && strlen(container_id) > 12) {
        memcpy(short_id, container_id, 12);
    } else if (container_id) {
        snprintf(short_id, sizeof(short_id), "%.*s", (int)(sizeof(short_id) - 1), container_id);
    }

    /* Get or create container state */
    struct container_state *c = get_or_create_container(w, container_name, short_id);

    /* Call callback for the original event */
    if (callback) {
        callback(action, container_name, short_id, exit_code, false, 0, user_data);
    }

    /* Check for crashloop (may trigger additional callback) */
    if (strcmp(action, "restart") == 0 ||
        strcmp(action, "die") == 0 ||
        strcmp(action, "start") == 0) {
        check_crashloop(w, c, action, callback, user_data);
    } else {
        /* Update last action for non-restart events */
        snprintf(c->last_action, sizeof(c->last_action), "%.*s", (int)(sizeof(c->last_action) - 1), action);
    }

    json_decref(root);
    return 1;
}

/* ==========================================================================
 * Public API
 * ========================================================================== */

bool docker_is_available(void)
{
    int status;
    pid_t pid = fork();

    if (pid < 0)
        return false;

    if (pid == 0) {
        /* Child: run docker info */
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDOUT_FILENO);
            dup2(devnull, STDERR_FILENO);
            close(devnull);
        }
        execlp("docker", "docker", "info", NULL);
        _exit(127);
    }

    /* Parent: wait for child */
    waitpid(pid, &status, 0);
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

struct docker_watcher *docker_watcher_create(void)
{
    struct docker_watcher *w;
    int pipefd[2];

    w = calloc(1, sizeof(*w));
    if (!w)
        return NULL;

    /* Create pipe for child stdout */
    if (pipe(pipefd) < 0) {
        free(w);
        return NULL;
    }

    /* Fork subprocess */
    w->child_pid = fork();
    if (w->child_pid < 0) {
        close(pipefd[0]);
        close(pipefd[1]);
        free(w);
        return NULL;
    }

    if (w->child_pid == 0) {
        /* Child process */
        close(pipefd[0]);  /* Close read end */
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[1]);

        /* Redirect stderr to /dev/null */
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDERR_FILENO);
            close(devnull);
        }

        /* Execute docker events */
        execlp("docker", "docker", "events",
               "--format", "json",
               "--filter", "type=container",
               NULL);

        _exit(127);
    }

    /* Parent process */
    close(pipefd[1]);  /* Close write end */
    w->stdout_fd = pipefd[0];
    w->running = true;

    /* Set non-blocking */
    set_nonblocking(w->stdout_fd);

    return w;
}

void docker_watcher_destroy(struct docker_watcher *w)
{
    if (!w)
        return;

    if (w->child_pid > 0) {
        kill(w->child_pid, SIGTERM);
        waitpid(w->child_pid, NULL, WNOHANG);
    }

    if (w->stdout_fd >= 0)
        close(w->stdout_fd);

    free(w);
}

int docker_watcher_get_fd(struct docker_watcher *w)
{
    return w ? w->stdout_fd : -1;
}

bool docker_watcher_is_running(struct docker_watcher *w)
{
    if (!w || !w->running)
        return false;

    /* Check if child is still running */
    int status;
    pid_t result = waitpid(w->child_pid, &status, WNOHANG);
    if (result == w->child_pid) {
        w->running = false;
        return false;
    }

    return true;
}

int docker_watcher_process(struct docker_watcher *w,
                           docker_event_callback callback,
                           void *user_data)
{
    char buf[1024];
    ssize_t n;
    int events_processed = 0;

    if (!w || w->stdout_fd < 0)
        return -1;

    /* Read available data */
    while ((n = read(w->stdout_fd, buf, sizeof(buf) - 1)) > 0) {
        for (ssize_t i = 0; i < n; i++) {
            char c = buf[i];

            if (c == '\n') {
                /* Complete line - process it */
                w->line_buffer[w->line_pos] = '\0';
                if (w->line_pos > 0) {
                    events_processed += process_event_json(w, w->line_buffer,
                                                           callback, user_data);
                }
                w->line_pos = 0;
            } else if (w->line_pos < LINE_BUFFER_SIZE - 1) {
                w->line_buffer[w->line_pos++] = c;
            }
        }
    }

    if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
        /* Read error */
        return -1;
    }

    return events_processed;
}
