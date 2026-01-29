// SPDX-License-Identifier: MIT
/**
 * gpud.h - Main header for elled-gpud
 *
 * GPU telemetry daemon that polls NVIDIA GPUs via NVML and
 * emits NDJSON events to the telemetryd socket.
 */

#ifndef __GPUD_H__
#define __GPUD_H__

#include <stdint.h>
#include <stdbool.h>
#include <time.h>

/* ==========================================================================
 * Constants
 * ========================================================================== */

/* Default socket path (same as telemetryd - we connect as a client) */
#define GPUD_DEFAULT_SOCKET "/run/elle/telemetry.sock"

/* Default poll interval in milliseconds */
#define GPUD_DEFAULT_INTERVAL_MS 1000

/* Threshold defaults */
#define GPUD_DEFAULT_TEMP_WARNING 80
#define GPUD_DEFAULT_TEMP_CRITICAL 90
#define GPUD_DEFAULT_MEM_WARNING 90
#define GPUD_DEFAULT_MEM_CRITICAL 95

/* Maximum lengths */
#define GPUD_MAX_MESSAGE 2048
#define GPUD_MAX_ENTITY 128
#define GPUD_MAX_FINGERPRINT 17
#define GPUD_MAX_GPU_NAME 96
#define GPUD_MAX_GPU_UUID 80
#define GPUD_MAX_PROCESSES 32

/* ==========================================================================
 * Severity Levels (matches telemetryd)
 * ========================================================================== */

typedef enum {
    GPUD_SEV_DEBUG = 0,
    GPUD_SEV_INFO,
    GPUD_SEV_NOTICE,
    GPUD_SEV_WARNING,
    GPUD_SEV_ERROR,
    GPUD_SEV_CRITICAL,
} gpud_severity_t;

static inline const char *gpud_severity_to_str(gpud_severity_t sev)
{
    switch (sev) {
    case GPUD_SEV_DEBUG:    return "debug";
    case GPUD_SEV_INFO:     return "info";
    case GPUD_SEV_NOTICE:   return "notice";
    case GPUD_SEV_WARNING:  return "warning";
    case GPUD_SEV_ERROR:    return "error";
    case GPUD_SEV_CRITICAL: return "critical";
    default:                return "info";
    }
}

/* ==========================================================================
 * GPU Process Info
 * ========================================================================== */

struct gpud_process {
    unsigned int pid;
    char name[64];
    unsigned long long mem_bytes;
};

/* ==========================================================================
 * GPU Device Info
 * ========================================================================== */

struct gpud_device {
    unsigned int index;
    char name[GPUD_MAX_GPU_NAME];
    char uuid[GPUD_MAX_GPU_UUID];

    /* Memory */
    unsigned long long mem_used_bytes;
    unsigned long long mem_total_bytes;
    unsigned int mem_pct;

    /* Utilization */
    unsigned int util_gpu_pct;
    unsigned int util_mem_pct;

    /* Thermal */
    unsigned int temp_c;

    /* Power */
    unsigned int power_mw;

    /* Throttling */
    unsigned long long throttle_reasons;

    /* ECC errors */
    unsigned long long ecc_errors_corrected;
    unsigned long long ecc_errors_uncorrected;

    /* Running processes */
    struct gpud_process processes[GPUD_MAX_PROCESSES];
    unsigned int process_count;
};

/* ==========================================================================
 * GPU Event
 * ========================================================================== */

struct gpud_event {
    uint64_t ts_ns;
    gpud_severity_t severity;
    char message[GPUD_MAX_MESSAGE];
    char entity[GPUD_MAX_ENTITY];
    char fingerprint[GPUD_MAX_FINGERPRINT];
    struct gpud_device *device;  /* For raw data */
};

/* ==========================================================================
 * Configuration
 * ========================================================================== */

struct gpud_config {
    char socket_path[256];
    int interval_ms;
    int temp_warning;
    int temp_critical;
    int mem_warning;
    int mem_critical;
    int log_level;
    bool emit_status;  /* Emit gpu_status events every poll */
};

#define GPUD_DEFAULT_CONFIG {                     \
    .socket_path = GPUD_DEFAULT_SOCKET,           \
    .interval_ms = GPUD_DEFAULT_INTERVAL_MS,      \
    .temp_warning = GPUD_DEFAULT_TEMP_WARNING,    \
    .temp_critical = GPUD_DEFAULT_TEMP_CRITICAL,  \
    .mem_warning = GPUD_DEFAULT_MEM_WARNING,      \
    .mem_critical = GPUD_DEFAULT_MEM_CRITICAL,    \
    .log_level = 2,                               \
    .emit_status = true,                          \
}

/* ==========================================================================
 * Utility Functions
 * ========================================================================== */

static inline uint64_t gpud_get_timestamp_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static inline uint64_t gpud_get_monotonic_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* ==========================================================================
 * Logging Macros
 * ========================================================================== */

#define LOG_ERROR 0
#define LOG_WARN  1
#define LOG_INFO  2
#define LOG_DEBUG 3

extern int gpud_log_level;

#define gpud_log_error(fmt, ...) \
    do { if (gpud_log_level >= LOG_ERROR) fprintf(stderr, "ERROR: " fmt "\n", ##__VA_ARGS__); } while (0)
#define gpud_log_warn(fmt, ...) \
    do { if (gpud_log_level >= LOG_WARN) fprintf(stderr, "WARN: " fmt "\n", ##__VA_ARGS__); } while (0)
#define gpud_log_info(fmt, ...) \
    do { if (gpud_log_level >= LOG_INFO) fprintf(stderr, "INFO: " fmt "\n", ##__VA_ARGS__); } while (0)
#define gpud_log_debug(fmt, ...) \
    do { if (gpud_log_level >= LOG_DEBUG) fprintf(stderr, "DEBUG: " fmt "\n", ##__VA_ARGS__); } while (0)

#endif /* __GPUD_H__ */
