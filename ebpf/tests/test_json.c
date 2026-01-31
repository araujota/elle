/*
 * test_json.c - Test harness for JSON serialization
 *
 * Tests: json_t reference counting, telem_event serialization,
 * field presence/absence, and memory management with jansson.
 */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <jansson.h>

#include "json.h"
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

/* Helper: create a populated telem_event */
static struct telem_event make_event(telem_category_t cat,
                                     telem_severity_t sev,
                                     telem_source_t src,
                                     const char *message,
                                     const char *entity,
                                     const char *fingerprint)
{
    struct telem_event evt;
    memset(&evt, 0, sizeof(evt));

    evt.ts_ns = 1706300000000000000ULL;
    evt.category = cat;
    evt.severity = sev;
    evt.source = src;

    if (message)
        snprintf(evt.message, sizeof(evt.message), "%s", message);
    if (entity) {
        snprintf(evt.entity, sizeof(evt.entity), "%s", entity);
        evt.has_entity = true;
    }
    if (fingerprint)
        snprintf(evt.fingerprint, sizeof(evt.fingerprint), "%s", fingerprint);

    return evt;
}

/* --- Test: basic event serialization --- */
static void test_basic_serialization(void)
{
    struct telem_event evt = make_event(
        TELEM_CAT_OOM, TELEM_SEV_ERROR, TELEM_SRC_JOURNAL,
        "Out of memory: killed process python3",
        "pid:1234",
        "a1b2c3d4e5f6g7h8"
    );

    char *json = telem_json_event(&evt);
    assert(json != NULL);
    assert(strlen(json) > 0);

    /* Verify key fields are present in JSON output */
    assert(strstr(json, "\"category\"") != NULL);
    assert(strstr(json, "\"oom\"") != NULL);
    assert(strstr(json, "\"severity\"") != NULL);
    assert(strstr(json, "\"error\"") != NULL);
    assert(strstr(json, "\"source\"") != NULL);
    assert(strstr(json, "\"journal\"") != NULL);
    assert(strstr(json, "\"message\"") != NULL);
    assert(strstr(json, "\"entity\"") != NULL);
    assert(strstr(json, "\"fingerprint\"") != NULL);
    assert(strstr(json, "\"ts\"") != NULL);

    free(json);
}

/* --- Test: JSON is valid (parseable) --- */
static void test_json_parseable(void)
{
    struct telem_event evt = make_event(
        TELEM_CAT_DISK, TELEM_SEV_WARNING, TELEM_SRC_KERNEL,
        "I/O error, dev sda, sector 12345",
        "disk:sda",
        "b2c3d4e5f6g7h8a1"
    );

    char *json_str = telem_json_event(&evt);
    assert(json_str != NULL);

    /* Parse the JSON to verify it's valid */
    json_error_t error;
    json_t *root = json_loads(json_str, 0, &error);
    assert(root != NULL);
    assert(json_is_object(root));

    /* Verify typed fields */
    json_t *ts = json_object_get(root, "ts");
    assert(ts != NULL);
    assert(json_is_integer(ts));

    json_t *cat = json_object_get(root, "category");
    assert(cat != NULL);
    assert(json_is_string(cat));
    assert(strcmp(json_string_value(cat), "disk") == 0);

    json_t *sev = json_object_get(root, "severity");
    assert(sev != NULL);
    assert(json_is_string(sev));

    json_decref(root);
    free(json_str);
}

/* --- Test: event without entity --- */
static void test_no_entity(void)
{
    struct telem_event evt = make_event(
        TELEM_CAT_OTHER, TELEM_SEV_INFO, TELEM_SRC_JOURNAL,
        "Some generic log message",
        NULL, /* no entity */
        "c3d4e5f6g7h8a1b2"
    );

    char *json_str = telem_json_event(&evt);
    assert(json_str != NULL);

    /* Parse and verify entity is absent or null */
    json_error_t error;
    json_t *root = json_loads(json_str, 0, &error);
    assert(root != NULL);

    json_t *entity = json_object_get(root, "entity");
    /* Entity may be absent or empty string depending on implementation */
    if (entity != NULL && json_is_string(entity)) {
        assert(strlen(json_string_value(entity)) == 0);
    }

    json_decref(root);
    free(json_str);
}

/* --- Test: all category strings --- */
static void test_all_categories(void)
{
    telem_category_t cats[] = {
        TELEM_CAT_OOM, TELEM_CAT_DISK, TELEM_CAT_NET,
        TELEM_CAT_AUTH, TELEM_CAT_SERVICE, TELEM_CAT_THERMAL,
        TELEM_CAT_SMART, TELEM_CAT_FS, TELEM_CAT_PKG,
        TELEM_CAT_DOCKER, TELEM_CAT_KERNEL_PANIC, TELEM_CAT_OTHER,
    };

    for (size_t i = 0; i < sizeof(cats) / sizeof(cats[0]); i++) {
        struct telem_event evt = make_event(
            cats[i], TELEM_SEV_INFO, TELEM_SRC_JOURNAL,
            "test message", NULL, "0000000000000000"
        );

        char *json_str = telem_json_event(&evt);
        assert(json_str != NULL);

        json_error_t error;
        json_t *root = json_loads(json_str, 0, &error);
        assert(root != NULL);

        json_t *cat = json_object_get(root, "category");
        assert(cat != NULL);
        assert(json_is_string(cat));
        assert(strlen(json_string_value(cat)) > 0);

        json_decref(root);
        free(json_str);
    }
}

/* --- Test: all severity strings --- */
static void test_all_severities(void)
{
    telem_severity_t sevs[] = {
        TELEM_SEV_DEBUG, TELEM_SEV_INFO, TELEM_SEV_NOTICE,
        TELEM_SEV_WARNING, TELEM_SEV_ERROR, TELEM_SEV_CRITICAL,
    };

    for (size_t i = 0; i < sizeof(sevs) / sizeof(sevs[0]); i++) {
        struct telem_event evt = make_event(
            TELEM_CAT_OTHER, sevs[i], TELEM_SRC_JOURNAL,
            "test message", NULL, "0000000000000000"
        );

        char *json_str = telem_json_event(&evt);
        assert(json_str != NULL);

        json_error_t error;
        json_t *root = json_loads(json_str, 0, &error);
        assert(root != NULL);

        json_t *sev = json_object_get(root, "severity");
        assert(sev != NULL);
        assert(json_is_string(sev));
        assert(strlen(json_string_value(sev)) > 0);

        json_decref(root);
        free(json_str);
    }
}

/* --- Test: long message truncation --- */
static void test_long_message(void)
{
    char long_msg[TELEM_MAX_MESSAGE + 100];
    memset(long_msg, 'A', sizeof(long_msg) - 1);
    long_msg[sizeof(long_msg) - 1] = '\0';

    struct telem_event evt = make_event(
        TELEM_CAT_OTHER, TELEM_SEV_INFO, TELEM_SRC_JOURNAL,
        long_msg, NULL, "0000000000000000"
    );

    char *json_str = telem_json_event(&evt);
    assert(json_str != NULL);

    /* Should not crash and should produce valid JSON */
    json_error_t error;
    json_t *root = json_loads(json_str, 0, &error);
    assert(root != NULL);

    json_decref(root);
    free(json_str);
}

/* --- Test: special characters in message --- */
static void test_special_characters(void)
{
    struct telem_event evt = make_event(
        TELEM_CAT_OTHER, TELEM_SEV_INFO, TELEM_SRC_JOURNAL,
        "Message with \"quotes\" and \\backslashes\\ and \nnewlines\tand\ttabs",
        NULL, "0000000000000000"
    );

    char *json_str = telem_json_event(&evt);
    assert(json_str != NULL);

    /* Special characters must be escaped for valid JSON */
    json_error_t error;
    json_t *root = json_loads(json_str, 0, &error);
    assert(root != NULL);

    json_decref(root);
    free(json_str);
}

/* --- Test: json_t reference counting with repeated serialization --- */
static void test_repeated_serialization(void)
{
    struct telem_event evt = make_event(
        TELEM_CAT_NET, TELEM_SEV_WARNING, TELEM_SRC_PROBE,
        "connection refused to 10.0.0.1:5432",
        "port:5432",
        "d4e5f6g7h8a1b2c3"
    );

    /* Serialize the same event multiple times to detect ref count leaks */
    for (int i = 0; i < 100; i++) {
        char *json_str = telem_json_event(&evt);
        assert(json_str != NULL);
        free(json_str);
    }
}

int main(void)
{
    printf("=== JSON Serialization Test Harness ===\n");

    RUN_TEST(test_basic_serialization);
    RUN_TEST(test_json_parseable);
    RUN_TEST(test_no_entity);
    RUN_TEST(test_all_categories);
    RUN_TEST(test_all_severities);
    RUN_TEST(test_long_message);
    RUN_TEST(test_special_characters);
    RUN_TEST(test_repeated_serialization);

    printf("\n%d/%d tests passed.\n", tests_passed, tests_run);
    return (tests_passed == tests_run) ? 0 : 1;
}
