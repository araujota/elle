// SPDX-License-Identifier: MIT
/**
 * entity.c - Entity extraction via PCRE2 patterns
 *
 * Implements all entity extraction patterns from Python normalizer.py:
 * - CPU (2 patterns)
 * - Module (2 patterns)
 * - Function (1 pattern)
 * - Service (2 patterns)
 * - Interface (1 pattern)
 * - Disk (2 patterns)
 * - Container (2 patterns)
 * - Mount (1 pattern)
 * - User (2 patterns)
 * - PID (1 pattern)
 * - Port (3 patterns)
 * - File (2 patterns)
 * - WireGuard interface (1 pattern)
 */

#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "entity.h"

/* ==========================================================================
 * Entity Type Definitions
 * ========================================================================== */

typedef enum {
    ENTITY_TYPE_CPU,
    ENTITY_TYPE_MODULE,
    ENTITY_TYPE_FUNCTION,
    ENTITY_TYPE_SERVICE,
    ENTITY_TYPE_INTERFACE,
    ENTITY_TYPE_DISK,
    ENTITY_TYPE_CONTAINER,
    ENTITY_TYPE_MOUNT,
    ENTITY_TYPE_USER,
    ENTITY_TYPE_PID,
    ENTITY_TYPE_PORT,
    ENTITY_TYPE_FILE,
    ENTITY_TYPE_WG_INTERFACE,
} entity_type_t;

static const char *entity_type_prefix[] = {
    [ENTITY_TYPE_CPU]          = "cpu",
    [ENTITY_TYPE_MODULE]       = "module",
    [ENTITY_TYPE_FUNCTION]     = "function",
    [ENTITY_TYPE_SERVICE]      = "service",
    [ENTITY_TYPE_INTERFACE]    = "interface",
    [ENTITY_TYPE_DISK]         = "disk",
    [ENTITY_TYPE_CONTAINER]    = "container",
    [ENTITY_TYPE_MOUNT]        = "mount",
    [ENTITY_TYPE_USER]         = "user",
    [ENTITY_TYPE_PID]          = "pid",
    [ENTITY_TYPE_PORT]         = "port",
    [ENTITY_TYPE_FILE]         = "file",
    [ENTITY_TYPE_WG_INTERFACE] = "interface",  /* WG interfaces use "interface" prefix */
};

/* ==========================================================================
 * Pattern Definition
 * ========================================================================== */

struct entity_rule {
    const char *pattern;        /* PCRE2 pattern string */
    entity_type_t type;         /* Entity type */
    int capture_group;          /* Which capture group contains the entity name */
    pcre2_code *compiled;       /* Compiled pattern (set at init) */
    pcre2_match_data *match_data; /* Match data (reused) */
};

/*
 * Entity patterns ported from Python normalizer.py ENTITY_PATTERNS
 */
static struct entity_rule rules[] = {
    /* ===== Kernel panic entities (check first for kernel events) ===== */
    {"CPU[#:\\s]+(\\d+)", ENTITY_TYPE_CPU, 1, NULL, NULL},
    {"CPU:\\s*(\\d+)\\s+PID:", ENTITY_TYPE_CPU, 1, NULL, NULL},
    {"(\\w+)\\.ko[+\\s]", ENTITY_TYPE_MODULE, 1, NULL, NULL},
    {"at\\s+(\\w+)\\.ko\\+0x", ENTITY_TYPE_MODULE, 1, NULL, NULL},
    {"RIP:\\s+\\d{4}:\\s*(\\w+)\\+0x", ENTITY_TYPE_FUNCTION, 1, NULL, NULL},

    /* ===== Service units ===== */
    {"([a-zA-Z0-9_@-]+)\\.service", ENTITY_TYPE_SERVICE, 1, NULL, NULL},
    {"(?:unit|service)\\s+([a-zA-Z0-9_@-]+)", ENTITY_TYPE_SERVICE, 1, NULL, NULL},

    /* ===== Network interfaces ===== */
    {"\\b(eth\\d+|enp\\d+s\\d+|ens\\d+|wlan\\d+|wlp\\d+s\\d+)\\b", ENTITY_TYPE_INTERFACE, 1, NULL, NULL},

    /* ===== Block devices ===== */
    {"(/dev/(?:sd[a-z]\\d*|nvme\\d+n\\d+(?:p\\d+)?|loop\\d+))", ENTITY_TYPE_DISK, 1, NULL, NULL},
    {"\\b(sd[a-z]|nvme\\d+n\\d+)\\b", ENTITY_TYPE_DISK, 1, NULL, NULL},

    /* ===== Docker containers ===== */
    {"container[=:\\s]+([a-f0-9]{12}|[a-f0-9]{64})", ENTITY_TYPE_CONTAINER, 1, NULL, NULL},
    {"container\\s+([a-zA-Z0-9_-]+)\\s+\\(", ENTITY_TYPE_CONTAINER, 1, NULL, NULL},

    /* ===== Mount points ===== */
    {"(?:mount point|mounted on|umount)\\s+([/\\w]+)", ENTITY_TYPE_MOUNT, 1, NULL, NULL},

    /* ===== Users (for auth events) ===== */
    {"user[=:\\s]+([a-zA-Z0-9_-]+)", ENTITY_TYPE_USER, 1, NULL, NULL},
    {"invalid user\\s+([a-zA-Z0-9_-]+)", ENTITY_TYPE_USER, 1, NULL, NULL},

    /* ===== PIDs (for process events) ===== */
    {"(?:pid|process)\\s*[=:\\s]*(\\d+)", ENTITY_TYPE_PID, 1, NULL, NULL},

    /* ===== Ports (for network events) ===== */
    {"port[:\\s]+(\\d+)/(tcp|udp)", ENTITY_TYPE_PORT, 1, NULL, NULL},
    {"port\\s+(\\d+)", ENTITY_TYPE_PORT, 1, NULL, NULL},
    {"listening on.*:(\\d+)", ENTITY_TYPE_PORT, 1, NULL, NULL},

    /* ===== Files (for file monitoring events) ===== */
    {"File (?:modified|created|deleted):\\s*(.+?)(?:\\s|$)", ENTITY_TYPE_FILE, 1, NULL, NULL},
    {"(authorized_keys|sshd_config|sudoers)", ENTITY_TYPE_FILE, 1, NULL, NULL},

    /* ===== WireGuard interfaces ===== */
    {"\\b(wg\\d+)\\b", ENTITY_TYPE_WG_INTERFACE, 1, NULL, NULL},

    /* End marker */
    {NULL, 0, 0, NULL, NULL},
};

#define NUM_RULES (sizeof(rules) / sizeof(rules[0]) - 1)  /* Exclude end marker */

static bool initialized = false;

/* ==========================================================================
 * Initialization
 * ========================================================================== */

int entity_init(void)
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
            fprintf(stderr, "ERROR: Failed to compile entity pattern '%s' at offset %zu: %s\n",
                    rules[i].pattern, erroroffset, buffer);
            entity_cleanup();
            return -1;
        }

        /* JIT compile for better performance */
        pcre2_jit_compile(rules[i].compiled, PCRE2_JIT_COMPLETE);

        /* Create match data */
        rules[i].match_data = pcre2_match_data_create_from_pattern(
            rules[i].compiled, NULL
        );
        if (!rules[i].match_data) {
            fprintf(stderr, "ERROR: Failed to create match data for entity pattern '%s'\n",
                    rules[i].pattern);
            entity_cleanup();
            return -1;
        }
    }

    initialized = true;
    return 0;
}

/* ==========================================================================
 * Cleanup
 * ========================================================================== */

void entity_cleanup(void)
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
 * Helper: Check if entity type is relevant for category
 * ========================================================================== */

static bool is_entity_relevant(entity_type_t type, telem_category_t category)
{
    switch (category) {
    case TELEM_CAT_SERVICE:
        return type == ENTITY_TYPE_SERVICE;

    case TELEM_CAT_NET:
        return type == ENTITY_TYPE_INTERFACE ||
               type == ENTITY_TYPE_PORT ||
               type == ENTITY_TYPE_WG_INTERFACE;

    case TELEM_CAT_DISK:
    case TELEM_CAT_SMART:
    case TELEM_CAT_FS:
        return type == ENTITY_TYPE_DISK || type == ENTITY_TYPE_MOUNT;

    case TELEM_CAT_DOCKER:
        return type == ENTITY_TYPE_CONTAINER;

    case TELEM_CAT_AUTH:
        return type == ENTITY_TYPE_USER || type == ENTITY_TYPE_FILE;

    case TELEM_CAT_KERNEL_PANIC:
        return type == ENTITY_TYPE_CPU ||
               type == ENTITY_TYPE_MODULE ||
               type == ENTITY_TYPE_FUNCTION;

    default:
        /* For other categories, accept most entity types except PID */
        return type != ENTITY_TYPE_PID;
    }
}

/* ==========================================================================
 * Helper: Remove .service suffix
 * ========================================================================== */

static void remove_service_suffix(char *name)
{
    size_t len = strlen(name);
    if (len > 8 && strcmp(name + len - 8, ".service") == 0) {
        name[len - 8] = '\0';
    }
}

/* ==========================================================================
 * Helper: Truncate container ID to 12 chars
 * ========================================================================== */

static void truncate_container_id(char *id)
{
    size_t len = strlen(id);
    if (len > 12) {
        id[12] = '\0';
    }
}

/* ==========================================================================
 * Extraction
 * ========================================================================== */

bool entity_extract(const char *message, telem_category_t category,
                    char *out_entity, size_t out_len)
{
    if (!message || !out_entity || out_len < 2 || !initialized)
        return false;

    size_t msg_len = strlen(message);
    if (msg_len == 0)
        return false;

    /* Check patterns */
    for (size_t i = 0; i < NUM_RULES; i++) {
        if (!rules[i].compiled)
            continue;

        int rc = pcre2_match(
            rules[i].compiled,
            (PCRE2_SPTR)message,
            msg_len,
            0,                  /* Start offset */
            0,                  /* Options */
            rules[i].match_data,
            NULL                /* Match context */
        );

        if (rc >= 2) {  /* Need at least 2 (full match + capture group) */
            /* Check if entity type is relevant for this category */
            if (!is_entity_relevant(rules[i].type, category))
                continue;

            /* Extract capture group */
            PCRE2_SIZE *ovector = pcre2_get_ovector_pointer(rules[i].match_data);
            PCRE2_SIZE start = ovector[(PCRE2_SIZE)rules[i].capture_group * 2];
            PCRE2_SIZE end = ovector[(PCRE2_SIZE)rules[i].capture_group * 2 + 1];

            if (start == PCRE2_UNSET || end == PCRE2_UNSET)
                continue;

            size_t name_len = end - start;
            if (name_len == 0 || name_len >= out_len - 32)  /* Leave room for prefix */
                continue;

            /* Build entity string: "type:name" */
            char name[256];
            if (name_len >= sizeof(name))
                name_len = sizeof(name) - 1;
            memcpy(name, message + start, name_len);
            name[name_len] = '\0';

            /* Post-process based on type */
            if (rules[i].type == ENTITY_TYPE_SERVICE) {
                remove_service_suffix(name);
            } else if (rules[i].type == ENTITY_TYPE_CONTAINER) {
                truncate_container_id(name);
            }

            snprintf(out_entity, out_len, "%s:%s",
                     entity_type_prefix[rules[i].type], name);
            return true;
        }
    }

    return false;
}

bool entity_extract_from_raw(const char *systemd_unit, const char *unit,
                             const char *message, telem_category_t category,
                             char *out_entity, size_t out_len)
{
    if (!out_entity || out_len < 2)
        return false;

    /* For service category, try raw journal fields first */
    if (category == TELEM_CAT_SERVICE) {
        const char *unit_name = systemd_unit ? systemd_unit : unit;
        if (unit_name && unit_name[0]) {
            char name[256];
            snprintf(name, sizeof(name), "%.*s", (int)(sizeof(name) - 1), unit_name);
            remove_service_suffix(name);
            snprintf(out_entity, out_len, "service:%s", name);
            return true;
        }
    }

    /* Fall back to pattern matching */
    return entity_extract(message, category, out_entity, out_len);
}
