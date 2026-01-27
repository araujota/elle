// SPDX-License-Identifier: MIT
/**
 * config.c - Runtime configuration for elled-ebpfd
 */

#include <stdio.h>
#include "config.h"

void elled_config_init(struct elled_config *cfg)
{
    *cfg = (struct elled_config)ELLED_DEFAULT_CONFIG;
}

int elled_config_validate(const struct elled_config *cfg)
{
    /* Validate sample rates (must be positive) */
    if (cfg->exec_sample_rate == 0) {
        fprintf(stderr, "ERROR: exec_sample_rate must be > 0\n");
        return -1;
    }

    if (cfg->net_drop_sample_rate == 0) {
        fprintf(stderr, "ERROR: net_drop_sample_rate must be > 0\n");
        return -1;
    }

    return 0;
}
