// SPDX-License-Identifier: MIT
/**
 * socket.c - Unix socket management for elled-ebpfd
 *
 * Manages a Unix stream socket for outputting NDJSON events.
 * Supports multiple concurrent client connections.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <poll.h>

#include "socket.h"

/* Maximum concurrent clients */
#define MAX_CLIENTS 8

/* Socket state */
struct elled_socket {
    int listen_fd;
    char *path;
    int clients[MAX_CLIENTS];
    int num_clients;
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
 * Socket Creation
 * ========================================================================== */

struct elled_socket *elled_socket_create(const char *path)
{
    struct elled_socket *sock;
    struct sockaddr_un addr;
    int fd = -1;

    sock = calloc(1, sizeof(*sock));
    if (!sock) {
        fprintf(stderr, "ERROR: Failed to allocate socket\n");
        return NULL;
    }

    sock->path = strdup(path);
    if (!sock->path) {
        fprintf(stderr, "ERROR: Failed to allocate path\n");
        free(sock);
        return NULL;
    }

    /* Initialize client array */
    for (int i = 0; i < MAX_CLIENTS; i++)
        sock->clients[i] = -1;

    /* Remove existing socket */
    unlink(path);

    /* Create socket */
    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        fprintf(stderr, "ERROR: socket() failed: %s\n", strerror(errno));
        goto error;
    }

    /* Bind to path */
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        fprintf(stderr, "ERROR: bind(%s) failed: %s\n", path, strerror(errno));
        goto error;
    }

    /* Set permissions (world-readable for daemon access) */
    chmod(path, 0666);

    /* Listen for connections */
    if (listen(fd, MAX_CLIENTS) < 0) {
        fprintf(stderr, "ERROR: listen() failed: %s\n", strerror(errno));
        goto error;
    }

    /* Set non-blocking */
    if (set_nonblocking(fd) < 0) {
        fprintf(stderr, "ERROR: Failed to set non-blocking\n");
        goto error;
    }

    sock->listen_fd = fd;
    return sock;

error:
    if (fd >= 0)
        close(fd);
    free(sock->path);
    free(sock);
    return NULL;
}

/* ==========================================================================
 * Accept New Connections
 * ========================================================================== */

static void accept_clients(struct elled_socket *sock)
{
    while (1) {
        int client_fd = accept(sock->listen_fd, NULL, NULL);
        if (client_fd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                break;
            /* Real error, ignore */
            break;
        }

        /* Find free slot */
        int slot = -1;
        for (int i = 0; i < MAX_CLIENTS; i++) {
            if (sock->clients[i] < 0) {
                slot = i;
                break;
            }
        }

        if (slot < 0) {
            /* No free slots, reject client */
            close(client_fd);
            continue;
        }

        /* Set non-blocking and store */
        set_nonblocking(client_fd);
        sock->clients[slot] = client_fd;
        sock->num_clients++;
    }
}

/* ==========================================================================
 * Write to Clients
 * ========================================================================== */

int elled_socket_write(struct elled_socket *sock, const char *data, size_t len)
{
    char line[len + 2];  /* data + newline + null */
    int written = 0;

    /* Accept any pending connections first */
    accept_clients(sock);

    if (sock->num_clients == 0)
        return -1;

    /* Build NDJSON line */
    memcpy(line, data, len);
    line[len] = '\n';
    line[len + 1] = '\0';
    size_t line_len = len + 1;

    /* Write to all connected clients */
    for (int i = 0; i < MAX_CLIENTS; i++) {
        int fd = sock->clients[i];
        if (fd < 0)
            continue;

        ssize_t n = write(fd, line, line_len);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                /* Buffer full, skip this client for now */
                continue;
            }
            /* Client disconnected or error */
            close(fd);
            sock->clients[i] = -1;
            sock->num_clients--;
            continue;
        }

        if ((size_t)n < line_len) {
            /* Partial write - for simplicity, disconnect client */
            close(fd);
            sock->clients[i] = -1;
            sock->num_clients--;
            continue;
        }

        written++;
    }

    return written > 0 ? 0 : -1;
}

/* ==========================================================================
 * Socket Destruction
 * ========================================================================== */

void elled_socket_destroy(struct elled_socket *sock)
{
    if (!sock)
        return;

    /* Close all client connections */
    for (int i = 0; i < MAX_CLIENTS; i++) {
        if (sock->clients[i] >= 0)
            close(sock->clients[i]);
    }

    /* Close listen socket */
    if (sock->listen_fd >= 0)
        close(sock->listen_fd);

    /* Remove socket file */
    if (sock->path) {
        unlink(sock->path);
        free(sock->path);
    }

    free(sock);
}
