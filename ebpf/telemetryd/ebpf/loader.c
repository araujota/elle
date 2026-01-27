// SPDX-License-Identifier: MIT
/**
 * loader.c - eBPF program loader for elled-telemetryd
 *
 * Manages BPF program lifecycle and ring buffer event processing.
 * Adapted from elled-ebpfd collector.c for telemetryd integration.
 *
 * Events are processed through the normalizer before emission.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/resource.h>
#include <arpa/inet.h>

#ifdef ENABLE_EBPF
#include <bpf/libbpf.h>
#include <bpf/bpf.h>

/* Include generated skeletons */
#include "oom.skel.h"
#include "exec.skel.h"
#include "block_io.skel.h"
#include "tcp_retrans.skel.h"
#include "cap_deny.skel.h"
#include "file_deny.skel.h"
#include "mount.skel.h"
#endif /* ENABLE_EBPF */

#include "loader.h"
#include "../json.h"

/* Include shared eBPF event structures */
#include "../../bpf/elled.h"

/* ==========================================================================
 * Internal State
 * ========================================================================== */

struct ebpf_loader {
    /* Configuration */
    struct ebpf_config cfg;
    struct normalizer *norm;
    struct telem_socket *sock;

#ifdef ENABLE_EBPF
    /* BPF skeletons (NULL if not loaded) */
    struct oom_bpf *oom_skel;
    struct exec_bpf *exec_skel;
    struct block_io_bpf *block_io_skel;
    struct tcp_retrans_bpf *tcp_retrans_skel;
    struct cap_deny_bpf *cap_deny_skel;
    struct file_deny_bpf *file_deny_skel;
    struct mount_bpf *mount_skel;

    /* Ring buffer */
    struct ring_buffer *rb;
#endif

    /* Statistics */
    uint64_t total_events;
    uint64_t total_errors;
};

/* ==========================================================================
 * Availability Check
 * ========================================================================== */

bool ebpf_is_available(void)
{
#ifdef ENABLE_EBPF
    /* Check if we can create a simple BPF map */
    int fd = bpf_map_create(BPF_MAP_TYPE_ARRAY, NULL, sizeof(int),
                            sizeof(int), 1, NULL);
    if (fd >= 0) {
        close(fd);
        return true;
    }
    return false;
#else
    return false;
#endif
}

#ifdef ENABLE_EBPF

/* Forward declarations */
static int handle_event(void *ctx, void *data, size_t size);
static int setup_ring_buffer(struct ebpf_loader *loader);
static void update_config_map(struct ebpf_loader *loader, int map_fd);

/* ==========================================================================
 * libbpf Logging
 * ========================================================================== */

static int libbpf_print_fn(enum libbpf_print_level level,
                           const char *format, va_list args)
{
    /* Only print warnings and errors */
    if (level >= LIBBPF_WARN)
        return vfprintf(stderr, format, args);
    return 0;
}

/* ==========================================================================
 * Loader Creation
 * ========================================================================== */

struct ebpf_loader *ebpf_loader_create(
    const struct ebpf_config *cfg,
    struct normalizer *norm,
    struct telem_socket *sock)
{
    struct ebpf_loader *loader;

    if (!cfg || !norm) {
        fprintf(stderr, "ERROR: ebpf_loader_create: invalid arguments\n");
        return NULL;
    }

    loader = calloc(1, sizeof(*loader));
    if (!loader) {
        fprintf(stderr, "ERROR: Failed to allocate eBPF loader\n");
        return NULL;
    }

    loader->cfg = *cfg;
    loader->norm = norm;
    loader->sock = sock;

    /* Set up libbpf logging */
    libbpf_set_print(libbpf_print_fn);

    /* Bump RLIMIT_MEMLOCK for BPF maps */
    struct rlimit rlim = {
        .rlim_cur = 128 * 1024 * 1024,  /* 128 MB */
        .rlim_max = 128 * 1024 * 1024,
    };
    if (setrlimit(RLIMIT_MEMLOCK, &rlim)) {
        fprintf(stderr, "WARNING: Failed to set RLIMIT_MEMLOCK: %s\n",
                strerror(errno));
    }

    return loader;
}

/* ==========================================================================
 * BPF Program Loading (one per program type)
 * ========================================================================== */

static int load_oom_program(struct ebpf_loader *loader)
{
    if (!loader->cfg.enable_oom)
        return 0;

    loader->oom_skel = oom_bpf__open();
    if (!loader->oom_skel) {
        fprintf(stderr, "ERROR: Failed to open OOM BPF program\n");
        return -1;
    }

    if (oom_bpf__load(loader->oom_skel)) {
        fprintf(stderr, "ERROR: Failed to load OOM BPF program\n");
        oom_bpf__destroy(loader->oom_skel);
        loader->oom_skel = NULL;
        return -1;
    }

    if (oom_bpf__attach(loader->oom_skel)) {
        fprintf(stderr, "ERROR: Failed to attach OOM BPF program\n");
        oom_bpf__destroy(loader->oom_skel);
        loader->oom_skel = NULL;
        return -1;
    }

    update_config_map(loader, bpf_map__fd(loader->oom_skel->maps.config));
    fprintf(stderr, "Loaded OOM eBPF program\n");
    return 0;
}

static int load_exec_program(struct ebpf_loader *loader)
{
    if (!loader->cfg.enable_exec)
        return 0;

    loader->exec_skel = exec_bpf__open();
    if (!loader->exec_skel) {
        fprintf(stderr, "ERROR: Failed to open exec BPF program\n");
        return -1;
    }

    if (exec_bpf__load(loader->exec_skel)) {
        fprintf(stderr, "ERROR: Failed to load exec BPF program\n");
        exec_bpf__destroy(loader->exec_skel);
        loader->exec_skel = NULL;
        return -1;
    }

    if (exec_bpf__attach(loader->exec_skel)) {
        fprintf(stderr, "ERROR: Failed to attach exec BPF program\n");
        exec_bpf__destroy(loader->exec_skel);
        loader->exec_skel = NULL;
        return -1;
    }

    update_config_map(loader, bpf_map__fd(loader->exec_skel->maps.config));
    fprintf(stderr, "Loaded exec eBPF program\n");
    return 0;
}

static int load_block_io_program(struct ebpf_loader *loader)
{
    if (!loader->cfg.enable_block_io)
        return 0;

    loader->block_io_skel = block_io_bpf__open();
    if (!loader->block_io_skel) {
        fprintf(stderr, "ERROR: Failed to open block_io BPF program\n");
        return -1;
    }

    if (block_io_bpf__load(loader->block_io_skel)) {
        fprintf(stderr, "ERROR: Failed to load block_io BPF program\n");
        block_io_bpf__destroy(loader->block_io_skel);
        loader->block_io_skel = NULL;
        return -1;
    }

    if (block_io_bpf__attach(loader->block_io_skel)) {
        fprintf(stderr, "ERROR: Failed to attach block_io BPF program\n");
        block_io_bpf__destroy(loader->block_io_skel);
        loader->block_io_skel = NULL;
        return -1;
    }

    update_config_map(loader, bpf_map__fd(loader->block_io_skel->maps.config));
    fprintf(stderr, "Loaded block_io eBPF program\n");
    return 0;
}

static int load_tcp_retrans_program(struct ebpf_loader *loader)
{
    if (!loader->cfg.enable_tcp_retrans)
        return 0;

    loader->tcp_retrans_skel = tcp_retrans_bpf__open();
    if (!loader->tcp_retrans_skel) {
        fprintf(stderr, "ERROR: Failed to open tcp_retrans BPF program\n");
        return -1;
    }

    if (tcp_retrans_bpf__load(loader->tcp_retrans_skel)) {
        fprintf(stderr, "ERROR: Failed to load tcp_retrans BPF program\n");
        tcp_retrans_bpf__destroy(loader->tcp_retrans_skel);
        loader->tcp_retrans_skel = NULL;
        return -1;
    }

    if (tcp_retrans_bpf__attach(loader->tcp_retrans_skel)) {
        fprintf(stderr, "ERROR: Failed to attach tcp_retrans BPF program\n");
        tcp_retrans_bpf__destroy(loader->tcp_retrans_skel);
        loader->tcp_retrans_skel = NULL;
        return -1;
    }

    update_config_map(loader, bpf_map__fd(loader->tcp_retrans_skel->maps.config));
    fprintf(stderr, "Loaded tcp_retrans eBPF program\n");
    return 0;
}

static int load_cap_deny_program(struct ebpf_loader *loader)
{
    if (!loader->cfg.enable_cap_deny)
        return 0;

    loader->cap_deny_skel = cap_deny_bpf__open();
    if (!loader->cap_deny_skel) {
        fprintf(stderr, "ERROR: Failed to open cap_deny BPF program\n");
        return -1;
    }

    if (cap_deny_bpf__load(loader->cap_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to load cap_deny BPF program\n");
        cap_deny_bpf__destroy(loader->cap_deny_skel);
        loader->cap_deny_skel = NULL;
        return -1;
    }

    if (cap_deny_bpf__attach(loader->cap_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to attach cap_deny BPF program\n");
        cap_deny_bpf__destroy(loader->cap_deny_skel);
        loader->cap_deny_skel = NULL;
        return -1;
    }

    update_config_map(loader, bpf_map__fd(loader->cap_deny_skel->maps.config));
    fprintf(stderr, "Loaded cap_deny eBPF program\n");
    return 0;
}

static int load_file_deny_program(struct ebpf_loader *loader)
{
    if (!loader->cfg.enable_file_deny)
        return 0;

    loader->file_deny_skel = file_deny_bpf__open();
    if (!loader->file_deny_skel) {
        fprintf(stderr, "ERROR: Failed to open file_deny BPF program\n");
        return -1;
    }

    if (file_deny_bpf__load(loader->file_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to load file_deny BPF program\n");
        file_deny_bpf__destroy(loader->file_deny_skel);
        loader->file_deny_skel = NULL;
        return -1;
    }

    if (file_deny_bpf__attach(loader->file_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to attach file_deny BPF program\n");
        file_deny_bpf__destroy(loader->file_deny_skel);
        loader->file_deny_skel = NULL;
        return -1;
    }

    update_config_map(loader, bpf_map__fd(loader->file_deny_skel->maps.config));
    fprintf(stderr, "Loaded file_deny eBPF program\n");
    return 0;
}

static int load_mount_program(struct ebpf_loader *loader)
{
    if (!loader->cfg.enable_mount)
        return 0;

    loader->mount_skel = mount_bpf__open();
    if (!loader->mount_skel) {
        fprintf(stderr, "ERROR: Failed to open mount BPF program\n");
        return -1;
    }

    if (mount_bpf__load(loader->mount_skel)) {
        fprintf(stderr, "ERROR: Failed to load mount BPF program\n");
        mount_bpf__destroy(loader->mount_skel);
        loader->mount_skel = NULL;
        return -1;
    }

    if (mount_bpf__attach(loader->mount_skel)) {
        fprintf(stderr, "ERROR: Failed to attach mount BPF program\n");
        mount_bpf__destroy(loader->mount_skel);
        loader->mount_skel = NULL;
        return -1;
    }

    update_config_map(loader, bpf_map__fd(loader->mount_skel->maps.config));
    fprintf(stderr, "Loaded mount eBPF program\n");
    return 0;
}

/* ==========================================================================
 * Configuration Map Update
 * ========================================================================== */

static void update_config_map(struct ebpf_loader *loader, int map_fd)
{
    struct elled_config bpf_cfg = {
        .enable_oom = loader->cfg.enable_oom ? 1 : 0,
        .enable_exec = loader->cfg.enable_exec ? 1 : 0,
        .enable_block = loader->cfg.enable_block_io ? 1 : 0,
        .enable_tcp_retrans = loader->cfg.enable_tcp_retrans ? 1 : 0,
        .enable_cap_deny = loader->cfg.enable_cap_deny ? 1 : 0,
        .enable_file_deny = loader->cfg.enable_file_deny ? 1 : 0,
        .enable_mount = loader->cfg.enable_mount ? 1 : 0,
        .enable_net_drop = 0,
        .enable_process_exit = 0,
        .block_latency_threshold_us = 0,
        .exec_sample_rate = 1,
        .net_drop_sample_rate = 100,
        .filter_pid = 0,
        .filter_uid = 0xFFFFFFFF,
    };
    __u32 key = 0;
    bpf_map_update_elem(map_fd, &key, &bpf_cfg, BPF_ANY);
}

/* ==========================================================================
 * Ring Buffer Setup
 * ========================================================================== */

static int setup_ring_buffer(struct ebpf_loader *loader)
{
    struct ring_buffer *rb = NULL;
    int err = 0;

    /* Create ring buffer manager */
    rb = ring_buffer__new(0, handle_event, loader, NULL);
    if (!rb) {
        fprintf(stderr, "ERROR: Failed to create ring buffer manager\n");
        return -1;
    }

    /* Add ring buffers from each loaded program */
    if (loader->oom_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(loader->oom_skel->maps.events),
                               handle_event, loader);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add OOM ring buffer\n");
            goto cleanup;
        }
    }

    if (loader->exec_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(loader->exec_skel->maps.events),
                               handle_event, loader);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add exec ring buffer\n");
            goto cleanup;
        }
    }

    if (loader->block_io_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(loader->block_io_skel->maps.events),
                               handle_event, loader);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add block_io ring buffer\n");
            goto cleanup;
        }
    }

    if (loader->tcp_retrans_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(loader->tcp_retrans_skel->maps.events),
                               handle_event, loader);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add tcp_retrans ring buffer\n");
            goto cleanup;
        }
    }

    if (loader->cap_deny_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(loader->cap_deny_skel->maps.events),
                               handle_event, loader);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add cap_deny ring buffer\n");
            goto cleanup;
        }
    }

    if (loader->file_deny_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(loader->file_deny_skel->maps.events),
                               handle_event, loader);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add file_deny ring buffer\n");
            goto cleanup;
        }
    }

    if (loader->mount_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(loader->mount_skel->maps.events),
                               handle_event, loader);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add mount ring buffer\n");
            goto cleanup;
        }
    }

    loader->rb = rb;
    return 0;

cleanup:
    ring_buffer__free(rb);
    return -1;
}

/* ==========================================================================
 * Event Processing - Convert eBPF events to normalized telem_events
 * ========================================================================== */

static void emit_normalized_event(struct ebpf_loader *loader,
                                  telem_severity_t severity,
                                  telem_category_t category,
                                  const char *message,
                                  const char *entity,
                                  uint32_t pid)
{
    struct telem_event evt;
    bool emit;
    char *json_str;

    emit = normalizer_process_prenormalized(
        loader->norm,
        TELEM_SRC_EBPF,
        severity,
        category,
        message,
        entity,
        pid,
        &evt);

    if (emit) {
        json_str = telem_json_event(&evt);
        if (json_str) {
            if (loader->sock) {
                telem_socket_write(loader->sock, json_str, strlen(json_str));
            }
            free(json_str);
        }
    }
}

static int handle_event(void *ctx, void *data, size_t size)
{
    struct ebpf_loader *loader = ctx;
    struct elled_event_hdr *hdr = data;
    char message[512];
    char entity[128];

    /* Validate minimum size */
    if (size < sizeof(*hdr)) {
        loader->total_errors++;
        return 0;
    }

    /* Process event based on type */
    switch (hdr->type) {
    case ELLED_EVENT_OOM_KILL: {
        struct elled_oom_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        snprintf(message, sizeof(message),
                 "OOM killer invoked, killed process %.15s (PID %u, score_adj %d)",
                 evt->comm, evt->pid, evt->oom_score_adj);
        snprintf(entity, sizeof(entity), "process:%s", evt->comm);

        emit_normalized_event(loader, TELEM_SEV_CRITICAL, TELEM_CAT_OOM,
                              message, entity, evt->pid);
        break;
    }

    case ELLED_EVENT_EXEC_EXIT: {
        struct elled_exec_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        if (evt->ret == 0) {
            snprintf(message, sizeof(message),
                     "Process %.15s (PID %u) executed %.200s",
                     evt->comm, evt->pid, evt->filename);
            snprintf(entity, sizeof(entity), "process:%s", evt->comm);
            emit_normalized_event(loader, TELEM_SEV_INFO, TELEM_CAT_SERVICE,
                                  message, entity, evt->pid);
        } else {
            snprintf(message, sizeof(message),
                     "Process %.15s (PID %u) failed to execute %.200s (error %d)",
                     evt->comm, evt->pid, evt->filename, evt->ret);
            snprintf(entity, sizeof(entity), "process:%s", evt->comm);
            emit_normalized_event(loader, TELEM_SEV_WARNING, TELEM_CAT_SERVICE,
                                  message, entity, evt->pid);
        }
        break;
    }

    case ELLED_EVENT_PROCESS_EXIT: {
        struct elled_exit_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        /* Only emit for abnormal exits (signal deaths) */
        if (evt->signal > 0) {
            const char *severity_level = "warning";
            telem_severity_t sev = TELEM_SEV_WARNING;

            /* SIGKILL, SIGSEGV, SIGABRT are more severe */
            if (evt->signal == 9 || evt->signal == 11 || evt->signal == 6) {
                sev = TELEM_SEV_ERROR;
            }

            snprintf(message, sizeof(message),
                     "Process %.15s (PID %u) killed by signal %d",
                     evt->comm, evt->pid, evt->signal);
            snprintf(entity, sizeof(entity), "process:%s", evt->comm);
            emit_normalized_event(loader, sev, TELEM_CAT_SERVICE,
                                  message, entity, evt->pid);
        }
        break;
    }

    case ELLED_EVENT_BLOCK_COMPLETE: {
        struct elled_block_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        /* Only emit for high latency or errors */
        if (evt->error != 0) {
            snprintf(message, sizeof(message),
                     "Block I/O error on device %u:%u sector %llu (error %d)",
                     (evt->dev >> 20) & 0xfff, evt->dev & 0xfffff,
                     (unsigned long long)evt->sector, evt->error);
            snprintf(entity, sizeof(entity), "device:%u:%u",
                     (evt->dev >> 20) & 0xfff, evt->dev & 0xfffff);
            emit_normalized_event(loader, TELEM_SEV_ERROR, TELEM_CAT_DISK,
                                  message, entity, 0);
        } else if (evt->latency_ns > 1000000000ULL) { /* > 1 second */
            snprintf(message, sizeof(message),
                     "High block I/O latency on device %u:%u: %llu ms (%s)",
                     (evt->dev >> 20) & 0xfff, evt->dev & 0xfffff,
                     (unsigned long long)(evt->latency_ns / 1000000), evt->rwbs);
            snprintf(entity, sizeof(entity), "device:%u:%u",
                     (evt->dev >> 20) & 0xfff, evt->dev & 0xfffff);
            emit_normalized_event(loader, TELEM_SEV_WARNING, TELEM_CAT_DISK,
                                  message, entity, 0);
        }
        break;
    }

    case ELLED_EVENT_TCP_RETRANS: {
        struct elled_tcp_retrans_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        /* Format IP addresses */
        char saddr_str[16], daddr_str[16];
        inet_ntop(AF_INET, &evt->saddr, saddr_str, sizeof(saddr_str));
        inet_ntop(AF_INET, &evt->daddr, daddr_str, sizeof(daddr_str));

        snprintf(message, sizeof(message),
                 "TCP retransmit %s:%u -> %s:%u (%.15s)",
                 saddr_str, ntohs(evt->sport),
                 daddr_str, ntohs(evt->dport),
                 evt->comm[0] ? evt->comm : "unknown");
        snprintf(entity, sizeof(entity), "connection:%s:%u",
                 daddr_str, ntohs(evt->dport));

        emit_normalized_event(loader, TELEM_SEV_WARNING, TELEM_CAT_NET,
                              message, entity, evt->pid);
        break;
    }

    case ELLED_EVENT_CAP_DENY: {
        struct elled_cap_deny_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        /* Map capability number to name */
        const char *cap_name;
        switch (evt->cap) {
        case 0:  cap_name = "CAP_CHOWN"; break;
        case 1:  cap_name = "CAP_DAC_OVERRIDE"; break;
        case 2:  cap_name = "CAP_DAC_READ_SEARCH"; break;
        case 7:  cap_name = "CAP_SETUID"; break;
        case 10: cap_name = "CAP_NET_BIND_SERVICE"; break;
        case 12: cap_name = "CAP_NET_ADMIN"; break;
        case 13: cap_name = "CAP_NET_RAW"; break;
        case 19: cap_name = "CAP_SYS_PTRACE"; break;
        case 21: cap_name = "CAP_SYS_ADMIN"; break;
        default: cap_name = "CAP_UNKNOWN"; break;
        }

        snprintf(message, sizeof(message),
                 "Capability denied: %.15s (PID %u) needs %s",
                 evt->comm, evt->pid, cap_name);
        snprintf(entity, sizeof(entity), "process:%s", evt->comm);

        emit_normalized_event(loader, TELEM_SEV_WARNING, TELEM_CAT_AUTH,
                              message, entity, evt->pid);
        break;
    }

    case ELLED_EVENT_FILE_DENY: {
        struct elled_file_deny_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        const char *error_str = "permission denied";
        if (evt->error == 1) error_str = "operation not permitted";
        else if (evt->error == 30) error_str = "read-only filesystem";

        snprintf(message, sizeof(message),
                 "File access denied: %.15s (PID %u) -> %.200s (%s)",
                 evt->comm, evt->pid, evt->filename, error_str);
        snprintf(entity, sizeof(entity), "file:%.100s", evt->filename);

        emit_normalized_event(loader, TELEM_SEV_WARNING, TELEM_CAT_AUTH,
                              message, entity, evt->pid);
        break;
    }

    case ELLED_EVENT_MOUNT_OP: {
        struct elled_mount_event *evt = data;
        if (size < sizeof(*evt)) {
            loader->total_errors++;
            return 0;
        }

        telem_severity_t sev = TELEM_SEV_INFO;

        if (evt->ret != 0) {
            snprintf(message, sizeof(message),
                     "Mount failed: %.60s -> %.100s (error %d)",
                     evt->source, evt->target, evt->ret);
            sev = TELEM_SEV_ERROR;
        } else if (evt->is_remount_ro) {
            snprintf(message, sizeof(message),
                     "Filesystem remounted read-only: %.100s (possible disk error)",
                     evt->target);
            sev = TELEM_SEV_CRITICAL;
        } else {
            snprintf(message, sizeof(message),
                     "Mount: %.60s -> %.100s (%.15s)",
                     evt->source, evt->target, evt->fstype);
        }

        snprintf(entity, sizeof(entity), "mount:%.100s", evt->target);
        emit_normalized_event(loader, sev, TELEM_CAT_FS,
                              message, entity, evt->pid);
        break;
    }

    default:
        loader->total_errors++;
        return 0;
    }

    loader->total_events++;
    return 0;
}

/* ==========================================================================
 * Loader Start
 * ========================================================================== */

int ebpf_loader_start(struct ebpf_loader *loader)
{
    int loaded = 0;

    if (!loader) return -1;

    /* Load enabled BPF programs */
    if (load_oom_program(loader) == 0 && loader->oom_skel)
        loaded++;

    if (load_exec_program(loader) == 0 && loader->exec_skel)
        loaded++;

    if (load_block_io_program(loader) == 0 && loader->block_io_skel)
        loaded++;

    if (load_tcp_retrans_program(loader) == 0 && loader->tcp_retrans_skel)
        loaded++;

    if (load_cap_deny_program(loader) == 0 && loader->cap_deny_skel)
        loaded++;

    if (load_file_deny_program(loader) == 0 && loader->file_deny_skel)
        loaded++;

    if (load_mount_program(loader) == 0 && loader->mount_skel)
        loaded++;

    if (loaded == 0) {
        fprintf(stderr, "ERROR: No eBPF programs loaded\n");
        return -1;
    }

    fprintf(stderr, "Loaded %d eBPF programs\n", loaded);

    /* Setup ring buffer polling */
    if (setup_ring_buffer(loader) < 0) {
        return -1;
    }

    return loaded;
}

/* ==========================================================================
 * Loader Poll
 * ========================================================================== */

int ebpf_loader_poll(struct ebpf_loader *loader, int timeout_ms)
{
    if (!loader || !loader->rb)
        return -1;

    return ring_buffer__poll(loader->rb, timeout_ms);
}

/* ==========================================================================
 * Get File Descriptor for poll()
 * ========================================================================== */

int ebpf_loader_get_fd(struct ebpf_loader *loader)
{
    if (!loader || !loader->rb)
        return -1;

    return ring_buffer__epoll_fd(loader->rb);
}

/* ==========================================================================
 * Statistics
 * ========================================================================== */

void ebpf_loader_get_stats(struct ebpf_loader *loader,
                           uint64_t *events,
                           uint64_t *errors)
{
    if (!loader) return;
    if (events) *events = loader->total_events;
    if (errors) *errors = loader->total_errors;
}

/* ==========================================================================
 * Loader Destroy
 * ========================================================================== */

void ebpf_loader_destroy(struct ebpf_loader *loader)
{
    if (!loader)
        return;

    /* Free ring buffer */
    if (loader->rb)
        ring_buffer__free(loader->rb);

    /* Destroy BPF skeletons */
    if (loader->oom_skel)
        oom_bpf__destroy(loader->oom_skel);
    if (loader->exec_skel)
        exec_bpf__destroy(loader->exec_skel);
    if (loader->block_io_skel)
        block_io_bpf__destroy(loader->block_io_skel);
    if (loader->tcp_retrans_skel)
        tcp_retrans_bpf__destroy(loader->tcp_retrans_skel);
    if (loader->cap_deny_skel)
        cap_deny_bpf__destroy(loader->cap_deny_skel);
    if (loader->file_deny_skel)
        file_deny_bpf__destroy(loader->file_deny_skel);
    if (loader->mount_skel)
        mount_bpf__destroy(loader->mount_skel);

    free(loader);
}

#else /* !ENABLE_EBPF */

/* Stub implementations when eBPF is disabled */

struct ebpf_loader *ebpf_loader_create(
    const struct ebpf_config *cfg,
    struct normalizer *norm,
    struct telem_socket *sock)
{
    (void)cfg;
    (void)norm;
    (void)sock;
    fprintf(stderr, "ERROR: eBPF support not compiled in\n");
    return NULL;
}

int ebpf_loader_start(struct ebpf_loader *loader)
{
    (void)loader;
    return -1;
}

int ebpf_loader_poll(struct ebpf_loader *loader, int timeout_ms)
{
    (void)loader;
    (void)timeout_ms;
    return -1;
}

int ebpf_loader_get_fd(struct ebpf_loader *loader)
{
    (void)loader;
    return -1;
}

void ebpf_loader_get_stats(struct ebpf_loader *loader,
                           uint64_t *events,
                           uint64_t *errors)
{
    (void)loader;
    if (events) *events = 0;
    if (errors) *errors = 0;
}

void ebpf_loader_destroy(struct ebpf_loader *loader)
{
    (void)loader;
}

#endif /* ENABLE_EBPF */
