# Security

ELLE is designed with security as a foundational concern. Every mutation goes through policy enforcement, dangerous commands are blocked, and privileged operations use Polkit instead of sudo.

## Safety model: Explain, Plan, Confirm, Apply

Every system-modifying task follows this workflow:

1. **Explain** — ELLE describes what it will do and why
2. **Plan** — Shows the exact capabilities to execute
3. **Confirm** — Asks for your approval (based on risk level and confirmation preference)
4. **Apply** — Executes with full audit trail and rollback capability

## Command denylist

These patterns are blocked even in shell passthrough mode:

| Pattern | Reason |
|---------|--------|
| `rm -rf /` | Destructive rm patterns |
| `:(){ :\|:& };:` | Fork bombs |
| `mkfs`, `mkswap` | Disk formatting |
| `dd of=/dev/sda` | Raw disk writes |
| `curl \| bash` | Pipe-to-shell patterns |
| `sudo` | Use Polkit instead |

The denylist is enforced at the command preflight stage. Commands matching these patterns are rejected before reaching the shell.

## Privilege levels

ELLE never uses `sudo`. Privileged operations go through Polkit, which provides fine-grained access control with authentication caching.

| Level | Description | Use case |
|-------|-------------|----------|
| **Secure** (default) | Always require password. Polkit caches auth for 5 minutes per session. | Production servers, shared machines |
| **Convenient** | Members of the `elle` group skip password prompts. | Personal workstations |
| **Passwordless** | No authentication required for ELLE operations. | Development VMs, single-user systems |

The privilege level is configured during the [[Setup Wizard|Setup-Wizard]] and stored as Polkit rules in `/etc/polkit-1/rules.d/50-elle.rules`.

## Policy engine

The Policy Engine evaluates rules for each capability execution:

- **Allow** — Execute without prompting
- **Deny** — Block execution
- **Require confirmation** — Ask user before executing

Rules are evaluated based on capability name, domain, risk level, trust level, and user identity. The safety level configured during setup translates to a default policy set:

| Safety Level | Behavior |
|-------------|----------|
| Standard | Blocks dangerous commands, confirms high-risk operations |
| Cautious | Confirms most changes, previews all config edits |
| Minimal | Only blocks the most dangerous patterns |

User policy rules are stored in `~/.config/elle/policy.yaml`.

## API authentication

The REST API (port 8377) supports a layered authentication hierarchy:

| Method | Priority | Description |
|--------|----------|-------------|
| Session token | Highest | `X-Elle-Session` header, generated per CLI session |
| UDS peer credentials | High | Unix domain socket with matching UID |
| API key | Medium | Stored in `/var/lib/elle/api_keys.db` |
| Anonymous | Lowest | Readonly only, disabled by default |

Anonymous access is disabled by default (`allow_anonymous = false` in config).

## mTLS for cloud sync

Communication with the [[Private Incident Vault|Private-Incident-Vault]] (elle-cloud) uses mutual TLS:

- Private CA generated during elle-cloud setup
- Server certificate identifies the vault
- Client certificates issued per ELLE installation
- No fallback to system trust store

## mTLS for mobile gateway

The mobile gateway uses the same mTLS model:

- Per-device client certificates issued during QR code pairing
- Certificate pinning (device pins server fingerprint from QR code)
- Minimum TLS 1.2
- No HTTP fallback

## Incident anonymization

Before syncing to the shared vault, incident reports are anonymized:

| Strategy | What happens |
|----------|-------------|
| **Redact** | Sensitive data removed (passwords, API keys, IPs) |
| **Generalize** | Specific values replaced with categories (`192.168.1.100` → `internal_ip`) |
| **Preserve** | Non-sensitive data kept (package names, error codes) |

## Capability risk enforcement

Every capability declares a risk level. The policy engine uses this to decide whether to allow, confirm, or deny:

| Risk Level | Typical policy (Standard safety) |
|-----------|--------------------------------|
| `none` | Allow |
| `low` | Allow |
| `medium` | Allow |
| `high` | Require confirmation |
| `critical` | Require confirmation |

With Cautious safety, `medium` and above require confirmation.

## Audit trail

Every significant action creates or updates an incident record:

- Capability executions are recorded automatically by the executor
- Command module actions use `record_arm_action()` from `incident_recorder.py`
- Background operations create incidents with provenance
- Config file modifications are tracked with before/after SHA256 hashes

The incident record includes:
- Full system snapshot at incident start
- Each action taken (command, exit code, stdout/stderr, duration)
- Decision rationale with provenance citations
- Outcome assessment (improved, partial, no change, worse)
