// SPDX-License-Identifier: MIT
/**
 * disk.c - Disk usage probe
 *
 * Monitors disk space via statvfs on mount points.
 * Ported from Python DiskProbe in src/elle/daemon/telemetry/probes.py
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/statvfs.h>

#include "disk.h"
#include "../json.h"

/* Filesystem types to skip */
static const char *SKIP_FSTYPES[] = {
    "squashfs", "tmpfs", "devtmpfs", "overlay",
    "cgroup", "cgroup2", "proc", "sysfs", "devpts",
    "securityfs", "pstore", "debugfs", "hugetlbfs",
    "mqueue", "fusectl", "configfs", "binfmt_misc",
    "autofs", "efivarfs", "bpf", "tracefs", NULL
};

/* Mount prefixes to check */
static const char *MOUNT_PREFIXES[] = {
    "/", "/home", "/var", "/tmp", "/boot", "/opt", "/srv", "/data", NULL
};

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct disk_probe_ctx *disk_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct disk_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->warning_pct = 0.90;  /* 90% full */

    (void)cfg;
    return ctx;
}

void disk_probe_destroy_ctx(struct disk_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * Helpers
 * ========================================================================== */

static bool should_skip_fstype(const char *fstype)
{
    for (int i = 0; SKIP_FSTYPES[i]; i++) {
        if (strcmp(fstype, SKIP_FSTYPES[i]) == 0)
            return true;
    }
    return false;
}

static bool is_valid_mount(const char *mount)
{
    for (int i = 0; MOUNT_PREFIXES[i]; i++) {
        if (strncmp(mount, MOUNT_PREFIXES[i], strlen(MOUNT_PREFIXES[i])) == 0)
            return true;
    }
    return false;
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int disk_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct disk_probe_ctx *ctx = (struct disk_probe_ctx *)ctx_ptr;
    FILE *f;
    char line[512];

    f = fopen("/proc/mounts", "r");
    if (!f)
        return -1;

    while (fgets(line, sizeof(line), f)) {
        char device[256], mount[256], fstype[64];
        struct statvfs stat;
        uint64_t total, free, used;
        double used_pct;

        /* Parse mount line: device mountpoint fstype options dump pass */
        if (sscanf(line, "%255s %255s %63s", device, mount, fstype) != 3)
            continue;

        /* Skip pseudo-filesystems */
        if (should_skip_fstype(fstype))
            continue;

        /* Only check valid mount prefixes */
        if (!is_valid_mount(mount))
            continue;

        /* Get filesystem stats */
        if (statvfs(mount, &stat) < 0)
            continue;

        /* Calculate usage */
        total = (uint64_t)stat.f_blocks * stat.f_frsize;
        free = (uint64_t)stat.f_bavail * stat.f_frsize;

        if (total == 0)
            continue;

        used = total - free;
        used_pct = (double)used / (double)total;

        /* Check threshold */
        if (used_pct > ctx->warning_pct) {
            struct telem_event evt;
            char message[512];
            char entity[128];
            char *json_str;
            bool emit;

            snprintf(message, sizeof(message),
                     "Disk space low on %s: %.1f%% used (free: %lu GB, total: %lu GB)",
                     mount,
                     used_pct * 100.0,
                     free / (1024 * 1024 * 1024),
                     total / (1024 * 1024 * 1024));

            snprintf(entity, sizeof(entity), "mount:%s", mount);

            emit = normalizer_process_prenormalized(norm,
                TELEM_SRC_PROBE,
                TELEM_SEV_WARNING,
                TELEM_CAT_DISK,
                message,
                entity,
                0,
                &evt);

            if (emit) {
                json_str = telem_json_event(&evt);
                if (json_str) {
                    telem_socket_write(sock, json_str, strlen(json_str));
                    free(json_str);
                }
            }
        }
    }

    fclose(f);
    return 0;
}
