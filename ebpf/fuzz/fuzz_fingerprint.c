/*
 * fuzz_fingerprint.c - AFL++ harness for fingerprint_compute()
 *
 * Fuzzes SHA256 hashing + string normalization (date/time/number removal).
 * Input is split into category:entity:message fields.
 *
 * Uses __AFL_LOOP persistent mode for throughput.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "normalize/fingerprint.h"

#ifndef AFL_LOOP_COUNT
#define AFL_LOOP_COUNT 10000
#endif

#define MAX_INPUT_SIZE 4096

int main(void)
{
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

    unsigned char buf[MAX_INPUT_SIZE + 1];

#ifdef __AFL_LOOP
    while (__AFL_LOOP(AFL_LOOP_COUNT)) {
#else
    {
#endif
        ssize_t len = read(STDIN_FILENO, buf, MAX_INPUT_SIZE);
        if (len <= 0)
            continue;

        buf[len] = '\0';

        /*
         * Split input into three fields using first two ':' separators.
         * Format: "category:entity:message"
         * If no separators found, use entire input as message.
         */
        char *input = (char *)buf;
        char *category = input;
        char *entity = "";
        char *message = "";

        char *sep1 = strchr(input, ':');
        if (sep1) {
            *sep1 = '\0';
            entity = sep1 + 1;

            char *sep2 = strchr(entity, ':');
            if (sep2) {
                *sep2 = '\0';
                message = sep2 + 1;
            }
        } else {
            message = input;
            category = "";
        }

        /* Exercise both normalized and raw fingerprint computation */
        char fp[FINGERPRINT_LEN];

        fingerprint_compute(category, entity, message, fp);
        fingerprint_compute_raw(category, entity, message, fp);
    }

    return 0;
}
