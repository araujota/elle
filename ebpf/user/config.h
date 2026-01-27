// SPDX-License-Identifier: MIT
/**
 * config.h - Runtime configuration for elled-ebpfd
 */

#ifndef __ELLED_CONFIG_H__
#define __ELLED_CONFIG_H__

#include "elled.h"

/* Include RLIMIT_MEMLOCK for setrlimit */
#include <sys/resource.h>

/**
 * elled_config_init - Initialize configuration with defaults
 * @cfg: Configuration structure to initialize
 */
void elled_config_init(struct elled_config *cfg);

/**
 * elled_config_validate - Validate configuration
 * @cfg: Configuration to validate
 *
 * Returns: 0 if valid, -1 if invalid
 */
int elled_config_validate(const struct elled_config *cfg);

#endif /* __ELLED_CONFIG_H__ */
