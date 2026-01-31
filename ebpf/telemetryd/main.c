// SPDX-License-Identifier: MIT
/**
 * main.c - Entry point for elled-telemetryd
 *
 * Unified telemetry daemon that collects, normalizes, and emits
 * telemetry events from multiple sources:
 * - journald (via sd-journal API)
 * - Docker events
 * - inotify file monitoring
 * - Periodic probes (PSI, memory, disk, network, thermal, package, port)
 *
 * Events are normalized (category, entity, fingerprint) and
 * deduplicated before being emitted via Unix socket.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <getopt.h>
#include <poll.h>
#include <errno.h>
#include <sys/stat.h>

#include "telemetryd.h"
#include "socket.h"
#include "json.h"
#include "normalize/normalizer.h"
#include "watchers/journal.h"
#include "watchers/docker.h"
#include "watchers/inotify.h"
#include "probes/scheduler.h"
#include "probes/psi.h"
#include "probes/memory.h"
#include "probes/disk.h"
#include "probes/network.h"
#include "probes/thermal.h"
#include "probes/package.h"
#include "probes/port.h"
#include "probes/connpool.h"
#include "probes/topproc.h"
#include "probes/ioperf.h"
#include "probes/cgroup.h"

#ifdef ENABLE_EBPF
#include "ebpf/loader.h"
#endif

/* ==========================================================================
 * Globals
 * ========================================================================== */

static volatile sig_atomic_t shutdown_requested = 0;
static struct telemetryd_config config = TELEMETRYD_DEFAULT_CONFIG;
static int verbose = 0;

/* ==========================================================================
 * Signal Handling
 * ========================================================================== */

static void signal_handler(int sig)
{
    (void)sig;
    shutdown_requested = 1;
}

static void setup_signals(void)
{
    struct sigaction sa = {
        .sa_handler = signal_handler,
        .sa_flags = 0,
    };
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
}

/* ==========================================================================
 * Logging
 * ========================================================================== */

#define LOG_ERROR 0
#define LOG_WARN  1
#define LOG_INFO  2
#define LOG_DEBUG 3

#define log_error(fmt, ...) \
    do { if (config.log_level >= LOG_ERROR) fprintf(stderr, "ERROR: " fmt "\n", ##__VA_ARGS__); } while (0)
#define log_warn(fmt, ...) \
    do { if (config.log_level >= LOG_WARN) fprintf(stderr, "WARN: " fmt "\n", ##__VA_ARGS__); } while (0)
#define log_info(fmt, ...) \
    do { if (config.log_level >= LOG_INFO) fprintf(stderr, "INFO: " fmt "\n", ##__VA_ARGS__); } while (0)
#define log_debug(fmt, ...) \
    do { if (config.log_level >= LOG_DEBUG) fprintf(stderr, "DEBUG: " fmt "\n", ##__VA_ARGS__); } while (0)

/* ==========================================================================
 * Command-line Parsing
 * ========================================================================== */

static void print_usage(const char *prog)
{
    printf("Usage: %s [OPTIONS]\n\n", prog);
    printf("Unified telemetry daemon for ELLE.\n\n");
    printf("Options:\n");
    printf("  -s, --socket PATH    Socket path (default: %s)\n", TELEMETRYD_DEFAULT_SOCKET);
    printf("  -d, --dedupe SEC     Deduplication window in seconds (default: %d)\n",
           TELEMETRYD_DEFAULT_DEDUPE_WINDOW);
    printf("  -v, --verbose        Increase verbosity (can be repeated)\n");
    printf("  -h, --help           Show this help message\n");
    printf("\n");
    printf("Components can be enabled/disabled via config file.\n");
}

static int parse_args(int argc, char *argv[])
{
    static struct option long_options[] = {
        {"socket",  required_argument, 0, 's'},
        {"dedupe",  required_argument, 0, 'd'},
        {"verbose", no_argument,       0, 'v'},
        {"help",    no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    int option_index = 0;

    while ((opt = getopt_long(argc, argv, "s:d:vh", long_options, &option_index)) != -1) {
        switch (opt) {
        case 's':
            strncpy(config.socket_path, optarg, sizeof(config.socket_path) - 1);
            break;
        case 'd':
            config.dedupe_window_sec = (int)strtol(optarg, NULL, 10);
            if (config.dedupe_window_sec < 1 || config.dedupe_window_sec > 3600) {
                fprintf(stderr, "ERROR: Invalid dedupe window (must be 1-3600)\n");
                return -1;
            }
            break;
        case 'v':
            verbose++;
            config.log_level++;
            break;
        case 'h':
            print_usage(argv[0]);
            exit(0);
        default:
            print_usage(argv[0]);
            return -1;
        }
    }

    return 0;
}

/* ==========================================================================
 * Directory Setup
 * ========================================================================== */

static int ensure_runtime_dir(void)
{
    /* Ensure /run/elle exists */
    if (mkdir("/run/elle", 0755) < 0 && errno != EEXIST) {
        log_error("Failed to create /run/elle: %s", strerror(errno));
        return -1;
    }
    return 0;
}

/* ==========================================================================
 * Watcher Callbacks
 * ========================================================================== */

/* Context for journal callback */
struct journal_cb_ctx {
    struct normalizer *norm;
    struct telem_socket *sock;
    bool kernel_only;
};

static void journal_callback(uint64_t realtime_us, int priority,
                              const char *message, const char *systemd_unit,
                              const char *unit, const char *transport,
                              const char *comm, void *user_data)
{
    struct journal_cb_ctx *ctx = (struct journal_cb_ctx *)user_data;
    struct telem_event evt;
    char *json_str;
    bool emit;
    telem_source_t source;

    (void)comm;

    /* Determine source based on transport */
    if (transport && strcmp(transport, "kernel") == 0) {
        source = TELEM_SRC_KERNEL;
    } else {
        source = TELEM_SRC_JOURNAL;
    }

    emit = normalizer_process(ctx->norm,
        source,
        priority,
        message,
        systemd_unit,
        unit,
        realtime_us * 1000,  /* Convert to nanoseconds */
        &evt);

    if (emit) {
        json_str = telem_json_event(&evt);
        if (json_str) {
            telem_socket_write(ctx->sock, json_str, strlen(json_str));
            free(json_str);
        }
    }
}

/* Context for docker callback */
struct docker_cb_ctx {
    struct normalizer *norm;
    struct telem_socket *sock;
};

static void docker_callback(const char *action, const char *container_name,
                             const char *container_id, int exit_code,
                             bool is_crashloop, int restart_count, void *user_data)
{
    struct docker_cb_ctx *ctx = (struct docker_cb_ctx *)user_data;
    struct telem_event evt;
    char message[512];
    char entity[128];
    char *json_str;
    bool emit;
    telem_severity_t severity = TELEM_SEV_INFO;

    /* Build message */
    if (is_crashloop) {
        snprintf(message, sizeof(message),
                 "Container %s (%s) is crashlooping (%d restarts)",
                 container_name, container_id, restart_count);
        severity = TELEM_SEV_ERROR;
    } else if (strcmp(action, "die") == 0 && exit_code != 0) {
        snprintf(message, sizeof(message),
                 "Container %s (%s) died with exit code %d",
                 container_name, container_id, exit_code);
        severity = TELEM_SEV_WARNING;
    } else {
        snprintf(message, sizeof(message),
                 "Container %s (%s) %s",
                 container_name, container_id, action);
    }

    snprintf(entity, sizeof(entity), "container:%s", container_name);

    emit = normalizer_process_prenormalized(ctx->norm,
        TELEM_SRC_DOCKER,
        severity,
        TELEM_CAT_DOCKER,
        message,
        entity,
        0,
        &evt);

    if (emit) {
        json_str = telem_json_event(&evt);
        if (json_str) {
            telem_socket_write(ctx->sock, json_str, strlen(json_str));
            free(json_str);
        }
    }
}

/* Context for inotify callback */
struct inotify_cb_ctx {
    struct normalizer *norm;
    struct telem_socket *sock;
};

static void inotify_callback(const char *path, const char *event_type,
                              const char *diff, void *user_data)
{
    struct inotify_cb_ctx *ctx = (struct inotify_cb_ctx *)user_data;
    struct telem_event evt;
    char message[512];
    char entity[256];
    char *json_str;
    bool emit;

    (void)diff;  /* Could include in raw JSON if needed */

    snprintf(message, sizeof(message), "File %s: %s", event_type, path);
    snprintf(entity, sizeof(entity), "file:%s", path);

    emit = normalizer_process_prenormalized(ctx->norm,
        TELEM_SRC_INOTIFY,
        TELEM_SEV_WARNING,
        TELEM_CAT_AUTH,  /* Most watched files are auth-related */
        message,
        entity,
        0,
        &evt);

    if (emit) {
        json_str = telem_json_event(&evt);
        if (json_str) {
            telem_socket_write(ctx->sock, json_str, strlen(json_str));
            free(json_str);
        }
    }
}

/* ==========================================================================
 * Main Loop
 * ========================================================================== */

static int main_loop(struct normalizer *norm, struct telem_socket *sock)
{
    struct journal_watcher *journal = NULL;
    struct journal_watcher *kernel = NULL;
    struct docker_watcher *docker = NULL;
    struct inotify_watcher *inotify = NULL;
    struct telem_scheduler *probes = NULL;
#ifdef ENABLE_EBPF
    struct ebpf_loader *ebpf = NULL;
#endif

    /* Callback contexts */
    struct journal_cb_ctx journal_ctx = { .norm = norm, .sock = sock, .kernel_only = false };
    struct journal_cb_ctx kernel_ctx = { .norm = norm, .sock = sock, .kernel_only = true };
    struct docker_cb_ctx docker_ctx = { .norm = norm, .sock = sock };
    struct inotify_cb_ctx inotify_ctx = { .norm = norm, .sock = sock };

    /* Probe contexts */
    struct psi_probe_ctx *psi_ctx = NULL;
    struct memory_probe_ctx *memory_ctx = NULL;
    struct disk_probe_ctx *disk_ctx = NULL;
    struct network_probe_ctx *network_ctx = NULL;
    struct thermal_probe_ctx *thermal_ctx = NULL;
    struct package_probe_ctx *package_ctx = NULL;
    struct port_probe_ctx *port_ctx = NULL;
    struct connpool_probe_ctx *connpool_ctx = NULL;
    struct topproc_probe_ctx *topproc_ctx = NULL;
    struct ioperf_probe_ctx *ioperf_ctx = NULL;
    struct cgroup_probe_ctx *cgroup_ctx = NULL;

    /* Poll array */
    struct pollfd pfds[10];  /* Increased for eBPF */
    int nfds = 0;
    int journal_idx = -1, kernel_idx = -1, docker_idx = -1, inotify_idx = -1;
#ifdef ENABLE_EBPF
    int ebpf_idx = -1;
#endif

    log_info("Starting main loop (socket: %s)", config.socket_path);
    log_info("Dedupe window: %d seconds", config.dedupe_window_sec);

    /* Initialize journal watcher */
    if (config.journal_enabled) {
        journal = journal_watcher_create(false);
        if (journal) {
            journal_idx = nfds;
            pfds[nfds].fd = journal_watcher_get_fd(journal);
            pfds[nfds].events = POLLIN;
            nfds++;
            log_info("Journal watcher started");
        } else {
            log_warn("Failed to start journal watcher");
        }
    }

    /* Initialize kernel watcher */
    if (config.kernel_enabled) {
        kernel = journal_watcher_create(true);
        if (kernel) {
            kernel_idx = nfds;
            pfds[nfds].fd = journal_watcher_get_fd(kernel);
            pfds[nfds].events = POLLIN;
            nfds++;
            log_info("Kernel watcher started");
        } else {
            log_warn("Failed to start kernel watcher");
        }
    }

    /* Initialize docker watcher */
    if (config.docker_enabled && docker_is_available()) {
        docker = docker_watcher_create();
        if (docker) {
            docker_idx = nfds;
            pfds[nfds].fd = docker_watcher_get_fd(docker);
            pfds[nfds].events = POLLIN;
            nfds++;
            log_info("Docker watcher started");
        } else {
            log_warn("Failed to start docker watcher");
        }
    }

    /* Initialize inotify watcher */
    if (config.inotify_enabled) {
        inotify = inotify_watcher_create();
        if (inotify) {
            int count = inotify_watcher_add_default_paths(inotify);
            log_info("Inotify watcher started (%d paths)", count);
            inotify_idx = nfds;
            pfds[nfds].fd = inotify_watcher_get_fd(inotify);
            pfds[nfds].events = POLLIN;
            nfds++;
        } else {
            log_warn("Failed to start inotify watcher");
        }
    }

    /* Initialize probe scheduler */
    if (config.probes_enabled) {
        probes = telem_scheduler_create(norm, sock);
        if (probes) {
            log_info("Probe scheduler started");

            /* Register probes */
            if (config.psi_interval > 0) {
                psi_ctx = psi_probe_create_ctx(&config);
                if (psi_ctx) {
                    telem_scheduler_register(probes, "psi", psi_probe_run,
                                              psi_ctx, config.psi_interval, true);
                    log_debug("  PSI probe registered (interval: %ds)", config.psi_interval);
                }
            }

            if (config.memory_interval > 0) {
                memory_ctx = memory_probe_create_ctx(&config);
                if (memory_ctx) {
                    telem_scheduler_register(probes, "memory", memory_probe_run,
                                              memory_ctx, config.memory_interval, true);
                    log_debug("  Memory probe registered (interval: %ds)", config.memory_interval);
                }
            }

            if (config.disk_interval > 0) {
                disk_ctx = disk_probe_create_ctx(&config);
                if (disk_ctx) {
                    telem_scheduler_register(probes, "disk", disk_probe_run,
                                              disk_ctx, config.disk_interval, true);
                    log_debug("  Disk probe registered (interval: %ds)", config.disk_interval);
                }
            }

            if (config.network_interval > 0) {
                network_ctx = network_probe_create_ctx(&config);
                if (network_ctx) {
                    telem_scheduler_register(probes, "network", network_probe_run,
                                              network_ctx, config.network_interval, true);
                    log_debug("  Network probe registered (interval: %ds)", config.network_interval);
                }
            }

            if (config.thermal_interval > 0) {
                thermal_ctx = thermal_probe_create_ctx(&config);
                if (thermal_ctx) {
                    telem_scheduler_register(probes, "thermal", thermal_probe_run,
                                              thermal_ctx, config.thermal_interval, true);
                    log_debug("  Thermal probe registered (interval: %ds)", config.thermal_interval);
                }
            }

            if (config.package_interval > 0) {
                package_ctx = package_probe_create_ctx(&config);
                if (package_ctx) {
                    telem_scheduler_register(probes, "package", package_probe_run,
                                              package_ctx, config.package_interval, true);
                    log_debug("  Package probe registered (interval: %ds)", config.package_interval);
                }
            }

            if (config.port_interval > 0) {
                port_ctx = port_probe_create_ctx(&config);
                if (port_ctx) {
                    telem_scheduler_register(probes, "port", port_probe_run,
                                              port_ctx, config.port_interval, true);
                    log_debug("  Port probe registered (interval: %ds)", config.port_interval);
                }
            }

            if (config.connpool_interval > 0) {
                connpool_ctx = connpool_probe_create_ctx(&config);
                if (connpool_ctx) {
                    telem_scheduler_register(probes, "connpool", connpool_probe_run,
                                              connpool_ctx, config.connpool_interval, true);
                    log_debug("  Connpool probe registered (interval: %ds)", config.connpool_interval);
                }
            }

            if (config.topproc_interval > 0) {
                topproc_ctx = topproc_probe_create_ctx(&config);
                if (topproc_ctx) {
                    telem_scheduler_register(probes, "topproc", topproc_probe_run,
                                              topproc_ctx, config.topproc_interval, true);
                    log_debug("  Topproc probe registered (interval: %ds)", config.topproc_interval);
                }
            }

            if (config.ioperf_interval > 0) {
                ioperf_ctx = ioperf_probe_create_ctx(&config);
                if (ioperf_ctx) {
                    telem_scheduler_register(probes, "ioperf", ioperf_probe_run,
                                              ioperf_ctx, config.ioperf_interval, true);
                    log_debug("  I/O perf probe registered (interval: %ds)", config.ioperf_interval);
                }
            }

            if (config.cgroup_interval > 0) {
                cgroup_ctx = cgroup_probe_create_ctx(&config);
                if (cgroup_ctx) {
                    telem_scheduler_register(probes, "cgroup", cgroup_probe_run,
                                              cgroup_ctx, config.cgroup_interval, true);
                    log_debug("  Cgroup probe registered (interval: %ds)", config.cgroup_interval);
                }
            }
        } else {
            log_warn("Failed to create probe scheduler");
        }
    }

#ifdef ENABLE_EBPF
    /* Initialize eBPF loader */
    if (config.ebpf_enabled && ebpf_is_available()) {
        struct ebpf_config ebpf_cfg = EBPF_DEFAULT_CONFIG;

        ebpf = ebpf_loader_create(&ebpf_cfg, norm, sock);
        if (ebpf) {
            int loaded = ebpf_loader_start(ebpf);
            if (loaded > 0) {
                int ebpf_fd = ebpf_loader_get_fd(ebpf);
                if (ebpf_fd >= 0) {
                    ebpf_idx = nfds;
                    pfds[nfds].fd = ebpf_fd;
                    pfds[nfds].events = POLLIN;
                    nfds++;
                }
                log_info("eBPF loader started (%d programs)", loaded);
            } else {
                log_warn("Failed to start eBPF programs");
                ebpf_loader_destroy(ebpf);
                ebpf = NULL;
            }
        } else {
            log_warn("Failed to create eBPF loader");
        }
    } else if (config.ebpf_enabled) {
        log_warn("eBPF requested but not available on this system");
    }
#endif

    /* Add socket fd for accepting clients */
    pfds[nfds].fd = telem_socket_get_fd(sock);
    pfds[nfds].events = POLLIN;
    nfds++;

    /* Emit startup event */
    {
        struct telem_event evt;
        char *json_str;
        bool emit = normalizer_process(norm,
            TELEM_SRC_JOURNAL,
            6,  /* info priority */
            "elled-telemetryd started",
            NULL,
            NULL,
            0,
            &evt);
        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    /* Main event loop */
    while (!shutdown_requested) {
        int timeout = 1000;  /* Default 1 second */
        int ret;

        /* Get probe timeout */
        if (probes) {
            int probe_timeout = telem_scheduler_get_next_deadline(probes);
            if (probe_timeout >= 0 && probe_timeout < timeout)
                timeout = probe_timeout;
        }

        ret = poll(pfds, nfds, timeout);

        if (ret < 0) {
            if (errno == EINTR)
                continue;
            log_error("poll() failed: %s", strerror(errno));
            break;
        }

        /* Process journal events */
        if (journal_idx >= 0 && (pfds[journal_idx].revents & POLLIN)) {
            if (journal_watcher_wait(journal, 0) > 0) {
                journal_watcher_process(journal, journal_callback, &journal_ctx);
            }
        }

        /* Process kernel events */
        if (kernel_idx >= 0 && (pfds[kernel_idx].revents & POLLIN)) {
            if (journal_watcher_wait(kernel, 0) > 0) {
                journal_watcher_process(kernel, journal_callback, &kernel_ctx);
            }
        }

        /* Process docker events */
        if (docker_idx >= 0 && (pfds[docker_idx].revents & POLLIN)) {
            if (docker_watcher_is_running(docker)) {
                docker_watcher_process(docker, docker_callback, &docker_ctx);
            }
        }

        /* Process inotify events */
        if (inotify_idx >= 0 && (pfds[inotify_idx].revents & POLLIN)) {
            inotify_watcher_process(inotify, inotify_callback, &inotify_ctx);
        }

#ifdef ENABLE_EBPF
        /* Process eBPF events */
        if (ebpf_idx >= 0 && (pfds[ebpf_idx].revents & POLLIN)) {
            ebpf_loader_poll(ebpf, 0);
        }
#endif

        /* Run due probes */
        if (probes) {
            telem_scheduler_run_once(probes);
        }

        /* Log client count periodically (every ~30s) */
        static int counter = 0;
        if (++counter % 30 == 0) {
            int clients = telem_socket_num_clients(sock);
            log_debug("Connected clients: %d", clients);
        }
    }

    log_info("Shutdown requested");

    /* Cleanup */
#ifdef ENABLE_EBPF
    if (ebpf) ebpf_loader_destroy(ebpf);
#endif
    if (psi_ctx) psi_probe_destroy_ctx(psi_ctx);
    if (memory_ctx) memory_probe_destroy_ctx(memory_ctx);
    if (disk_ctx) disk_probe_destroy_ctx(disk_ctx);
    if (network_ctx) network_probe_destroy_ctx(network_ctx);
    if (thermal_ctx) thermal_probe_destroy_ctx(thermal_ctx);
    if (package_ctx) package_probe_destroy_ctx(package_ctx);
    if (port_ctx) port_probe_destroy_ctx(port_ctx);
    if (connpool_ctx) connpool_probe_destroy_ctx(connpool_ctx);
    if (topproc_ctx) topproc_probe_destroy_ctx(topproc_ctx);
    if (ioperf_ctx) ioperf_probe_destroy_ctx(ioperf_ctx);
    if (cgroup_ctx) cgroup_probe_destroy_ctx(cgroup_ctx);
    if (probes) telem_scheduler_destroy(probes);
    if (inotify) inotify_watcher_destroy(inotify);
    if (docker) docker_watcher_destroy(docker);
    if (kernel) journal_watcher_destroy(kernel);
    if (journal) journal_watcher_destroy(journal);

    return 0;
}

/* ==========================================================================
 * Main
 * ========================================================================== */

int main(int argc, char *argv[])
{
    struct normalizer *norm = NULL;
    struct telem_socket *sock = NULL;
    int ret = 1;

    /* Parse command line */
    if (parse_args(argc, argv) < 0)
        return 1;

    /* Setup signal handlers */
    setup_signals();

    /* Ensure runtime directory exists */
    if (ensure_runtime_dir() < 0)
        goto cleanup;

    /* Create normalizer */
    log_info("Initializing normalizer (dedupe window: %ds)", config.dedupe_window_sec);
    norm = normalizer_create(config.dedupe_window_sec);
    if (!norm) {
        log_error("Failed to create normalizer");
        goto cleanup;
    }

    /* Create socket */
    log_info("Creating socket: %s", config.socket_path);
    sock = telem_socket_create(config.socket_path);
    if (!sock) {
        log_error("Failed to create socket");
        goto cleanup;
    }

    /* Run main loop */
    ret = main_loop(norm, sock);

    /* Print stats */
    uint64_t processed, deduplicated;
    normalizer_get_stats(norm, &processed, &deduplicated, NULL);
    log_info("Stats: processed=%lu, deduplicated=%lu", processed, deduplicated);

cleanup:
    if (sock)
        telem_socket_destroy(sock);
    if (norm)
        normalizer_destroy(norm);

    return ret;
}
