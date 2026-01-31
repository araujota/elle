/*
 * fuzz_category.c - AFL++ harness for category_detect()
 *
 * Highest-value fuzz target: PCRE2 regex matching on untrusted input.
 * Tests 58 compiled patterns against arbitrary byte sequences.
 *
 * Uses __AFL_LOOP persistent mode for throughput.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "normalize/category.h"
#include "telemetryd.h"

/* AFL persistent mode loop count */
#ifndef AFL_LOOP_COUNT
#define AFL_LOOP_COUNT 10000
#endif

/* Maximum input size to prevent excessive memory use */
#define MAX_INPUT_SIZE 4096

int main(void)
{
    /* One-time initialization of PCRE2 patterns */
    if (category_init() != 0) {
        fprintf(stderr, "Failed to initialize category patterns\n");
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
        /* Read input from stdin */
        ssize_t len = read(STDIN_FILENO, buf, MAX_INPUT_SIZE);
        if (len <= 0)
            continue;

        /* Null-terminate for string functions */
        buf[len] = '\0';

        /* Exercise category detection */
        telem_category_t cat = category_detect((const char *)buf);
        (void)cat;

        /* Also exercise the string variant */
        const char *cat_str = category_detect_str((const char *)buf);
        (void)cat_str;
    }

    category_cleanup();
    return 0;
}
