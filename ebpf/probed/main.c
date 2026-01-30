// SPDX-License-Identifier: MIT
/**
 * main.c - elled-probed entry point
 *
 * Userspace polling daemon for ground truth monitoring.
 * Collects system metrics via polling and emits NDJSON events
 * to a Unix socket for consumption by the Python daemon.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <unistd.h>
#include <getopt.h>
#include <poll.h>
#include <sys/stat.h>
#include <time.h>

#include "probed.h"
#include "config.h"
#include "socket.h"
#include "scheduler.h"
#include "json.h"

#include "probes/psi.h"
#include "probes/systemd.h"
#include "probes/dns.h"
#include "probes/conntrack.h"
#include "probes/hardware.h"
#include "probes/auth.h"
#include "probes/cert.h"
#include "probes/sysstate.h"

/* ==========================================================================
 * Constants
 * ========================================================================== */

#define DEFAULT_SOCKET_PATH "/run/elle/probed.sock"
#define DEFAULT_CONFIG_PATH "/etc/elle/probed.toml"

/* ==========================================================================
 * Global State
 * ========================================================================== */

static volatile sig_atomic_t shutdown_requested = 0;
static int verbose = 0;
static int use_stdout = 0;

/* Probe contexts */
static struct psi_probe_ctx *psi_ctx = NULL;
static struct systemd_probe_ctx *systemd_ctx = NULL;
static struct dns_probe_ctx *dns_ctx = NULL;
static struct conntrack_probe_ctx *conntrack_ctx = NULL;
static struct hardware_probe_ctx *hardware_ctx = NULL;
static struct auth_probe_ctx *auth_ctx = NULL;
static struct cert_probe_ctx *cert_ctx = NULL;
static struct sysstate_probe_ctx *sysstate_ctx = NULL;

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
    struct sigaction sa;

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);

    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Ignore SIGPIPE from broken socket connections */
    sa.sa_handler = SIG_IGN;
    sigaction(SIGPIPE, &sa, NULL);
}

/* ==========================================================================
 * Stdout Output Wrapper
 * ========================================================================== */

/* Wrapper probe function that outputs to stdout instead of socket */
struct stdout_ctx {
    probe_func_t real_func;
    void *real_ctx;
};

static int stdout_probe_wrapper(struct probed_socket *sock, void *ctx)
{
    struct stdout_ctx *sctx = (struct stdout_ctx *)ctx;
    (void)sock;
    return sctx->real_func(NULL, sctx->real_ctx);
}

/* ==========================================================================
 * Ensure Run Directory Exists
 * ========================================================================== */

static int ensure_run_dir(void)
{
    struct stat st;

    if (stat("/run/elle", &st) == 0) {
        if (S_ISDIR(st.st_mode))
            return 0;
        fprintf(stderr, "ERROR: /run/elle exists but is not a directory\n");
        return -1;
    }

    if (mkdir("/run/elle", 0755) < 0) {
        if (errno != EEXIST) {
            fprintf(stderr, "ERROR: Failed to create /run/elle: %s\n",
                    strerror(errno));
            return -1;
        }
    }

    return 0;
}

/* ==========================================================================
 * Command Line Parsing
 * ========================================================================== */

static void print_usage(const char *prog)
{
    fprintf(stderr,
            "Usage: %s [OPTIONS]\n"
            "\n"
            "Options:\n"
            "  -s, --socket PATH     Socket path (default: %s)\n"
            "  -c, --config PATH     Config file (default: %s)\n"
            "  -o, --stdout          Output to stdout instead of socket\n"
            "  -v, --verbose         Verbose logging\n"
            "  -h, --help            Show this help\n"
            "\n"
            "Probe toggles:\n"
            "  --no-psi              Disable PSI probe\n"
            "  --no-systemd          Disable systemd probe\n"
            "  --no-dns              Disable DNS probe\n"
            "  --no-conntrack        Disable conntrack probe\n"
            "  --no-hardware         Disable hardware probe\n"
            "  --no-auth             Disable auth probe\n",
            prog, DEFAULT_SOCKET_PATH, DEFAULT_CONFIG_PATH);
}

struct options {
    const char *socket_path;
    const char *config_path;
    int use_stdout;
    int verbose;
    int no_psi;
    int no_systemd;
    int no_dns;
    int no_conntrack;
    int no_hardware;
    int no_auth;
};

static int parse_args(int argc, char **argv, struct options *opts)
{
    static struct option long_options[] = {
        {"socket",      required_argument, 0, 's'},
        {"config",      required_argument, 0, 'c'},
        {"stdout",      no_argument,       0, 'o'},
        {"verbose",     no_argument,       0, 'v'},
        {"help",        no_argument,       0, 'h'},
        {"no-psi",      no_argument,       0, 'P'},
        {"no-systemd",  no_argument,       0, 'S'},
        {"no-dns",      no_argument,       0, 'D'},
        {"no-conntrack", no_argument,      0, 'C'},
        {"no-hardware", no_argument,       0, 'H'},
        {"no-auth",     no_argument,       0, 'A'},
        {0, 0, 0, 0}
    };

    /* Defaults */
    memset(opts, 0, sizeof(*opts));
    opts->socket_path = DEFAULT_SOCKET_PATH;
    opts->config_path = DEFAULT_CONFIG_PATH;

    int c;
    while ((c = getopt_long(argc, argv, "s:c:ovh", long_options, NULL)) != -1) {
        switch (c) {
        case 's':
            opts->socket_path = optarg;
            break;
        case 'c':
            opts->config_path = optarg;
            break;
        case 'o':
            opts->use_stdout = 1;
            break;
        case 'v':
            opts->verbose = 1;
            break;
        case 'h':
            print_usage(argv[0]);
            exit(0);
        case 'P':
            opts->no_psi = 1;
            break;
        case 'S':
            opts->no_systemd = 1;
            break;
        case 'D':
            opts->no_dns = 1;
            break;
        case 'C':
            opts->no_conntrack = 1;
            break;
        case 'H':
            opts->no_hardware = 1;
            break;
        case 'A':
            opts->no_auth = 1;
            break;
        default:
            print_usage(argv[0]);
            return -1;
        }
    }

    return 0;
}

/* ==========================================================================
 * Cleanup
 * ========================================================================== */

static void cleanup(void)
{
    if (psi_ctx)
        psi_probe_destroy_ctx(psi_ctx);
    if (systemd_ctx)
        systemd_probe_destroy_ctx(systemd_ctx);
    if (dns_ctx)
        dns_probe_destroy_ctx(dns_ctx);
    if (conntrack_ctx)
        conntrack_probe_destroy_ctx(conntrack_ctx);
    if (hardware_ctx)
        hardware_probe_destroy_ctx(hardware_ctx);
    if (auth_ctx)
        auth_probe_destroy_ctx(auth_ctx);
    if (cert_ctx)
        cert_probe_destroy_ctx(cert_ctx);
    if (sysstate_ctx)
        sysstate_probe_destroy_ctx(sysstate_ctx);
}

/* ==========================================================================
 * Main
 * ========================================================================== */

int main(int argc, char **argv)
{
    struct options opts;
    struct probed_config cfg;
    struct probed_socket *sock = NULL;
    struct probed_scheduler *sched = NULL;
    int ret = 0;

    /* Parse arguments */
    if (parse_args(argc, argv, &opts) < 0)
        return 1;

    verbose = opts.verbose;
    use_stdout = opts.use_stdout;

    /* Load configuration */
    probed_config_init(&cfg);
    probed_config_load(&cfg, opts.config_path);

    /* Override config with command line options */
    if (opts.no_psi)
        cfg.psi_enabled = false;
    if (opts.no_systemd)
        cfg.systemd_enabled = false;
    if (opts.no_dns)
        cfg.dns_enabled = false;
    if (opts.no_conntrack)
        cfg.conntrack_enabled = false;
    if (opts.no_hardware)
        cfg.hardware_enabled = false;
    if (opts.no_auth)
        cfg.auth_enabled = false;

    if (verbose) {
        probed_config_dump(&cfg);
    }

    /* Setup signals */
    setup_signals();

    /* Ensure /run/elle exists */
    if (!use_stdout) {
        if (ensure_run_dir() < 0) {
            ret = 1;
            goto cleanup;
        }

        /* Create socket */
        sock = probed_socket_create(opts.socket_path);
        if (!sock) {
            fprintf(stderr, "ERROR: Failed to create socket at %s\n",
                    opts.socket_path);
            ret = 1;
            goto cleanup;
        }

        if (verbose) {
            fprintf(stderr, "Listening on %s\n", opts.socket_path);
        }
    } else {
        fprintf(stderr, "Running in stdout mode (no socket)\n");
    }

    /* Create scheduler */
    sched = probed_scheduler_create(sock);
    if (!sched) {
        fprintf(stderr, "ERROR: Failed to create scheduler\n");
        ret = 1;
        goto cleanup;
    }

    /* Create and register probes */
    if (cfg.psi_enabled) {
        psi_ctx = psi_probe_create_ctx(&cfg);
        if (psi_ctx) {
            probed_scheduler_register(sched, "psi",
                                       psi_probe_run, psi_ctx,
                                       cfg.psi_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered PSI probe (interval: %ds)\n",
                        cfg.psi_interval_sec);
        }
    }

    if (cfg.systemd_enabled) {
        systemd_ctx = systemd_probe_create_ctx(&cfg);
        if (systemd_ctx) {
            probed_scheduler_register(sched, "systemd",
                                       systemd_probe_run, systemd_ctx,
                                       cfg.systemd_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered systemd probe (interval: %ds)\n",
                        cfg.systemd_interval_sec);
        }
    }

    if (cfg.dns_enabled) {
        dns_ctx = dns_probe_create_ctx(&cfg);
        if (dns_ctx) {
            probed_scheduler_register(sched, "dns",
                                       dns_probe_run, dns_ctx,
                                       cfg.dns_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered DNS probe (interval: %ds)\n",
                        cfg.dns_interval_sec);
        }
    }

    if (cfg.conntrack_enabled) {
        conntrack_ctx = conntrack_probe_create_ctx(&cfg);
        if (conntrack_ctx) {
            probed_scheduler_register(sched, "conntrack",
                                       conntrack_probe_run, conntrack_ctx,
                                       cfg.conntrack_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered conntrack probe (interval: %ds)\n",
                        cfg.conntrack_interval_sec);
        }
    }

    if (cfg.hardware_enabled) {
        hardware_ctx = hardware_probe_create_ctx(&cfg);
        if (hardware_ctx) {
            probed_scheduler_register(sched, "hardware",
                                       hardware_probe_run, hardware_ctx,
                                       cfg.hardware_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered hardware probe (interval: %ds)\n",
                        cfg.hardware_interval_sec);
        }
    }

    if (cfg.auth_enabled) {
        auth_ctx = auth_probe_create_ctx(&cfg);
        if (auth_ctx) {
            probed_scheduler_register(sched, "auth",
                                       auth_probe_run, auth_ctx,
                                       cfg.auth_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered auth probe (interval: %ds)\n",
                        cfg.auth_interval_sec);
        }
    }

    if (cfg.cert_enabled) {
        cert_ctx = cert_probe_create_ctx(&cfg);
        if (cert_ctx) {
            probed_scheduler_register(sched, "cert",
                                       cert_probe_run, cert_ctx,
                                       cfg.cert_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered cert probe (interval: %ds)\n",
                        cfg.cert_interval_sec);
        }
    }

    if (cfg.sysstate_enabled) {
        sysstate_ctx = sysstate_probe_create_ctx(&cfg);
        if (sysstate_ctx) {
            probed_scheduler_register(sched, "sysstate",
                                       sysstate_probe_run, sysstate_ctx,
                                       cfg.sysstate_interval_sec, true);
            if (verbose)
                fprintf(stderr, "Registered sysstate probe (interval: %ds)\n",
                        cfg.sysstate_interval_sec);
        }
    }

    fprintf(stderr, "elled-probed started\n");

    /* Main loop */
    while (!shutdown_requested) {
        /* Run due probes */
        int ran = probed_scheduler_run_once(sched);
        if (verbose && ran > 0) {
            fprintf(stderr, "Ran %d probes\n", ran);
        }

        /* Sleep until next probe is due */
        int wait_ms = probed_scheduler_get_next_deadline(sched);
        if (wait_ms < 0)
            wait_ms = 1000;
        if (wait_ms > 1000)
            wait_ms = 1000;  /* Max 1s to check for shutdown */

        /* Use poll with no FDs to sleep interruptibly */
        poll(NULL, 0, wait_ms);
    }

    fprintf(stderr, "Shutting down...\n");

cleanup:
    cleanup();

    if (sched)
        probed_scheduler_destroy(sched);
    if (sock)
        probed_socket_destroy(sock);

    return ret;
}
