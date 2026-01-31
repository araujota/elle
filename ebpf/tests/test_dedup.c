/*
 * test_dedup.c - Test harness for the deduplication ring buffer
 *
 * Tests: create/destroy, duplicate detection, ring buffer wrap-around,
 * hash collision resilience, statistics, and capacity limits.
 */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "normalize/dedup.h"

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

/* --- Test: create and destroy without use --- */
static void test_create_destroy(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);
    dedup_destroy(w);
}

/* --- Test: first occurrence is not a duplicate --- */
static void test_first_not_duplicate(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    bool dup = dedup_check(w, "a1b2c3d4e5f6g7h8");
    assert(!dup);

    dedup_destroy(w);
}

/* --- Test: second occurrence within window is duplicate --- */
static void test_second_is_duplicate(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    bool first = dedup_check(w, "a1b2c3d4e5f6g7h8");
    assert(!first);

    bool second = dedup_check(w, "a1b2c3d4e5f6g7h8");
    assert(second);

    dedup_destroy(w);
}

/* --- Test: different fingerprints are not duplicates --- */
static void test_different_fingerprints(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    bool a = dedup_check(w, "aaaaaaaaaaaaaaaa");
    assert(!a);

    bool b = dedup_check(w, "bbbbbbbbbbbbbbbb");
    assert(!b);

    bool c = dedup_check(w, "cccccccccccccccc");
    assert(!c);

    /* Originals should still be duplicates */
    assert(dedup_check(w, "aaaaaaaaaaaaaaaa"));
    assert(dedup_check(w, "bbbbbbbbbbbbbbbb"));
    assert(dedup_check(w, "cccccccccccccccc"));

    dedup_destroy(w);
}

/* --- Test: ring buffer wrap-around with small capacity --- */
static void test_ring_buffer_wraparound(void)
{
    /* Use a very small capacity to force wrap-around */
    struct dedup_window *w = dedup_create(3600, 16);
    assert(w != NULL);

    char fp[17];

    /* Fill the ring buffer beyond capacity */
    for (int i = 0; i < 32; i++) {
        snprintf(fp, sizeof(fp), "%016x", i);
        dedup_check(w, fp);
    }

    /* Recent entries should still be detected as duplicates */
    snprintf(fp, sizeof(fp), "%016x", 31);
    assert(dedup_check(w, fp));

    /* Old entries (evicted by wrap-around) should no longer be duplicates */
    snprintf(fp, sizeof(fp), "%016x", 0);
    bool old_dup = dedup_check(w, fp);
    /* After wrap-around, entry 0 was evicted so it should be treated as new */
    assert(!old_dup);

    dedup_destroy(w);
}

/* --- Test: hash collision resilience --- */
static void test_hash_collisions(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    /* Insert many fingerprints that may collide in hash table buckets */
    char fp[17];
    for (int i = 0; i < 1000; i++) {
        snprintf(fp, sizeof(fp), "%016x", i);
        bool dup = dedup_check(w, fp);
        assert(!dup); /* All should be unique */
    }

    /* Verify all are still tracked as duplicates */
    for (int i = 0; i < 1000; i++) {
        snprintf(fp, sizeof(fp), "%016x", i);
        bool dup = dedup_check(w, fp);
        assert(dup); /* All should be duplicates now */
    }

    dedup_destroy(w);
}

/* --- Test: statistics tracking --- */
static void test_statistics(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    dedup_check(w, "aaaaaaaaaaaaaaaa"); /* new */
    dedup_check(w, "bbbbbbbbbbbbbbbb"); /* new */
    dedup_check(w, "aaaaaaaaaaaaaaaa"); /* dup */
    dedup_check(w, "cccccccccccccccc"); /* new */
    dedup_check(w, "bbbbbbbbbbbbbbbb"); /* dup */

    uint64_t total = 0, dupes = 0;
    size_t entries = 0;
    dedup_get_stats(w, &total, &dupes, &entries);

    assert(total == 5);
    assert(dupes == 2);
    assert(entries == 3);

    dedup_destroy(w);
}

/* --- Test: statistics reset --- */
static void test_stats_reset(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    dedup_check(w, "aaaaaaaaaaaaaaaa");
    dedup_check(w, "aaaaaaaaaaaaaaaa");

    uint64_t total = 0, dupes = 0;
    size_t entries = 0;
    dedup_get_stats(w, &total, &dupes, &entries);
    assert(total == 2);
    assert(dupes == 1);

    dedup_reset_stats(w);
    dedup_get_stats(w, &total, &dupes, &entries);
    assert(total == 0);
    assert(dupes == 0);
    /* entries may or may not reset depending on implementation */

    dedup_destroy(w);
}

/* --- Test: zero-length fingerprint --- */
static void test_empty_fingerprint(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    /* Empty string fingerprint should not crash */
    bool dup1 = dedup_check(w, "");
    assert(!dup1);

    bool dup2 = dedup_check(w, "");
    assert(dup2);

    dedup_destroy(w);
}

/* --- Test: large batch insertion --- */
static void test_large_batch(void)
{
    struct dedup_window *w = dedup_create(60, 0);
    assert(w != NULL);

    char fp[17];
    /* Insert 10K unique fingerprints */
    for (int i = 0; i < 10000; i++) {
        snprintf(fp, sizeof(fp), "%016x", i);
        dedup_check(w, fp);
    }

    uint64_t total = 0, dupes = 0;
    size_t entries = 0;
    dedup_get_stats(w, &total, &dupes, &entries);
    assert(total == 10000);
    assert(dupes == 0);

    dedup_destroy(w);
}

int main(void)
{
    printf("=== Dedup Test Harness ===\n");

    RUN_TEST(test_create_destroy);
    RUN_TEST(test_first_not_duplicate);
    RUN_TEST(test_second_is_duplicate);
    RUN_TEST(test_different_fingerprints);
    RUN_TEST(test_ring_buffer_wraparound);
    RUN_TEST(test_hash_collisions);
    RUN_TEST(test_statistics);
    RUN_TEST(test_stats_reset);
    RUN_TEST(test_empty_fingerprint);
    RUN_TEST(test_large_batch);

    printf("\n%d/%d tests passed.\n", tests_passed, tests_run);
    return (tests_passed == tests_run) ? 0 : 1;
}
