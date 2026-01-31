// SPDX-License-Identifier: MIT
/**
 * telemetryd.h - Main header for elled-telemetryd
 *
 * Defines the normalized telemetry event structure that is emitted
 * via Unix socket in NDJSON format.
 */

#ifndef __TELEMETRYD_H__
#define __TELEMETRYD_H__

#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <time.h>

/* ==========================================================================
 * Constants
 * ========================================================================== */

/* Maximum lengths for string fields */
#define TELEM_MAX_MESSAGE       2048
#define TELEM_MAX_ENTITY        128
#define TELEM_MAX_FINGERPRINT   17      /* 16 hex chars + null */
#define TELEM_MAX_CATEGORY      32
#define TELEM_MAX_SOURCE        16
#define TELEM_MAX_SEVERITY      16

/* Default socket path */
#define TELEMETRYD_DEFAULT_SOCKET "/run/elle/telemetry.sock"

/* Default deduplication window in seconds */
#define TELEMETRYD_DEFAULT_DEDUPE_WINDOW 60

/* ==========================================================================
 * Severity Levels
 * ========================================================================== */

typedef enum {
    TELEM_SEV_DEBUG = 0,
    TELEM_SEV_INFO,
    TELEM_SEV_NOTICE,
    TELEM_SEV_WARNING,
    TELEM_SEV_ERROR,
    TELEM_SEV_CRITICAL,
} telem_severity_t;

/* Convert journald priority (0-7) to severity */
static inline telem_severity_t priority_to_severity(int priority)
{
    switch (priority) {
    case 0:  /* emerg */
    case 1:  /* alert */
    case 2:  /* crit */
        return TELEM_SEV_CRITICAL;
    case 3:  /* err */
        return TELEM_SEV_ERROR;
    case 4:  /* warning */
        return TELEM_SEV_WARNING;
    case 5:  /* notice */
        return TELEM_SEV_NOTICE;
    case 6:  /* info */
        return TELEM_SEV_INFO;
    case 7:  /* debug */
    default:
        return TELEM_SEV_DEBUG;
    }
}

/* Severity to string */
static inline const char *severity_to_str(telem_severity_t sev)
{
    switch (sev) {
    case TELEM_SEV_DEBUG:    return "debug";
    case TELEM_SEV_INFO:     return "info";
    case TELEM_SEV_NOTICE:   return "notice";
    case TELEM_SEV_WARNING:  return "warning";
    case TELEM_SEV_ERROR:    return "error";
    case TELEM_SEV_CRITICAL: return "critical";
    default:                 return "info";
    }
}

/* ==========================================================================
 * Source Types
 * ========================================================================== */

typedef enum {
    TELEM_SRC_JOURNAL = 0,
    TELEM_SRC_KERNEL,
    TELEM_SRC_PROBE,
    TELEM_SRC_EBPF,
    TELEM_SRC_DOCKER,
    TELEM_SRC_INOTIFY,
} telem_source_t;

/* Source to string */
static inline const char *source_to_str(telem_source_t src)
{
    switch (src) {
    case TELEM_SRC_JOURNAL: return "journal";
    case TELEM_SRC_KERNEL:  return "kernel";
    case TELEM_SRC_PROBE:   return "probe";
    case TELEM_SRC_EBPF:    return "ebpf";
    case TELEM_SRC_DOCKER:  return "docker";
    case TELEM_SRC_INOTIFY: return "inotify";
    default:                return "unknown";
    }
}

/* ==========================================================================
 * Category Types
 * ========================================================================== */

typedef enum {
    TELEM_CAT_OOM = 0,        /* Out-of-memory events */
    TELEM_CAT_DISK,           /* Disk/storage issues */
    TELEM_CAT_NET,            /* Network issues */
    TELEM_CAT_AUTH,           /* Authentication/permission */
    TELEM_CAT_SERVICE,        /* Systemd service events */
    TELEM_CAT_THERMAL,        /* Temperature warnings */
    TELEM_CAT_SMART,          /* SMART disk health */
    TELEM_CAT_FS,             /* Filesystem events */
    TELEM_CAT_PKG,            /* Package management */
    TELEM_CAT_DOCKER,         /* Container events */
    TELEM_CAT_KERNEL_PANIC,   /* Kernel panic/oops */
    TELEM_CAT_OTHER,          /* Uncategorized */
} telem_category_t;

/* Category to string */
static inline const char *category_to_str(telem_category_t cat)
{
    switch (cat) {
    case TELEM_CAT_OOM:          return "oom";
    case TELEM_CAT_DISK:         return "disk";
    case TELEM_CAT_NET:          return "net";
    case TELEM_CAT_AUTH:         return "auth";
    case TELEM_CAT_SERVICE:      return "service";
    case TELEM_CAT_THERMAL:      return "thermal";
    case TELEM_CAT_SMART:        return "smart";
    case TELEM_CAT_FS:           return "fs";
    case TELEM_CAT_PKG:          return "pkg";
    case TELEM_CAT_DOCKER:       return "docker";
    case TELEM_CAT_KERNEL_PANIC: return "kernel_panic";
    default:                     return "other";
    }
}

/* String to category */
static inline telem_category_t str_to_category(const char *str)
{
    if (!str) return TELEM_CAT_OTHER;
    if (strcmp(str, "oom") == 0)          return TELEM_CAT_OOM;
    if (strcmp(str, "disk") == 0)         return TELEM_CAT_DISK;
    if (strcmp(str, "net") == 0)          return TELEM_CAT_NET;
    if (strcmp(str, "auth") == 0)         return TELEM_CAT_AUTH;
    if (strcmp(str, "service") == 0)      return TELEM_CAT_SERVICE;
    if (strcmp(str, "thermal") == 0)      return TELEM_CAT_THERMAL;
    if (strcmp(str, "smart") == 0)        return TELEM_CAT_SMART;
    if (strcmp(str, "fs") == 0)           return TELEM_CAT_FS;
    if (strcmp(str, "pkg") == 0)          return TELEM_CAT_PKG;
    if (strcmp(str, "docker") == 0)       return TELEM_CAT_DOCKER;
    if (strcmp(str, "kernel_panic") == 0) return TELEM_CAT_KERNEL_PANIC;
    return TELEM_CAT_OTHER;
}

/* ==========================================================================
 * Normalized Telemetry Event
 * ========================================================================== */

/**
 * struct telem_event - A fully normalized telemetry event
 *
 * This is the final output format emitted to the Unix socket.
 * All events from all sources are converted to this structure
 * before serialization to NDJSON.
 */
struct telem_event {
    /* Timestamp (nanoseconds since epoch) */
    uint64_t ts_ns;

    /* Source classification */
    telem_source_t source;
    telem_severity_t severity;
    telem_category_t category;

    /* Content */
    char message[TELEM_MAX_MESSAGE];

    /* Entity tracking (e.g., "service:nginx", "interface:eth0") */
    char entity[TELEM_MAX_ENTITY];
    bool has_entity;

    /* Fingerprint for deduplication (SHA256[:16]) */
    char fingerprint[TELEM_MAX_FINGERPRINT];

    /* Raw data pointer (optional, for pass-through) */
    void *raw_json;
    size_t raw_json_len;
};

/* ==========================================================================
 * Configuration
 * ========================================================================== */

/**
 * struct telemetryd_config - Runtime configuration
 */
struct telemetryd_config {
    /* Socket path */
    char socket_path[256];

    /* Deduplication window */
    int dedupe_window_sec;

    /* Log level (0=error, 1=warn, 2=info, 3=debug) */
    int log_level;

    /* Component enables */
    bool journal_enabled;
    bool kernel_enabled;
    bool docker_enabled;
    bool inotify_enabled;
    bool ebpf_enabled;
    bool probes_enabled;

    /* Probe intervals (seconds) */
    int psi_interval;
    int systemd_interval;
    int dns_interval;
    int conntrack_interval;
    int hardware_interval;
    int auth_interval;
    int memory_interval;
    int disk_interval;
    int network_interval;
    int thermal_interval;
    int package_interval;
    int port_interval;

    /* New probe intervals (monitoring sprint) */
    int connpool_interval;
    int topproc_interval;
    int ioperf_interval;
    int cgroup_interval;
};

/* Default configuration */
#define TELEMETRYD_DEFAULT_CONFIG {                     \
    .socket_path = TELEMETRYD_DEFAULT_SOCKET,           \
    .dedupe_window_sec = TELEMETRYD_DEFAULT_DEDUPE_WINDOW, \
    .log_level = 2,                                     \
    .journal_enabled = true,                            \
    .kernel_enabled = true,                             \
    .docker_enabled = true,                             \
    .inotify_enabled = true,                            \
    .ebpf_enabled = true,                               \
    .probes_enabled = true,                             \
    .psi_interval = 5,                                  \
    .systemd_interval = 10,                             \
    .dns_interval = 30,                                 \
    .conntrack_interval = 15,                           \
    .hardware_interval = 300,                           \
    .auth_interval = 5,                                 \
    .memory_interval = 30,                              \
    .disk_interval = 300,                               \
    .network_interval = 60,                             \
    .thermal_interval = 60,                             \
    .package_interval = 300,                            \
    .port_interval = 60,                                \
    .connpool_interval = 30,                            \
    .topproc_interval = 60,                             \
    .ioperf_interval = 30,                              \
    .cgroup_interval = 60,                              \
}

/* ==========================================================================
 * Utility Functions
 * ========================================================================== */

/**
 * get_timestamp_ns - Get current timestamp in nanoseconds
 */
static inline uint64_t get_timestamp_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/**
 * get_monotonic_ns - Get monotonic timestamp in nanoseconds
 */
static inline uint64_t get_monotonic_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

#endif /* __TELEMETRYD_H__ */
