// SPDX-License-Identifier: MIT
/**
 * fingerprint.c - SHA256-based event fingerprinting
 *
 * Implements the same fingerprinting algorithm as Python:
 * TelemetryEvent.compute_fingerprint()
 *
 * Algorithm:
 * 1. Normalize message (lowercase, remove dates/times/numbers)
 * 2. Build content string: "category:entity:normalized_message"
 * 3. Compute SHA256 hash
 * 4. Return first 16 hex characters
 */

#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <ctype.h>
#include <openssl/sha.h>

#include "fingerprint.h"

/* ==========================================================================
 * Message Normalization
 * ========================================================================== */

/**
 * normalize_message - Normalize message for fingerprinting
 * @message: Input message
 * @out: Output buffer
 * @out_len: Output buffer size
 *
 * Normalizes message by:
 * - Converting to lowercase
 * - Removing dates (YYYY-MM-DD)
 * - Removing times (HH:MM:SS)
 * - Removing numbers
 */
static void normalize_message(const char *message, char *out, size_t out_len)
{
    if (!message || !out || out_len < 2) {
        if (out && out_len > 0)
            out[0] = '\0';
        return;
    }

    size_t j = 0;
    size_t len = strlen(message);

    for (size_t i = 0; i < len && j < out_len - 1; i++) {
        char c = message[i];

        /* Check for date pattern: YYYY-MM-DD */
        if (i + 9 < len &&
            isdigit(message[i]) && isdigit(message[i+1]) &&
            isdigit(message[i+2]) && isdigit(message[i+3]) &&
            message[i+4] == '-' &&
            isdigit(message[i+5]) && isdigit(message[i+6]) &&
            message[i+7] == '-' &&
            isdigit(message[i+8]) && isdigit(message[i+9])) {
            i += 9;  /* Skip date */
            continue;
        }

        /* Check for time pattern: HH:MM:SS */
        if (i + 7 < len &&
            isdigit(message[i]) && isdigit(message[i+1]) &&
            message[i+2] == ':' &&
            isdigit(message[i+3]) && isdigit(message[i+4]) &&
            message[i+5] == ':' &&
            isdigit(message[i+6]) && isdigit(message[i+7])) {
            i += 7;  /* Skip time */
            continue;
        }

        /* Skip standalone numbers (surrounded by non-alphanumeric) */
        if (isdigit(c)) {
            /* Check if this is a standalone number */
            bool is_standalone = (i == 0 || !isalnum(message[i-1]));
            if (is_standalone) {
                /* Skip all digits */
                while (i < len && isdigit(message[i]))
                    i++;
                i--;  /* Will be incremented by for loop */
                continue;
            }
        }

        /* Convert to lowercase and copy */
        out[j++] = (char)tolower((unsigned char)c);
    }

    out[j] = '\0';
}

/* ==========================================================================
 * SHA256 Hashing
 * ========================================================================== */

static void sha256_to_hex16(const unsigned char *hash, char *out)
{
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 8; i++) {  /* Only first 8 bytes = 16 hex chars */
        out[(size_t)i * 2] = hex[(hash[i] >> 4) & 0xf];
        out[(size_t)i * 2 + 1] = hex[hash[i] & 0xf];
    }
    out[16] = '\0';
}

/* ==========================================================================
 * Public API
 * ========================================================================== */

void fingerprint_compute(const char *category, const char *entity,
                         const char *message, char *out_fp)
{
    char normalized[2048];
    char content[4096];
    unsigned char hash[SHA256_DIGEST_LENGTH];

    if (!out_fp)
        return;

    /* Normalize message */
    normalize_message(message, normalized, sizeof(normalized));

    /* Build content string */
    snprintf(content, sizeof(content), "%s:%s:%s",
             category ? category : "",
             entity ? entity : "",
             normalized);

    /* Compute SHA256 */
    SHA256((unsigned char *)content, strlen(content), hash);

    /* Convert to hex (first 16 chars) */
    sha256_to_hex16(hash, out_fp);
}

void fingerprint_compute_raw(const char *category, const char *entity,
                             const char *message, char *out_fp)
{
    char content[4096];
    unsigned char hash[SHA256_DIGEST_LENGTH];

    if (!out_fp)
        return;

    /* Build content string (no normalization) */
    snprintf(content, sizeof(content), "%s:%s:%s",
             category ? category : "",
             entity ? entity : "",
             message ? message : "");

    /* Compute SHA256 */
    SHA256((unsigned char *)content, strlen(content), hash);

    /* Convert to hex (first 16 chars) */
    sha256_to_hex16(hash, out_fp);
}
