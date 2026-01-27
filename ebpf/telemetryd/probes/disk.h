// SPDX-License-Identifier: MIT
/**
 * disk.h - Disk usage probe
 *
 * Monitors disk space via statvfs on mount points.
 */

#ifndef __TELEMETRYD_DISK_H__
#define __TELEMETRYD_DISK_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"

/**
 * struct disk_probe_ctx - Disk probe context
 */
struct disk_probe_ctx {
    double warning_pct;     /* Disk warning threshold (default: 0.90) */
};

/**
 * disk_probe_create_ctx - Create disk probe context
 * @cfg: Telemetryd configuration
 *
 * Returns: Context pointer, or NULL on failure
 */
struct disk_probe_ctx *disk_probe_create_ctx(const struct telemetryd_config *cfg);

/**
 * disk_probe_destroy_ctx - Free disk probe context
 * @ctx: Context to free
 */
void disk_probe_destroy_ctx(struct disk_probe_ctx *ctx);

/**
 * disk_probe_run - Run disk probe
 * @norm: Normalizer instance
 * @sock: Socket instance
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int disk_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_DISK_H__ */
