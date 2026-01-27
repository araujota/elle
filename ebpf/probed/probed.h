/* SPDX-License-Identifier: MIT */
/**
 * probed.h - Shared event structures for elled-probed
 *
 * This header defines the event structures for the polling daemon.
 * All structures use fixed-size types for JSON serialization consistency.
 *
 * Event flow:
 *   Probe Function -> Event Struct -> JSON -> Unix Socket
 */

#ifndef __PROBED_H__
#define __PROBED_H__

#include <stdint.h>
#include <stdbool.h>
#include <time.h>

/* ============================================================================
 * Event Type Enumeration
 * ============================================================================ */

enum probed_event_type {
    /* Probed events (30+, continues from eBPF event types) */
    PROBED_EVENT_PSI_PRESSURE    = 30,
    PROBED_EVENT_UNIT_STATE      = 31,
    PROBED_EVENT_DNS_PROBE       = 32,
    PROBED_EVENT_CONNTRACK_STATS = 33,
    PROBED_EVENT_HARDWARE_ERROR  = 34,
    PROBED_EVENT_AUTH_AGGREGATE  = 35,
};

/* String mappings for event types */
static const char *probed_event_type_str[] = {
    [PROBED_EVENT_PSI_PRESSURE - 30]    = "psi_pressure",
    [PROBED_EVENT_UNIT_STATE - 30]      = "unit_state",
    [PROBED_EVENT_DNS_PROBE - 30]       = "dns_probe",
    [PROBED_EVENT_CONNTRACK_STATS - 30] = "conntrack_stats",
    [PROBED_EVENT_HARDWARE_ERROR - 30]  = "hardware_error",
    [PROBED_EVENT_AUTH_AGGREGATE - 30]  = "auth_aggregate",
};

/* Category mappings for event types */
static const char *probed_event_category[] = {
    [PROBED_EVENT_PSI_PRESSURE - 30]    = "oom",
    [PROBED_EVENT_UNIT_STATE - 30]      = "service",
    [PROBED_EVENT_DNS_PROBE - 30]       = "net",
    [PROBED_EVENT_CONNTRACK_STATS - 30] = "net",
    [PROBED_EVENT_HARDWARE_ERROR - 30]  = "smart",
    [PROBED_EVENT_AUTH_AGGREGATE - 30]  = "auth",
};

/* ============================================================================
 * PSI Pressure Event
 * ============================================================================ */

/**
 * struct probed_psi_event - PSI pressure reading
 *
 * Read from /proc/pressure/cpu, /proc/pressure/memory, /proc/pressure/io.
 * PSI (Pressure Stall Information) indicates resource contention.
 */
struct probed_psi_event {
    uint64_t ts_ns;          /* Timestamp in nanoseconds (CLOCK_MONOTONIC) */
    char resource[8];        /* "cpu", "memory", "io" */

    double some_avg10;       /* % time at least one task stalled (10s avg) */
    double some_avg60;       /* % time at least one task stalled (60s avg) */
    double some_avg300;      /* % time at least one task stalled (300s avg) */

    double full_avg10;       /* % time ALL tasks stalled (10s avg) */
    double full_avg60;       /* % time ALL tasks stalled (60s avg) */
    double full_avg300;      /* % time ALL tasks stalled (300s avg) */

    uint64_t some_total_us;  /* Total some stall time in microseconds */
    uint64_t full_total_us;  /* Total full stall time in microseconds */

    bool warning;            /* Above warning threshold */
    bool critical;           /* Above critical threshold */
};

/* ============================================================================
 * Systemd Unit State Event
 * ============================================================================ */

/**
 * struct probed_unit_event - Systemd unit state change
 *
 * Obtained via sd-bus queries to systemd.
 * Tracks unit failures, restart loops, and dependency issues.
 */
struct probed_unit_event {
    uint64_t ts_ns;          /* Timestamp in nanoseconds */
    char unit[128];          /* Unit name (e.g., "nginx.service") */
    char active_state[32];   /* "active", "failed", "inactive", "activating" */
    char sub_state[32];      /* "running", "dead", "exited", "failed" */
    char result[32];         /* "success", "exit-code", "signal", "timeout" */

    int32_t exit_code;       /* Process exit code (if applicable) */
    int32_t exit_signal;     /* Signal that killed the process */
    uint32_t n_restarts;     /* Number of restarts */
    uint64_t restart_usec;   /* Time since last restart (microseconds) */

    bool crashloop;          /* n_restarts >= threshold in window */
    bool failed;             /* Unit is in failed state */
};

/* ============================================================================
 * DNS Health Event
 * ============================================================================ */

/**
 * struct probed_dns_event - DNS resolver health check
 *
 * Tests DNS resolution by querying configured domains.
 * Helps distinguish DNS failures from network issues.
 */
struct probed_dns_event {
    uint64_t ts_ns;          /* Timestamp in nanoseconds */
    char resolver[64];       /* Resolver IP address */
    char query[256];         /* Domain queried */

    bool success;            /* Query succeeded */
    uint32_t latency_ms;     /* Query latency in milliseconds */
    int error_code;          /* 0=ok, 1=timeout, 2=nxdomain, 3=servfail, 4=refused */
    char error_msg[64];      /* Human-readable error */
};

/* DNS error codes */
#define DNS_OK          0
#define DNS_TIMEOUT     1
#define DNS_NXDOMAIN    2
#define DNS_SERVFAIL    3
#define DNS_REFUSED     4
#define DNS_NETWORK     5

/* ============================================================================
 * Conntrack Stats Event
 * ============================================================================ */

/**
 * struct probed_conntrack_event - Connection tracking statistics
 *
 * Read from /proc/sys/net/netfilter/nf_conntrack_*.
 * Helps diagnose connection exhaustion issues.
 */
struct probed_conntrack_event {
    uint64_t ts_ns;          /* Timestamp in nanoseconds */

    uint32_t count;          /* Current entries (nf_conntrack_count) */
    uint32_t max;            /* Maximum entries (nf_conntrack_max) */
    double utilization;      /* count / max * 100 */

    uint32_t searched;       /* Entries searched */
    uint32_t found;          /* Entries found */
    uint32_t new;            /* New entries */
    uint32_t invalid;        /* Invalid entries */
    uint32_t ignore;         /* Ignored entries */
    uint32_t delete;         /* Deleted entries */
    uint32_t delete_list;    /* Entries on delete list */
    uint32_t insert;         /* Inserted entries */
    uint32_t insert_failed;  /* Failed insertions */
    uint32_t drop;           /* Dropped entries */
    uint32_t early_drop;     /* Early drops */

    bool warning;            /* Above warning threshold */
    bool critical;           /* Above critical threshold */
};

/* ============================================================================
 * Hardware Error Event
 * ============================================================================ */

/**
 * struct probed_hardware_event - Hardware error from EDAC/SMART
 *
 * Read from /sys/devices/system/edac/ for memory errors.
 * Helps correlate random system issues with failing hardware.
 */
struct probed_hardware_event {
    uint64_t ts_ns;          /* Timestamp in nanoseconds */
    char subsystem[16];      /* "memory", "pcie", "disk" */
    char device[64];         /* Device identifier */
    char error_type[32];     /* "correctable", "uncorrectable" */

    uint64_t error_count;    /* Number of errors */
    uint64_t prev_count;     /* Previous count (for delta) */

    char mc[16];             /* Memory controller (e.g., "mc0") */
    char csrow[16];          /* CS row (e.g., "csrow0") */
    char channel[16];        /* Channel (e.g., "ch0") */
};

/* ============================================================================
 * Auth Aggregate Event
 * ============================================================================ */

/**
 * struct probed_auth_event - Authentication failure aggregation
 *
 * Aggregated from journald auth messages.
 * Detects brute force attacks and account lockouts.
 */
struct probed_auth_event {
    uint64_t ts_ns;          /* Timestamp in nanoseconds */

    uint32_t failed_logins_1m;   /* Failed logins in last minute */
    uint32_t failed_sudo_1m;     /* Failed sudo attempts in last minute */
    uint32_t failed_ssh_1m;      /* Failed SSH attempts in last minute */
    uint32_t unique_sources;     /* Unique source IPs */

    bool likely_brute_force; /* Above brute force threshold */
    char last_user[32];      /* Last failed username */
    char last_source[64];    /* Last source IP */
};

/* ============================================================================
 * Configuration Structure
 * ============================================================================ */

/**
 * struct probed_config - Runtime configuration
 */
struct probed_config {
    /* Socket path */
    char socket_path[256];

    /* PSI probe config */
    bool psi_enabled;
    int psi_interval_sec;
    double memory_warning_pct;
    double memory_critical_pct;
    double io_warning_pct;
    double io_critical_pct;
    double cpu_warning_pct;
    double cpu_critical_pct;

    /* Systemd probe config */
    bool systemd_enabled;
    int systemd_interval_sec;
    int crashloop_threshold;
    int crashloop_window_sec;

    /* DNS probe config */
    bool dns_enabled;
    int dns_interval_sec;
    int dns_timeout_ms;
    char dns_test_domains[4][256];
    int dns_test_domain_count;

    /* Conntrack probe config */
    bool conntrack_enabled;
    int conntrack_interval_sec;
    double conntrack_warning_pct;
    double conntrack_critical_pct;

    /* Hardware probe config */
    bool hardware_enabled;
    int hardware_interval_sec;

    /* Auth probe config */
    bool auth_enabled;
    int auth_interval_sec;
    int brute_force_threshold;
};

/* Default configuration */
#define PROBED_DEFAULT_CONFIG {                                 \
    .socket_path = "/run/elle/probed.sock",                    \
    .psi_enabled = true,                                       \
    .psi_interval_sec = 5,                                     \
    .memory_warning_pct = 10.0,                                \
    .memory_critical_pct = 25.0,                               \
    .io_warning_pct = 20.0,                                    \
    .io_critical_pct = 50.0,                                   \
    .cpu_warning_pct = 25.0,                                   \
    .cpu_critical_pct = 50.0,                                  \
    .systemd_enabled = true,                                   \
    .systemd_interval_sec = 10,                                \
    .crashloop_threshold = 3,                                  \
    .crashloop_window_sec = 300,                               \
    .dns_enabled = true,                                       \
    .dns_interval_sec = 30,                                    \
    .dns_timeout_ms = 2000,                                    \
    .dns_test_domains = {{"connectivity-check.ubuntu.com"}},   \
    .dns_test_domain_count = 1,                                \
    .conntrack_enabled = true,                                 \
    .conntrack_interval_sec = 15,                              \
    .conntrack_warning_pct = 80.0,                             \
    .conntrack_critical_pct = 95.0,                            \
    .hardware_enabled = true,                                  \
    .hardware_interval_sec = 300,                              \
    .auth_enabled = true,                                      \
    .auth_interval_sec = 5,                                    \
    .brute_force_threshold = 5,                                \
}

/* ============================================================================
 * Helper Macros
 * ============================================================================ */

/* Get current timestamp in nanoseconds */
static inline uint64_t get_timestamp_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* Get event type string */
static inline const char *probed_event_type_to_str(enum probed_event_type type)
{
    if (type >= PROBED_EVENT_PSI_PRESSURE && type <= PROBED_EVENT_AUTH_AGGREGATE)
        return probed_event_type_str[type - 30];
    return "unknown";
}

/* Get event category */
static inline const char *probed_event_type_to_category(enum probed_event_type type)
{
    if (type >= PROBED_EVENT_PSI_PRESSURE && type <= PROBED_EVENT_AUTH_AGGREGATE)
        return probed_event_category[type - 30];
    return "other";
}

#endif /* __PROBED_H__ */
