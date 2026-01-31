// SPDX-License-Identifier: MIT
/**
 * json.c - NDJSON serialization for probed events
 *
 * Serializes probed events to NDJSON format compatible with
 * the Python telemetry pipeline.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jansson.h>

#include "json.h"

/* Priority mapping (severity to journald priority) */
static int get_priority(const char *severity)
{
    if (strcmp(severity, "critical") == 0) return 2;
    if (strcmp(severity, "error") == 0) return 3;
    if (strcmp(severity, "warning") == 0) return 4;
    if (strcmp(severity, "info") == 0) return 6;
    return 6;  /* default: info */
}

/* ==========================================================================
 * Helper: Add common fields
 * ========================================================================== */

static void add_common_fields(json_t *obj,
                               uint64_t ts_ns,
                               const char *event_type,
                               const char *category,
                               const char *message,
                               const char *severity)
{
    char priority_str[8];

    json_object_set_new(obj, "_SOURCE", json_string("probed"));
    json_object_set_new(obj, "_PROBED_EVENT_TYPE", json_string(event_type));
    json_object_set_new(obj, "_PROBED_CATEGORY", json_string(category));
    json_object_set_new(obj, "MESSAGE", json_string(message));

    snprintf(priority_str, sizeof(priority_str), "%d", get_priority(severity));
    json_object_set_new(obj, "PRIORITY", json_string(priority_str));

    json_object_set_new(obj, "_PROBED_TS_NS", json_integer((json_int_t)ts_ns));
}

/* ==========================================================================
 * PSI Event Serialization
 * ========================================================================== */

char *probed_json_psi_event(const struct probed_psi_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine severity */
    if (evt->critical)
        severity = "critical";
    else if (evt->warning)
        severity = "warning";
    else
        severity = "info";

    /* Format message */
    snprintf(message, sizeof(message),
             "PSI %s pressure: some=%.1f%% full=%.1f%% (10s avg)",
             evt->resource, evt->some_avg10, evt->full_avg10);

    /* Add common fields */
    add_common_fields(obj, evt->ts_ns, "psi_pressure", "oom", message, severity);

    /* Add PSI-specific fields */
    json_object_set_new(obj, "_PROBED_PSI_RESOURCE", json_string(evt->resource));

    json_object_set_new(obj, "_PROBED_PSI_SOME_AVG10", json_real(evt->some_avg10));
    json_object_set_new(obj, "_PROBED_PSI_SOME_AVG60", json_real(evt->some_avg60));
    json_object_set_new(obj, "_PROBED_PSI_SOME_AVG300", json_real(evt->some_avg300));

    json_object_set_new(obj, "_PROBED_PSI_FULL_AVG10", json_real(evt->full_avg10));
    json_object_set_new(obj, "_PROBED_PSI_FULL_AVG60", json_real(evt->full_avg60));
    json_object_set_new(obj, "_PROBED_PSI_FULL_AVG300", json_real(evt->full_avg300));

    json_object_set_new(obj, "_PROBED_PSI_SOME_TOTAL_US", json_integer((json_int_t)evt->some_total_us));
    json_object_set_new(obj, "_PROBED_PSI_FULL_TOTAL_US", json_integer((json_int_t)evt->full_total_us));

    json_object_set_new(obj, "_PROBED_PSI_WARNING", json_boolean(evt->warning));
    json_object_set_new(obj, "_PROBED_PSI_CRITICAL", json_boolean(evt->critical));

    /* Entity for correlation */
    char entity[32];
    snprintf(entity, sizeof(entity), "resource:%s", evt->resource);
    json_object_set_new(obj, "_PROBED_ENTITY", json_string(entity));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Unit State Event Serialization
 * ========================================================================== */

char *probed_json_unit_event(const struct probed_unit_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine severity */
    if (evt->failed)
        severity = "error";
    else if (evt->crashloop)
        severity = "warning";
    else
        severity = "info";

    /* Format message */
    if (evt->failed) {
        snprintf(message, sizeof(message),
                 "Unit %s failed: %s (exit=%d, signal=%d)",
                 evt->unit, evt->result, evt->exit_code, evt->exit_signal);
    } else if (evt->crashloop) {
        snprintf(message, sizeof(message),
                 "Unit %s in crash loop (%u restarts)",
                 evt->unit, evt->n_restarts);
    } else {
        snprintf(message, sizeof(message),
                 "Unit %s state: %s/%s",
                 evt->unit, evt->active_state, evt->sub_state);
    }

    /* Add common fields */
    add_common_fields(obj, evt->ts_ns, "unit_state", "service", message, severity);

    /* Add unit-specific fields */
    json_object_set_new(obj, "_PROBED_UNIT_NAME", json_string(evt->unit));
    json_object_set_new(obj, "_PROBED_UNIT_ACTIVE_STATE", json_string(evt->active_state));
    json_object_set_new(obj, "_PROBED_UNIT_SUB_STATE", json_string(evt->sub_state));
    json_object_set_new(obj, "_PROBED_UNIT_RESULT", json_string(evt->result));
    json_object_set_new(obj, "_PROBED_UNIT_EXIT_CODE", json_integer(evt->exit_code));
    json_object_set_new(obj, "_PROBED_UNIT_EXIT_SIGNAL", json_integer(evt->exit_signal));
    json_object_set_new(obj, "_PROBED_UNIT_N_RESTARTS", json_integer(evt->n_restarts));
    json_object_set_new(obj, "_PROBED_UNIT_RESTART_USEC", json_integer((json_int_t)evt->restart_usec));
    json_object_set_new(obj, "_PROBED_UNIT_CRASHLOOP", json_boolean(evt->crashloop));
    json_object_set_new(obj, "_PROBED_UNIT_FAILED", json_boolean(evt->failed));

    /* Entity for correlation */
    char entity[256];
    snprintf(entity, sizeof(entity), "service:%s", evt->unit);
    json_object_set_new(obj, "_PROBED_ENTITY", json_string(entity));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * DNS Event Serialization
 * ========================================================================== */

char *probed_json_dns_event(const struct probed_dns_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine severity */
    if (!evt->success)
        severity = "error";
    else if (evt->latency_ms > 500)
        severity = "warning";
    else
        severity = "info";

    /* Format message */
    if (evt->success) {
        snprintf(message, sizeof(message),
                 "DNS query %s via %s: OK (%ums)",
                 evt->query, evt->resolver, evt->latency_ms);
    } else {
        snprintf(message, sizeof(message),
                 "DNS query %s via %s: FAILED (%s)",
                 evt->query, evt->resolver, evt->error_msg);
    }

    /* Add common fields */
    add_common_fields(obj, evt->ts_ns, "dns_probe", "net", message, severity);

    /* Add DNS-specific fields */
    json_object_set_new(obj, "_PROBED_DNS_RESOLVER", json_string(evt->resolver));
    json_object_set_new(obj, "_PROBED_DNS_QUERY", json_string(evt->query));
    json_object_set_new(obj, "_PROBED_DNS_SUCCESS", json_boolean(evt->success));
    json_object_set_new(obj, "_PROBED_DNS_LATENCY_MS", json_integer(evt->latency_ms));
    json_object_set_new(obj, "_PROBED_DNS_ERROR_CODE", json_integer(evt->error_code));
    if (evt->error_msg[0])
        json_object_set_new(obj, "_PROBED_DNS_ERROR_MSG", json_string(evt->error_msg));

    /* Entity for correlation */
    char entity[128];
    snprintf(entity, sizeof(entity), "dns:%s", evt->resolver);
    json_object_set_new(obj, "_PROBED_ENTITY", json_string(entity));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Conntrack Event Serialization
 * ========================================================================== */

char *probed_json_conntrack_event(const struct probed_conntrack_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine severity */
    if (evt->critical)
        severity = "critical";
    else if (evt->warning)
        severity = "warning";
    else
        severity = "info";

    /* Format message */
    snprintf(message, sizeof(message),
             "Conntrack: %u/%u entries (%.1f%% utilization)",
             evt->count, evt->max, evt->utilization);

    /* Add common fields */
    add_common_fields(obj, evt->ts_ns, "conntrack_stats", "net", message, severity);

    /* Add conntrack-specific fields */
    json_object_set_new(obj, "_PROBED_CT_COUNT", json_integer(evt->count));
    json_object_set_new(obj, "_PROBED_CT_MAX", json_integer(evt->max));
    json_object_set_new(obj, "_PROBED_CT_UTILIZATION", json_real(evt->utilization));

    json_object_set_new(obj, "_PROBED_CT_SEARCHED", json_integer(evt->searched));
    json_object_set_new(obj, "_PROBED_CT_FOUND", json_integer(evt->found));
    json_object_set_new(obj, "_PROBED_CT_NEW", json_integer(evt->new));
    json_object_set_new(obj, "_PROBED_CT_INVALID", json_integer(evt->invalid));
    json_object_set_new(obj, "_PROBED_CT_IGNORE", json_integer(evt->ignore));
    json_object_set_new(obj, "_PROBED_CT_DELETE", json_integer(evt->delete));
    json_object_set_new(obj, "_PROBED_CT_DELETE_LIST", json_integer(evt->delete_list));
    json_object_set_new(obj, "_PROBED_CT_INSERT", json_integer(evt->insert));
    json_object_set_new(obj, "_PROBED_CT_INSERT_FAILED", json_integer(evt->insert_failed));
    json_object_set_new(obj, "_PROBED_CT_DROP", json_integer(evt->drop));
    json_object_set_new(obj, "_PROBED_CT_EARLY_DROP", json_integer(evt->early_drop));

    json_object_set_new(obj, "_PROBED_CT_WARNING", json_boolean(evt->warning));
    json_object_set_new(obj, "_PROBED_CT_CRITICAL", json_boolean(evt->critical));

    /* Entity for correlation */
    json_object_set_new(obj, "_PROBED_ENTITY", json_string("conntrack:nf_conntrack"));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Hardware Event Serialization
 * ========================================================================== */

char *probed_json_hardware_event(const struct probed_hardware_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine severity based on error type */
    if (strcmp(evt->error_type, "uncorrectable") == 0)
        severity = "critical";
    else if (evt->error_count > evt->prev_count)
        severity = "warning";
    else
        severity = "info";

    /* Format message */
    uint64_t delta = evt->error_count - evt->prev_count;
    if (delta > 0) {
        snprintf(message, sizeof(message),
                 "%s %s error on %s: %lu new (%lu total)",
                 evt->subsystem, evt->error_type, evt->device,
                 (unsigned long)delta, (unsigned long)evt->error_count);
    } else {
        snprintf(message, sizeof(message),
                 "%s %s errors on %s: %lu total",
                 evt->subsystem, evt->error_type, evt->device,
                 (unsigned long)evt->error_count);
    }

    /* Add common fields */
    add_common_fields(obj, evt->ts_ns, "hardware_error", "smart", message, severity);

    /* Add hardware-specific fields */
    json_object_set_new(obj, "_PROBED_HW_SUBSYSTEM", json_string(evt->subsystem));
    json_object_set_new(obj, "_PROBED_HW_DEVICE", json_string(evt->device));
    json_object_set_new(obj, "_PROBED_HW_ERROR_TYPE", json_string(evt->error_type));
    json_object_set_new(obj, "_PROBED_HW_ERROR_COUNT", json_integer((json_int_t)evt->error_count));
    json_object_set_new(obj, "_PROBED_HW_PREV_COUNT", json_integer((json_int_t)evt->prev_count));

    if (evt->mc[0])
        json_object_set_new(obj, "_PROBED_HW_MC", json_string(evt->mc));
    if (evt->csrow[0])
        json_object_set_new(obj, "_PROBED_HW_CSROW", json_string(evt->csrow));
    if (evt->channel[0])
        json_object_set_new(obj, "_PROBED_HW_CHANNEL", json_string(evt->channel));

    /* Entity for correlation */
    char entity[128];
    snprintf(entity, sizeof(entity), "hardware:%s", evt->device);
    json_object_set_new(obj, "_PROBED_ENTITY", json_string(entity));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}

/* ==========================================================================
 * Auth Event Serialization
 * ========================================================================== */

char *probed_json_auth_event(const struct probed_auth_event *evt)
{
    json_t *obj = json_object();
    char message[256];
    char *result;
    const char *severity;

    if (!obj)
        return NULL;

    /* Determine severity */
    if (evt->likely_brute_force)
        severity = "critical";
    else if (evt->failed_logins_1m > 0 || evt->failed_ssh_1m > 0)
        severity = "warning";
    else
        severity = "info";

    /* Format message */
    if (evt->likely_brute_force) {
        snprintf(message, sizeof(message),
                 "Possible brute force attack: %u failed logins, %u failed SSH from %u sources",
                 evt->failed_logins_1m, evt->failed_ssh_1m, evt->unique_sources);
    } else {
        snprintf(message, sizeof(message),
                 "Auth stats: %u failed logins, %u failed sudo, %u failed SSH (1m)",
                 evt->failed_logins_1m, evt->failed_sudo_1m, evt->failed_ssh_1m);
    }

    /* Add common fields */
    add_common_fields(obj, evt->ts_ns, "auth_aggregate", "auth", message, severity);

    /* Add auth-specific fields */
    json_object_set_new(obj, "_PROBED_AUTH_FAILED_LOGINS_1M", json_integer(evt->failed_logins_1m));
    json_object_set_new(obj, "_PROBED_AUTH_FAILED_SUDO_1M", json_integer(evt->failed_sudo_1m));
    json_object_set_new(obj, "_PROBED_AUTH_FAILED_SSH_1M", json_integer(evt->failed_ssh_1m));
    json_object_set_new(obj, "_PROBED_AUTH_UNIQUE_SOURCES", json_integer(evt->unique_sources));
    json_object_set_new(obj, "_PROBED_AUTH_BRUTE_FORCE", json_boolean(evt->likely_brute_force));

    if (evt->last_user[0])
        json_object_set_new(obj, "_PROBED_AUTH_LAST_USER", json_string(evt->last_user));
    if (evt->last_source[0])
        json_object_set_new(obj, "_PROBED_AUTH_LAST_SOURCE", json_string(evt->last_source));

    /* Entity for correlation */
    json_object_set_new(obj, "_PROBED_ENTITY", json_string("auth:system"));

    result = json_dumps(obj, JSON_COMPACT);
    json_decref(obj);

    return result;
}
