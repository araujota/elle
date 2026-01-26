"""Built-in reactive function templates.

Provides pre-configured reactive functions for common automation patterns
that can be enabled by users or triggered by the system.

Templates are designed to be:
- Safe by default (rate-limited, confirmations where appropriate)
- Integrable with the incident vault
- Composable with existing capabilities
"""

from __future__ import annotations

from elle.reactive.models import (
    ActionSpec,
    EventTrigger,
    PolicySpec,
    ReactiveFunction,
    Trigger,
)

# =============================================================================
# Docker Auto-Diagnose Template
# =============================================================================

DOCKER_AUTO_DIAGNOSE = ReactiveFunction(
    id="builtin:docker-auto-diagnose",
    name="docker-auto-diagnose",
    description=(
        "Automatically diagnose container failures when containers die, "
        "are OOM killed, or are terminated unexpectedly. Rate-limited to "
        "avoid spam during crash loops."
    ),
    enabled=True,
    created_by="builtin",
    trigger=Trigger(
        type="event",
        event=EventTrigger(
            source="probe",
            category="docker",
            severity="warning",
            match={
                # Match container death events
                # Docker events contain fields like:
                # - _DOCKER_EVENT: "die", "oom", "kill", "stop"
                # - _DOCKER_CONTAINER_ID: container ID
                # - _DOCKER_CONTAINER_NAME: container name
                "$or": [
                    {"_DOCKER_EVENT": "die"},
                    {"_DOCKER_EVENT": "oom"},
                    {"_DOCKER_EVENT": "kill"},
                ],
            },
        ),
    ),
    condition=None,  # Always run on matching events
    actions=(
        ActionSpec(
            capability="docker.diagnose",
            input={
                # Template variable resolved at execution time
                "container": "{event.raw._DOCKER_CONTAINER_ID}",
            },
            on_failure="continue",  # Don't block on diagnosis failure
        ),
    ),
    policy=PolicySpec(
        max_frequency="1m",  # Rate limit: at most once per minute per container
        max_daily_executions=100,  # Don't flood with diagnoses
        require_confirmation=False,  # Diagnostic is read-only
        escalate_on_failure=False,  # Don't alert if diagnosis fails
    ),
    tags=("docker", "diagnose", "builtin"),
    source_prompt=None,  # Not created from NL
)


# =============================================================================
# Disk Pressure Auto-Cleanup Template
# =============================================================================

DISK_PRESSURE_CLEANUP = ReactiveFunction(
    id="builtin:disk-pressure-cleanup",
    name="disk-pressure-cleanup",
    description=(
        "Automatically clean Docker resources when disk usage exceeds 85%. Requires confirmation before execution."
    ),
    enabled=False,  # Disabled by default - user must enable
    created_by="builtin",
    trigger=Trigger(
        type="event",
        event=EventTrigger(
            source="probe",
            category="disk",
            severity="warning",
        ),
    ),
    condition=None,  # Add condition for disk threshold in evaluator
    actions=(
        ActionSpec(
            capability="docker.prune",
            input={
                "all_images": False,  # Only dangling images
                "volumes": False,  # Don't touch volumes
            },
            on_failure="stop",
        ),
    ),
    policy=PolicySpec(
        max_frequency="1h",  # At most once per hour
        max_daily_executions=5,
        require_confirmation=True,  # Requires user confirmation
        escalate_on_failure=True,
    ),
    tags=("disk", "docker", "cleanup", "builtin"),
)


# =============================================================================
# Container Restart Alert Template
# =============================================================================

CONTAINER_RESTART_ALERT = ReactiveFunction(
    id="builtin:container-restart-alert",
    name="container-restart-alert",
    description=("Send notification when a container enters a restart loop (more than 3 restarts detected)."),
    enabled=False,  # Disabled by default
    created_by="builtin",
    trigger=Trigger(
        type="event",
        event=EventTrigger(
            source="probe",
            category="docker",
            severity="warning",
            match={
                "restart_count": {"$gt": 3},
            },
        ),
    ),
    condition=None,
    actions=(
        ActionSpec(
            capability="notify.send",
            input={
                "title": "Container Restart Loop",
                "message": "Container {event.raw._DOCKER_CONTAINER_NAME} has restarted {event.raw.restart_count} times",
                "priority": "high",
            },
            on_failure="continue",
        ),
    ),
    policy=PolicySpec(
        max_frequency="15m",  # Don't spam notifications
        max_daily_executions=20,
        require_confirmation=False,
    ),
    tags=("docker", "alert", "builtin"),
)


# =============================================================================
# Exports
# =============================================================================

# All built-in templates
BUILTIN_TEMPLATES: tuple[ReactiveFunction, ...] = (
    DOCKER_AUTO_DIAGNOSE,
    DISK_PRESSURE_CLEANUP,
    CONTAINER_RESTART_ALERT,
)


def get_template(template_id: str) -> ReactiveFunction | None:
    """Get a built-in template by ID.

    Args:
        template_id: Template ID (e.g., "builtin:docker-auto-diagnose").

    Returns:
        ReactiveFunction or None if not found.
    """
    for template in BUILTIN_TEMPLATES:
        if template.id == template_id:
            return template
    return None


def list_templates() -> list[ReactiveFunction]:
    """List all built-in templates.

    Returns:
        List of all built-in reactive function templates.
    """
    return list(BUILTIN_TEMPLATES)
