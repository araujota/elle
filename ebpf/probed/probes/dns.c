// SPDX-License-Identifier: MIT
/**
 * dns.c - DNS health probe
 *
 * Tests DNS resolution by querying configured domains.
 * Helps distinguish DNS failures from network issues.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <poll.h>
#include <fcntl.h>
#include <time.h>

#include "dns.h"
#include "../socket.h"
#include "../json.h"

/* DNS header structure */
struct dns_header {
    uint16_t id;
    uint16_t flags;
    uint16_t qdcount;
    uint16_t ancount;
    uint16_t nscount;
    uint16_t arcount;
};

/* DNS flags */
#define DNS_FLAG_QR     0x8000  /* Query/Response */
#define DNS_FLAG_OPCODE 0x7800  /* Opcode */
#define DNS_FLAG_AA     0x0400  /* Authoritative */
#define DNS_FLAG_TC     0x0200  /* Truncated */
#define DNS_FLAG_RD     0x0100  /* Recursion Desired */
#define DNS_FLAG_RA     0x0080  /* Recursion Available */
#define DNS_FLAG_RCODE  0x000F  /* Response Code */

/* DNS response codes */
#define DNS_RCODE_OK        0
#define DNS_RCODE_FORMERR   1
#define DNS_RCODE_SERVFAIL  2
#define DNS_RCODE_NXDOMAIN  3
#define DNS_RCODE_NOTIMP    4
#define DNS_RCODE_REFUSED   5

/* ==========================================================================
 * Context Management
 * ========================================================================== */

/* Parse /etc/resolv.conf to get DNS servers */
static int parse_resolv_conf(struct dns_probe_ctx *ctx)
{
    FILE *f;
    char line[256];

    f = fopen("/etc/resolv.conf", "r");
    if (!f) {
        /* Use default 127.0.0.53 (systemd-resolved) */
        snprintf(ctx->resolvers[0], sizeof(ctx->resolvers[0]), "%s", "127.0.0.53");
        ctx->resolver_count = 1;
        return 0;
    }

    ctx->resolver_count = 0;

    while (fgets(line, sizeof(line), f) && ctx->resolver_count < 4) {
        char ns[64];
        if (sscanf(line, "nameserver %63s", ns) == 1) {
            snprintf(ctx->resolvers[ctx->resolver_count],
                     sizeof(ctx->resolvers[0]), "%.*s", (int)(sizeof(ctx->resolvers[0]) - 1), ns);
            ctx->resolver_count++;
        }
    }

    fclose(f);

    if (ctx->resolver_count == 0) {
        /* Fallback to systemd-resolved */
        snprintf(ctx->resolvers[0], sizeof(ctx->resolvers[0]), "%s", "127.0.0.53");
        ctx->resolver_count = 1;
    }

    return 0;
}

struct dns_probe_ctx *dns_probe_create_ctx(const struct probed_config *cfg)
{
    struct dns_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->timeout_ms = cfg->dns_timeout_ms;

    /* Copy test domains */
    ctx->test_domain_count = cfg->dns_test_domain_count;
    for (int i = 0; i < ctx->test_domain_count; i++) {
        snprintf(ctx->test_domains[i], sizeof(ctx->test_domains[0]), "%.*s",
                 (int)(sizeof(ctx->test_domains[0]) - 1), cfg->dns_test_domains[i]);
    }

    /* Parse resolvers */
    parse_resolv_conf(ctx);

    return ctx;
}

void dns_probe_destroy_ctx(struct dns_probe_ctx *ctx)
{
    free(ctx);
}

/* ==========================================================================
 * DNS Query Building
 * ========================================================================== */

static int build_dns_query(const char *domain, uint8_t *buf, size_t buflen)
{
    struct dns_header *hdr = (struct dns_header *)buf;
    uint8_t *ptr;
    const char *src;
    size_t label_len;

    if (buflen < sizeof(struct dns_header) + strlen(domain) + 6)
        return -1;

    /* Build header */
    memset(hdr, 0, sizeof(*hdr));
    hdr->id = htons((uint16_t)random());
    hdr->flags = htons(DNS_FLAG_RD);  /* Recursion Desired */
    hdr->qdcount = htons(1);

    /* Build question section */
    ptr = buf + sizeof(struct dns_header);
    src = domain;

    while (*src) {
        /* Find end of label */
        const char *dot = strchr(src, '.');
        if (dot) {
            label_len = dot - src;
        } else {
            label_len = strlen(src);
        }

        if (label_len > 63 || label_len == 0)
            return -1;

        *ptr++ = (uint8_t)label_len;
        memcpy(ptr, src, label_len);
        ptr += label_len;

        src += label_len;
        if (*src == '.')
            src++;
    }

    *ptr++ = 0;  /* Root label */

    /* Type A (1), Class IN (1) */
    *ptr++ = 0; *ptr++ = 1;  /* Type A */
    *ptr++ = 0; *ptr++ = 1;  /* Class IN */

    return (int)(ptr - buf);
}

/* ==========================================================================
 * DNS Query Execution
 * ========================================================================== */

static int dns_query(const char *resolver, const char *domain,
                     int timeout_ms, struct probed_dns_event *evt)
{
    int sock = -1;
    struct sockaddr_in addr;
    uint8_t query[512];
    uint8_t response[512];
    int query_len;
    struct pollfd pfd;
    struct timespec start, end;
    int ret = -1;

    memset(evt, 0, sizeof(*evt));
    evt->ts_ns = get_timestamp_ns();
    snprintf(evt->resolver, sizeof(evt->resolver), "%.*s", (int)(sizeof(evt->resolver) - 1), resolver);
    snprintf(evt->query, sizeof(evt->query), "%.*s", (int)(sizeof(evt->query) - 1), domain);

    /* Build query */
    query_len = build_dns_query(domain, query, sizeof(query));
    if (query_len < 0) {
        evt->error_code = DNS_NETWORK;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "Invalid domain");
        return -1;
    }

    /* Create UDP socket */
    sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        evt->error_code = DNS_NETWORK;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "Socket creation failed");
        return -1;
    }

    /* Set non-blocking */
    int flags = fcntl(sock, F_GETFL, 0);
    fcntl(sock, F_SETFL, flags | O_NONBLOCK);

    /* Setup server address */
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(53);
    if (inet_pton(AF_INET, resolver, &addr.sin_addr) != 1) {
        close(sock);
        evt->error_code = DNS_NETWORK;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "Invalid resolver address");
        return -1;
    }

    /* Record start time */
    clock_gettime(CLOCK_MONOTONIC, &start);

    /* Send query */
    if (sendto(sock, query, query_len, 0,
               (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(sock);
        evt->error_code = DNS_NETWORK;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "Send failed");
        return -1;
    }

    /* Wait for response */
    pfd.fd = sock;
    pfd.events = POLLIN;

    int poll_ret = poll(&pfd, 1, timeout_ms);

    /* Record end time */
    clock_gettime(CLOCK_MONOTONIC, &end);

    uint64_t latency_ns = (end.tv_sec - start.tv_sec) * 1000000000ULL +
                          (end.tv_nsec - start.tv_nsec);
    evt->latency_ms = latency_ns / 1000000;

    if (poll_ret == 0) {
        /* Timeout */
        close(sock);
        evt->error_code = DNS_TIMEOUT;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "Query timeout");
        return -1;
    }

    if (poll_ret < 0) {
        close(sock);
        evt->error_code = DNS_NETWORK;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "Poll failed");
        return -1;
    }

    /* Receive response */
    ssize_t recv_len = recv(sock, response, sizeof(response), 0);
    close(sock);

    if (recv_len < (ssize_t)sizeof(struct dns_header)) {
        evt->error_code = DNS_NETWORK;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "Invalid response");
        return -1;
    }

    /* Parse response */
    struct dns_header *hdr = (struct dns_header *)response;
    uint16_t flags_val = ntohs(hdr->flags);
    int rcode = flags_val & DNS_FLAG_RCODE;

    switch (rcode) {
    case DNS_RCODE_OK:
        evt->success = true;
        evt->error_code = DNS_OK;
        ret = 0;
        break;
    case DNS_RCODE_NXDOMAIN:
        evt->error_code = DNS_NXDOMAIN;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "NXDOMAIN");
        break;
    case DNS_RCODE_SERVFAIL:
        evt->error_code = DNS_SERVFAIL;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "SERVFAIL");
        break;
    case DNS_RCODE_REFUSED:
        evt->error_code = DNS_REFUSED;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "%s", "REFUSED");
        break;
    default:
        evt->error_code = DNS_SERVFAIL;
        snprintf(evt->error_msg, sizeof(evt->error_msg), "RCODE %d", rcode);
        break;
    }

    return ret;
}

/* ==========================================================================
 * Latency Percentile Helpers
 * ========================================================================== */

/* Comparison function for qsort */
static int uint32_compare(const void *a, const void *b)
{
    uint32_t va = *(const uint32_t *)a;
    uint32_t vb = *(const uint32_t *)b;
    if (va < vb) return -1;
    if (va > vb) return 1;
    return 0;
}

static void compute_percentiles(struct dns_probe_ctx *ctx,
                                 uint32_t *p50, uint32_t *p95, uint32_t *p99)
{
    uint32_t sorted[100];
    int count = ctx->history_count;

    if (count == 0) {
        *p50 = 0;
        *p95 = 0;
        *p99 = 0;
        return;
    }

    /* Copy and sort */
    memcpy(sorted, ctx->latency_history, sizeof(uint32_t) * count);
    qsort(sorted, count, sizeof(uint32_t), uint32_compare);

    *p50 = sorted[count / 2];
    *p95 = sorted[count * 95 / 100];
    *p99 = sorted[count * 99 / 100];
}

/* ==========================================================================
 * Probe Entry Point
 * ========================================================================== */

int dns_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct dns_probe_ctx *ctx = (struct dns_probe_ctx *)ctx_ptr;
    struct probed_dns_event evt;
    int ret = 0;

    /* Test each domain with first resolver */
    for (int i = 0; i < ctx->test_domain_count; i++) {
        dns_query(ctx->resolvers[0], ctx->test_domains[i],
                  ctx->timeout_ms, &evt);

        /* Record latency for percentile tracking */
        if (evt.success) {
            ctx->latency_history[ctx->history_idx] = evt.latency_ms;
            ctx->history_idx = (ctx->history_idx + 1) % 100;
            if (ctx->history_count < 100)
                ctx->history_count++;
            ctx->total_query_count++;
        }

        /* Emit event (success or failure) */
        char *json = probed_json_dns_event(&evt);
        if (json) {
            probed_socket_write(sock, json, strlen(json));
            free(json);
        }

        if (!evt.success)
            ret = -1;
    }

    /* Emit percentile stats if we have enough data */
    if (ctx->history_count >= 5) {
        uint32_t p50, p95, p99;
        compute_percentiles(ctx, &p50, &p95, &p99);

        char json_buf[512];
        snprintf(json_buf, sizeof(json_buf),
                 "{\"type\":\"dns_percentiles\","
                 "\"p50_ms\":%u,\"p95_ms\":%u,\"p99_ms\":%u,"
                 "\"query_count\":%lu}\n",
                 p50, p95, p99, (unsigned long)ctx->total_query_count);
        probed_socket_write(sock, json_buf, strlen(json_buf));
    }

    return ret;
}
