// SPDX-License-Identifier: MIT
/**
 * main.c - Entry point for elled-gpud
 *
 * GPU telemetry daemon that polls NVIDIA GPUs via NVML and
 * emits NDJSON events to the telemetryd socket.
 *
 * gpud is a client that connects to telemetryd's Unix socket
 * and writes GPU events for the Python daemon to consume.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <getopt.h>
#include <poll.h>
#include <errno.h>

#include "gpud.h"
#include "nvml.h"
#include "client.h"
#include "collector.h"

/* ==========================================================================
 * Globals
 * ========================================================================== */

static volatile sig_atomic_t shutdown_requested = 0;
static struct gpud_config config = GPUD_DEFAULT_CONFIG;
int gpud_log_level = LOG_INFO;

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

    /* Ignore SIGPIPE */
    sa.sa_handler = SIG_IGN;
    sigaction(SIGPIPE, &sa, NULL);
}

/* ==========================================================================
 * Command-line Parsing
 * ========================================================================== */

static void print_usage(const char *prog)
{
    printf("Usage: %s [OPTIONS]\n\n", prog);
    printf("GPU telemetry daemon for ELLE.\n\n");
    printf("Options:\n");
    printf("  -s, --socket PATH      Socket path (default: %s)\n", GPUD_DEFAULT_SOCKET);
    printf("  -i, --interval MS      Poll interval in milliseconds (default: %d)\n",
           GPUD_DEFAULT_INTERVAL_MS);
    printf("  --temp-warning C       Temperature warning threshold (default: %d)\n",
           GPUD_DEFAULT_TEMP_WARNING);
    printf("  --temp-critical C      Temperature critical threshold (default: %d)\n",
           GPUD_DEFAULT_TEMP_CRITICAL);
    printf("  --mem-warning PCT      Memory warning threshold %% (default: %d)\n",
           GPUD_DEFAULT_MEM_WARNING);
    printf("  --mem-critical PCT     Memory critical threshold %% (default: %d)\n",
           GPUD_DEFAULT_MEM_CRITICAL);
    printf("  --no-status            Don't emit periodic status events\n");
    printf("  -v, --verbose          Increase verbosity (can be repeated)\n");
    printf("  -h, --help             Show this help message\n");
}

static int parse_args(int argc, char *argv[])
{
    static struct option long_options[] = {
        {"socket",        required_argument, 0, 's'},
        {"interval",      required_argument, 0, 'i'},
        {"temp-warning",  required_argument, 0, 'T'},
        {"temp-critical", required_argument, 0, 't'},
        {"mem-warning",   required_argument, 0, 'M'},
        {"mem-critical",  required_argument, 0, 'm'},
        {"no-status",     no_argument,       0, 'S'},
        {"verbose",       no_argument,       0, 'v'},
        {"help",          no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    int option_index = 0;

    while ((opt = getopt_long(argc, argv, "s:i:vh", long_options, &option_index)) != -1) {
        switch (opt) {
        case 's':
            snprintf(config.socket_path, sizeof(config.socket_path), "%s", optarg);
            break;
        case 'i':
            config.interval_ms = (int)strtol(optarg, NULL, 10);
            if (config.interval_ms < 100 || config.interval_ms > 60000) {
                fprintf(stderr, "ERROR: Invalid interval (must be 100-60000ms)\n");
                return -1;
            }
            break;
        case 'T':
            config.temp_warning = (int)strtol(optarg, NULL, 10);
            break;
        case 't':
            config.temp_critical = (int)strtol(optarg, NULL, 10);
            break;
        case 'M':
            config.mem_warning = (int)strtol(optarg, NULL, 10);
            break;
        case 'm':
            config.mem_critical = (int)strtol(optarg, NULL, 10);
            break;
        case 'S':
            config.emit_status = false;
            break;
        case 'v':
            gpud_log_level++;
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
 * Main
 * ========================================================================== */

int main(int argc, char *argv[])
{
    nvml_api_t *api = NULL;
    struct gpud_client *client = NULL;
    struct gpud_collector *collector = NULL;
    int ret = 0;

    /* Parse command line */
    if (parse_args(argc, argv) < 0)
        return 1;

    gpud_log_info("elled-gpud starting");

    /* Setup signal handlers */
    setup_signals();

    /* Check if NVML is available */
    if (!nvml_available()) {
        gpud_log_info("NVML not available (no NVIDIA GPU), exiting");
        return 0;  /* Not an error - just no GPU */
    }

    /* Load NVML */
    api = nvml_load();
    if (!api) {
        gpud_log_info("Failed to initialize NVML, exiting");
        return 0;  /* Not an error - just no working GPU */
    }

    /* Create collector */
    collector = gpud_collector_create(api, &config);
    if (!collector) {
        gpud_log_info("No GPUs found or collector creation failed, exiting");
        nvml_unload(api);
        return 0;  /* Not an error - just no GPU */
    }

    gpud_log_info("Monitoring %u GPU(s)", gpud_collector_get_device_count(collector));

    /* Create client connection */
    client = gpud_client_create(config.socket_path);
    if (!client) {
        gpud_log_error("Failed to create client");
        ret = 1;
        goto cleanup;
    }

    gpud_log_info("Poll interval: %dms", config.interval_ms);
    gpud_log_info("Thresholds: temp=%d/%dC, mem=%d/%d%%",
                  config.temp_warning, config.temp_critical,
                  config.mem_warning, config.mem_critical);

    /* Main loop */
    uint64_t next_poll = gpud_get_monotonic_ns();

    while (!shutdown_requested) {
        uint64_t now = gpud_get_monotonic_ns();

        /* Poll if due */
        if (now >= next_poll) {
            int events = gpud_collector_poll(collector, client);
            if (events >= 0) {
                gpud_log_debug("Polled GPUs, emitted %d events", events);
            }

            next_poll = now + (uint64_t)config.interval_ms * 1000000ULL;
        }

        /* Sleep until next poll (interruptible) */
        int timeout_ms = (int)((next_poll - now) / 1000000);
        if (timeout_ms > 0) {
            poll(NULL, 0, timeout_ms > 1000 ? 1000 : timeout_ms);
        }
    }

    gpud_log_info("Shutdown requested");

cleanup:
    if (client)
        gpud_client_destroy(client);
    if (collector)
        gpud_collector_destroy(collector);
    if (api)
        nvml_unload(api);

    return ret;
}
