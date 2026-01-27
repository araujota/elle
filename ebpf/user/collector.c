// SPDX-License-Identifier: MIT
/**
 * collector.c - eBPF program loader and event collector
 *
 * Manages BPF program lifecycle and ring buffer event processing.
 * Uses libbpf skeletons generated at build time.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <bpf/libbpf.h>
#include <bpf/bpf.h>

#include "collector.h"
#include "json.h"

/* Include generated skeletons */
#include "oom.skel.h"
#include "exec.skel.h"
#include "block_io.skel.h"
#include "tcp_retrans.skel.h"
#include "cap_deny.skel.h"
#include "file_deny.skel.h"
#include "mount.skel.h"

/* Collector state */
struct elled_collector {
    /* Configuration */
    struct elled_config cfg;
    struct elled_socket *sock;
    int use_stdout;
    int verbose;

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

    /* Statistics */
    uint64_t total_events;
    uint64_t total_errors;
};

/* Forward declarations */
static int handle_event(void *ctx, void *data, size_t size);
static int setup_ring_buffer(struct elled_collector *c);
static void update_config_map(struct elled_collector *c, int map_fd);

/* ==========================================================================
 * libbpf Logging
 * ========================================================================== */

static int libbpf_print_fn(enum libbpf_print_level level, const char *format, va_list args)
{
    /* Only print warnings and errors */
    if (level >= LIBBPF_WARN)
        return vfprintf(stderr, format, args);
    return 0;
}

/* ==========================================================================
 * Collector Creation
 * ========================================================================== */

struct elled_collector *elled_collector_create(
    struct elled_config *cfg,
    struct elled_socket *sock,
    int use_stdout,
    int verbose)
{
    struct elled_collector *c;

    c = calloc(1, sizeof(*c));
    if (!c) {
        fprintf(stderr, "ERROR: Failed to allocate collector\n");
        return NULL;
    }

    c->cfg = *cfg;
    c->sock = sock;
    c->use_stdout = use_stdout;
    c->verbose = verbose;

    /* Set up libbpf logging */
    libbpf_set_print(libbpf_print_fn);

    /* Bump RLIMIT_MEMLOCK for BPF maps */
    struct rlimit rlim = {
        .rlim_cur = 128 * 1024 * 1024,  /* 128 MB */
        .rlim_max = 128 * 1024 * 1024,
    };
    if (setrlimit(RLIMIT_MEMLOCK, &rlim) && verbose) {
        fprintf(stderr, "WARNING: Failed to set RLIMIT_MEMLOCK\n");
    }

    return c;
}

/* ==========================================================================
 * BPF Program Loading
 * ========================================================================== */

static int load_oom_program(struct elled_collector *c)
{
    if (!c->cfg.enable_oom)
        return 0;

    c->oom_skel = oom_bpf__open();
    if (!c->oom_skel) {
        fprintf(stderr, "ERROR: Failed to open OOM BPF program\n");
        return -1;
    }

    if (oom_bpf__load(c->oom_skel)) {
        fprintf(stderr, "ERROR: Failed to load OOM BPF program\n");
        oom_bpf__destroy(c->oom_skel);
        c->oom_skel = NULL;
        return -1;
    }

    if (oom_bpf__attach(c->oom_skel)) {
        fprintf(stderr, "ERROR: Failed to attach OOM BPF program\n");
        oom_bpf__destroy(c->oom_skel);
        c->oom_skel = NULL;
        return -1;
    }

    /* Update config map */
    update_config_map(c, bpf_map__fd(c->oom_skel->maps.config));

    if (c->verbose)
        fprintf(stderr, "Loaded OOM program\n");

    return 0;
}

static int load_exec_program(struct elled_collector *c)
{
    if (!c->cfg.enable_exec)
        return 0;

    c->exec_skel = exec_bpf__open();
    if (!c->exec_skel) {
        fprintf(stderr, "ERROR: Failed to open exec BPF program\n");
        return -1;
    }

    if (exec_bpf__load(c->exec_skel)) {
        fprintf(stderr, "ERROR: Failed to load exec BPF program\n");
        exec_bpf__destroy(c->exec_skel);
        c->exec_skel = NULL;
        return -1;
    }

    if (exec_bpf__attach(c->exec_skel)) {
        fprintf(stderr, "ERROR: Failed to attach exec BPF program\n");
        exec_bpf__destroy(c->exec_skel);
        c->exec_skel = NULL;
        return -1;
    }

    /* Update config map */
    update_config_map(c, bpf_map__fd(c->exec_skel->maps.config));

    if (c->verbose)
        fprintf(stderr, "Loaded exec program\n");

    return 0;
}

static int load_block_io_program(struct elled_collector *c)
{
    if (!c->cfg.enable_block)
        return 0;

    c->block_io_skel = block_io_bpf__open();
    if (!c->block_io_skel) {
        fprintf(stderr, "ERROR: Failed to open block_io BPF program\n");
        return -1;
    }

    if (block_io_bpf__load(c->block_io_skel)) {
        fprintf(stderr, "ERROR: Failed to load block_io BPF program\n");
        block_io_bpf__destroy(c->block_io_skel);
        c->block_io_skel = NULL;
        return -1;
    }

    if (block_io_bpf__attach(c->block_io_skel)) {
        fprintf(stderr, "ERROR: Failed to attach block_io BPF program\n");
        block_io_bpf__destroy(c->block_io_skel);
        c->block_io_skel = NULL;
        return -1;
    }

    /* Update config map */
    update_config_map(c, bpf_map__fd(c->block_io_skel->maps.config));

    if (c->verbose)
        fprintf(stderr, "Loaded block_io program\n");

    return 0;
}

static int load_tcp_retrans_program(struct elled_collector *c)
{
    if (!c->cfg.enable_tcp_retrans)
        return 0;

    c->tcp_retrans_skel = tcp_retrans_bpf__open();
    if (!c->tcp_retrans_skel) {
        fprintf(stderr, "ERROR: Failed to open tcp_retrans BPF program\n");
        return -1;
    }

    if (tcp_retrans_bpf__load(c->tcp_retrans_skel)) {
        fprintf(stderr, "ERROR: Failed to load tcp_retrans BPF program\n");
        tcp_retrans_bpf__destroy(c->tcp_retrans_skel);
        c->tcp_retrans_skel = NULL;
        return -1;
    }

    if (tcp_retrans_bpf__attach(c->tcp_retrans_skel)) {
        fprintf(stderr, "ERROR: Failed to attach tcp_retrans BPF program\n");
        tcp_retrans_bpf__destroy(c->tcp_retrans_skel);
        c->tcp_retrans_skel = NULL;
        return -1;
    }

    /* Update config map */
    update_config_map(c, bpf_map__fd(c->tcp_retrans_skel->maps.config));

    if (c->verbose)
        fprintf(stderr, "Loaded tcp_retrans program\n");

    return 0;
}

static int load_cap_deny_program(struct elled_collector *c)
{
    if (!c->cfg.enable_cap_deny)
        return 0;

    c->cap_deny_skel = cap_deny_bpf__open();
    if (!c->cap_deny_skel) {
        fprintf(stderr, "ERROR: Failed to open cap_deny BPF program\n");
        return -1;
    }

    if (cap_deny_bpf__load(c->cap_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to load cap_deny BPF program\n");
        cap_deny_bpf__destroy(c->cap_deny_skel);
        c->cap_deny_skel = NULL;
        return -1;
    }

    if (cap_deny_bpf__attach(c->cap_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to attach cap_deny BPF program\n");
        cap_deny_bpf__destroy(c->cap_deny_skel);
        c->cap_deny_skel = NULL;
        return -1;
    }

    /* Update config map */
    update_config_map(c, bpf_map__fd(c->cap_deny_skel->maps.config));

    if (c->verbose)
        fprintf(stderr, "Loaded cap_deny program\n");

    return 0;
}

static int load_file_deny_program(struct elled_collector *c)
{
    if (!c->cfg.enable_file_deny)
        return 0;

    c->file_deny_skel = file_deny_bpf__open();
    if (!c->file_deny_skel) {
        fprintf(stderr, "ERROR: Failed to open file_deny BPF program\n");
        return -1;
    }

    if (file_deny_bpf__load(c->file_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to load file_deny BPF program\n");
        file_deny_bpf__destroy(c->file_deny_skel);
        c->file_deny_skel = NULL;
        return -1;
    }

    if (file_deny_bpf__attach(c->file_deny_skel)) {
        fprintf(stderr, "ERROR: Failed to attach file_deny BPF program\n");
        file_deny_bpf__destroy(c->file_deny_skel);
        c->file_deny_skel = NULL;
        return -1;
    }

    /* Update config map */
    update_config_map(c, bpf_map__fd(c->file_deny_skel->maps.config));

    if (c->verbose)
        fprintf(stderr, "Loaded file_deny program\n");

    return 0;
}

static int load_mount_program(struct elled_collector *c)
{
    if (!c->cfg.enable_mount)
        return 0;

    c->mount_skel = mount_bpf__open();
    if (!c->mount_skel) {
        fprintf(stderr, "ERROR: Failed to open mount BPF program\n");
        return -1;
    }

    if (mount_bpf__load(c->mount_skel)) {
        fprintf(stderr, "ERROR: Failed to load mount BPF program\n");
        mount_bpf__destroy(c->mount_skel);
        c->mount_skel = NULL;
        return -1;
    }

    if (mount_bpf__attach(c->mount_skel)) {
        fprintf(stderr, "ERROR: Failed to attach mount BPF program\n");
        mount_bpf__destroy(c->mount_skel);
        c->mount_skel = NULL;
        return -1;
    }

    /* Update config map */
    update_config_map(c, bpf_map__fd(c->mount_skel->maps.config));

    if (c->verbose)
        fprintf(stderr, "Loaded mount program\n");

    return 0;
}

/* ==========================================================================
 * Configuration Map Update
 * ========================================================================== */

static void update_config_map(struct elled_collector *c, int map_fd)
{
    __u32 key = 0;
    bpf_map_update_elem(map_fd, &key, &c->cfg, BPF_ANY);
}

/* ==========================================================================
 * Ring Buffer Setup
 * ========================================================================== */

static int setup_ring_buffer(struct elled_collector *c)
{
    struct ring_buffer *rb = NULL;
    int err = 0;

    /* Create ring buffer manager */
    rb = ring_buffer__new(0, handle_event, c, NULL);
    if (!rb) {
        fprintf(stderr, "ERROR: Failed to create ring buffer\n");
        return -1;
    }

    /* Add ring buffers from each loaded program */
    if (c->oom_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(c->oom_skel->maps.events),
                               handle_event, c);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add OOM ring buffer\n");
            goto cleanup;
        }
    }

    if (c->exec_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(c->exec_skel->maps.events),
                               handle_event, c);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add exec ring buffer\n");
            goto cleanup;
        }
    }

    if (c->block_io_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(c->block_io_skel->maps.events),
                               handle_event, c);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add block_io ring buffer\n");
            goto cleanup;
        }
    }

    if (c->tcp_retrans_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(c->tcp_retrans_skel->maps.events),
                               handle_event, c);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add tcp_retrans ring buffer\n");
            goto cleanup;
        }
    }

    if (c->cap_deny_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(c->cap_deny_skel->maps.events),
                               handle_event, c);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add cap_deny ring buffer\n");
            goto cleanup;
        }
    }

    if (c->file_deny_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(c->file_deny_skel->maps.events),
                               handle_event, c);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add file_deny ring buffer\n");
            goto cleanup;
        }
    }

    if (c->mount_skel) {
        err = ring_buffer__add(rb, bpf_map__fd(c->mount_skel->maps.events),
                               handle_event, c);
        if (err) {
            fprintf(stderr, "ERROR: Failed to add mount ring buffer\n");
            goto cleanup;
        }
    }

    c->rb = rb;
    return 0;

cleanup:
    ring_buffer__free(rb);
    return -1;
}

/* ==========================================================================
 * Event Handler
 * ========================================================================== */

static int handle_event(void *ctx, void *data, size_t size)
{
    struct elled_collector *c = ctx;
    struct elled_event_hdr *hdr = data;
    char *json_str = NULL;

    /* Validate minimum size */
    if (size < sizeof(*hdr)) {
        c->total_errors++;
        return 0;
    }

    /* Serialize event to JSON based on type */
    switch (hdr->type) {
    case ELLED_EVENT_OOM_KILL:
        if (size >= sizeof(struct elled_oom_event))
            json_str = elled_json_oom_event((struct elled_oom_event *)data);
        break;

    case ELLED_EVENT_EXEC_EXIT:
        if (size >= sizeof(struct elled_exec_event))
            json_str = elled_json_exec_event((struct elled_exec_event *)data);
        break;

    case ELLED_EVENT_PROCESS_EXIT:
        if (size >= sizeof(struct elled_exit_event))
            json_str = elled_json_exit_event((struct elled_exit_event *)data);
        break;

    case ELLED_EVENT_BLOCK_COMPLETE:
        if (size >= sizeof(struct elled_block_event))
            json_str = elled_json_block_event((struct elled_block_event *)data);
        break;

    case ELLED_EVENT_TCP_RETRANS:
        if (size >= sizeof(struct elled_tcp_retrans_event))
            json_str = elled_json_tcp_event((struct elled_tcp_retrans_event *)data);
        break;

    case ELLED_EVENT_CAP_DENY:
        if (size >= sizeof(struct elled_cap_deny_event))
            json_str = elled_json_cap_deny_event((struct elled_cap_deny_event *)data);
        break;

    case ELLED_EVENT_FILE_DENY:
        if (size >= sizeof(struct elled_file_deny_event))
            json_str = elled_json_file_deny_event((struct elled_file_deny_event *)data);
        break;

    case ELLED_EVENT_MOUNT_OP:
        if (size >= sizeof(struct elled_mount_event))
            json_str = elled_json_mount_event((struct elled_mount_event *)data);
        break;

    default:
        if (c->verbose)
            fprintf(stderr, "Unknown event type: %u\n", hdr->type);
        c->total_errors++;
        return 0;
    }

    if (!json_str) {
        c->total_errors++;
        return 0;
    }

    /* Output JSON */
    if (c->use_stdout) {
        printf("%s\n", json_str);
        fflush(stdout);
    } else if (c->sock) {
        if (elled_socket_write(c->sock, json_str, strlen(json_str)) < 0) {
            c->total_errors++;
        }
    }

    free(json_str);
    c->total_events++;

    return 0;
}

/* ==========================================================================
 * Collector Start
 * ========================================================================== */

int elled_collector_start(struct elled_collector *c)
{
    int loaded = 0;

    /* Load enabled BPF programs */
    if (load_oom_program(c) == 0 && c->oom_skel)
        loaded++;

    if (load_exec_program(c) == 0 && c->exec_skel)
        loaded++;

    if (load_block_io_program(c) == 0 && c->block_io_skel)
        loaded++;

    if (load_tcp_retrans_program(c) == 0 && c->tcp_retrans_skel)
        loaded++;

    if (load_cap_deny_program(c) == 0 && c->cap_deny_skel)
        loaded++;

    if (load_file_deny_program(c) == 0 && c->file_deny_skel)
        loaded++;

    if (load_mount_program(c) == 0 && c->mount_skel)
        loaded++;

    if (loaded == 0) {
        fprintf(stderr, "ERROR: No BPF programs loaded\n");
        return -1;
    }

    if (c->verbose)
        fprintf(stderr, "Loaded %d BPF programs\n", loaded);

    /* Setup ring buffer polling */
    if (setup_ring_buffer(c) < 0) {
        return -1;
    }

    return 0;
}

/* ==========================================================================
 * Collector Poll
 * ========================================================================== */

int elled_collector_poll(struct elled_collector *c, int timeout_ms)
{
    if (!c->rb)
        return -1;

    return ring_buffer__poll(c->rb, timeout_ms);
}

/* ==========================================================================
 * Collector Destroy
 * ========================================================================== */

void elled_collector_destroy(struct elled_collector *c)
{
    if (!c)
        return;

    /* Free ring buffer */
    if (c->rb)
        ring_buffer__free(c->rb);

    /* Destroy BPF skeletons */
    if (c->oom_skel)
        oom_bpf__destroy(c->oom_skel);
    if (c->exec_skel)
        exec_bpf__destroy(c->exec_skel);
    if (c->block_io_skel)
        block_io_bpf__destroy(c->block_io_skel);
    if (c->tcp_retrans_skel)
        tcp_retrans_bpf__destroy(c->tcp_retrans_skel);
    if (c->cap_deny_skel)
        cap_deny_bpf__destroy(c->cap_deny_skel);
    if (c->file_deny_skel)
        file_deny_bpf__destroy(c->file_deny_skel);
    if (c->mount_skel)
        mount_bpf__destroy(c->mount_skel);

    free(c);
}

/* ==========================================================================
 * Statistics
 * ========================================================================== */

void elled_collector_stats(
    struct elled_collector *c,
    uint64_t *events,
    uint64_t *errors)
{
    if (events)
        *events = c->total_events;
    if (errors)
        *errors = c->total_errors;
}
