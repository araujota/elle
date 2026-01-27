// SPDX-License-Identifier: MIT
/**
 * package.h - Package monitoring probe
 *
 * Monitors /var/lib/dpkg/status for package version changes.
 */

#ifndef __TELEMETRYD_PACKAGE_H__
#define __TELEMETRYD_PACKAGE_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"
#include <time.h>

#define PACKAGE_MAX_TRACKED 4096
#define PACKAGE_NAME_LEN 64
#define PACKAGE_VERSION_LEN 64

/**
 * struct pkg_entry - Single package entry
 */
struct pkg_entry {
    char name[PACKAGE_NAME_LEN];
    char version[PACKAGE_VERSION_LEN];
    uint32_t hash;  /* For fast lookup */
};

/**
 * struct package_probe_ctx - Package probe context
 */
struct package_probe_ctx {
    struct pkg_entry *all_packages;     /* All installed packages */
    size_t num_packages;
    size_t capacity;
    time_t last_mtime;                  /* stat() check before re-read */
    bool detect_new;                    /* Detect new package installs */
    bool initialized;
};

/**
 * package_probe_create_ctx - Create package probe context
 * @cfg: Telemetryd configuration
 *
 * Returns: Context pointer, or NULL on failure
 */
struct package_probe_ctx *package_probe_create_ctx(const struct telemetryd_config *cfg);

/**
 * package_probe_destroy_ctx - Free package probe context
 * @ctx: Context to free
 */
void package_probe_destroy_ctx(struct package_probe_ctx *ctx);

/**
 * package_probe_run - Run package probe
 * @norm: Normalizer instance
 * @sock: Socket instance
 * @ctx: Probe context
 *
 * Returns: 0 on success, -1 on error
 */
int package_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx);

#endif /* __TELEMETRYD_PACKAGE_H__ */
