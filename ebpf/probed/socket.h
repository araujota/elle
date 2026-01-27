// SPDX-License-Identifier: MIT
/**
 * socket.h - Unix socket management for elled-probed
 */

#ifndef __PROBED_SOCKET_H__
#define __PROBED_SOCKET_H__

#include <stddef.h>

/* Opaque socket handle */
struct probed_socket;

/**
 * probed_socket_create - Create and bind Unix socket
 * @path: Socket path
 *
 * Creates a Unix stream socket, binds to path, and listens.
 * Removes any existing socket at path first.
 *
 * Returns: Socket handle on success, NULL on failure
 */
struct probed_socket *probed_socket_create(const char *path);

/**
 * probed_socket_write - Write data to all connected clients
 * @sock: Socket handle
 * @data: Data to write
 * @len: Data length
 *
 * Writes NDJSON line to all connected clients.
 * Disconnects clients that fail.
 *
 * Returns: 0 on success, -1 if no clients connected
 */
int probed_socket_write(struct probed_socket *sock, const char *data, size_t len);

/**
 * probed_socket_destroy - Close socket and cleanup
 * @sock: Socket handle
 */
void probed_socket_destroy(struct probed_socket *sock);

/**
 * probed_socket_client_count - Get number of connected clients
 * @sock: Socket handle
 *
 * Returns: Number of connected clients
 */
int probed_socket_client_count(struct probed_socket *sock);

#endif /* __PROBED_SOCKET_H__ */
