// SPDX-License-Identifier: MIT
/**
 * socket.h - Unix socket management for elled-telemetryd
 *
 * Manages a Unix stream socket for outputting NDJSON events.
 * Supports multiple concurrent client connections.
 */

#ifndef __TELEMETRYD_SOCKET_H__
#define __TELEMETRYD_SOCKET_H__

#include <stddef.h>

/* Opaque socket handle */
struct telem_socket;

/**
 * telem_socket_create - Create and bind Unix socket
 * @path: Socket path
 *
 * Creates a Unix stream socket, binds to path, and listens.
 * Removes any existing socket at path first.
 *
 * Returns: Socket handle on success, NULL on failure
 */
struct telem_socket *telem_socket_create(const char *path);

/**
 * telem_socket_write - Write data to all connected clients
 * @sock: Socket handle
 * @data: Data to write
 * @len: Data length
 *
 * Writes NDJSON line to all connected clients.
 * Disconnects clients that fail.
 *
 * Returns: 0 on success, -1 if no clients connected
 */
int telem_socket_write(struct telem_socket *sock, const char *data, size_t len);

/**
 * telem_socket_get_fd - Get the listening socket fd for poll()
 * @sock: Socket handle
 *
 * Returns: File descriptor, or -1 if invalid
 */
int telem_socket_get_fd(struct telem_socket *sock);

/**
 * telem_socket_num_clients - Get number of connected clients
 * @sock: Socket handle
 *
 * Returns: Number of connected clients
 */
int telem_socket_num_clients(struct telem_socket *sock);

/**
 * telem_socket_destroy - Close socket and cleanup
 * @sock: Socket handle
 */
void telem_socket_destroy(struct telem_socket *sock);

#endif /* __TELEMETRYD_SOCKET_H__ */
