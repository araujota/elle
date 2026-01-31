# Configuration

ELLE is configured via TOML files with environment variable overrides.

## Config file locations

Configuration is loaded in this order (later sources override earlier ones):

1. `/etc/elle/elle.toml` — System-wide defaults
2. `~/.config/elle/elle.toml` — User overrides
3. Environment variables (`ELLE_*` prefix)
4. `-c` flag — Explicit config file path

## Complete TOML reference

### `[daemon]`

Top-level daemon settings.

```toml
[daemon]
log_level = "INFO"                        # DEBUG, INFO, WARNING, ERROR
journal_enabled = true                    # Monitor systemd journal
kernel_enabled = true                     # Monitor kernel messages
probes_enabled = true                     # Periodic system checks
docker_enabled = true                     # Monitor Docker events
auto_learn_new_packages = true            # Generate capabilities for new packages
capability_versioning_enabled = true      # Auto-regenerate capabilities on package upgrade
```

### `[daemon.api]`

REST API configuration. ELLE exposes an OpenAI-compatible API.

```toml
[daemon.api]
enabled = true                            # Enable/disable API
host = "127.0.0.1"                        # Bind address
port = 8377                               # Port number
```

### `[daemon.api_auth]`

API authentication settings.

```toml
[daemon.api_auth]
allow_anonymous = false                   # Allow unauthenticated requests (readonly)
api_keys_db_path = "/var/lib/elle/api_keys.db"  # API key database path
```

Authentication hierarchy: session token > UDS peer credentials > API key > anonymous (if enabled).

### `[daemon.probes]`

Probe intervals in seconds.

```toml
[daemon.probes]
memory_interval = 30                      # Memory probe (default: 30s)
disk_interval = 300                       # Disk probe (default: 5 min)
network_interval = 60                     # Network probe (default: 60s)
thermal_interval = 60                     # Thermal probe (default: 60s)
smart_interval = 3600                     # SMART probe (default: 1 hour)
```

### `[daemon.thresholds]`

Alert thresholds.

```toml
[daemon.thresholds]
memory_warning_pct = 0.85                 # Memory pressure warning (85%)
disk_warning_pct = 0.90                   # Disk usage warning (90%)
thermal_warning_c = 80.0                  # Temperature warning (80°C)
network_error_threshold = 10              # Network error count to alert
```

### `[daemon.correlation]`

Event correlation settings.

```toml
[daemon.correlation]
time_window_sec = 600                     # Correlation window (10 min)
dedupe_window_sec = 60                    # Deduplication window (1 min)
min_events_for_incident = 1               # Min events to create incident
stale_incident_sec = 900                  # Close stale incidents (15 min)
```

### `[daemon.queues]`

Internal queue sizing.

```toml
[daemon.queues]
raw_queue_size = 10000                    # Raw event queue capacity
event_queue_size = 5000                   # Processed event queue capacity
```

### `[daemon.ebpf]`

eBPF telemetry configuration.

```toml
[daemon.ebpf]
enabled = true                            # Master enable/disable
programs = ["oom", "block_io", "process", "thermal", "net_drops"]
```

### `[daemon.cloud_sync]`

Cloud incident sync retry queue configuration.

```toml
[daemon.cloud_sync]
enabled = true
retry_initial_delay_sec = 30.0
retry_max_delay_sec = 3600.0
retry_backoff_factor = 2.0
max_retries = 20
queue_max_size = 1000
batch_size = 10
health_check_interval_sec = 60.0
stale_entry_hours = 168                   # 7 days
worker_poll_interval_sec = 30.0
```

### `[daemon.inotify]`

File change monitoring.

```toml
[daemon.inotify]
watch_paths = [
  "/root/.ssh/authorized_keys",
  "/etc/ssh/sshd_config",
]
```

### `[database]`

PostgreSQL connection settings.

```toml
[database]
host = ""                                 # Empty = Unix socket
port = 5432
dbname = "elle"
user = "elle"
password = ""                             # Use ELLE_DB_PASSWORD env var
socket_dir = "/var/run/postgresql"
min_pool_size = 2
max_pool_size = 10
max_idle_sec = 300.0
sslmode = "prefer"
encryption_key_path = "/etc/elle/db.key"
```

### `[llm.provider]`

Primary LLM provider. See [[LLM Providers|LLM-Providers]] for full details.

```toml
[llm.provider]
type = "ollama"                           # "ollama" or "openai"
host = "http://localhost:11434"
model = "qwen2.5:7b-instruct-q8_0"
api_key = ""                              # Use ELLE_LLM_API_KEY env var
timeout = 120.0
max_tokens = 4096
temperature = 0.7
```

### `[llm.fallback]`

Fallback LLM provider (used when primary fails).

```toml
[llm.fallback]
enabled = true
host = "http://localhost:11434"
model = "qwen2.5:7b-instruct-q8_0"
retry_interval = 60.0
```

### `[mobile]`

Mobile gateway settings. See the [[Private Incident Vault|Private-Incident-Vault]] page for the mobile client.

```toml
[mobile]
enabled = false
bind_host = "0.0.0.0"
bind_port = 8378
overlay_host = ""                         # Public-facing hostname (optional)
pairing_token_ttl_seconds = 90
max_paired_devices = 10
default_elevation_ttl_seconds = 600       # 10 minutes
max_elevation_ttl_seconds = 3600          # 1 hour
default_role = "mobile_readonly"          # mobile_readonly or mobile_operator
```

## Environment variable overrides

All settings can be overridden with `ELLE_` prefixed environment variables. Boolean values accept `true`, `1`, `yes` (case-insensitive).

### Daemon

| Variable | Config key |
|----------|-----------|
| `ELLE_LOG_LEVEL` | `daemon.log_level` |
| `ELLE_API_ENABLED` | `daemon.api.enabled` |
| `ELLE_API_HOST` | `daemon.api.host` |
| `ELLE_API_PORT` | `daemon.api.port` |
| `ELLE_EBPF_ENABLED` | `daemon.ebpf.enabled` |
| `ELLE_AUTO_LEARN_NEW_PACKAGES` | `daemon.auto_learn_new_packages` |

### Probes

| Variable | Config key |
|----------|-----------|
| `ELLE_PROBE_MEMORY_INTERVAL` | `daemon.probes.memory_interval` |
| `ELLE_PROBE_DISK_INTERVAL` | `daemon.probes.disk_interval` |
| `ELLE_PROBE_NETWORK_INTERVAL` | `daemon.probes.network_interval` |
| `ELLE_PROBE_THERMAL_INTERVAL` | `daemon.probes.thermal_interval` |
| `ELLE_PROBE_SMART_INTERVAL` | `daemon.probes.smart_interval` |

### Database

| Variable | Config key |
|----------|-----------|
| `ELLE_DB_HOST` | `database.host` |
| `ELLE_DB_PORT` | `database.port` |
| `ELLE_DB_NAME` | `database.dbname` |
| `ELLE_DB_USER` | `database.user` |
| `ELLE_DB_PASSWORD` | `database.password` |

### LLM

| Variable | Config key |
|----------|-----------|
| `ELLE_LLM_PROVIDER_TYPE` | `llm.provider.type` |
| `ELLE_LLM_PROVIDER_HOST` | `llm.provider.host` |
| `ELLE_LLM_PROVIDER_MODEL` | `llm.provider.model` |
| `ELLE_LLM_API_KEY` | `llm.provider.api_key` |
| `ELLE_LLM_FALLBACK_ENABLED` | `llm.fallback.enabled` |

### Mobile

| Variable | Config key |
|----------|-----------|
| `ELLE_MOBILE_ENABLED` | `mobile.enabled` |
| `ELLE_MOBILE_BIND_HOST` | `mobile.bind_host` |
| `ELLE_MOBILE_BIND_PORT` | `mobile.bind_port` |

### Cloud sync

| Variable | Config key |
|----------|-----------|
| `ELLE_CLOUD_SYNC_ENABLED` | `daemon.cloud_sync.enabled` |

## Policy file

User policy rules are stored in `~/.config/elle/policy.yaml`. The policy engine evaluates rules to decide whether to `allow`, `deny`, or `require_confirmation` for each capability execution. Policy is configured through the [[Setup Wizard|Setup-Wizard]] or manually edited.
