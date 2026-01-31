/*
 * test_fingerprint.c - Test harness for fingerprint computation
 *
 * Tests: NULL inputs, empty strings, long messages, normalization
 * (date/time/number removal), determinism, and uniqueness.
 */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "normalize/fingerprint.h"

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

/* --- Test: basic fingerprint computation --- */
static void test_basic_compute(void)
{
    char fp[FINGERPRINT_LEN];
    fingerprint_compute("oom", "pid:1234", "out of memory", fp);

    assert(strlen(fp) == 16);

    /* Verify it's hex characters only */
    for (int i = 0; i < 16; i++) {
        char c = fp[i];
        assert((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
    }
}

/* --- Test: determinism --- */
static void test_determinism(void)
{
    char fp1[FINGERPRINT_LEN];
    char fp2[FINGERPRINT_LEN];

    fingerprint_compute("disk", "disk:sda", "I/O error on device", fp1);
    fingerprint_compute("disk", "disk:sda", "I/O error on device", fp2);

    assert(strcmp(fp1, fp2) == 0);
}

/* --- Test: different inputs produce different fingerprints --- */
static void test_uniqueness(void)
{
    char fp1[FINGERPRINT_LEN];
    char fp2[FINGERPRINT_LEN];
    char fp3[FINGERPRINT_LEN];

    fingerprint_compute("oom", "pid:1", "out of memory", fp1);
    fingerprint_compute("disk", "disk:sda", "I/O error", fp2);
    fingerprint_compute("auth", "user:root", "auth failure", fp3);

    assert(strcmp(fp1, fp2) != 0);
    assert(strcmp(fp1, fp3) != 0);
    assert(strcmp(fp2, fp3) != 0);
}

/* --- Test: date normalization --- */
static void test_date_normalization(void)
{
    char fp_with_date[FINGERPRINT_LEN];
    char fp_without_date[FINGERPRINT_LEN];

    /* Messages differing only by date should produce the same fingerprint */
    fingerprint_compute("oom", "pid:1", "out of memory 2024-01-15", fp_with_date);
    fingerprint_compute("oom", "pid:1", "out of memory 2025-06-30", fp_without_date);

    assert(strcmp(fp_with_date, fp_without_date) == 0);
}

/* --- Test: time normalization --- */
static void test_time_normalization(void)
{
    char fp1[FINGERPRINT_LEN];
    char fp2[FINGERPRINT_LEN];

    fingerprint_compute("net", "interface:eth0", "link down at 10:15:30", fp1);
    fingerprint_compute("net", "interface:eth0", "link down at 23:59:59", fp2);

    assert(strcmp(fp1, fp2) == 0);
}

/* --- Test: number normalization --- */
static void test_number_normalization(void)
{
    char fp1[FINGERPRINT_LEN];
    char fp2[FINGERPRINT_LEN];

    fingerprint_compute("oom", "", "killed process 1234 total-vm:500000kB", fp1);
    fingerprint_compute("oom", "", "killed process 5678 total-vm:900000kB", fp2);

    assert(strcmp(fp1, fp2) == 0);
}

/* --- Test: empty message --- */
static void test_empty_message(void)
{
    char fp[FINGERPRINT_LEN];
    fingerprint_compute("other", "", "", fp);

    assert(strlen(fp) == 16);
}

/* --- Test: empty category and entity --- */
static void test_empty_category_entity(void)
{
    char fp[FINGERPRINT_LEN];
    fingerprint_compute("", "", "some message", fp);

    assert(strlen(fp) == 16);
}

/* --- Test: NULL inputs --- */
static void test_null_inputs(void)
{
    char fp[FINGERPRINT_LEN];

    /* NULL category */
    fingerprint_compute(NULL, "entity", "message", fp);
    assert(strlen(fp) == 16);

    /* NULL entity */
    fingerprint_compute("cat", NULL, "message", fp);
    assert(strlen(fp) == 16);

    /* NULL message */
    fingerprint_compute("cat", "entity", NULL, fp);
    assert(strlen(fp) == 16);

    /* All NULL */
    fingerprint_compute(NULL, NULL, NULL, fp);
    assert(strlen(fp) == 16);
}

/* --- Test: very long message --- */
static void test_long_message(void)
{
    char long_msg[8192];
    memset(long_msg, 'X', sizeof(long_msg) - 1);
    long_msg[sizeof(long_msg) - 1] = '\0';

    char fp[FINGERPRINT_LEN];
    fingerprint_compute("other", "", long_msg, fp);

    assert(strlen(fp) == 16);
}

/* --- Test: raw fingerprint (no normalization) --- */
static void test_raw_fingerprint(void)
{
    char fp_norm[FINGERPRINT_LEN];
    char fp_raw[FINGERPRINT_LEN];

    fingerprint_compute("oom", "pid:1", "killed process 1234 at 10:15:30", fp_norm);
    fingerprint_compute_raw("oom", "pid:1", "killed process 1234 at 10:15:30", fp_raw);

    /* Raw should differ from normalized because numbers/times are preserved */
    assert(strcmp(fp_norm, fp_raw) != 0);
}

/* --- Test: case insensitivity --- */
static void test_case_insensitive(void)
{
    char fp_lower[FINGERPRINT_LEN];
    char fp_upper[FINGERPRINT_LEN];

    fingerprint_compute("oom", "pid:1", "out of memory killed process", fp_lower);
    fingerprint_compute("oom", "pid:1", "OUT OF MEMORY KILLED PROCESS", fp_upper);

    assert(strcmp(fp_lower, fp_upper) == 0);
}

/* --- Test: entity affects fingerprint --- */
static void test_entity_affects_fingerprint(void)
{
    char fp1[FINGERPRINT_LEN];
    char fp2[FINGERPRINT_LEN];

    fingerprint_compute("service", "service:nginx", "failed to start", fp1);
    fingerprint_compute("service", "service:apache2", "failed to start", fp2);

    assert(strcmp(fp1, fp2) != 0);
}

/* --- Test: category affects fingerprint --- */
static void test_category_affects_fingerprint(void)
{
    char fp1[FINGERPRINT_LEN];
    char fp2[FINGERPRINT_LEN];

    fingerprint_compute("disk", "disk:sda", "error occurred", fp1);
    fingerprint_compute("net", "disk:sda", "error occurred", fp2);

    assert(strcmp(fp1, fp2) != 0);
}

int main(void)
{
    printf("=== Fingerprint Test Harness ===\n");

    RUN_TEST(test_basic_compute);
    RUN_TEST(test_determinism);
    RUN_TEST(test_uniqueness);
    RUN_TEST(test_date_normalization);
    RUN_TEST(test_time_normalization);
    RUN_TEST(test_number_normalization);
    RUN_TEST(test_empty_message);
    RUN_TEST(test_empty_category_entity);
    RUN_TEST(test_null_inputs);
    RUN_TEST(test_long_message);
    RUN_TEST(test_raw_fingerprint);
    RUN_TEST(test_case_insensitive);
    RUN_TEST(test_entity_affects_fingerprint);
    RUN_TEST(test_category_affects_fingerprint);

    printf("\n%d/%d tests passed.\n", tests_passed, tests_run);
    return (tests_passed == tests_run) ? 0 : 1;
}
