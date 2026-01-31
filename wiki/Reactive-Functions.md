# Reactive Functions

Reactive functions are event-driven automations that let ELLE respond automatically to system events. You define them in natural language, and ELLE translates them into trigger conditions and capability-based actions.

## Creating a reactive function

```
elle> /react create
What should trigger this automation?
> when disk usage exceeds 90%

What should happen?
> clean apt cache and docker images, then notify me

Created reactive function: disk-cleanup-90
```

## Trigger types

| Type | Description | Example |
|------|-------------|---------|
| **Event** | Respond to telemetry events | "when a container dies" |
| **Schedule** | Cron-based execution | "every Sunday at 3am" |
| **Manual** | On-demand execution | "when I say 'backup'" |
| **Forecast** | Respond to trend predictions | "when disk is predicted to fill within 24h" |

## How it works

1. ELLE translates your natural language description into a structured reactive function definition
2. The definition includes trigger conditions, actions (as capabilities), and policies
3. The reactive engine in the daemon evaluates trigger conditions against incoming telemetry
4. When a trigger fires, the function's actions execute through the standard Capability pipeline
5. Execution is recorded to the Incident Vault like any other operation

Because reactive functions execute through capabilities, they benefit from the same policy enforcement, risk assessment, and audit trail as interactive operations.

## Managing reactive functions

| Command | Description |
|---------|-------------|
| `/react list` | List all functions |
| `/react show <id>` | Show function details |
| `/react enable <id>` | Enable a function |
| `/react disable <id>` | Disable a function |
| `/react delete <id>` | Delete a function |
| `/react history` | Execution history |
| `/react test <id>` | Test with a mock event |

## Examples

### Disk pressure response

```
elle> /react create
> when disk usage exceeds 85%
> clean old journal logs, prune docker images, and send a notification
```

### Docker crash response

```
elle> /react create
> when a container crashes unexpectedly
> diagnose the container, create an incident report, and notify me
```

### Scheduled maintenance

```
elle> /react create
> every day at 4am
> clean apt cache and rotate logs
```

### Thermal alert

```
elle> /react create
> when CPU temperature exceeds 85°C
> send a notification with current thermal readings
```

### Service health check

```
elle> /react create
> when nginx fails
> restart it and log the incident
```

## Function policies

Each reactive function has execution policies to prevent runaway automation:

| Policy | Description | Default |
|--------|-------------|---------|
| `max_frequency` | Minimum time between executions | 1 minute |
| `max_daily_executions` | Maximum executions per day | Unlimited |
| `require_confirmation` | Ask before executing | Based on risk level |
| `allowed_hours` | Restrict execution to certain hours | Any time |

## Rate limiting

Reactive functions are rate-limited to prevent cascading execution. If a trigger condition fires repeatedly (e.g., disk stays above 90%), the function executes once and then respects the `max_frequency` cooldown before it can fire again.

## Storage

Reactive function definitions are stored in the `reactive` schema in PostgreSQL. Execution history is stored alongside the definition and also recorded as incidents in the Incident Vault.

## Integration with The Spine

Reactive functions are not a bypass of ELLE's architecture. When a reactive function fires:

1. An Incident Report is created
2. The actions execute through the CapabilityExecutor
3. Policy is enforced (actions can be blocked or require confirmation)
4. Evidence is recorded
5. The outcome is stored in Incident Memory

This means reactive function executions benefit from the same learning loop as interactive operations — if a reactive function's action resolves an issue, that solution is remembered for future similar incidents.
