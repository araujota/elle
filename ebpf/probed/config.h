// SPDX-License-Identifier: MIT
/**
 * config.h - Configuration management for elled-probed
 */

#ifndef __PROBED_CONFIG_H__
#define __PROBED_CONFIG_H__

#include "probed.h"

/**
 * probed_config_init - Initialize configuration with defaults
 * @cfg: Configuration structure to initialize
 */
void probed_config_init(struct probed_config *cfg);

/**
 * probed_config_load - Load configuration from TOML file
 * @cfg: Configuration structure to populate
 * @path: Path to TOML configuration file
 *
 * Returns: 0 on success, -1 on error (uses defaults on missing file)
 */
int probed_config_load(struct probed_config *cfg, const char *path);

/**
 * probed_config_dump - Print configuration to stderr (for debugging)
 * @cfg: Configuration structure
 */
void probed_config_dump(const struct probed_config *cfg);

#endif /* __PROBED_CONFIG_H__ */
