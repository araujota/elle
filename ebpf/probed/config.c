// SPDX-License-Identifier: MIT
/**
 * config.c - Configuration management for elled-probed
 *
 * Handles loading and managing runtime configuration.
 * Currently uses defaults; TOML parsing can be added later.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#include "config.h"

/* ==========================================================================
 * Default Configuration
 * ========================================================================== */

void probed_config_init(struct probed_config *cfg)
{
    if (!cfg)
        return;

    /* Socket path */
    snprintf(cfg->socket_path, sizeof(cfg->socket_path), "%s", "/run/elle/probed.sock");

    /* PSI probe defaults */
    cfg->psi_enabled = true;
    cfg->psi_interval_sec = 5;
    cfg->memory_warning_pct = 10.0;
    cfg->memory_critical_pct = 25.0;
    cfg->io_warning_pct = 20.0;
    cfg->io_critical_pct = 50.0;
    cfg->cpu_warning_pct = 25.0;
    cfg->cpu_critical_pct = 50.0;

    /* Systemd probe defaults */
    cfg->systemd_enabled = true;
    cfg->systemd_interval_sec = 10;
    cfg->crashloop_threshold = 3;
    cfg->crashloop_window_sec = 300;

    /* DNS probe defaults */
    cfg->dns_enabled = true;
    cfg->dns_interval_sec = 30;
    cfg->dns_timeout_ms = 2000;
    snprintf(cfg->dns_test_domains[0], sizeof(cfg->dns_test_domains[0]), "%s",
             "connectivity-check.ubuntu.com");
    cfg->dns_test_domain_count = 1;

    /* Conntrack probe defaults */
    cfg->conntrack_enabled = true;
    cfg->conntrack_interval_sec = 15;
    cfg->conntrack_warning_pct = 80.0;
    cfg->conntrack_critical_pct = 95.0;

    /* Hardware probe defaults */
    cfg->hardware_enabled = true;
    cfg->hardware_interval_sec = 300;

    /* Auth probe defaults */
    cfg->auth_enabled = true;
    cfg->auth_interval_sec = 5;
    cfg->brute_force_threshold = 5;
}

/* ==========================================================================
 * Configuration Loading
 * ========================================================================== */

int probed_config_load(struct probed_config *cfg, const char *path)
{
    FILE *f;

    if (!cfg)
        return -1;

    /* Start with defaults */
    probed_config_init(cfg);

    if (!path)
        return 0;

    /* Try to open config file */
    f = fopen(path, "r");
    if (!f) {
        if (errno == ENOENT) {
            /* File doesn't exist, use defaults */
            return 0;
        }
        fprintf(stderr, "WARNING: Failed to open config %s: %s\n",
                path, strerror(errno));
        return -1;
    }

    /*
     * TODO: Implement TOML parsing
     * For now, just use defaults and close the file
     */
    fclose(f);

    return 0;
}

/* ==========================================================================
 * Configuration Debugging
 * ========================================================================== */

void probed_config_dump(const struct probed_config *cfg)
{
    if (!cfg)
        return;

    fprintf(stderr, "=== elled-probed Configuration ===\n");
    fprintf(stderr, "Socket: %s\n", cfg->socket_path);
    fprintf(stderr, "\n");

    fprintf(stderr, "PSI Probe:\n");
    fprintf(stderr, "  enabled: %s\n", cfg->psi_enabled ? "true" : "false");
    fprintf(stderr, "  interval: %ds\n", cfg->psi_interval_sec);
    fprintf(stderr, "  memory warning: %.1f%%\n", cfg->memory_warning_pct);
    fprintf(stderr, "  memory critical: %.1f%%\n", cfg->memory_critical_pct);
    fprintf(stderr, "  io warning: %.1f%%\n", cfg->io_warning_pct);
    fprintf(stderr, "  io critical: %.1f%%\n", cfg->io_critical_pct);
    fprintf(stderr, "\n");

    fprintf(stderr, "Systemd Probe:\n");
    fprintf(stderr, "  enabled: %s\n", cfg->systemd_enabled ? "true" : "false");
    fprintf(stderr, "  interval: %ds\n", cfg->systemd_interval_sec);
    fprintf(stderr, "  crashloop threshold: %d restarts in %ds\n",
            cfg->crashloop_threshold, cfg->crashloop_window_sec);
    fprintf(stderr, "\n");

    fprintf(stderr, "DNS Probe:\n");
    fprintf(stderr, "  enabled: %s\n", cfg->dns_enabled ? "true" : "false");
    fprintf(stderr, "  interval: %ds\n", cfg->dns_interval_sec);
    fprintf(stderr, "  timeout: %dms\n", cfg->dns_timeout_ms);
    for (int i = 0; i < cfg->dns_test_domain_count; i++) {
        fprintf(stderr, "  test domain: %s\n", cfg->dns_test_domains[i]);
    }
    fprintf(stderr, "\n");

    fprintf(stderr, "Conntrack Probe:\n");
    fprintf(stderr, "  enabled: %s\n", cfg->conntrack_enabled ? "true" : "false");
    fprintf(stderr, "  interval: %ds\n", cfg->conntrack_interval_sec);
    fprintf(stderr, "  warning: %.1f%%\n", cfg->conntrack_warning_pct);
    fprintf(stderr, "  critical: %.1f%%\n", cfg->conntrack_critical_pct);
    fprintf(stderr, "\n");

    fprintf(stderr, "Hardware Probe:\n");
    fprintf(stderr, "  enabled: %s\n", cfg->hardware_enabled ? "true" : "false");
    fprintf(stderr, "  interval: %ds\n", cfg->hardware_interval_sec);
    fprintf(stderr, "\n");

    fprintf(stderr, "Auth Probe:\n");
    fprintf(stderr, "  enabled: %s\n", cfg->auth_enabled ? "true" : "false");
    fprintf(stderr, "  interval: %ds\n", cfg->auth_interval_sec);
    fprintf(stderr, "  brute force threshold: %d\n", cfg->brute_force_threshold);
    fprintf(stderr, "==================================\n");
}
