// SPDX-License-Identifier: MIT
/**
 * topproc.c - Top process and zombie monitoring
 *
 * Scans /proc/[pid]/stat for CPU/memory usage and zombie state.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <ctype.h>

#include "topproc.h"
#include "../json.h"

struct topproc_probe_ctx *topproc_probe_create_ctx(const struct telemetryd_config *cfg)
{
    struct topproc_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->zombie_threshold = 10;
    ctx->process_count_threshold = 1000;
    ctx->cpu_hog_threshold = 80.0;
    ctx->prev_total_jiffies = 0;
    ctx->num_prev = 0;

    (void)cfg;
    return ctx;
}

void topproc_probe_destroy_ctx(struct topproc_probe_ctx *ctx)
{
    free(ctx);
}

static unsigned long get_total_jiffies(void)
{
    FILE *f;
    unsigned long user, nice, system, idle, iowait, irq, softirq, steal;
    unsigned long total = 0;

    f = fopen("/proc/stat", "r");
    if (!f)
        return 0;

    if (fscanf(f, "cpu %lu %lu %lu %lu %lu %lu %lu %lu",
               &user, &nice, &system, &idle, &iowait, &irq, &softirq, &steal) == 8) {
        total = user + nice + system + idle + iowait + irq + softirq + steal;
    }

    fclose(f);
    return total;
}

static int read_proc_stat(int pid, struct proc_stats *ps)
{
    FILE *f;
    char path[64];
    char buf[1024];
    char *comm_start, *comm_end;

    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    f = fopen(path, "r");
    if (!f)
        return -1;

    if (!fgets(buf, sizeof(buf), f)) {
        fclose(f);
        return -1;
    }
    fclose(f);

    ps->pid = pid;

    /* Extract comm (inside parentheses - may contain spaces) */
    comm_start = strchr(buf, '(');
    comm_end = strrchr(buf, ')');
    if (!comm_start || !comm_end || comm_end <= comm_start) {
        return -1;
    }

    {
        size_t len = (size_t)(comm_end - comm_start - 1);
        if (len >= sizeof(ps->name))
            len = sizeof(ps->name) - 1;
        memcpy(ps->name, comm_start + 1, len);
        ps->name[len] = '\0';
    }

    /* Fields after ')': state(3) ppid(4) ... utime(14) stime(15) ... rss(24) */
    {
        char state;
        unsigned long utime, stime;
        long rss;
        int n = sscanf(comm_end + 2,
                       "%c %*d %*d %*d %*d %*d %*u %*u %*u %*u %*u %lu %lu "
                       "%*d %*d %*d %*d %*d %*d %*u %*u %ld",
                       &state, &utime, &stime, &rss);
        if (n < 4)
            return -1;

        ps->state = state;
        ps->utime = utime;
        ps->stime = stime;
        ps->rss_pages = rss;
    }

    return 0;
}

static struct proc_stats *find_prev(struct topproc_probe_ctx *ctx, int pid)
{
    for (int i = 0; i < ctx->num_prev; i++) {
        if (ctx->prev[i].pid == pid)
            return &ctx->prev[i];
    }
    return NULL;
}

struct ranked_proc {
    int pid;
    char name[64];
    double value;  /* CPU % or RSS MB */
};

static void insert_top(struct ranked_proc *top, int n, int pid, const char *name, double value)
{
    /* Insert into sorted array (descending) */
    for (int i = 0; i < n; i++) {
        if (value > top[i].value) {
            /* Shift down */
            for (int j = n - 1; j > i; j--)
                top[j] = top[j - 1];
            top[i].pid = pid;
            strncpy(top[i].name, name, sizeof(top[i].name) - 1);
            top[i].name[sizeof(top[i].name) - 1] = '\0';
            top[i].value = value;
            return;
        }
    }
}

int topproc_probe_run(struct normalizer *norm, struct telem_socket *sock, void *ctx_ptr)
{
    struct topproc_probe_ctx *ctx = (struct topproc_probe_ctx *)ctx_ptr;
    DIR *proc_dir;
    struct dirent *entry;
    int process_count = 0, zombie_count = 0;
    int scan_count = 0;
    unsigned long total_jiffies, delta_total;

    struct proc_stats current[TOPPROC_MAX_TRACKED];
    int num_current = 0;

    struct ranked_proc top_cpu[TOPPROC_TOP_N];
    struct ranked_proc top_mem[TOPPROC_TOP_N];
    memset(top_cpu, 0, sizeof(top_cpu));
    memset(top_mem, 0, sizeof(top_mem));

    total_jiffies = get_total_jiffies();
    delta_total = total_jiffies - ctx->prev_total_jiffies;

    proc_dir = opendir("/proc");
    if (!proc_dir)
        return -1;

    while ((entry = readdir(proc_dir)) != NULL && scan_count < TOPPROC_MAX_TRACKED) {
        int pid;
        struct proc_stats ps;

        /* Only numeric directories (PIDs) */
        if (!isdigit((unsigned char)entry->d_name[0]))
            continue;

        {
            char *endptr;
            long val = strtol(entry->d_name, &endptr, 10);
            if (endptr == entry->d_name || val <= 0)
                continue;
            pid = (int)val;
        }

        if (read_proc_stat(pid, &ps) < 0)
            continue;

        process_count++;
        scan_count++;

        if (ps.state == 'Z')
            zombie_count++;

        /* Compute CPU % from delta */
        if (ctx->prev_total_jiffies > 0 && delta_total > 0) {
            struct proc_stats *prev = find_prev(ctx, pid);
            if (prev) {
                unsigned long proc_delta = (ps.utime + ps.stime) - (prev->utime + prev->stime);
                double cpu_pct = (double)proc_delta / (double)delta_total * 100.0;
                insert_top(top_cpu, TOPPROC_TOP_N, pid, ps.name, cpu_pct);
            }
        }

        /* RSS in MB (page size assumed 4096) */
        double rss_mb = (double)ps.rss_pages * 4096.0 / (1024.0 * 1024.0);
        insert_top(top_mem, TOPPROC_TOP_N, pid, ps.name, rss_mb);

        /* Store for next cycle */
        if (num_current < TOPPROC_MAX_TRACKED) {
            current[num_current++] = ps;
        }
    }
    closedir(proc_dir);

    /* Update previous state */
    memcpy(ctx->prev, current, sizeof(struct proc_stats) * num_current);
    ctx->num_prev = num_current;
    ctx->prev_total_jiffies = total_jiffies;

    /* Check emit conditions */
    if (zombie_count > ctx->zombie_threshold) {
        struct telem_event evt;
        char message[256];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "Zombie process accumulation: %d zombies (threshold: %d)",
                 zombie_count, ctx->zombie_threshold);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_SERVICE,
            message, "system:processes", 0, &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    if (process_count > ctx->process_count_threshold) {
        struct telem_event evt;
        char message[256];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "High process count: %d processes (threshold: %d)",
                 process_count, ctx->process_count_threshold);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_SERVICE,
            message, "system:processes", 0, &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    /* Check for CPU hogs (top process > threshold for 2 consecutive polls) */
    if (top_cpu[0].value > ctx->cpu_hog_threshold) {
        struct telem_event evt;
        char message[256];
        char *json_str;
        bool emit;

        snprintf(message, sizeof(message),
                 "CPU hog: process %s (pid %d) at %.1f%% CPU",
                 top_cpu[0].name, top_cpu[0].pid, top_cpu[0].value);

        emit = normalizer_process_prenormalized(norm,
            TELEM_SRC_PROBE, TELEM_SEV_WARNING, TELEM_CAT_SERVICE,
            message, "system:processes", 0, &evt);

        if (emit) {
            json_str = telem_json_event(&evt);
            if (json_str) {
                telem_socket_write(sock, json_str, strlen(json_str));
                free(json_str);
            }
        }
    }

    return 0;
}
