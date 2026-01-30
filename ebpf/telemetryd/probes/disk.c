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
    ctx->inode_warning_pct = 0.80;
    ctx->swap_thrashing_threshold = 100.0;
    ctx->has_prev_vmstat = false;

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
        char device[256], mount[256], fstype[64], options[256];
        struct statvfs stat;
        uint64_t total, free, used;
        double used_pct;

        /* Parse mount line: device mountpoint fstype options dump pass */
        if (sscanf(line, "%255s %255s %63s %255s", device, mount, fstype, options) < 3)
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

        /* Check inode usage */
        if (stat.f_files > 0) {
            double inode_pct = 1.0 - (double)stat.f_ffree / (double)stat.f_files;
            if (inode_pct > ctx->inode_warning_pct) {
                struct telem_event inode_evt;
                char inode_msg[512];
                char inode_entity[128];
                char *inode_json;
                bool inode_emit;

                snprintf(inode_msg, sizeof(inode_msg),
                         "Inode exhaustion on %s: %.1f%% used (%lu free of %lu)",
                         mount,
                         inode_pct * 100.0,
                         (unsigned long)stat.f_ffree,
                         (unsigned long)stat.f_files);

                snprintf(inode_entity, sizeof(inode_entity), "mount:%s", mount);

                inode_emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE,
                    TELEM_SEV_WARNING,
                    TELEM_CAT_DISK,
                    inode_msg,
                    inode_entity,
                    0,
                    &inode_evt);

                if (inode_emit) {
                    inode_json = telem_json_event(&inode_evt);
                    if (inode_json) {
                        telem_socket_write(sock, inode_json, strlen(inode_json));
                        free(inode_json);
                    }
                }
            }
        }

        /* Check for readonly mount */
        {
            char *opt_tok, *opt_saveptr;
            char opts_copy[256];
            bool is_readonly = false;

            strncpy(opts_copy, options, sizeof(opts_copy) - 1);
            opts_copy[sizeof(opts_copy) - 1] = '\0';

            opt_tok = strtok_r(opts_copy, ",", &opt_saveptr);
            while (opt_tok) {
                if (strcmp(opt_tok, "ro") == 0) {
                    is_readonly = true;
                    break;
                }
                opt_tok = strtok_r(NULL, ",", &opt_saveptr);
            }

            if (is_readonly) {
                struct telem_event ro_evt;
                char ro_msg[256];
                char ro_entity[128];
                char *ro_json;
                bool ro_emit;

                snprintf(ro_msg, sizeof(ro_msg),
                         "Filesystem mounted read-only: %s (%s)", mount, device);
                snprintf(ro_entity, sizeof(ro_entity), "mount:%s", mount);

                ro_emit = normalizer_process_prenormalized(norm,
                    TELEM_SRC_PROBE,
                    TELEM_SEV_WARNING,
                    TELEM_CAT_DISK,
                    ro_msg,
                    ro_entity,
                    0,
                    &ro_evt);

                if (ro_emit) {
                    ro_json = telem_json_event(&ro_evt);
                    if (ro_json) {
                        telem_socket_write(sock, ro_json, strlen(ro_json));
                        free(ro_json);
                    }
                }
            }
        }
    }

    fclose(f);

    /* ======================================================================
     * VM/Swap Pressure from /proc/vmstat
     * ====================================================================== */
    {
        FILE *vmstat_fp;
        char vmstat_line[256];
        uint64_t pswpin = 0, pswpout = 0, pgpgin = 0, pgpgout = 0, oom_kill = 0;

        vmstat_fp = fopen("/proc/vmstat", "r");
        if (vmstat_fp) {
            while (fgets(vmstat_line, sizeof(vmstat_line), vmstat_fp)) {
                char key[64];
                uint64_t val;

                if (sscanf(vmstat_line, "%63s %lu", key, &val) != 2)
                    continue;

                if (strcmp(key, "pgpgin") == 0)
                    pgpgin = val;
                else if (strcmp(key, "pgpgout") == 0)
                    pgpgout = val;
                else if (strcmp(key, "pswpin") == 0)
                    pswpin = val;
                else if (strcmp(key, "pswpout") == 0)
                    pswpout = val;
                else if (strcmp(key, "oom_kill") == 0)
                    oom_kill = val;
            }
            fclose(vmstat_fp);

            if (ctx->has_prev_vmstat) {
                uint64_t delta_pswpin = pswpin - ctx->prev_pswpin;
                uint64_t delta_pswpout = pswpout - ctx->prev_pswpout;

                /* Check swap thrashing: page-in rate exceeds threshold */
                if ((double)delta_pswpin > ctx->swap_thrashing_threshold) {
                    struct telem_event swap_evt;
                    char swap_msg[512];
                    char *swap_json;
                    bool swap_emit;

                    snprintf(swap_msg, sizeof(swap_msg),
                             "Swap thrashing detected: pswpin delta=%lu pswpout delta=%lu "
                             "(pgpgin=%lu pgpgout=%lu oom_kill=%lu)",
                             delta_pswpin, delta_pswpout,
                             pgpgin - ctx->prev_pgpgin,
                             pgpgout - ctx->prev_pgpgout,
                             oom_kill - ctx->prev_oom_kill);

                    swap_emit = normalizer_process_prenormalized(norm,
                        TELEM_SRC_PROBE,
                        TELEM_SEV_WARNING,
                        TELEM_CAT_DISK,
                        swap_msg,
                        "swap:thrashing",
                        0,
                        &swap_evt);

                    if (swap_emit) {
                        swap_json = telem_json_event(&swap_evt);
                        if (swap_json) {
                            telem_socket_write(sock, swap_json, strlen(swap_json));
                            free(swap_json);
                        }
                    }
                }

                /* Check for OOM kills */
                if (oom_kill > ctx->prev_oom_kill) {
                    struct telem_event oom_evt;
                    char oom_msg[256];
                    char *oom_json;
                    bool oom_emit;

                    snprintf(oom_msg, sizeof(oom_msg),
                             "OOM killer invoked: %lu new kills (total: %lu)",
                             oom_kill - ctx->prev_oom_kill, oom_kill);

                    oom_emit = normalizer_process_prenormalized(norm,
                        TELEM_SRC_PROBE,
                        TELEM_SEV_WARNING,
                        TELEM_CAT_DISK,
                        oom_msg,
                        "memory:oom_kill",
                        0,
                        &oom_evt);

                    if (oom_emit) {
                        oom_json = telem_json_event(&oom_evt);
                        if (oom_json) {
                            telem_socket_write(sock, oom_json, strlen(oom_json));
                            free(oom_json);
                        }
                    }
                }
            }

            /* Update previous vmstat values */
            ctx->prev_pswpin = pswpin;
            ctx->prev_pswpout = pswpout;
            ctx->prev_pgpgin = pgpgin;
            ctx->prev_pgpgout = pgpgout;
            ctx->prev_oom_kill = oom_kill;
            ctx->has_prev_vmstat = true;
        }
    }

    return 0;
}
