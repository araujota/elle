/*
 * fuzz_normalizer.c - AFL++ harness for the full normalization pipeline
 *
 * Fuzzes normalizer_process(): category detection → entity extraction →
 * fingerprint computation → deduplication. Exercises the complete path
 * that untrusted journal/kernel messages traverse.
 *
 * Uses __AFL_LOOP persistent mode for throughput.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "normalize/normalizer.h"
#include "telemetryd.h"

#ifndef AFL_LOOP_COUNT
#define AFL_LOOP_COUNT 10000
#endif

#define MAX_INPUT_SIZE 4096

int main(void)
{
    /* Create normalizer with 60-second dedup window */
    struct normalizer *n = normalizer_create(60);
    if (!n) {
        fprintf(stderr, "Failed to create normalizer\n");
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
         * Use first byte to select source type,
         * second byte for priority, rest is message.
         */
        telem_source_t source = TELEM_SRC_JOURNAL;
        int priority = 3;
        const char *message = (const char *)buf;

        if (len >= 3) {
            source = buf[0] % 6;     /* 6 source types */
            priority = buf[1] % 8;   /* 0-7 journal priority */
            message = (const char *)buf + 2;
        }

        struct telem_event evt;
        memset(&evt, 0, sizeof(evt));

        bool emitted = normalizer_process(
            n, source, priority,
            message,
            NULL, /* systemd_unit */
            NULL, /* unit */
            0,    /* auto-timestamp */
            &evt
        );
        (void)emitted;
    }

    normalizer_destroy(n);
    return 0;
}
