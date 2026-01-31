// SPDX-License-Identifier: MIT
/**
 * client.c - Unix socket client implementation
 *
 * Connects to telemetryd's socket as a client to emit GPU events.
 * Handles connection, automatic reconnection, and non-blocking writes.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <poll.h>

#include "client.h"
#include "gpud.h"

/* Reconnection backoff */
#define MIN_RECONNECT_MS 100
#define MAX_RECONNECT_MS 5000

struct gpud_client {
    int fd;
    char *socket_path;
    uint64_t last_reconnect_attempt;
    int reconnect_backoff_ms;
};

/* ==========================================================================
 * Helper: Set socket non-blocking
 * ========================================================================== */

static int set_nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0)
        return -1;
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

/* ==========================================================================
 * Helper: Connect to socket
 * ========================================================================== */

static int connect_to_socket(const char *path)
{
    int fd;
    struct sockaddr_un addr;

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        gpud_log_debug("socket() failed: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%.*s", (int)(sizeof(addr.sun_path) - 1), path);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        gpud_log_debug("connect(%s) failed: %s", path, strerror(errno));
        close(fd);
        return -1;
    }

    /* Set non-blocking for writes */
    if (set_nonblocking(fd) < 0) {
        gpud_log_debug("Failed to set non-blocking: %s", strerror(errno));
        close(fd);
        return -1;
    }

    return fd;
}

/* ==========================================================================
 * Public API
 * ========================================================================== */

struct gpud_client *gpud_client_create(const char *socket_path)
{
    struct gpud_client *client;

    client = calloc(1, sizeof(*client));
    if (!client) {
        gpud_log_error("Failed to allocate client");
        return NULL;
    }

    client->socket_path = strdup(socket_path);
    if (!client->socket_path) {
        gpud_log_error("Failed to allocate socket path");
        free(client);
        return NULL;
    }

    client->fd = -1;
    client->reconnect_backoff_ms = MIN_RECONNECT_MS;

    /* Attempt initial connection */
    client->fd = connect_to_socket(socket_path);
    if (client->fd >= 0) {
        gpud_log_info("Connected to %s", socket_path);
    } else {
        gpud_log_warn("Initial connection to %s failed, will retry", socket_path);
    }

    return client;
}

void gpud_client_destroy(struct gpud_client *client)
{
    if (!client)
        return;

    if (client->fd >= 0)
        close(client->fd);

    free(client->socket_path);
    free(client);
}

bool gpud_client_connected(struct gpud_client *client)
{
    return client && client->fd >= 0;
}

int gpud_client_reconnect(struct gpud_client *client)
{
    uint64_t now;

    if (!client)
        return -1;

    /* Check if enough time has passed since last attempt */
    now = gpud_get_monotonic_ns();
    if (now - client->last_reconnect_attempt <
        (uint64_t)client->reconnect_backoff_ms * 1000000ULL) {
        return -1;
    }

    client->last_reconnect_attempt = now;

    /* Close old socket if any */
    if (client->fd >= 0) {
        close(client->fd);
        client->fd = -1;
    }

    /* Attempt connection */
    client->fd = connect_to_socket(client->socket_path);
    if (client->fd >= 0) {
        gpud_log_info("Reconnected to %s", client->socket_path);
        client->reconnect_backoff_ms = MIN_RECONNECT_MS;
        return 0;
    }

    /* Increase backoff */
    client->reconnect_backoff_ms *= 2;
    if (client->reconnect_backoff_ms > MAX_RECONNECT_MS)
        client->reconnect_backoff_ms = MAX_RECONNECT_MS;

    return -1;
}

int gpud_client_write(struct gpud_client *client, const char *data, size_t len)
{
    char *line;
    ssize_t n;

    if (!client || !data)
        return -1;

    /* Try to reconnect if not connected */
    if (client->fd < 0) {
        if (gpud_client_reconnect(client) < 0)
            return -1;
    }

    /* Build NDJSON line (data + newline) */
    line = malloc(len + 2);
    if (!line)
        return -1;

    memcpy(line, data, len);
    line[len] = '\n';
    line[len + 1] = '\0';

    n = write(client->fd, line, len + 1);
    free(line);

    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            /* Buffer full - not a fatal error, just skip */
            gpud_log_debug("Socket buffer full, skipping event");
            return 0;
        }

        /* Connection error */
        gpud_log_warn("Write failed: %s, will reconnect", strerror(errno));
        close(client->fd);
        client->fd = -1;
        return -1;
    }

    if ((size_t)n < len + 1) {
        /* Partial write - treat as error */
        gpud_log_warn("Partial write (%zd/%zu), will reconnect", n, len + 1);
        close(client->fd);
        client->fd = -1;
        return -1;
    }

    return 0;
}
