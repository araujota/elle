// SPDX-License-Identifier: MIT
/**
 * loader.h - eBPF program loader for elled-telemetryd
 *
 * Optional component that loads and manages eBPF programs.
 * Compiled in when ENABLE_EBPF=1 and libbpf is available.
 */

#ifndef __TELEMETRYD_EBPF_LOADER_H__
#define __TELEMETRYD_EBPF_LOADER_H__

#include "../telemetryd.h"
#include "../normalize/normalizer.h"
#include "../socket.h"
#include <stdbool.h>
#include <stdint.h>

/* Opaque eBPF loader handle */
struct ebpf_loader;

/**
 * struct ebpf_config - eBPF program enable flags
 */
struct ebpf_config {
    bool enable_oom;         /* OOM killer events */
    bool enable_exec;        /* Process exec events */
    bool enable_block_io;    /* Block I/O completion */
    bool enable_tcp_retrans; /* TCP retransmissions */
    bool enable_cap_deny;    /* Capability denials */
    bool enable_file_deny;   /* File access denials */
    bool enable_mount;       /* Mount operations */
};

/* Default eBPF configuration */
#define EBPF_DEFAULT_CONFIG {       \
    .enable_oom = true,             \
    .enable_exec = false,           \
    .enable_block_io = false,       \
    .enable_tcp_retrans = true,     \
    .enable_cap_deny = true,        \
    .enable_file_deny = true,       \
    .enable_mount = true,           \
}

/**
 * ebpf_loader_create - Create eBPF loader
 * @cfg: eBPF configuration
 * @norm: Normalizer for event processing
 * @sock: Socket for event output
 *
 * Returns: Loader handle, or NULL on failure
 */
struct ebpf_loader *ebpf_loader_create(
    const struct ebpf_config *cfg,
    struct normalizer *norm,
    struct telem_socket *sock
);

/**
 * ebpf_loader_start - Load and attach eBPF programs
 * @loader: Loader handle
 *
 * Returns: Number of programs loaded, or -1 on error
 */
int ebpf_loader_start(struct ebpf_loader *loader);

/**
 * ebpf_loader_poll - Poll ring buffers for events
 * @loader: Loader handle
 * @timeout_ms: Poll timeout in milliseconds
 *
 * Returns: Number of events processed, or -1 on error
 */
int ebpf_loader_poll(struct ebpf_loader *loader, int timeout_ms);

/**
 * ebpf_loader_get_fd - Get poll fd for ring buffer
 * @loader: Loader handle
 *
 * Returns: File descriptor, or -1 if not available
 */
int ebpf_loader_get_fd(struct ebpf_loader *loader);

/**
 * ebpf_loader_get_stats - Get loader statistics
 * @loader: Loader handle
 * @events: Output for total events (may be NULL)
 * @errors: Output for total errors (may be NULL)
 */
void ebpf_loader_get_stats(struct ebpf_loader *loader,
                            uint64_t *events,
                            uint64_t *errors);

/**
 * ebpf_loader_destroy - Destroy loader and cleanup
 * @loader: Loader handle
 */
void ebpf_loader_destroy(struct ebpf_loader *loader);

/**
 * ebpf_is_available - Check if eBPF is available on this system
 *
 * Returns: true if eBPF can be used, false otherwise
 */
bool ebpf_is_available(void);

#endif /* __TELEMETRYD_EBPF_LOADER_H__ */
