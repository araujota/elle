// SPDX-License-Identifier: MIT
/**
 * category.h - Category detection via PCRE2 patterns
 *
 * Implements the 58 category patterns from Python normalizer.py
 * using PCRE2 for efficient regex matching.
 */

#ifndef __TELEMETRYD_CATEGORY_H__
#define __TELEMETRYD_CATEGORY_H__

#include "../telemetryd.h"

/**
 * category_init - Initialize category detection patterns
 *
 * Compiles all PCRE2 patterns. Must be called once at startup.
 *
 * Returns: 0 on success, -1 on failure
 */
int category_init(void);

/**
 * category_cleanup - Free compiled patterns
 *
 * Should be called at shutdown.
 */
void category_cleanup(void);

/**
 * category_detect - Detect category from message
 * @message: Log message to analyze
 *
 * Returns: Detected category enum value
 */
telem_category_t category_detect(const char *message);

/**
 * category_detect_str - Detect category and return as string
 * @message: Log message to analyze
 *
 * Returns: Category name string (static, do not free)
 */
const char *category_detect_str(const char *message);

#endif /* __TELEMETRYD_CATEGORY_H__ */
