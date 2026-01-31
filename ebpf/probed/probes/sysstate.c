// SPDX-License-Identifier: MIT
/**
 * sysstate.c - System state health monitoring
 *
 * Checks for pending reboots, kernel mismatches, and pending security updates.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <dirent.h>

#include "sysstate.h"
#include "../json.h"

struct sysstate_probe_ctx *sysstate_probe_create_ctx(const struct probed_config *cfg)
{
    struct sysstate_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->security_update_warning = 0;
    ctx->security_update_error = 10;

    (void)cfg;
    return ctx;
}

void sysstate_probe_destroy_ctx(struct sysstate_probe_ctx *ctx)
{
    free(ctx);
}

static bool file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}


static char *find_latest_kernel_in_boot(void)
{
    static char latest[128] = {0};
    DIR *dir;
    struct dirent *entry;
    char best[128] = {0};

    dir = opendir("/boot");
    if (!dir)
        return NULL;

    while ((entry = readdir(dir)) != NULL) {
        if (strncmp(entry->d_name, "vmlinuz-", 8) == 0) {
            const char *ver = entry->d_name + 8;
            if (strcmp(ver, best) > 0) {
                snprintf(best, sizeof(best), "%s", ver);
            }
        }
    }
    closedir(dir);

    if (best[0] != '\0') {
        snprintf(latest, sizeof(latest), "%s", best);
        return latest;
    }
    return NULL;
}

static int get_pending_updates(int *total, int *security)
{
    FILE *f;
    char buf[256];

    *total = 0;
    *security = 0;

    /* Try reading from update-notifier cache */
    f = fopen("/var/lib/update-notifier/updates-available", "r");
    if (f) {
        while (fgets(buf, sizeof(buf), f)) {
            int n;
            if (sscanf(buf, "%d updates can be applied immediately.", &n) == 1)
                *total = n;
            if (sscanf(buf, "%d of these updates are standard security updates.", &n) == 1 ||
                sscanf(buf, "%d of these updates are security updates.", &n) == 1)
                *security = n;
        }
        fclose(f);
    }

    return 0;
}

int sysstate_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct sysstate_probe_ctx *ctx = (struct sysstate_probe_ctx *)ctx_ptr;
    struct utsname uts;
    char json_buf[2048];
    bool reboot_required;
    char reboot_pkgs[512] = {0};
    char *latest_kernel = NULL;
    bool kernel_mismatch = false;
    int total_updates = 0, security_updates = 0;
    const char *severity = "info";

    /* Check reboot required */
    reboot_required = file_exists("/var/run/reboot-required");

    if (reboot_required) {
        /* Read packages requiring reboot */
        FILE *f = fopen("/var/run/reboot-required.pkgs", "r");
        if (f) {
            size_t offset = 0;
            char line[128];
            while (fgets(line, sizeof(line), f) && offset < sizeof(reboot_pkgs) - 2) {
                size_t len = strlen(line);
                if (len > 0 && line[len-1] == '\n')
                    line[--len] = '\0';
                if (offset > 0) {
                    reboot_pkgs[offset++] = ',';
                }
                size_t copy_len = len;
                if (offset + copy_len >= sizeof(reboot_pkgs) - 1)
                    copy_len = sizeof(reboot_pkgs) - 1 - offset;
                memcpy(reboot_pkgs + offset, line, copy_len);
                offset += copy_len;
            }
            reboot_pkgs[offset] = '\0';
            fclose(f);
        }
    }

    /* Get running kernel */
    if (uname(&uts) < 0)
        return -1;

    /* Find latest installed kernel */
    latest_kernel = find_latest_kernel_in_boot();
    if (latest_kernel) {
        kernel_mismatch = (strcmp(uts.release, latest_kernel) != 0);
    }

    /* Get pending updates */
    get_pending_updates(&total_updates, &security_updates);

    /* Determine severity */
    if (security_updates > ctx->security_update_error) {
        severity = "error";
    } else if (security_updates > ctx->security_update_warning || reboot_required) {
        severity = "warning";
    } else if (kernel_mismatch) {
        severity = "info";
    }

    /* Emit JSON */
    snprintf(json_buf, sizeof(json_buf),
             "{\"type\":\"sysstate\","
             "\"reboot_required\":%s,"
             "\"reboot_packages\":\"%s\","
             "\"running_kernel\":\"%s\","
             "\"latest_kernel\":\"%s\","
             "\"kernel_mismatch\":%s,"
             "\"security_updates_pending\":%d,"
             "\"total_updates_pending\":%d,"
             "\"severity\":\"%s\"}\n",
             reboot_required ? "true" : "false",
             reboot_pkgs,
             uts.release,
             latest_kernel ? latest_kernel : uts.release,
             kernel_mismatch ? "true" : "false",
             security_updates,
             total_updates,
             severity);

    probed_socket_write(sock, json_buf, strlen(json_buf));

    return 0;
}
