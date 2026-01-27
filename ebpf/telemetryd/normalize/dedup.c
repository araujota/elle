// SPDX-License-Identifier: MIT
/**
 * dedup.c - Ring buffer deduplication
 *
 * Implements a fixed-size ring buffer for fingerprint-based event
 * deduplication. Uses a hash table for O(1) lookup and a ring buffer
 * for efficient expiration of old entries.
 *
 * Memory usage: ~2MB for 100K entries
 * - Ring buffer: 100K * 16 bytes = 1.6MB
 * - Hash table: 128K buckets * 4 bytes = 0.5MB
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "dedup.h"

/* ==========================================================================
 * Data Structures
 * ========================================================================== */

/* Entry in the ring buffer */
struct dedup_entry {
    uint64_t fp_hash;       /* First 8 bytes of fingerprint as uint64 */
    uint64_t seen_ns;       /* Monotonic timestamp when seen */
};

/* Hash table bucket (linked list for collisions) */
struct hash_bucket {
    size_t ring_idx;        /* Index into ring buffer */
    struct hash_bucket *next;
};

/* Dedup window state */
struct dedup_window {
    /* Ring buffer */
    struct dedup_entry *ring;
    size_t capacity;
    size_t head;            /* Next write position */
    size_t count;           /* Current entries */

    /* Hash table */
    struct hash_bucket **buckets;
    size_t num_buckets;

    /* Configuration */
    int window_sec;
    uint64_t window_ns;

    /* Statistics */
    uint64_t total_checked;
    uint64_t duplicates;
};

/* ==========================================================================
 * Helper Functions
 * ========================================================================== */

static inline uint64_t get_monotonic_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* Convert first 8 bytes of hex fingerprint to uint64 */
static uint64_t fingerprint_to_hash(const char *fp)
{
    uint64_t hash = 0;
    for (int i = 0; i < 16 && fp[i]; i++) {
        char c = fp[i];
        uint64_t nibble;
        if (c >= '0' && c <= '9')
            nibble = c - '0';
        else if (c >= 'a' && c <= 'f')
            nibble = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F')
            nibble = c - 'A' + 10;
        else
            continue;
        hash = (hash << 4) | nibble;
    }
    return hash;
}

static inline size_t hash_to_bucket(uint64_t hash, size_t num_buckets)
{
    return hash % num_buckets;
}

/* ==========================================================================
 * Hash Table Operations
 * ========================================================================== */

static void hashtable_insert(struct dedup_window *w, uint64_t fp_hash, size_t ring_idx)
{
    size_t bucket_idx = hash_to_bucket(fp_hash, w->num_buckets);

    struct hash_bucket *entry = malloc(sizeof(*entry));
    if (!entry)
        return;  /* Best effort - dedup still works via ring scan */

    entry->ring_idx = ring_idx;
    entry->next = w->buckets[bucket_idx];
    w->buckets[bucket_idx] = entry;
}

static bool hashtable_find(struct dedup_window *w, uint64_t fp_hash, uint64_t now_ns)
{
    size_t bucket_idx = hash_to_bucket(fp_hash, w->num_buckets);
    struct hash_bucket *entry = w->buckets[bucket_idx];
    struct hash_bucket *prev = NULL;

    while (entry) {
        struct dedup_entry *ring_entry = &w->ring[entry->ring_idx];

        /* Check if this entry matches and is still valid */
        if (ring_entry->fp_hash == fp_hash) {
            if (now_ns - ring_entry->seen_ns < w->window_ns) {
                return true;  /* Found valid duplicate */
            }
            /* Expired - remove from hash table */
            if (prev)
                prev->next = entry->next;
            else
                w->buckets[bucket_idx] = entry->next;
            struct hash_bucket *to_free = entry;
            entry = entry->next;
            free(to_free);
            continue;
        }

        prev = entry;
        entry = entry->next;
    }

    return false;
}

static void hashtable_remove_idx(struct dedup_window *w, size_t ring_idx)
{
    uint64_t fp_hash = w->ring[ring_idx].fp_hash;
    size_t bucket_idx = hash_to_bucket(fp_hash, w->num_buckets);
    struct hash_bucket *entry = w->buckets[bucket_idx];
    struct hash_bucket *prev = NULL;

    while (entry) {
        if (entry->ring_idx == ring_idx) {
            if (prev)
                prev->next = entry->next;
            else
                w->buckets[bucket_idx] = entry->next;
            free(entry);
            return;
        }
        prev = entry;
        entry = entry->next;
    }
}

/* ==========================================================================
 * Public API
 * ========================================================================== */

struct dedup_window *dedup_create(int window_sec, size_t capacity)
{
    struct dedup_window *w = calloc(1, sizeof(*w));
    if (!w)
        return NULL;

    w->capacity = capacity > 0 ? capacity : DEDUP_DEFAULT_CAPACITY;
    w->window_sec = window_sec;
    w->window_ns = (uint64_t)window_sec * 1000000000ULL;

    /* Allocate ring buffer */
    w->ring = calloc(w->capacity, sizeof(struct dedup_entry));
    if (!w->ring) {
        free(w);
        return NULL;
    }

    /* Allocate hash table (use ~1.3x capacity for good load factor) */
    w->num_buckets = w->capacity + (w->capacity >> 2);
    w->buckets = calloc(w->num_buckets, sizeof(struct hash_bucket *));
    if (!w->buckets) {
        free(w->ring);
        free(w);
        return NULL;
    }

    return w;
}

void dedup_destroy(struct dedup_window *w)
{
    if (!w)
        return;

    /* Free hash table entries */
    for (size_t i = 0; i < w->num_buckets; i++) {
        struct hash_bucket *entry = w->buckets[i];
        while (entry) {
            struct hash_bucket *next = entry->next;
            free(entry);
            entry = next;
        }
    }

    free(w->buckets);
    free(w->ring);
    free(w);
}

bool dedup_check(struct dedup_window *w, const char *fingerprint)
{
    if (!w || !fingerprint)
        return false;

    w->total_checked++;
    uint64_t now_ns = get_monotonic_ns();
    uint64_t fp_hash = fingerprint_to_hash(fingerprint);

    /* Check if duplicate exists in hash table */
    if (hashtable_find(w, fp_hash, now_ns)) {
        w->duplicates++;
        return true;
    }

    /* Not a duplicate - add to ring buffer */

    /* If ring is full, remove oldest entry */
    if (w->count >= w->capacity) {
        hashtable_remove_idx(w, w->head);
    } else {
        w->count++;
    }

    /* Insert new entry */
    w->ring[w->head].fp_hash = fp_hash;
    w->ring[w->head].seen_ns = now_ns;
    hashtable_insert(w, fp_hash, w->head);

    /* Advance head */
    w->head = (w->head + 1) % w->capacity;

    return false;
}

void dedup_get_stats(struct dedup_window *w,
                     uint64_t *out_total,
                     uint64_t *out_dupes,
                     size_t *out_entries)
{
    if (!w)
        return;

    if (out_total)
        *out_total = w->total_checked;
    if (out_dupes)
        *out_dupes = w->duplicates;
    if (out_entries)
        *out_entries = w->count;
}

void dedup_reset_stats(struct dedup_window *w)
{
    if (!w)
        return;

    w->total_checked = 0;
    w->duplicates = 0;
}
