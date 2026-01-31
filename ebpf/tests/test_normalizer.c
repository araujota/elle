/*
 * test_normalizer.c - Test harness for the normalization pipeline
 *
 * Tests: create/process/destroy lifecycle, category detection through
 * the pipeline, deduplication behavior, and statistics tracking.
 */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "normalize/normalizer.h"
#include "telemetryd.h"

static int tests_passed = 0;
static int tests_run = 0;

#define RUN_TEST(fn)                              \
    do {                                          \
        tests_run++;                              \
        printf("  [%d] %s... ", tests_run, #fn);  \
        fn();                                     \
        tests_passed++;                           \
        printf("OK\n");                           \
    } while (0)

/* --- Test: create and destroy without processing --- */
static void test_create_destroy(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);
    normalizer_destroy(n);
}

/* --- Test: process a single OOM event --- */
static void test_process_oom_event(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;
    memset(&evt, 0, sizeof(evt));

    bool emitted = normalizer_process(
        n,
        TELEM_SRC_JOURNAL,
        3, /* priority: error */
        "Out of memory: Killed process 1234 (python3) total-vm:1024000kB",
        "system.slice",
        NULL,
        0, /* auto-timestamp */
        &evt
    );

    assert(emitted);
    assert(evt.category == TELEM_CAT_OOM);
    assert(evt.severity == TELEM_SEV_ERROR);
    assert(strlen(evt.fingerprint) == 16);
    assert(strlen(evt.message) > 0);

    normalizer_destroy(n);
}

/* --- Test: deduplication within window --- */
static void test_dedup_within_window(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;
    const char *msg = "Out of memory: Killed process 5678 (java)";

    /* First event should be emitted */
    memset(&evt, 0, sizeof(evt));
    bool first = normalizer_process(n, TELEM_SRC_JOURNAL, 3, msg,
                                    NULL, NULL, 0, &evt);
    assert(first);

    /* Identical event should be deduplicated */
    memset(&evt, 0, sizeof(evt));
    bool second = normalizer_process(n, TELEM_SRC_JOURNAL, 3, msg,
                                     NULL, NULL, 0, &evt);
    assert(!second);

    normalizer_destroy(n);
}

/* --- Test: different messages are not deduped --- */
static void test_different_messages_not_deduped(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;

    memset(&evt, 0, sizeof(evt));
    bool first = normalizer_process(
        n, TELEM_SRC_JOURNAL, 3,
        "link up: eth0",
        NULL, NULL, 0, &evt
    );
    assert(first);

    memset(&evt, 0, sizeof(evt));
    bool second = normalizer_process(
        n, TELEM_SRC_JOURNAL, 3,
        "I/O error, dev sda, sector 12345",
        NULL, NULL, 0, &evt
    );
    assert(second);

    normalizer_destroy(n);
}

/* --- Test: multiple categories --- */
static void test_multiple_categories(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;
    const struct {
        const char *message;
        telem_category_t expected_cat;
    } cases[] = {
        {"Out of memory: oom-killer invoked", TELEM_CAT_OOM},
        {"I/O error, dev sda, sector 999", TELEM_CAT_DISK},
        {"authentication failure; user=root", TELEM_CAT_AUTH},
        {"Started nginx.service", TELEM_CAT_SERVICE},
        {"Kernel panic - not syncing: Fatal exception", TELEM_CAT_KERNEL_PANIC},
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        memset(&evt, 0, sizeof(evt));
        bool emitted = normalizer_process(
            n, TELEM_SRC_JOURNAL, 3,
            cases[i].message, NULL, NULL, 0, &evt
        );
        assert(emitted);
        assert(evt.category == cases[i].expected_cat);
    }

    normalizer_destroy(n);
}

/* --- Test: statistics tracking --- */
static void test_statistics(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;
    memset(&evt, 0, sizeof(evt));

    /* Process 3 events: 2 unique, 1 duplicate */
    normalizer_process(n, TELEM_SRC_JOURNAL, 3,
                       "Out of memory: killed process 1", NULL, NULL, 0, &evt);
    normalizer_process(n, TELEM_SRC_JOURNAL, 3,
                       "I/O error, dev sda", NULL, NULL, 0, &evt);
    normalizer_process(n, TELEM_SRC_JOURNAL, 3,
                       "Out of memory: killed process 1", NULL, NULL, 0, &evt);

    uint64_t processed = 0, deduplicated = 0, by_category = 0;
    normalizer_get_stats(n, &processed, &deduplicated, &by_category);

    assert(processed == 3);
    assert(deduplicated == 1);

    normalizer_destroy(n);
}

/* --- Test: prenormalized event path --- */
static void test_prenormalized(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;
    memset(&evt, 0, sizeof(evt));

    bool emitted = normalizer_process_prenormalized(
        n,
        TELEM_SRC_EBPF,
        TELEM_SEV_ERROR,
        TELEM_CAT_OOM,
        "eBPF detected OOM kill for cgroup /docker/abc123",
        "container:abc123",
        0,
        &evt
    );

    assert(emitted);
    assert(evt.category == TELEM_CAT_OOM);
    assert(evt.source == TELEM_SRC_EBPF);
    assert(evt.severity == TELEM_SEV_ERROR);

    normalizer_destroy(n);
}

/* --- Test: NULL message handling --- */
static void test_null_message(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;
    memset(&evt, 0, sizeof(evt));

    /* NULL message should not crash; behavior is implementation-defined */
    bool emitted = normalizer_process(
        n, TELEM_SRC_JOURNAL, 3,
        NULL, NULL, NULL, 0, &evt
    );
    /* Just verify no crash; don't assert on result */
    (void)emitted;

    normalizer_destroy(n);
}

/* --- Test: empty message --- */
static void test_empty_message(void)
{
    struct normalizer *n = normalizer_create(60);
    assert(n != NULL);

    struct telem_event evt;
    memset(&evt, 0, sizeof(evt));

    bool emitted = normalizer_process(
        n, TELEM_SRC_JOURNAL, 6,
        "", NULL, NULL, 0, &evt
    );
    /* Empty message should be classified as OTHER */
    if (emitted) {
        assert(evt.category == TELEM_CAT_OTHER);
    }

    normalizer_destroy(n);
}

int main(void)
{
    printf("=== Normalizer Test Harness ===\n");

    RUN_TEST(test_create_destroy);
    RUN_TEST(test_process_oom_event);
    RUN_TEST(test_dedup_within_window);
    RUN_TEST(test_different_messages_not_deduped);
    RUN_TEST(test_multiple_categories);
    RUN_TEST(test_statistics);
    RUN_TEST(test_prenormalized);
    RUN_TEST(test_null_message);
    RUN_TEST(test_empty_message);

    printf("\n%d/%d tests passed.\n", tests_passed, tests_run);
    return (tests_passed == tests_run) ? 0 : 1;
}
