// SPDX-License-Identifier: MIT
/**
 * nvml.h - NVML runtime loading wrapper
 *
 * Loads NVIDIA Management Library (NVML) at runtime via dlopen.
 * This allows gpud to build without CUDA/NVML headers and gracefully
 * handle systems without NVIDIA GPUs.
 */

#ifndef __GPUD_NVML_H__
#define __GPUD_NVML_H__

#include <stdbool.h>
#include <stdint.h>

/* ==========================================================================
 * NVML Types (copied from nvml.h to avoid build dependency)
 * ========================================================================== */

typedef enum {
    NVML_SUCCESS = 0,
    NVML_ERROR_UNINITIALIZED = 1,
    NVML_ERROR_INVALID_ARGUMENT = 2,
    NVML_ERROR_NOT_SUPPORTED = 3,
    NVML_ERROR_NO_PERMISSION = 4,
    NVML_ERROR_NOT_FOUND = 6,
    NVML_ERROR_INSUFFICIENT_SIZE = 7,
    NVML_ERROR_INSUFFICIENT_POWER = 8,
    NVML_ERROR_DRIVER_NOT_LOADED = 9,
    NVML_ERROR_TIMEOUT = 10,
    NVML_ERROR_IRQ_ISSUE = 11,
    NVML_ERROR_LIBRARY_NOT_FOUND = 12,
    NVML_ERROR_FUNCTION_NOT_FOUND = 13,
    NVML_ERROR_CORRUPTED_INFOROM = 14,
    NVML_ERROR_GPU_IS_LOST = 15,
    NVML_ERROR_RESET_REQUIRED = 16,
    NVML_ERROR_OPERATING_SYSTEM = 17,
    NVML_ERROR_LIB_RM_VERSION_MISMATCH = 18,
    NVML_ERROR_UNKNOWN = 999,
} nvmlReturn_t;

typedef enum {
    NVML_TEMPERATURE_GPU = 0,
} nvmlTemperatureSensors_t;

typedef enum {
    NVML_MEMORY_ERROR_TYPE_CORRECTED = 0,
    NVML_MEMORY_ERROR_TYPE_UNCORRECTED = 1,
} nvmlMemoryErrorType_t;

typedef enum {
    NVML_VOLATILE_ECC = 0,
    NVML_AGGREGATE_ECC = 1,
} nvmlEccCounterType_t;

typedef enum {
    NVML_MEMORY_LOCATION_L1_CACHE = 0,
    NVML_MEMORY_LOCATION_L2_CACHE = 1,
    NVML_MEMORY_LOCATION_DRAM = 2,
    NVML_MEMORY_LOCATION_DEVICE_MEMORY = 2,
    NVML_MEMORY_LOCATION_REGISTER_FILE = 3,
    NVML_MEMORY_LOCATION_TEXTURE_MEMORY = 4,
    NVML_MEMORY_LOCATION_TEXTURE_SHM = 5,
    NVML_MEMORY_LOCATION_CBU = 6,
    NVML_MEMORY_LOCATION_SRAM = 7,
} nvmlMemoryLocation_t;

/* Opaque handle */
typedef struct nvmlDevice_st *nvmlDevice_t;

/* Memory info */
typedef struct {
    unsigned long long total;
    unsigned long long free;
    unsigned long long used;
} nvmlMemory_t;

/* Utilization rates */
typedef struct {
    unsigned int gpu;
    unsigned int memory;
} nvmlUtilization_t;

/* Process info */
typedef struct {
    unsigned int pid;
    unsigned long long usedGpuMemory;
    unsigned int gpuInstanceId;
    unsigned int computeInstanceId;
} nvmlProcessInfo_t;

/* Throttle reason bitmasks */
#define nvmlClocksThrottleReasonGpuIdle                   0x0000000000000001LL
#define nvmlClocksThrottleReasonApplicationsClocksSetting 0x0000000000000002LL
#define nvmlClocksThrottleReasonSwPowerCap                0x0000000000000004LL
#define nvmlClocksThrottleReasonHwSlowdown                0x0000000000000008LL
#define nvmlClocksThrottleReasonSyncBoost                 0x0000000000000010LL
#define nvmlClocksThrottleReasonSwThermalSlowdown         0x0000000000000020LL
#define nvmlClocksThrottleReasonHwThermalSlowdown         0x0000000000000040LL
#define nvmlClocksThrottleReasonHwPowerBrakeSlowdown      0x0000000000000080LL
#define nvmlClocksThrottleReasonDisplayClockSetting       0x0000000000000100LL
#define nvmlClocksThrottleReasonNone                      0x0000000000000000LL

/* ==========================================================================
 * NVML API Wrapper
 * ========================================================================== */

typedef struct {
    void *handle;  /* dlopen handle */

    /* Core functions */
    nvmlReturn_t (*Init)(void);
    nvmlReturn_t (*InitWithFlags)(unsigned int flags);
    nvmlReturn_t (*Shutdown)(void);
    const char *(*ErrorString)(nvmlReturn_t result);

    /* Device enumeration */
    nvmlReturn_t (*DeviceGetCount)(unsigned int *deviceCount);
    nvmlReturn_t (*DeviceGetHandleByIndex)(unsigned int index, nvmlDevice_t *device);

    /* Device info */
    nvmlReturn_t (*DeviceGetName)(nvmlDevice_t device, char *name, unsigned int length);
    nvmlReturn_t (*DeviceGetUUID)(nvmlDevice_t device, char *uuid, unsigned int length);

    /* Metrics */
    nvmlReturn_t (*DeviceGetMemoryInfo)(nvmlDevice_t device, nvmlMemory_t *memory);
    nvmlReturn_t (*DeviceGetUtilizationRates)(nvmlDevice_t device, nvmlUtilization_t *utilization);
    nvmlReturn_t (*DeviceGetTemperature)(nvmlDevice_t device, nvmlTemperatureSensors_t sensorType,
                                          unsigned int *temp);
    nvmlReturn_t (*DeviceGetPowerUsage)(nvmlDevice_t device, unsigned int *power);

    /* Throttling */
    nvmlReturn_t (*DeviceGetCurrentClocksThrottleReasons)(nvmlDevice_t device,
                                                           unsigned long long *clocksThrottleReasons);

    /* ECC errors */
    nvmlReturn_t (*DeviceGetTotalEccErrors)(nvmlDevice_t device, nvmlMemoryErrorType_t errorType,
                                             nvmlEccCounterType_t counterType,
                                             unsigned long long *eccCounts);

    /* Processes */
    nvmlReturn_t (*DeviceGetComputeRunningProcesses)(nvmlDevice_t device,
                                                      unsigned int *infoCount,
                                                      nvmlProcessInfo_t *infos);
    nvmlReturn_t (*DeviceGetGraphicsRunningProcesses)(nvmlDevice_t device,
                                                       unsigned int *infoCount,
                                                       nvmlProcessInfo_t *infos);
} nvml_api_t;

/* ==========================================================================
 * Public API
 * ========================================================================== */

/**
 * nvml_load - Load NVML library at runtime
 *
 * Returns: Pointer to NVML API wrapper, or NULL if loading failed.
 *          Caller must call nvml_unload() when done.
 */
nvml_api_t *nvml_load(void);

/**
 * nvml_unload - Unload NVML library
 */
void nvml_unload(nvml_api_t *api);

/**
 * nvml_available - Check if NVML is available on this system
 *
 * Returns: true if NVML can be loaded, false otherwise.
 */
bool nvml_available(void);

/**
 * nvml_error_string - Get human-readable error string
 */
const char *nvml_error_string(nvml_api_t *api, nvmlReturn_t result);

#endif /* __GPUD_NVML_H__ */
