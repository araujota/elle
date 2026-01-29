// SPDX-License-Identifier: MIT
/**
 * nvml.c - NVML runtime loading implementation
 *
 * Dynamically loads libnvidia-ml.so.1 at runtime to avoid
 * build-time dependency on CUDA/NVML.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>

#include "nvml.h"
#include "gpud.h"

/* Library search paths */
static const char *nvml_lib_paths[] = {
    "libnvidia-ml.so.1",
    "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1",
    "/usr/lib64/libnvidia-ml.so.1",
    "/usr/local/cuda/lib64/libnvidia-ml.so.1",
    NULL
};

/* ==========================================================================
 * Helper: Load function from library
 * ========================================================================== */

#define LOAD_FUNC(api, name) \
    do { \
        api->name = dlsym(api->handle, "nvml" #name); \
        if (!api->name) { \
            gpud_log_debug("Failed to load nvml" #name ": %s", dlerror()); \
        } \
    } while (0)

#define LOAD_FUNC_REQUIRED(api, name) \
    do { \
        api->name = dlsym(api->handle, "nvml" #name); \
        if (!api->name) { \
            gpud_log_error("Required function nvml" #name " not found"); \
            nvml_unload(api); \
            return NULL; \
        } \
    } while (0)

/* ==========================================================================
 * Public API
 * ========================================================================== */

bool nvml_available(void)
{
    for (int i = 0; nvml_lib_paths[i]; i++) {
        void *handle = dlopen(nvml_lib_paths[i], RTLD_LAZY | RTLD_LOCAL);
        if (handle) {
            dlclose(handle);
            return true;
        }
    }
    return false;
}

nvml_api_t *nvml_load(void)
{
    nvml_api_t *api = calloc(1, sizeof(*api));
    if (!api) {
        gpud_log_error("Failed to allocate NVML API structure");
        return NULL;
    }

    /* Try to load library from known paths */
    for (int i = 0; nvml_lib_paths[i]; i++) {
        api->handle = dlopen(nvml_lib_paths[i], RTLD_LAZY | RTLD_LOCAL);
        if (api->handle) {
            gpud_log_debug("Loaded NVML from %s", nvml_lib_paths[i]);
            break;
        }
    }

    if (!api->handle) {
        gpud_log_info("NVML library not found (no NVIDIA GPU?)");
        free(api);
        return NULL;
    }

    /* Load required functions */
    LOAD_FUNC_REQUIRED(api, Init);
    LOAD_FUNC_REQUIRED(api, Shutdown);
    LOAD_FUNC_REQUIRED(api, DeviceGetCount);
    LOAD_FUNC_REQUIRED(api, DeviceGetHandleByIndex);
    LOAD_FUNC_REQUIRED(api, DeviceGetMemoryInfo);

    /* Load optional functions */
    LOAD_FUNC(api, InitWithFlags);
    LOAD_FUNC(api, ErrorString);
    LOAD_FUNC(api, DeviceGetName);
    LOAD_FUNC(api, DeviceGetUUID);
    LOAD_FUNC(api, DeviceGetUtilizationRates);
    LOAD_FUNC(api, DeviceGetTemperature);
    LOAD_FUNC(api, DeviceGetPowerUsage);
    LOAD_FUNC(api, DeviceGetCurrentClocksThrottleReasons);
    LOAD_FUNC(api, DeviceGetTotalEccErrors);
    LOAD_FUNC(api, DeviceGetComputeRunningProcesses);
    LOAD_FUNC(api, DeviceGetGraphicsRunningProcesses);

    /* Initialize NVML */
    nvmlReturn_t ret = api->Init();
    if (ret != NVML_SUCCESS) {
        gpud_log_warn("nvmlInit() failed: %s", nvml_error_string(api, ret));
        nvml_unload(api);
        return NULL;
    }

    gpud_log_info("NVML initialized successfully");
    return api;
}

void nvml_unload(nvml_api_t *api)
{
    if (!api)
        return;

    if (api->handle) {
        if (api->Shutdown)
            api->Shutdown();
        dlclose(api->handle);
    }

    free(api);
}

const char *nvml_error_string(nvml_api_t *api, nvmlReturn_t result)
{
    if (api && api->ErrorString)
        return api->ErrorString(result);

    /* Fallback error strings */
    switch (result) {
    case NVML_SUCCESS:                     return "Success";
    case NVML_ERROR_UNINITIALIZED:         return "Uninitialized";
    case NVML_ERROR_INVALID_ARGUMENT:      return "Invalid argument";
    case NVML_ERROR_NOT_SUPPORTED:         return "Not supported";
    case NVML_ERROR_NO_PERMISSION:         return "No permission";
    case NVML_ERROR_NOT_FOUND:             return "Not found";
    case NVML_ERROR_INSUFFICIENT_SIZE:     return "Insufficient size";
    case NVML_ERROR_DRIVER_NOT_LOADED:     return "Driver not loaded";
    case NVML_ERROR_LIBRARY_NOT_FOUND:     return "Library not found";
    case NVML_ERROR_GPU_IS_LOST:           return "GPU is lost";
    default:                               return "Unknown error";
    }
}
