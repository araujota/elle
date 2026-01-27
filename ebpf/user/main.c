// SPDX-License-Identifier: MIT
/**
 * main.c - elled-ebpfd entry point
 *
 * CO-RE eBPF telemetry collector for ELLE.
 * Loads BPF programs, polls ring buffer, outputs NDJSON to Unix socket.
 *
 * Usage:
 *   elled-ebpfd [options]
 *
 * Options:
 *   --socket PATH    Unix socket path (default: /run/elle/telemetry.sock)
 *   --stdout         Output to stdout instead of socket
 *   --verbose        Enable verbose logging
 *   --no-oom         Disable OOM tracing
 *   --no-exec        Disable exec tracing
 *   --no-block       Disable block I/O tracing
 *   --no-tcp         Disable TCP retransmit tracing (default: disabled)
 *   --help           Show this help
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <errno.h>
#include <unistd.h>
#include <getopt.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "collector.h"
#include "socket.h"
#include "config.h"

/* Default socket path */
#define DEFAULT_SOCKET_PATH "/run/elle/telemetry.sock"

/* Global shutdown flag */
static volatile sig_atomic_t shutdown_requested = 0;

/* Program configuration */
struct elled_options {
    const char *socket_path;
    int use_stdout;
    int verbose;
    int enable_oom;
    int enable_exec;
    int enable_block;
    int enable_tcp;
};

/* ==========================================================================
 * Signal Handlers
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
 * Argument Parsing
 * ========================================================================== */

static void print_usage(const char *prog)
{
    fprintf(stderr, "elled-ebpfd - ELLE eBPF Telemetry Collector\n\n");
    fprintf(stderr, "Usage: %s [options]\n\n", prog);
    fprintf(stderr, "Options:\n");
    fprintf(stderr, "  --socket PATH    Unix socket path (default: %s)\n",
            DEFAULT_SOCKET_PATH);
    fprintf(stderr, "  --stdout         Output to stdout instead of socket\n");
    fprintf(stderr, "  --verbose        Enable verbose logging\n");
    fprintf(stderr, "  --no-oom         Disable OOM tracing\n");
    fprintf(stderr, "  --no-exec        Disable exec tracing\n");
    fprintf(stderr, "  --no-block       Disable block I/O tracing\n");
    fprintf(stderr, "  --no-tcp         Disable TCP retransmit tracing (default: disabled)\n");
    fprintf(stderr, "  --enable-tcp     Enable TCP retransmit tracing\n");
    fprintf(stderr, "  --help           Show this help\n");
}

static int parse_args(int argc, char **argv, struct elled_options *opts)
{
    static struct option long_options[] = {
        {"socket",     required_argument, 0, 's'},
        {"stdout",     no_argument,       0, 'o'},
        {"verbose",    no_argument,       0, 'v'},
        {"no-oom",     no_argument,       0, 'O'},
        {"no-exec",    no_argument,       0, 'E'},
        {"no-block",   no_argument,       0, 'B'},
        {"no-tcp",     no_argument,       0, 'T'},
        {"enable-tcp", no_argument,       0, 't'},
        {"help",       no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    /* Set defaults */
    opts->socket_path = DEFAULT_SOCKET_PATH;
    opts->use_stdout = 0;
    opts->verbose = 0;
    opts->enable_oom = 1;
    opts->enable_exec = 1;
    opts->enable_block = 1;
    opts->enable_tcp = 0;  /* Disabled by default (high volume) */

    int opt;
    int option_index = 0;

    while ((opt = getopt_long(argc, argv, "s:ovh", long_options, &option_index)) != -1) {
        switch (opt) {
        case 's':
            opts->socket_path = optarg;
            break;
        case 'o':
            opts->use_stdout = 1;
            break;
        case 'v':
            opts->verbose = 1;
            break;
        case 'O':
            opts->enable_oom = 0;
            break;
        case 'E':
            opts->enable_exec = 0;
            break;
        case 'B':
            opts->enable_block = 0;
            break;
        case 'T':
            opts->enable_tcp = 0;
            break;
        case 't':
            opts->enable_tcp = 1;
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
 * Runtime Directory Setup
 * ========================================================================== */

static int ensure_run_directory(void)
{
    const char *run_dir = "/run/elle";
    struct stat st;

    if (stat(run_dir, &st) == 0) {
        if (S_ISDIR(st.st_mode))
            return 0;
        fprintf(stderr, "ERROR: %s exists but is not a directory\n", run_dir);
        return -1;
    }

    if (mkdir(run_dir, 0755) < 0) {
        fprintf(stderr, "ERROR: Failed to create %s: %s\n",
                run_dir, strerror(errno));
        return -1;
    }

    return 0;
}

/* ==========================================================================
 * Main
 * ========================================================================== */

int main(int argc, char **argv)
{
    struct elled_options opts;
    struct elled_collector *collector = NULL;
    struct elled_socket *sock = NULL;
    struct elled_config cfg;
    int err = 0;

    /* Parse arguments */
    if (parse_args(argc, argv, &opts) < 0)
        return 1;

    if (opts.verbose) {
        fprintf(stderr, "elled-ebpfd starting...\n");
        fprintf(stderr, "  OOM: %s\n", opts.enable_oom ? "enabled" : "disabled");
        fprintf(stderr, "  Exec: %s\n", opts.enable_exec ? "enabled" : "disabled");
        fprintf(stderr, "  Block I/O: %s\n", opts.enable_block ? "enabled" : "disabled");
        fprintf(stderr, "  TCP retransmit: %s\n", opts.enable_tcp ? "enabled" : "disabled");
    }

    /* Setup signal handlers */
    setup_signals();

    /* Build configuration */
    cfg = (struct elled_config)ELLED_DEFAULT_CONFIG;
    cfg.enable_oom = opts.enable_oom;
    cfg.enable_exec = opts.enable_exec;
    cfg.enable_block = opts.enable_block;
    cfg.enable_tcp_retrans = opts.enable_tcp;

    /* Setup output */
    if (!opts.use_stdout) {
        if (ensure_run_directory() < 0) {
            err = 1;
            goto cleanup;
        }

        sock = elled_socket_create(opts.socket_path);
        if (!sock) {
            fprintf(stderr, "ERROR: Failed to create socket\n");
            err = 1;
            goto cleanup;
        }

        if (opts.verbose)
            fprintf(stderr, "Listening on %s\n", opts.socket_path);
    }

    /* Create and start collector */
    collector = elled_collector_create(&cfg, sock, opts.use_stdout, opts.verbose);
    if (!collector) {
        fprintf(stderr, "ERROR: Failed to create collector\n");
        err = 1;
        goto cleanup;
    }

    if (elled_collector_start(collector) < 0) {
        fprintf(stderr, "ERROR: Failed to start collector\n");
        err = 1;
        goto cleanup;
    }

    if (opts.verbose)
        fprintf(stderr, "Collector started, polling for events...\n");

    /* Main loop */
    while (!shutdown_requested) {
        if (elled_collector_poll(collector, 100) < 0) {
            if (errno != EINTR) {
                fprintf(stderr, "ERROR: Poll failed: %s\n", strerror(errno));
                err = 1;
                break;
            }
        }
    }

    if (opts.verbose)
        fprintf(stderr, "\nShutdown requested, cleaning up...\n");

cleanup:
    if (collector)
        elled_collector_destroy(collector);
    if (sock)
        elled_socket_destroy(sock);

    if (opts.verbose)
        fprintf(stderr, "Exiting with code %d\n", err);

    return err;
}
