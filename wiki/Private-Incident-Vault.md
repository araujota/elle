# Private Incident Vault

The Private Incident Vault (elle-cloud) is a companion service that enables teams to share anonymized incident knowledge across ELLE installations. It's a self-hosted Docker service that accepts incident reports over mTLS.

## What it does

When ELLE resolves an incident, it can optionally sync an anonymized version of the incident report to a shared vault. Other ELLE installations connected to the same vault can then search these reports when they encounter similar situations, benefiting from the team's collective experience.

## Privacy model

Before syncing, incident reports are anonymized using three strategies:

| Strategy | What happens | Example |
|----------|-------------|---------|
| **REDACT** | Sensitive data removed entirely | Passwords, API keys, IP addresses |
| **GENERALIZE** | Specific values replaced with categories | `192.168.1.100` → `internal_ip` |
| **PRESERVE** | Non-sensitive data kept as-is | Package names, error codes, commands |

Hostnames, usernames, file paths with user-specific segments, and other identifying information are redacted or generalized. The fingerprint vector (31 numeric dimensions) is preserved for similarity matching.

## elle-cloud deployment

The elle-cloud service is in a [separate repository](https://github.com/araujota/elle-cloud).

### Prerequisites

- Docker and docker-compose
- A machine accessible to your ELLE installations (can be the same machine)

### Quick start

```bash
git clone https://github.com/araujota/elle-cloud.git
cd elle-cloud

# Initialize CA and server certificates
./scripts/init-certs.sh

# Start the service
docker compose up -d

# Issue a client certificate for an ELLE installation
./scripts/issue-client-cert.sh my-server-01
```

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ELLE_CLOUD_MODE` | Deployment mode | `production` |
| `ORG_NAME` | Organization name for certificates | `elle` |
| `ELLE_CLOUD_DB_PATH` | SQLite database path | `/data/incidents.db` |

### Container

The service runs on `python:3.10-slim-bookworm` with FastAPI and mTLS client certificate authentication.

## Certificate management

elle-cloud uses a private CA for mutual TLS:

- **CA certificate** — Root of trust, generated during `init-certs.sh`
- **Server certificate** — Identifies the elle-cloud service
- **Client certificates** — One per ELLE installation, issued via `issue-client-cert.sh`

### Issuing client certificates

```bash
# Issue a cert for a new ELLE installation
./scripts/issue-client-cert.sh production-web-01

# The script outputs:
# - certs/clients/production-web-01.crt
# - certs/clients/production-web-01.key
```

### Distributing certificates

Securely copy the client certificate and key to each ELLE installation:

```bash
scp certs/clients/production-web-01.{crt,key} admin@production-web-01:/etc/elle/cloud/
scp certs/ca.crt admin@production-web-01:/etc/elle/cloud/
```

## Connecting ELLE to the vault

Configure ELLE to sync incidents:

```toml
# /etc/elle/elle.toml
[daemon.cloud_sync]
enabled = true
```

Place the client certificates in `/etc/elle/cloud/` (or the path configured in your installation).

### Sync behavior

- Incidents are queued for sync with exponential backoff retry
- Initial retry delay: 30 seconds
- Maximum retry delay: 1 hour (backoff factor 2.0)
- Maximum retries: 20 per incident
- Queue capacity: 1,000 pending incidents
- Batch size: 10 incidents per sync
- Stale entries (>7 days) are cleaned up automatically
- Worker polls every 30 seconds for new work

## Similarity search

The vault supports similarity search using incident fingerprints. Each fingerprint is a 31-dimensional vector capturing:

- Resource pressure (disk, memory, swap, CPU)
- Event counts (OOM kills, network flaps, service failures, auth failures)
- Affected entities
- Hardware indicators (SMART status, temperature)
- Container state

When ELLE encounters a new incident, it can query the vault for similar past incidents and weight their proven solutions higher in its reasoning.

## Monitoring

### Health endpoints

```bash
# Basic health check
curl https://your-vault:8443/health

# Detailed status
curl https://your-vault:8443/health/detailed
```

### Metrics

The sync worker exposes queue metrics:
- Queue depth (pending incidents)
- Retry counts
- Sync success/failure rates

## Team setup

1. Deploy elle-cloud on a machine accessible to all ELLE installations
2. Run `init-certs.sh` to create the CA
3. Issue a client certificate for each ELLE installation
4. Securely distribute certificates to each machine
5. Enable `cloud_sync` in each installation's `elle.toml`
6. Verify connectivity with `curl --cert ... --key ... --cacert ... https://vault:8443/health`

## Backup

The incident vault is a SQLite database. Back it up with:

```bash
# Inside the container
sqlite3 /data/incidents.db ".backup /data/incidents-backup.db"

# Or copy the volume
docker cp elle-cloud:/data/incidents.db ./backup/
```
