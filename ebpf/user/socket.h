// SPDX-License-Identifier: MIT
/**
 * socket.h - Unix socket management for elled-ebpfd
 */

#ifndef __ELLED_SOCKET_H__
#define __ELLED_SOCKET_H__

#include <stddef.h>

/* Opaque socket handle */
struct elled_socket;

/**
 * elled_socket_create - Create and bind Unix socket
 * @path: Socket path
 *
 * Creates a Unix stream socket, binds to path, and listens.
 * Removes any existing socket at path first.
 *
 * Returns: Socket handle on success, NULL on failure
 */
struct elled_socket *elled_socket_create(const char *path);

/**
 * elled_socket_write - Write data to all connected clients
 * @sock: Socket handle
 * @data: Data to write
 * @len: Data length
 *
 * Writes NDJSON line to all connected clients.
 * Disconnects clients that fail.
 *
 * Returns: 0 on success, -1 if no clients connected
 */
int elled_socket_write(struct elled_socket *sock, const char *data, size_t len);

/**
 * elled_socket_destroy - Close socket and cleanup
 * @sock: Socket handle
 */
void elled_socket_destroy(struct elled_socket *sock);

#endif /* __ELLED_SOCKET_H__ */
