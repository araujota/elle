// SPDX-License-Identifier: GPL-2.0 OR MIT
/**
 * exec.bpf.c - Process execution tracing for elled-ebpfd
 *
 * Traces execve syscalls with enter/exit correlation to capture:
 *   - What program was executed (filename)
 *   - Whether it succeeded or failed
 *   - Process context (pid, ppid, uid)
 *
 * Uses LRU hash map for PID -> filename correlation between enter/exit.
 *
 * CO-RE (Compile Once - Run Everywhere) for kernel portability.
 */

#define __BPF__
#include "elled.h"

char LICENSE[] SEC("license") = "Dual MIT/GPL";

/* Maximum tracked concurrent exec calls */
#define MAX_TRACKED_PIDS 4096

/* ==========================================================================
 * Maps
 * ========================================================================== */

/* Ring buffer for events */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, ELLED_RINGBUF_SIZE);
} events SEC(".maps");

/* Configuration map */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct elled_config);
} config SEC(".maps");

/* Enter data stored between sys_enter and sys_exit */
struct exec_enter_data {
    __u64 ts_ns;
    __u32 ppid;
    char filename[256];
};

/* LRU map for enter/exit correlation (key: pid) */
struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, MAX_TRACKED_PIDS);
    __type(key, __u32);
    __type(value, struct exec_enter_data);
} exec_enter_map SEC(".maps");

/* Sampling counter */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u32);
} sample_counter SEC(".maps");

/* ==========================================================================
 * Helper: Check sampling rate
 * ========================================================================== */

static __always_inline bool should_sample(__u32 rate)
{
    __u32 key = 0;
    __u32 *counter;

    if (rate <= 1)
        return true;

    counter = bpf_map_lookup_elem(&sample_counter, &key);
    if (!counter)
        return true;

    (*counter)++;
    return (*counter % rate) == 0;
}

/* ==========================================================================
 * Tracepoint: sys_enter_execve
 *
 * Captures the filename being executed before the syscall.
 * Stores in LRU map for correlation with sys_exit.
 * ========================================================================== */

SEC("tp/syscalls/sys_enter_execve")
int trace_sys_enter_execve(struct trace_event_raw_sys_enter *ctx)
{
    struct elled_config *cfg;
    struct exec_enter_data data = {};
    struct task_struct *task;
    __u32 key = 0;
    __u32 pid;
    const char *filename_ptr;

    /* Check if exec tracing is enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_exec)
        return 0;

    /* Check sampling rate */
    if (cfg && !should_sample(cfg->exec_sample_rate))
        return 0;

    /* Get current PID/TID */
    pid = bpf_get_current_pid_tgid() >> 32;

    /* Check PID filter */
    if (cfg && cfg->filter_pid != 0 && cfg->filter_pid != pid)
        return 0;

    /* Get current task for ppid */
    task = (struct task_struct *)bpf_get_current_task();

    /* Record enter timestamp and parent */
    data.ts_ns = bpf_ktime_get_ns();
    data.ppid = BPF_CORE_READ(task, real_parent, tgid);

    /* Read filename from syscall args
     * execve args: (const char *filename, char *const argv[], char *const envp[])
     * args[0] = filename pointer
     */
    filename_ptr = (const char *)ctx->args[0];
    bpf_probe_read_user_str(data.filename, sizeof(data.filename), filename_ptr);

    /* Store for exit correlation */
    bpf_map_update_elem(&exec_enter_map, &pid, &data, BPF_ANY);

    return 0;
}

/* ==========================================================================
 * Tracepoint: sys_exit_execve
 *
 * Captures the result of execve syscall.
 * Correlates with enter data and emits event.
 * ========================================================================== */

SEC("tp/syscalls/sys_exit_execve")
int trace_sys_exit_execve(struct trace_event_raw_sys_exit *ctx)
{
    struct elled_exec_event *evt;
    struct exec_enter_data *enter_data;
    struct task_struct *task;
    struct elled_config *cfg;
    __u32 key = 0;
    __u32 pid, tgid;
    __u64 pid_tgid;
    __s32 ret;

    /* Check if exec tracing is enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_exec)
        return 0;

    /* Get current PID/TID */
    pid_tgid = bpf_get_current_pid_tgid();
    pid = pid_tgid >> 32;
    tgid = pid_tgid >> 32;

    /* Look up enter data */
    enter_data = bpf_map_lookup_elem(&exec_enter_map, &pid);
    if (!enter_data) {
        /* No enter event recorded (sampling or race), skip */
        return 0;
    }

    /* Get return value */
    ret = ctx->ret;

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt) {
        bpf_map_delete_elem(&exec_enter_map, &pid);
        return 0;
    }

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_EXEC_EXIT;

    /* Process identification */
    evt->pid = pid;
    evt->tgid = tgid;
    evt->ppid = enter_data->ppid;

    task = (struct task_struct *)bpf_get_current_task();
    evt->uid = BPF_CORE_READ(task, cred, uid.val);

    /* Execution result */
    evt->ret = ret;
    evt->is_exit = 1;

    /* Read current comm (may have changed on successful exec) */
    bpf_get_current_comm(evt->comm, sizeof(evt->comm));

    /* Copy filename from enter data */
    __builtin_memcpy(evt->filename, enter_data->filename, sizeof(evt->filename));

    /* Cleanup enter data */
    bpf_map_delete_elem(&exec_enter_map, &pid);

    /* Submit event */
    bpf_ringbuf_submit(evt, 0);

    return 0;
}

/* ==========================================================================
 * Tracepoint: sched_process_exit
 *
 * Captures process exits, especially signal deaths.
 * Useful for detecting crashes (SIGSEGV, SIGABRT, etc.)
 * ========================================================================== */

SEC("tp/sched/sched_process_exit")
int trace_process_exit(struct trace_event_raw_sched_process_template *ctx)
{
    struct elled_exit_event *evt;
    struct task_struct *task;
    struct elled_config *cfg;
    __u32 key = 0;
    __u32 exit_code;
    __s32 signal;

    /* Check if process exit tracing is enabled */
    cfg = bpf_map_lookup_elem(&config, &key);
    if (cfg && !cfg->enable_process_exit)
        return 0;

    /* Get current task */
    task = (struct task_struct *)bpf_get_current_task();

    /* Read exit code */
    exit_code = BPF_CORE_READ(task, exit_code);

    /* Extract signal from lower 7 bits */
    signal = exit_code & 0x7f;

    /* Only report signal deaths (signal != 0) to reduce noise
     * Normal exits are usually not interesting for telemetry
     */
    if (signal == 0)
        return 0;

    /* Check PID filter */
    if (cfg && cfg->filter_pid != 0) {
        __u32 pid = BPF_CORE_READ(task, pid);
        if (cfg->filter_pid != pid)
            return 0;
    }

    /* Reserve ring buffer space */
    evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
    if (!evt)
        return 0;

    /* Fill header */
    evt->hdr.ts_ns = bpf_ktime_get_ns();
    evt->hdr.cpu = bpf_get_smp_processor_id();
    evt->hdr.type = ELLED_EVENT_PROCESS_EXIT;

    /* Process identification */
    evt->pid = BPF_CORE_READ(task, pid);
    evt->tgid = BPF_CORE_READ(task, tgid);
    evt->uid = BPF_CORE_READ(task, cred, uid.val);

    /* Exit information */
    evt->raw_exit_code = exit_code;
    evt->exit_code = (exit_code >> 8) & 0xff;  /* Upper 8 bits */
    evt->signal = signal;

    /* Process name */
    bpf_probe_read_kernel_str(evt->comm, sizeof(evt->comm), task->comm);

    /* Submit event */
    bpf_ringbuf_submit(evt, 0);

    return 0;
}
