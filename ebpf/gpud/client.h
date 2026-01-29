// SPDX-License-Identifier: MIT
/**
 * client.h - Unix socket client for connecting to telemetryd
 *
 * gpud connects as a client to telemetryd's socket and writes
 * NDJSON events. This module handles connection, reconnection,
 * and writing.
 */

#ifndef __GPUD_CLIENT_H__
#define __GPUD_CLIENT_H__

#include <stddef.h>
#include <stdbool.h>

/* Forward declaration */
struct gpud_client;

/**
 * gpud_client_create - Create client connection to telemetryd
 *
 * @socket_path: Path to telemetryd Unix socket
 *
 * Returns: Client handle, or NULL on failure.
 */
struct gpud_client *gpud_client_create(const char *socket_path);

/**
 * gpud_client_destroy - Close and free client
 */
void gpud_client_destroy(struct gpud_client *client);

/**
 * gpud_client_write - Write NDJSON event to socket
 *
 * @client: Client handle
 * @data: JSON data to write (without trailing newline)
 * @len: Length of data
 *
 * Returns: 0 on success, -1 on failure (will attempt reconnect)
 */
int gpud_client_write(struct gpud_client *client, const char *data, size_t len);

/**
 * gpud_client_connected - Check if client is connected
 */
bool gpud_client_connected(struct gpud_client *client);

/**
 * gpud_client_reconnect - Attempt to reconnect to server
 *
 * Returns: 0 on success, -1 on failure
 */
int gpud_client_reconnect(struct gpud_client *client);

#endif /* __GPUD_CLIENT_H__ */
