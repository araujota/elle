// SPDX-License-Identifier: MIT
/**
 * category.c - Category detection via PCRE2 patterns
 *
 * Implements all 58 category patterns from Python normalizer.py:
 * - Kernel panic (8 patterns)
 * - OOM (2 patterns)
 * - Disk/storage (3 patterns)
 * - SMART (1 pattern)
 * - Filesystem (2 patterns)
 * - Network (10 patterns)
 * - Authentication (7 patterns)
 * - Service (4 patterns)
 * - Thermal (2 patterns)
 * - Package (3 patterns)
 * - Docker (4 patterns)
 * - Port monitoring (3 patterns)
 * - WireGuard (2 patterns)
 * - Mount/filesystem (1 pattern)
 */

#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "category.h"

/* ==========================================================================
 * Pattern Definition
 * ========================================================================== */

struct category_rule {
    const char *pattern;        /* PCRE2 pattern string */
    telem_category_t category;  /* Category to assign */
    int priority;               /* Higher = check first */
    pcre2_code *compiled;       /* Compiled pattern (set at init) */
    pcre2_match_data *match_data; /* Match data (reused) */
};

/*
 * Category patterns ported from Python normalizer.py CATEGORY_PATTERNS
 * Ordered by priority (kernel panic highest, then OOM, etc.)
 */
static struct category_rule rules[] = {
    /* ===== Kernel panic events (highest priority - check before OOM) ===== */
    {"Kernel panic\\s*-?\\s*not syncing", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},
    {"\\bBUG:\\s|kernel BUG at", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},
    {"\\bOops:\\s*\\[?#?\\d", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},
    {"general protection fault:\\s*\\d", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},
    {"double fault:\\s*\\d", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},
    {"\\bRIP:\\s+\\d{4}:", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},
    {"Call Trace:", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},
    {"Segmentation fault at\\s+0x", TELEM_CAT_KERNEL_PANIC, 100, NULL, NULL},

    /* ===== OOM events (high priority - check before disk) ===== */
    {"Out of memory|oom[-_]?kill|invoked oom-killer|Killed process", TELEM_CAT_OOM, 90, NULL, NULL},
    {"cannot allocate memory|memory cgroup out of memory", TELEM_CAT_OOM, 90, NULL, NULL},

    /* ===== Disk/storage events ===== */
    {"I/O error|buffer I/O error|SCSI error|read error|write error", TELEM_CAT_DISK, 80, NULL, NULL},
    {"No space left|disk full|quota exceeded|ENOSPC", TELEM_CAT_DISK, 80, NULL, NULL},

    /* ===== SMART events ===== */
    {"SMART|smartd|sector|bad block|pending sector", TELEM_CAT_SMART, 80, NULL, NULL},

    /* ===== Filesystem events ===== */
    {"EXT4-fs error|XFS error|BTRFS error|filesystem error", TELEM_CAT_FS, 80, NULL, NULL},
    {"mount|umount|fstab|mounted|unmounted", TELEM_CAT_FS, 60, NULL, NULL},

    /* ===== Network events ===== */
    {"link (up|down)|carrier (lost|acquired)", TELEM_CAT_NET, 70, NULL, NULL},
    {"connection (refused|timed out|reset)", TELEM_CAT_NET, 70, NULL, NULL},
    {"NetworkManager|network unreachable|no route to host", TELEM_CAT_NET, 70, NULL, NULL},
    {"DHCP|DNS|timeout waiting for|network is down", TELEM_CAT_NET, 70, NULL, NULL},
    {"(eth|enp|ens|wlan|wlp)\\d+.*error", TELEM_CAT_NET, 70, NULL, NULL},
    {"New listener on port|unauthorized.*port", TELEM_CAT_NET, 70, NULL, NULL},
    {"process.*listening on.*port", TELEM_CAT_NET, 70, NULL, NULL},
    {"port:\\d+/(tcp|udp)", TELEM_CAT_NET, 70, NULL, NULL},
    {"wireguard|wg\\d+|peer.*handshake.*stale", TELEM_CAT_NET, 70, NULL, NULL},
    {"wg-quick|wg show|handshake.*minutes ago", TELEM_CAT_NET, 70, NULL, NULL},

    /* ===== Authentication events ===== */
    {"authentication failure|pam_unix.*failed", TELEM_CAT_AUTH, 75, NULL, NULL},
    {"invalid user|failed password|Failed publickey", TELEM_CAT_AUTH, 75, NULL, NULL},
    {"unauthorized|permission denied|access denied", TELEM_CAT_AUTH, 75, NULL, NULL},
    {"sshd.*Failed|sudo:.*incorrect password", TELEM_CAT_AUTH, 75, NULL, NULL},
    {"authorized_keys|sshd_config|ssh key", TELEM_CAT_AUTH, 75, NULL, NULL},
    {"/etc/passwd|/etc/shadow|/etc/sudoers", TELEM_CAT_AUTH, 75, NULL, NULL},
    {"File modified:.*/(authorized_keys|sshd_config|sudoers)", TELEM_CAT_AUTH, 75, NULL, NULL},

    /* ===== Service events ===== */
    {"(Started|Stopped|Starting|Stopping).*\\.service", TELEM_CAT_SERVICE, 65, NULL, NULL},
    {"Failed to start|entered failed state|failed with result", TELEM_CAT_SERVICE, 65, NULL, NULL},
    {"systemd\\[\\d+\\]:", TELEM_CAT_SERVICE, 65, NULL, NULL},
    {"Unit .* entered failed state", TELEM_CAT_SERVICE, 65, NULL, NULL},

    /* ===== Thermal events ===== */
    {"thermal|temperature|throttl|overheat", TELEM_CAT_THERMAL, 70, NULL, NULL},
    {"CPU\\d+ package.*above threshold", TELEM_CAT_THERMAL, 70, NULL, NULL},

    /* ===== Package management ===== */
    {"apt|apt-get|dpkg|snap|flatpak", TELEM_CAT_PKG, 60, NULL, NULL},
    {"package|dependency|unmet dependencies", TELEM_CAT_PKG, 60, NULL, NULL},
    {"Unable to fetch|repository.*failed", TELEM_CAT_PKG, 60, NULL, NULL},

    /* ===== Docker/container events ===== */
    {"docker|container|containerd|podman|runc", TELEM_CAT_DOCKER, 65, NULL, NULL},
    {"container (started|stopped|died|killed)", TELEM_CAT_DOCKER, 65, NULL, NULL},
    {"Container .* (crashloop|oom|died)", TELEM_CAT_DOCKER, 65, NULL, NULL},
    {"crashlooping|restart.*times", TELEM_CAT_DOCKER, 65, NULL, NULL},

    /* End marker */
    {NULL, TELEM_CAT_OTHER, 0, NULL, NULL},
};

#define NUM_RULES (sizeof(rules) / sizeof(rules[0]) - 1)  /* Exclude end marker */

static bool initialized = false;

/* ==========================================================================
 * Initialization
 * ========================================================================== */

int category_init(void)
{
    int errorcode;
    PCRE2_SIZE erroroffset;

    if (initialized)
        return 0;

    for (size_t i = 0; i < NUM_RULES; i++) {
        if (!rules[i].pattern)
            continue;

        /* Compile with case-insensitive flag */
        rules[i].compiled = pcre2_compile(
            (PCRE2_SPTR)rules[i].pattern,
            PCRE2_ZERO_TERMINATED,
            PCRE2_CASELESS | PCRE2_UTF,
            &errorcode,
            &erroroffset,
            NULL
        );

        if (!rules[i].compiled) {
            PCRE2_UCHAR buffer[256];
            pcre2_get_error_message(errorcode, buffer, sizeof(buffer));
            fprintf(stderr, "ERROR: Failed to compile pattern '%s' at offset %zu: %s\n",
                    rules[i].pattern, erroroffset, buffer);
            category_cleanup();
            return -1;
        }

        /* JIT compile for better performance */
        pcre2_jit_compile(rules[i].compiled, PCRE2_JIT_COMPLETE);

        /* Create match data */
        rules[i].match_data = pcre2_match_data_create_from_pattern(
            rules[i].compiled, NULL
        );
        if (!rules[i].match_data) {
            fprintf(stderr, "ERROR: Failed to create match data for pattern '%s'\n",
                    rules[i].pattern);
            category_cleanup();
            return -1;
        }
    }

    initialized = true;
    return 0;
}

/* ==========================================================================
 * Cleanup
 * ========================================================================== */

void category_cleanup(void)
{
    for (size_t i = 0; i < NUM_RULES; i++) {
        if (rules[i].match_data) {
            pcre2_match_data_free(rules[i].match_data);
            rules[i].match_data = NULL;
        }
        if (rules[i].compiled) {
            pcre2_code_free(rules[i].compiled);
            rules[i].compiled = NULL;
        }
    }
    initialized = false;
}

/* ==========================================================================
 * Detection
 * ========================================================================== */

telem_category_t category_detect(const char *message)
{
    if (!message || !initialized)
        return TELEM_CAT_OTHER;

    size_t len = strlen(message);
    if (len == 0)
        return TELEM_CAT_OTHER;

    /* Check patterns in priority order (already sorted in array) */
    for (size_t i = 0; i < NUM_RULES; i++) {
        if (!rules[i].compiled)
            continue;

        int rc = pcre2_match(
            rules[i].compiled,
            (PCRE2_SPTR)message,
            len,
            0,                  /* Start offset */
            0,                  /* Options */
            rules[i].match_data,
            NULL                /* Match context */
        );

        if (rc >= 0) {
            /* Match found */
            return rules[i].category;
        }
        /* rc == PCRE2_ERROR_NOMATCH is expected, continue to next pattern */
    }

    return TELEM_CAT_OTHER;
}

const char *category_detect_str(const char *message)
{
    return category_to_str(category_detect(message));
}
