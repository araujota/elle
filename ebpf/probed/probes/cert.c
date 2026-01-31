// SPDX-License-Identifier: MIT
/**
 * cert.c - TLS certificate expiry monitoring
 *
 * Uses OpenSSL to connect to local TLS endpoints and check certificate expiry.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <openssl/err.h>
#include <openssl/bio.h>

#include "cert.h"
#include "../json.h"

struct cert_probe_ctx *cert_probe_create_ctx(const struct probed_config *cfg)
{
    struct cert_probe_ctx *ctx = calloc(1, sizeof(*ctx));
    if (!ctx)
        return NULL;

    ctx->warning_days = 30;
    ctx->critical_days = 7;
    ctx->connect_timeout_sec = 5;
    ctx->num_endpoints = 0;

    /* Parse cert_endpoints from config (comma-separated host:port) */
    if (cfg->cert_endpoints[0] != '\0') {
        char buf[1024];
        snprintf(buf, sizeof(buf), "%.*s", (int)(sizeof(buf) - 1), cfg->cert_endpoints);

        char *saveptr;
        char *token = strtok_r(buf, ",", &saveptr);
        while (token && ctx->num_endpoints < CERT_MAX_ENDPOINTS) {
            /* Trim whitespace */
            while (*token == ' ') token++;
            char *end = token + strlen(token) - 1;
            while (end > token && *end == ' ') *end-- = '\0';

            /* Parse host:port */
            char *colon = strrchr(token, ':');
            if (colon) {
                *colon = '\0';
                snprintf(ctx->endpoints[ctx->num_endpoints].host,
                         sizeof(ctx->endpoints[0].host), "%.*s", (int)(sizeof(ctx->endpoints[0].host) - 1), token);
                ctx->endpoints[ctx->num_endpoints].port = atoi(colon + 1);
            } else {
                snprintf(ctx->endpoints[ctx->num_endpoints].host,
                         sizeof(ctx->endpoints[0].host), "%.*s", (int)(sizeof(ctx->endpoints[0].host) - 1), token);
                ctx->endpoints[ctx->num_endpoints].port = 443;
            }
            ctx->num_endpoints++;
            token = strtok_r(NULL, ",", &saveptr);
        }
    }

    /* Default endpoints if none configured */
    if (ctx->num_endpoints == 0) {
        snprintf(ctx->endpoints[0].host, sizeof(ctx->endpoints[0].host), "%s", "localhost");
        ctx->endpoints[0].port = 443;
        ctx->num_endpoints = 1;
    }

    return ctx;
}

void cert_probe_destroy_ctx(struct cert_probe_ctx *ctx)
{
    free(ctx);
}

static int get_days_until_expiry(const ASN1_TIME *not_after)
{
    int day_diff = 0, sec_diff = 0;

    if (!ASN1_TIME_diff(&day_diff, &sec_diff, NULL, not_after))
        return -1;

    return day_diff;
}

static int check_endpoint(struct cert_probe_ctx *ctx, int idx,
                           struct probed_socket *sock)
{
    struct cert_endpoint *ep = &ctx->endpoints[idx];
    SSL_CTX *ssl_ctx = NULL;
    SSL *ssl = NULL;
    BIO *bio = NULL;
    X509 *cert = NULL;
    char connect_str[256];
    char json_buf[1024];
    int days = -1;
    const char *severity;
    char subject_cn[256] = {0};
    char issuer_cn[256] = {0};

    ssl_ctx = SSL_CTX_new(TLS_client_method());
    if (!ssl_ctx)
        goto err;

    /* Don't verify - we just want the cert info */
    SSL_CTX_set_verify(ssl_ctx, SSL_VERIFY_NONE, NULL);

    snprintf(connect_str, sizeof(connect_str), "%s:%d", ep->host, ep->port);

    bio = BIO_new_ssl_connect(ssl_ctx);
    if (!bio)
        goto err;

    BIO_get_ssl(bio, &ssl);
    if (!ssl)
        goto err;

    SSL_set_tlsext_host_name(ssl, ep->host);
    BIO_set_conn_hostname(bio, connect_str);

    /* Set timeout */
    BIO_set_nbio(bio, 0);

    if (BIO_do_connect(bio) <= 0)
        goto err;

    if (BIO_do_handshake(bio) <= 0)
        goto err;

    cert = SSL_get_peer_certificate(ssl);
    if (!cert)
        goto err;

    /* Get days until expiry */
    days = get_days_until_expiry(X509_get0_notAfter(cert));

    /* Extract subject CN */
    X509_NAME_get_text_by_NID(X509_get_subject_name(cert), NID_commonName,
                               subject_cn, sizeof(subject_cn));

    /* Extract issuer CN */
    X509_NAME_get_text_by_NID(X509_get_issuer_name(cert), NID_commonName,
                               issuer_cn, sizeof(issuer_cn));

    /* Determine severity */
    if (days <= ctx->critical_days) {
        severity = "critical";
    } else if (days <= ctx->warning_days) {
        severity = "warning";
    } else {
        severity = "info";
    }

    /* Emit JSON event */
    snprintf(json_buf, sizeof(json_buf),
             "{\"type\":\"cert_expiry\",\"endpoint\":\"%s:%d\","
             "\"days_until_expiry\":%d,\"subject_cn\":\"%s\","
             "\"issuer_cn\":\"%s\",\"severity\":\"%s\"}\n",
             ep->host, ep->port, days, subject_cn, issuer_cn, severity);

    probed_socket_write(sock, json_buf, strlen(json_buf));

    X509_free(cert);
    BIO_free_all(bio);
    SSL_CTX_free(ssl_ctx);
    return 0;

err:
    /* Connection failure */
    snprintf(json_buf, sizeof(json_buf),
             "{\"type\":\"cert_expiry\",\"endpoint\":\"%s:%d\","
             "\"error\":\"connection_failed\",\"severity\":\"error\"}\n",
             ep->host, ep->port);
    probed_socket_write(sock, json_buf, strlen(json_buf));

    if (cert) X509_free(cert);
    if (bio) BIO_free_all(bio);
    if (ssl_ctx) SSL_CTX_free(ssl_ctx);
    return -1;
}

int cert_probe_run(struct probed_socket *sock, void *ctx_ptr)
{
    struct cert_probe_ctx *ctx = (struct cert_probe_ctx *)ctx_ptr;

    for (int i = 0; i < ctx->num_endpoints; i++) {
        check_endpoint(ctx, i, sock);
    }

    return 0;
}
