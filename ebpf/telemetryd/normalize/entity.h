// SPDX-License-Identifier: MIT
/**
 * entity.h - Entity extraction via PCRE2 patterns
 *
 * Implements the 17+ entity extraction patterns from Python normalizer.py
 * to extract entities like "service:nginx", "interface:eth0", etc.
 */

#ifndef __TELEMETRYD_ENTITY_H__
#define __TELEMETRYD_ENTITY_H__

#include "../telemetryd.h"
#include <stdbool.h>

/**
 * entity_init - Initialize entity extraction patterns
 *
 * Compiles all PCRE2 patterns. Must be called once at startup.
 *
 * Returns: 0 on success, -1 on failure
 */
int entity_init(void);

/**
 * entity_cleanup - Free compiled patterns
 *
 * Should be called at shutdown.
 */
void entity_cleanup(void);

/**
 * entity_extract - Extract entity from message
 * @message: Log message to analyze
 * @category: Detected category (for context-aware extraction)
 * @out_entity: Output buffer for entity string
 * @out_len: Size of output buffer
 *
 * Extracts entities like "service:nginx", "interface:eth0", etc.
 * based on the message content and category.
 *
 * Returns: true if entity was extracted, false otherwise
 */
bool entity_extract(const char *message, telem_category_t category,
                    char *out_entity, size_t out_len);

/**
 * entity_extract_from_raw - Extract entity from raw journal fields
 * @systemd_unit: _SYSTEMD_UNIT field value (may be NULL)
 * @unit: UNIT field value (may be NULL)
 * @message: Log message
 * @category: Detected category
 * @out_entity: Output buffer for entity string
 * @out_len: Size of output buffer
 *
 * First tries to extract from raw journal fields, then falls back
 * to pattern matching on the message.
 *
 * Returns: true if entity was extracted, false otherwise
 */
bool entity_extract_from_raw(const char *systemd_unit, const char *unit,
                             const char *message, telem_category_t category,
                             char *out_entity, size_t out_len);

#endif /* __TELEMETRYD_ENTITY_H__ */
