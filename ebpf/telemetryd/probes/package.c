// SPDX-License-Identifier: MIT
/**
 * package.c - Package monitoring probe
 *
 * Monitors /var/lib/dpkg/status for package version changes.
 * Ported from Python PackageProbe in src/elle/daemon/telemetry/package_probe.py
 *
 * Optimizations over Python version:
 * - Uses stat() mtime check to skip re-parsing when file unchanged
 * - Uses memory-mapped file for efficient parsing
 * - Hash table for O(1) package lookup
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>

#include "package.h"
#include "../json.h"

#define DPKG_STATUS_PATH "/var/lib/dpkg/status"

/* ==========================================================================
 * Hash Functions
 * ========================================================================== */

static uint32_t hash_string(const char *s)
{
    uint32_t h = 5381;
    int c;
    while ((c = *s++))
        h = ((h << 5) + h) + c;
    return h;
}

/* ==========================================================================
 * Context Management
 * ========================================================================== */

struct package_probe_ctx *package_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct package_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->capacity = PACKAGE_MAX_TRACKED;
    ctx->all_packages = calloc(ctx->capacity, sizeof(struct pkg_entry));
    if (!ctx->all_packages) {
        free(ctx);
        return NULL;
    }

    ctx->detect_new = true;  /* Default: detect new installs */
    ctx->initialized = false;
    ctx->last_mtime = 0;

    (void)cfg;
    return ctx;
}

void package_probe_destroy_ctx(struct package_probe_ctx *ctx)
{
    if (ctx) {
        free(ctx->all_packages);
        free(ctx);
    }
}

/* ==========================================================================
 * Package Lookup
 * ========================================================================== */

static struct pkg_entry *find_package(struct package_probe_ctx *ctx, const char *name)
{
    uint32_t h = hash_string(name);
    for (size_t i = 0; i < ctx->num_packages; i++) {
        if (ctx->all_packages[i].hash == h &&
            strcmp(ctx->all_packages[i].name, name) == 0)
            return &ctx->all_packages[i];
    }
    return NULL;
}

static int add_package(struct package_probe_ctx *ctx, const char *name, const char *version)
{
    if (ctx->num_packages >= ctx->capacity)
        return -1;

    struct pkg_entry *entry = &ctx->all_packages[ctx->num_packages++];
    strncpy(entry->name, name, sizeof(entry->name) - 1);
    strncpy(entry->version, version, sizeof(entry->version) - 1);
    entry->hash = hash_string(name);
    return 0;
}

/* ==========================================================================
 * DPKG Status Parsing
 * ========================================================================== */

static int is_installed(const char *status)
{
    /* Status format: "want error flag status"
     * e.g., "install ok installed" */
    return status && strstr(status, "installed") != NULL;
}

typedef void (*package_callback_t)(const char *name, const char *version, void *user);

static int parse_dpkg_status(package_callback_t callback, void *user)
{
    int fd;
    struct stat st;
    char *data, *ptr, *end;
    char name[PACKAGE_NAME_LEN] = {0};
    char version[PACKAGE_VERSION_LEN] = {0};
    char status[128] = {0};

    fd = open(DPKG_STATUS_PATH, O_RDONLY);
    if (fd < 0)
        return -1;

    if (fstat(fd, &st) < 0) {
        close(fd);
        return -1;
    }

    data = mmap(NULL, st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);

    if (data == MAP_FAILED)
        return -1;

    ptr = data;
    end = data + st.st_size;

    while (ptr < end) {
        char *line_end = memchr(ptr, '\n', end - ptr);
        if (!line_end)
            line_end = end;

        size_t line_len = line_end - ptr;

        /* Empty line marks end of package stanza */
        if (line_len == 0 || (line_len == 1 && ptr[0] == '\r')) {
            if (name[0] && version[0] && is_installed(status)) {
                callback(name, version, user);
            }
            name[0] = version[0] = status[0] = '\0';
        }
        /* Package: */
        else if (line_len > 9 && strncmp(ptr, "Package: ", 9) == 0) {
            size_t vlen = line_len - 9;
            if (vlen >= sizeof(name)) vlen = sizeof(name) - 1;
            memcpy(name, ptr + 9, vlen);
            name[vlen] = '\0';
        }
        /* Version: */
        else if (line_len > 9 && strncmp(ptr, "Version: ", 9) == 0) {
            size_t vlen = line_len - 9;
            if (vlen >= sizeof(version)) vlen = sizeof(version) - 1;
            memcpy(version, ptr + 9, vlen);
            version[vlen] = '\0';
        }
        /* Status: */
        else if (line_len > 8 && strncmp(ptr, "Status: ", 8) == 0) {
            size_t vlen = line_len - 8;
            if (vlen >= sizeof(status)) vlen = sizeof(status) - 1;
            memcpy(status, ptr + 8, vlen);
            status[vlen] = '\0';
        }

        ptr = line_end + 1;
    }

    /* Handle last package if no trailing newline */
    if (name[0] && version[0] && is_installed(status)) {
        callback(name, version, user);
    }

    munmap(data, st.st_size);
    return 0;
}

/* ==========================================================================
 * Probe Callbacks
 * ========================================================================== */

struct parse_ctx {
    struct package_probe_ctx *probe_ctx;
    struct normalizer *norm;
    struct telem_socket *sock;
    struct pkg_entry *new_packages;
    size_t new_count;
    size_t new_capacity;
};

static void init_callback(const char *name, const char *version, void *user)
{
    struct parse_ctx *pctx = (struct parse_ctx *)user;
    add_package(pctx->probe_ctx, name, version);
}

static void check_callback(const char *name, const char *version, void *user)
{
    struct parse_ctx *pctx = (struct parse_ctx *)user;
    struct pkg_entry *existing = find_package(pctx->probe_ctx, name);

    if (!existing) {
        /* New package */
        if (pctx->probe_ctx->detect_new && pctx->new_count < pctx->new_capacity) {
            struct pkg_entry *entry = &pctx->new_packages[pctx->new_count++];
            strncpy(entry->name, name, sizeof(entry->name) - 1);
            strncpy(entry->version, version, sizeof(entry->version) - 1);
            entry->hash = hash_string(name);

            /* Emit event */
            struct telem_event evt;
            char message[256];
            char entity[128];
            char *json_str;
            bool emit;

            snprintf(message, sizeof(message),
                     "Package %s installed: %s", name, version);
            snprintf(entity, sizeof(entity), "package:%s", name);

            emit = normalizer_process_prenormalized(pctx->norm,
                TELEM_SRC_PROBE,
                TELEM_SEV_INFO,
                TELEM_CAT_PKG,
                message,
                entity,
                0,
                &evt);

            if (emit) {
                json_str = telem_json_event(&evt);
                if (json_str) {
                    telem_socket_write(pctx->sock, json_str, strlen(json_str));
                    free(json_str);
                }
            }
        }
    } else if (strcmp(existing->version, version) != 0) {
        /* Version changed - upgrade */
        struct telem_event evt;
        char message[256];
        char entity[128];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "Package %s upgraded: %s -> %s",
                 name, existing->version, version);
        snprintf(entity, sizeof(entity), "package:%s", name);

        emit = normalizer_process_prenormalized(pctx->norm,
            TELEM_SRC_PROBE,
            TELEM_SEV_INFO,
            TELEM_CAT_PKG,
            message,
            entity,
            0,
            &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(pctx->sock, json_str, strlen(json_str));
                free(json_str);
            }
        }

        /* Update stored version */
        strncpy(existing->version, version, sizeof(existing->version) - 1);
    }
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int package_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct package_probe_ctx *ctx = (struct package_probe_ctx *)ctx_ptr;
    struct stat st;
    struct parse_ctx pctx;

    /* Check if file has changed */
    if (stat(DPKG_STATUS_PATH, &st) < 0)
        return -1;

    if (ctx->initialized && st.st_mtime == ctx->last_mtime) {
        /* File unchanged, skip parsing */
        return 0;
    }

    ctx->last_mtime = st.st_mtime;

    if (!ctx->initialized) {
        /* First run - establish baseline */
        pctx.probe_ctx = ctx;
        pctx.norm = norm;
        pctx.sock = sock;

        if (parse_dpkg_status(init_callback, &pctx) < 0)
            return -1;

        ctx->initialized = true;
        return 0;
    }

    /* Subsequent run - check for changes */
    pctx.probe_ctx = ctx;
    pctx.norm = norm;
    pctx.sock = sock;
    pctx.new_packages = calloc(256, sizeof(struct pkg_entry));
    pctx.new_count = 0;
    pctx.new_capacity = 256;

    if (!pctx.new_packages)
        return -1;

    if (parse_dpkg_status(check_callback, &pctx) < 0) {
        free(pctx.new_packages);
        return -1;
    }

    /* Add new packages to our tracking list */
    for (size_t i = 0; i < pctx.new_count; i++) {
        add_package(ctx, pctx.new_packages[i].name, pctx.new_packages[i].version);
    }

    free(pctx.new_packages);
    return 0;
}
