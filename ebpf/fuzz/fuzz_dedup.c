/*
 * fuzz_dedup.c - AFL++ harness for dedup ring buffer
 *
 * Fuzzes dedup_check() with random fingerprint strings.
 * Tests hash table operations, ring buffer wrap-around,
 * and time-based expiration under adversarial input.
 *
 * Uses __AFL_LOOP persistent mode for throughput.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "normalize/dedup.h"

#ifndef AFL_LOOP_COUNT
#define AFL_LOOP_COUNT 10000
#endif

#define MAX_INPUT_SIZE 4096

int main(void)
{
    /*
     * Use a small capacity to increase likelihood of
     * triggering wrap-around and hash collisions.
     */
    struct dedup_window *w = dedup_create(60, 256);
    if (!w) {
        fprintf(stderr, "Failed to create dedup window\n");
        return 1;
    }

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
         * Interpret input as a sequence of 16-byte fingerprints.
         * Each 16-byte chunk is checked against the dedup window.
         */
        for (ssize_t i = 0; i + 16 <= len; i += 16) {
            char fp[17];
            memcpy(fp, buf + i, 16);
            fp[16] = '\0';

            bool dup = dedup_check(w, fp);
            (void)dup;
        }

        /* Also check any remaining bytes as a short fingerprint */
        if (len > 0) {
            char fp[17];
            size_t remaining = (size_t)len % 16;
            if (remaining == 0)
                remaining = 16;
            if (remaining > 16)
                remaining = 16;
            memcpy(fp, buf + (len - remaining), remaining);
            fp[remaining] = '\0';

            bool dup = dedup_check(w, fp);
            (void)dup;
        }
    }

    dedup_destroy(w);
    return 0;
}
