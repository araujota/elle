// SPDX-License-Identifier: MIT
/**
 * collector.c - GPU metrics collector implementation
 *
 * Polls NVIDIA GPUs via NVML and generates telemetry events.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "collector.h"
#include "json.h"

#define MAX_GPUS 8

struct gpud_collector {
    nvml_api_t *api;
    struct gpud_config config;
    unsigned int device_count;
    nvmlDevice_t devices[MAX_GPUS];
    struct gpud_device device_info[MAX_GPUS];
};

/* ==========================================================================
 * Helper: Get process name from PID
 * ========================================================================== */

static void get_process_name(unsigned int pid, char *name, size_t len)
{
    char path[64];
    FILE *f;

    snprintf(path, sizeof(path), "/proc/%u/comm", pid);
    f = fopen(path, "r");
    if (f) {
        if (fgets(name, (int)len, f)) {
            /* Remove trailing newline */
            char *nl = strchr(name, '\n');
            if (nl)
                *nl = '\0';
        } else {
            snprintf(name, len, "pid:%u", pid);
        }
        fclose(f);
    } else {
        snprintf(name, len, "pid:%u", pid);
    }
}

/* ==========================================================================
 * Helper: Compute fingerprint
 * ========================================================================== */

static void compute_fingerprint(const char *message, const char *entity, char *fp)
{
    /* Simple hash for fingerprint - combine message and entity */
    unsigned long hash = 5381;
    const char *str;

    for (str = message; *str; str++)
        hash = ((hash << 5) + hash) + (unsigned char)*str;

    if (entity) {
        for (str = entity; *str; str++)
            hash = ((hash << 5) + hash) + (unsigned char)*str;
    }

    snprintf(fp, GPUD_MAX_FINGERPRINT, "%016lx", hash);
}

/* ==========================================================================
 * Helper: Poll single device
 * ========================================================================== */

static int poll_device(struct gpud_collector *collector, unsigned int idx)
{
    nvml_api_t *api = collector->api;
    nvmlDevice_t device = collector->devices[idx];
    struct gpud_device *info = &collector->device_info[idx];
    nvmlReturn_t ret;

    info->index = idx;

    /* Get device name */
    if (api->DeviceGetName) {
        ret = api->DeviceGetName(device, info->name, sizeof(info->name));
        if (ret != NVML_SUCCESS)
            snprintf(info->name, sizeof(info->name), "GPU %u", idx);
    } else {
        snprintf(info->name, sizeof(info->name), "GPU %u", idx);
    }

    /* Get UUID */
    if (api->DeviceGetUUID) {
        ret = api->DeviceGetUUID(device, info->uuid, sizeof(info->uuid));
        if (ret != NVML_SUCCESS)
            info->uuid[0] = '\0';
    }

    /* Get memory info */
    nvmlMemory_t mem;
    ret = api->DeviceGetMemoryInfo(device, &mem);
    if (ret == NVML_SUCCESS) {
        info->mem_used_bytes = mem.used;
        info->mem_total_bytes = mem.total;
        if (mem.total > 0)
            info->mem_pct = (unsigned int)((mem.used * 100) / mem.total);
        else
            info->mem_pct = 0;
    }

    /* Get utilization */
    if (api->DeviceGetUtilizationRates) {
        nvmlUtilization_t util;
        ret = api->DeviceGetUtilizationRates(device, &util);
        if (ret == NVML_SUCCESS) {
            info->util_gpu_pct = util.gpu;
            info->util_mem_pct = util.memory;
        }
    }

    /* Get temperature */
    if (api->DeviceGetTemperature) {
        unsigned int temp;
        ret = api->DeviceGetTemperature(device, NVML_TEMPERATURE_GPU, &temp);
        if (ret == NVML_SUCCESS)
            info->temp_c = temp;
    }

    /* Get power */
    if (api->DeviceGetPowerUsage) {
        unsigned int power;
        ret = api->DeviceGetPowerUsage(device, &power);
        if (ret == NVML_SUCCESS)
            info->power_mw = power;
    }

    /* Get throttle reasons */
    if (api->DeviceGetCurrentClocksThrottleReasons) {
        ret = api->DeviceGetCurrentClocksThrottleReasons(device, &info->throttle_reasons);
        if (ret != NVML_SUCCESS)
            info->throttle_reasons = 0;
    }

    /* Get ECC errors */
    if (api->DeviceGetTotalEccErrors) {
        unsigned long long errors;

        ret = api->DeviceGetTotalEccErrors(device, NVML_MEMORY_ERROR_TYPE_CORRECTED,
                                            NVML_VOLATILE_ECC, &errors);
        if (ret == NVML_SUCCESS)
            info->ecc_errors_corrected = errors;

        ret = api->DeviceGetTotalEccErrors(device, NVML_MEMORY_ERROR_TYPE_UNCORRECTED,
                                            NVML_VOLATILE_ECC, &errors);
        if (ret == NVML_SUCCESS)
            info->ecc_errors_uncorrected = errors;
    }

    /* Get running processes */
    info->process_count = 0;

    if (api->DeviceGetComputeRunningProcesses) {
        nvmlProcessInfo_t procs[GPUD_MAX_PROCESSES];
        unsigned int count = GPUD_MAX_PROCESSES;

        ret = api->DeviceGetComputeRunningProcesses(device, &count, procs);
        if (ret == NVML_SUCCESS || ret == NVML_ERROR_INSUFFICIENT_SIZE) {
            for (unsigned int i = 0; i < count && info->process_count < GPUD_MAX_PROCESSES; i++) {
                struct gpud_process *p = &info->processes[info->process_count++];
                p->pid = procs[i].pid;
                p->mem_bytes = procs[i].usedGpuMemory;
                get_process_name(p->pid, p->name, sizeof(p->name));
            }
        }
    }

    if (api->DeviceGetGraphicsRunningProcesses) {
        nvmlProcessInfo_t procs[GPUD_MAX_PROCESSES];
        unsigned int count = GPUD_MAX_PROCESSES;

        ret = api->DeviceGetGraphicsRunningProcesses(device, &count, procs);
        if (ret == NVML_SUCCESS || ret == NVML_ERROR_INSUFFICIENT_SIZE) {
            for (unsigned int i = 0; i < count && info->process_count < GPUD_MAX_PROCESSES; i++) {
                /* Check for duplicates */
                bool dup = false;
                for (unsigned int j = 0; j < info->process_count; j++) {
                    if (info->processes[j].pid == procs[i].pid) {
                        dup = true;
                        break;
                    }
                }
                if (!dup) {
                    struct gpud_process *p = &info->processes[info->process_count++];
                    p->pid = procs[i].pid;
                    p->mem_bytes = procs[i].usedGpuMemory;
                    get_process_name(p->pid, p->name, sizeof(p->name));
                }
            }
        }
    }

    return 0;
}

/* ==========================================================================
 * Helper: Emit event for device
 * ========================================================================== */

static int emit_event(struct gpud_collector *collector, struct gpud_client *client,
                      struct gpud_device *dev, gpud_severity_t sev,
                      const char *message)
{
    struct gpud_event evt;
    char *json;
    (void)collector;

    memset(&evt, 0, sizeof(evt));
    evt.ts_ns = gpud_get_timestamp_ns();
    evt.severity = sev;
    evt.device = dev;

    snprintf(evt.message, sizeof(evt.message), "%s", message);
    snprintf(evt.entity, sizeof(evt.entity), "gpu:%u", dev->index);
    compute_fingerprint(message, evt.entity, evt.fingerprint);

    json = gpud_json_event(&evt);
    if (!json)
        return -1;

    int ret = gpud_client_write(client, json, strlen(json));
    free(json);

    return ret;
}

/* ==========================================================================
 * Public API
 * ========================================================================== */

struct gpud_collector *gpud_collector_create(nvml_api_t *api,
                                              const struct gpud_config *config)
{
    struct gpud_collector *collector;
    nvmlReturn_t ret;

    if (!api)
        return NULL;

    collector = calloc(1, sizeof(*collector));
    if (!collector) {
        gpud_log_error("Failed to allocate collector");
        return NULL;
    }

    collector->api = api;
    if (config)
        collector->config = *config;

    /* Get device count */
    ret = api->DeviceGetCount(&collector->device_count);
    if (ret != NVML_SUCCESS) {
        gpud_log_error("DeviceGetCount failed: %s", nvml_error_string(api, ret));
        free(collector);
        return NULL;
    }

    if (collector->device_count == 0) {
        gpud_log_info("No NVIDIA GPUs detected");
        free(collector);
        return NULL;
    }

    if (collector->device_count > MAX_GPUS)
        collector->device_count = MAX_GPUS;

    gpud_log_info("Found %u NVIDIA GPU(s)", collector->device_count);

    /* Get device handles */
    for (unsigned int i = 0; i < collector->device_count; i++) {
        ret = api->DeviceGetHandleByIndex(i, &collector->devices[i]);
        if (ret != NVML_SUCCESS) {
            gpud_log_warn("Failed to get handle for GPU %u: %s",
                          i, nvml_error_string(api, ret));
            collector->devices[i] = NULL;
        }
    }

    return collector;
}

void gpud_collector_destroy(struct gpud_collector *collector)
{
    free(collector);
}

unsigned int gpud_collector_get_device_count(struct gpud_collector *collector)
{
    return collector ? collector->device_count : 0;
}

int gpud_collector_poll(struct gpud_collector *collector, struct gpud_client *client)
{
    int events_emitted = 0;

    if (!collector || !client)
        return -1;

    for (unsigned int i = 0; i < collector->device_count; i++) {
        if (!collector->devices[i])
            continue;

        /* Poll device metrics */
        if (poll_device(collector, i) < 0)
            continue;

        struct gpud_device *dev = &collector->device_info[i];
        char msg[512];

        /* Check memory thresholds */
        if (dev->mem_pct >= (unsigned int)collector->config.mem_critical) {
            snprintf(msg, sizeof(msg),
                     "GPU %u (%s) memory CRITICAL at %u%% (%.1f/%.1f GB)",
                     i, dev->name, dev->mem_pct,
                     (double)dev->mem_used_bytes / (1024.0 * 1024.0 * 1024.0),
                     (double)dev->mem_total_bytes / (1024.0 * 1024.0 * 1024.0));
            emit_event(collector, client, dev, GPUD_SEV_CRITICAL, msg);
            events_emitted++;
        } else if (dev->mem_pct >= (unsigned int)collector->config.mem_warning) {
            snprintf(msg, sizeof(msg),
                     "GPU %u (%s) memory at %u%% (%.1f/%.1f GB)",
                     i, dev->name, dev->mem_pct,
                     (double)dev->mem_used_bytes / (1024.0 * 1024.0 * 1024.0),
                     (double)dev->mem_total_bytes / (1024.0 * 1024.0 * 1024.0));
            emit_event(collector, client, dev, GPUD_SEV_WARNING, msg);
            events_emitted++;
        }

        /* Check thermal thresholds */
        if (dev->temp_c >= (unsigned int)collector->config.temp_critical) {
            snprintf(msg, sizeof(msg),
                     "GPU %u (%s) temperature CRITICAL at %u C",
                     i, dev->name, dev->temp_c);
            emit_event(collector, client, dev, GPUD_SEV_CRITICAL, msg);
            events_emitted++;
        } else if (dev->temp_c >= (unsigned int)collector->config.temp_warning) {
            snprintf(msg, sizeof(msg),
                     "GPU %u (%s) temperature warning at %u C",
                     i, dev->name, dev->temp_c);
            emit_event(collector, client, dev, GPUD_SEV_WARNING, msg);
            events_emitted++;
        }

        /* Check throttling */
        if (dev->throttle_reasons != 0 &&
            dev->throttle_reasons != nvmlClocksThrottleReasonGpuIdle) {
            snprintf(msg, sizeof(msg),
                     "GPU %u (%s) throttling active (reasons: 0x%llx)",
                     i, dev->name, dev->throttle_reasons);
            emit_event(collector, client, dev, GPUD_SEV_WARNING, msg);
            events_emitted++;
        }

        /* Check ECC errors */
        if (dev->ecc_errors_uncorrected > 0) {
            snprintf(msg, sizeof(msg),
                     "GPU %u (%s) has %llu uncorrected ECC errors",
                     i, dev->name, dev->ecc_errors_uncorrected);
            emit_event(collector, client, dev, GPUD_SEV_ERROR, msg);
            events_emitted++;
        }

        /* Emit status event if configured */
        if (collector->config.emit_status) {
            snprintf(msg, sizeof(msg),
                     "GPU %u (%s) status: mem=%u%%, util=%u%%, temp=%uC, power=%.1fW",
                     i, dev->name, dev->mem_pct, dev->util_gpu_pct,
                     dev->temp_c, (double)dev->power_mw / 1000.0);
            emit_event(collector, client, dev, GPUD_SEV_INFO, msg);
            events_emitted++;
        }
    }

    return events_emitted;
}
