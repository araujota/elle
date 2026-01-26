"""Human-readable formatters for notification messages.

Transforms raw telemetry data, incident reports, and system metrics
into clear, descriptive notification text without requiring LLM inference.
"""

from __future__ import annotations

from typing import Any

# =============================================================================
# Entity Formatting
# =============================================================================


def format_entity(entity: str | None) -> str:
    """Format an entity string into human-readable text.

    Args:
        entity: Entity string like "service:nginx", "interface:eth0", "disk:/dev/sda"

    Returns:
        Human-readable string like "the nginx service" or "interface eth0"
    """
    if not entity:
        return ""

    if ":" not in entity:
        return entity

    entity_type, entity_name = entity.split(":", 1)

    # Map entity types to natural language
    type_formats = {
        "service": f"the {entity_name} service",
        "interface": f"network interface {entity_name}",
        "disk": f"disk {entity_name}",
        "mount": f"mount point {entity_name}",
        "container": f"container {entity_name}",
        "process": f"process {entity_name}",
        "user": f"user {entity_name}",
        "package": f"package {entity_name}",
        "file": f"file {entity_name}",
    }

    return type_formats.get(entity_type, f"{entity_type} {entity_name}")


def format_entities(entities: tuple[str, ...] | list[str], max_show: int = 3) -> str:
    """Format multiple entities into a readable list.

    Args:
        entities: List of entity strings.
        max_show: Maximum entities to show before "and X more".

    Returns:
        Human-readable list like "nginx, postgres, and redis services"
    """
    if not entities:
        return ""

    formatted = [format_entity(e) for e in entities[:max_show]]

    if len(entities) > max_show:
        remaining = len(entities) - max_show
        formatted.append(f"and {remaining} more")

    if len(formatted) == 1:
        return formatted[0]
    elif len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    else:
        return ", ".join(formatted[:-1]) + ", " + formatted[-1]


# =============================================================================
# Metric Formatting
# =============================================================================


def format_percentage(value: float, label: str) -> str:
    """Format a percentage metric.

    Args:
        value: Value between 0 and 1 (or 0 and 100).
        label: What the percentage represents.

    Returns:
        Human-readable string like "disk at 92%"
    """
    # Normalize to percentage
    pct = value * 100 if value <= 1 else value
    return f"{label} at {pct:.0f}%"


def format_bytes(bytes_val: int, label: str = "") -> str:
    """Format bytes into human-readable size.

    Args:
        bytes_val: Size in bytes.
        label: Optional label prefix.

    Returns:
        Human-readable string like "512 MB" or "2.5 GB"
    """
    if bytes_val < 1024:
        size_str = f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        size_str = f"{bytes_val / 1024:.0f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        size_str = f"{bytes_val / (1024 * 1024):.0f} MB"
    else:
        size_str = f"{bytes_val / (1024 * 1024 * 1024):.1f} GB"

    return f"{label} {size_str}".strip() if label else size_str


def format_mb(mb_val: int, label: str = "") -> str:
    """Format megabytes into human-readable size.

    Args:
        mb_val: Size in megabytes.
        label: Optional label prefix.

    Returns:
        Human-readable string like "512 MB" or "2.5 GB"
    """
    size_str = f"{mb_val} MB" if mb_val < 1024 else f"{mb_val / 1024:.1f} GB"
    return f"{label} {size_str}".strip() if label else size_str


def format_duration_seconds(seconds: int) -> str:
    """Format duration in seconds to human-readable form.

    Args:
        seconds: Duration in seconds.

    Returns:
        Human-readable string like "5 minutes" or "2 hours 30 minutes"
    """
    if seconds < 60:
        return f"{seconds} seconds"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins} minute{'s' if mins != 1 else ''}"
    elif seconds < 86400:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        if mins:
            return f"{hours} hour{'s' if hours != 1 else ''} {mins} min"
        return f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"


# =============================================================================
# Domain-Specific Formatters
# =============================================================================


def format_disk_issue(
    mount: str | None = None,
    used_pct: float | None = None,
    avail_gb: float | None = None,
) -> str:
    """Format a disk space issue into human-readable text.

    Args:
        mount: Mount point (e.g., "/", "/home", "/var").
        used_pct: Percentage of disk used.
        avail_gb: Available space in GB.

    Returns:
        Human-readable description like "Disk /var is 95% full (2.1 GB free)"
    """
    parts = []

    if mount:
        parts.append(f"Disk {mount}")
    else:
        parts.append("Disk")

    if used_pct is not None:
        # Normalize to percentage
        pct = used_pct * 100 if used_pct <= 1 else used_pct
        parts.append(f"is {pct:.0f}% full")

    if avail_gb is not None:
        if avail_gb < 1:
            parts.append(f"({avail_gb * 1024:.0f} MB free)")
        else:
            parts.append(f"({avail_gb:.1f} GB free)")

    return " ".join(parts)


def format_memory_issue(
    available_mb: int | None = None,
    total_mb: int | None = None,
    swap_used_mb: int | None = None,
    swap_total_mb: int | None = None,
) -> str:
    """Format a memory issue into human-readable text.

    Args:
        available_mb: Available memory in MB.
        total_mb: Total memory in MB.
        swap_used_mb: Used swap in MB.
        swap_total_mb: Total swap in MB.

    Returns:
        Human-readable description like "Low memory: 256 MB available of 8 GB"
    """
    parts = []

    if available_mb is not None and total_mb is not None:
        pct_used = ((total_mb - available_mb) / total_mb) * 100
        parts.append(
            f"Memory at {pct_used:.0f}%: {format_mb(available_mb)} available "
            f"of {format_mb(total_mb)}"
        )
    elif available_mb is not None:
        parts.append(f"Only {format_mb(available_mb)} memory available")

    if swap_used_mb is not None and swap_total_mb is not None and swap_total_mb > 0:
        swap_pct = (swap_used_mb / swap_total_mb) * 100
        if swap_pct > 50:
            parts.append(f"Swap at {swap_pct:.0f}% ({format_mb(swap_used_mb)} used)")

    return ". ".join(parts) if parts else "Memory pressure detected"


def format_service_issue(
    service_name: str,
    state: str | None = None,
    exit_code: int | None = None,
) -> str:
    """Format a service issue into human-readable text.

    Args:
        service_name: Name of the service.
        state: Current state (failed, inactive, etc.).
        exit_code: Exit code if failed.

    Returns:
        Human-readable description like "nginx.service failed (exit code 1)"
    """
    parts = [f"{service_name}"]

    if state:
        state_descriptions = {
            "failed": "has failed",
            "inactive": "is stopped",
            "activating": "is starting",
            "deactivating": "is stopping",
            "reloading": "is reloading",
            "active": "is running",
        }
        parts.append(state_descriptions.get(state, f"is {state}"))
    else:
        parts.append("has failed")

    if exit_code is not None and exit_code != 0:
        parts.append(f"(exit code {exit_code})")

    return " ".join(parts)


def format_network_issue(
    interface: str | None = None,
    state: str | None = None,
    rx_errors: int | None = None,
    tx_errors: int | None = None,
) -> str:
    """Format a network issue into human-readable text.

    Args:
        interface: Network interface name.
        state: Interface state (up, down, etc.).
        rx_errors: Receive errors.
        tx_errors: Transmit errors.

    Returns:
        Human-readable description like "eth0 is down" or "eth0 has 15 rx errors"
    """
    parts = []

    if interface:
        parts.append(f"Interface {interface}")
    else:
        parts.append("Network interface")

    if state:
        if state.lower() == "down":
            parts.append("is down")
        elif state.lower() == "up":
            parts.append("is up")
        else:
            parts.append(f"state is {state}")

    error_parts = []
    if rx_errors and rx_errors > 0:
        error_parts.append(f"{rx_errors} receive errors")
    if tx_errors and tx_errors > 0:
        error_parts.append(f"{tx_errors} transmit errors")

    if error_parts:
        if "is" not in " ".join(parts):
            parts.append("has")
        parts.append(" and ".join(error_parts))

    return " ".join(parts)


def format_docker_issue(
    container: str | None = None,
    state: str | None = None,
    exit_code: int | None = None,
    image: str | None = None,
) -> str:
    """Format a Docker container issue into human-readable text.

    Args:
        container: Container name.
        state: Container state (running, exited, etc.).
        exit_code: Exit code if exited.
        image: Container image name.

    Returns:
        Human-readable description like "Container webapp exited (code 137)"
    """
    parts = []

    if container:
        parts.append(f"Container {container}")
    elif image:
        parts.append(f"Container ({image})")
    else:
        parts.append("Container")

    if state:
        state_lower = state.lower()
        if state_lower == "exited":
            if exit_code is not None:
                if exit_code == 137:
                    parts.append("was killed (OOM or SIGKILL)")
                elif exit_code == 143:
                    parts.append("was terminated (SIGTERM)")
                elif exit_code == 0:
                    parts.append("exited normally")
                else:
                    parts.append(f"exited with code {exit_code}")
            else:
                parts.append("has exited")
        elif state_lower == "dead":
            parts.append("is in dead state")
        elif state_lower == "restarting":
            parts.append("is restarting")
        elif state_lower == "paused":
            parts.append("is paused")
        elif state_lower == "running":
            parts.append("is running")
        else:
            parts.append(f"is {state}")

    return " ".join(parts)


def format_oom_event(
    process: str | None = None,
    pid: int | None = None,
    score: int | None = None,
) -> str:
    """Format an OOM kill event into human-readable text.

    Args:
        process: Process name that was killed.
        pid: Process ID.
        score: OOM score.

    Returns:
        Human-readable description like "Process mysql (PID 1234) killed by OOM"
    """
    parts = []

    if process:
        parts.append(f"Process {process}")
        if pid:
            parts.append(f"(PID {pid})")
    elif pid:
        parts.append(f"Process with PID {pid}")
    else:
        parts.append("A process")

    parts.append("was killed by the OOM killer")

    if score is not None:
        parts.append(f"(score: {score})")

    return " ".join(parts)


def format_temperature_issue(
    sensor: str | None = None,
    temp_c: int | float | None = None,
    threshold_c: int | float | None = None,
) -> str:
    """Format a temperature warning into human-readable text.

    Args:
        sensor: Sensor name.
        temp_c: Current temperature in Celsius.
        threshold_c: Warning threshold.

    Returns:
        Human-readable description like "CPU temperature at 85°C (warning at 80°C)"
    """
    parts = []

    if sensor:
        # Clean up common sensor names
        sensor_clean = sensor.replace("_", " ").replace("-", " ")
        if "core" in sensor_clean.lower():
            parts.append("CPU core temperature")
        elif "gpu" in sensor_clean.lower():
            parts.append("GPU temperature")
        else:
            parts.append(f"{sensor_clean} temperature")
    else:
        parts.append("Temperature")

    if temp_c is not None:
        parts.append(f"at {temp_c:.0f}°C")

    if threshold_c is not None:
        parts.append(f"(threshold: {threshold_c:.0f}°C)")

    return " ".join(parts)


# =============================================================================
# Incident Formatters
# =============================================================================


def format_incident_title(
    domain: str,
    title: str,
    severity: str,
    entity: str | None = None,
) -> str:
    """Format an incident title for notification.

    Args:
        domain: Incident domain (disk, net, oom, etc.).
        title: Original incident title.
        severity: Incident severity.
        entity: Primary affected entity.

    Returns:
        Formatted title like "Disk Full on /var" or "Service Failed: nginx"
    """
    # Domain-specific title formatting
    domain_lower = domain.lower()

    if domain_lower == "disk":
        if entity and "mount:" in entity:
            mount = entity.split(":", 1)[1]
            if "full" in title.lower() or "space" in title.lower():
                return f"Disk Full on {mount}"
            return f"Disk Issue on {mount}"
        return title

    elif domain_lower == "oom":
        return "Out of Memory"

    elif domain_lower == "service":
        if entity and "service:" in entity:
            service = entity.split(":", 1)[1]
            if "fail" in title.lower():
                return f"Service Failed: {service}"
            return f"Service Issue: {service}"
        return title

    elif domain_lower == "net":
        if entity and "interface:" in entity:
            iface = entity.split(":", 1)[1]
            if "down" in title.lower():
                return f"Network Down: {iface}"
            return f"Network Issue: {iface}"
        return "Network Issue"

    elif domain_lower == "docker":
        if entity and "container:" in entity:
            container = entity.split(":", 1)[1]
            if "exit" in title.lower() or "fail" in title.lower():
                return f"Container Failed: {container}"
            return f"Container Issue: {container}"
        return "Container Issue"

    elif domain_lower == "auth":
        return "Authentication Issue"

    elif domain_lower == "pkg":
        return "Package Issue"

    elif domain_lower == "fs":
        return "Filesystem Issue"

    # Default: use original title with severity indicator for critical
    if severity == "critical":
        return f"CRITICAL: {title}"
    return title


def format_incident_body(
    domain: str,
    summary: str | None = None,
    symptoms: tuple[str, ...] | list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    entities: tuple[str, ...] | list[str] | None = None,
    suspected_causes: tuple[str, ...] | list[str] | None = None,
    max_lines: int = 4,
) -> str:
    """Format an incident body for notification.

    Args:
        domain: Incident domain.
        summary: Incident summary text.
        symptoms: List of symptom descriptions.
        metrics: Dictionary of relevant metrics.
        entities: Affected entities.
        suspected_causes: Suspected causes.
        max_lines: Maximum lines for the body.

    Returns:
        Formatted notification body.
    """
    lines = []

    # Add domain-specific metric summary
    if metrics:
        metric_line = _format_domain_metrics(domain, metrics)
        if metric_line:
            lines.append(metric_line)

    # Add affected entities if not too many
    if entities and len(entities) <= 3:
        entity_line = format_entities(list(entities), max_show=3)
        if entity_line and "and" not in entity_line:  # Skip if already complex
            lines.append(f"Affected: {entity_line}")

    # Add top symptom or summary
    if symptoms:
        # Use first symptom, it's usually the most important
        first_symptom = symptoms[0]
        if len(first_symptom) <= 80:
            lines.append(first_symptom)
        else:
            lines.append(first_symptom[:77] + "...")
    elif summary:
        # Use summary if no symptoms
        if len(summary) <= 100:
            lines.append(summary)
        else:
            # Take first sentence
            first_sentence = summary.split(". ")[0]
            if len(first_sentence) <= 100:
                lines.append(first_sentence + ".")
            else:
                lines.append(first_sentence[:97] + "...")

    # Add suspected cause if space allows
    if len(lines) < max_lines and suspected_causes:
        cause = suspected_causes[0]
        if len(cause) <= 60:
            lines.append(f"Possible cause: {cause}")

    return "\n".join(lines[:max_lines])


def _format_domain_metrics(domain: str, metrics: dict[str, Any]) -> str:
    """Format domain-specific metrics into a summary line.

    Args:
        domain: Incident domain.
        metrics: Metrics dictionary.

    Returns:
        Single line metric summary.
    """
    domain_lower = domain.lower()

    if domain_lower == "disk":
        # Look for disk percentage
        for key in ("disk_pct", "used_pct", "disk_pressure"):
            if key in metrics:
                pct = metrics[key]
                pct = pct * 100 if pct <= 1 else pct
                mount = metrics.get("mount", metrics.get("path", ""))
                if mount:
                    return f"{mount} at {pct:.0f}% full"
                return f"Disk at {pct:.0f}% full"
        # Look for available space
        if "avail_gb" in metrics:
            return f"{metrics['avail_gb']:.1f} GB remaining"

    elif domain_lower == "oom":
        if "mem_pressure" in metrics:
            pct = metrics["mem_pressure"]
            pct = pct * 100 if pct <= 1 else pct
            return f"Memory at {pct:.0f}% usage"
        if "oom_count_1h" in metrics:
            count = metrics["oom_count_1h"]
            return f"{count} OOM kill{'s' if count != 1 else ''} in the last hour"

    elif domain_lower == "service":
        if "failed_services" in metrics:
            count = metrics["failed_services"]
            return f"{count} service{'s' if count != 1 else ''} failed"

    elif domain_lower == "net":
        if "rx_errors" in metrics or "tx_errors" in metrics:
            rx = metrics.get("rx_errors", 0)
            tx = metrics.get("tx_errors", 0)
            total = rx + tx
            return f"{total} network error{'s' if total != 1 else ''}"
        if "net_flaps_1h" in metrics:
            flaps = metrics["net_flaps_1h"]
            return f"{flaps} network state change{'s' if flaps != 1 else ''} in the last hour"

    elif domain_lower == "docker":
        if "docker_exited_count" in metrics:
            count = metrics["docker_exited_count"]
            return f"{count} container{'s' if count != 1 else ''} exited"

    return ""


# =============================================================================
# Health Alert Formatters
# =============================================================================


def format_health_title(component: str, severity: str) -> str:
    """Format a health alert title.

    Args:
        component: System component (disk, memory, cpu, etc.).
        severity: Alert severity.

    Returns:
        Formatted title like "Disk Space Warning" or "CRITICAL: Memory"
    """
    component_titles = {
        "disk": "Disk Space",
        "memory": "Memory",
        "mem": "Memory",
        "swap": "Swap Usage",
        "cpu": "CPU Load",
        "temperature": "Temperature",
        "temp": "Temperature",
        "network": "Network",
        "net": "Network",
        "service": "Service Health",
        "docker": "Container Health",
    }

    title = component_titles.get(component.lower(), component.title())

    severity_formats = {
        "critical": f"CRITICAL: {title}",
        "warning": f"{title} Warning",
        "recovered": f"{title} Recovered",
    }
    return severity_formats.get(severity, title)


def format_health_body(
    component: str,
    message: str,
    metrics: dict[str, Any] | None = None,
) -> str:
    """Format a health alert body with context.

    Args:
        component: System component.
        message: Alert message.
        metrics: Optional metrics for context.

    Returns:
        Formatted body with context.
    """
    lines = []

    # Add specific metric context based on component
    if metrics:
        component_lower = component.lower()

        if component_lower in ("disk", "storage"):
            if "mount" in metrics and "used_pct" in metrics:
                pct = metrics["used_pct"]
                pct = pct * 100 if pct <= 1 else pct
                lines.append(f"{metrics['mount']} is {pct:.0f}% full")
            if "avail_gb" in metrics:
                lines.append(f"{metrics['avail_gb']:.1f} GB remaining")

        elif component_lower in ("memory", "mem", "ram"):
            if "available_mb" in metrics:
                lines.append(f"{format_mb(metrics['available_mb'])} available")
            if "used_pct" in metrics:
                pct = metrics["used_pct"]
                pct = pct * 100 if pct <= 1 else pct
                lines.append(f"Memory at {pct:.0f}%")

        elif component_lower == "swap":
            if "used_pct" in metrics:
                pct = metrics["used_pct"]
                pct = pct * 100 if pct <= 1 else pct
                lines.append(f"Swap at {pct:.0f}%")

        elif component_lower in ("cpu", "load"):
            if "load_1m" in metrics:
                lines.append(f"Load average: {metrics['load_1m']:.2f}")

        elif component_lower in ("temperature", "temp", "thermal"):
            if "temp_c" in metrics:
                lines.append(f"Temperature: {metrics['temp_c']:.0f}°C")

    # Add the original message if not redundant with metrics
    if message and message not in "\n".join(lines):
        lines.append(message)

    return "\n".join(lines)


# =============================================================================
# Event Formatters
# =============================================================================


def format_event_message(
    category: str,
    message: str,
    entity: str | None = None,
    raw: dict[str, Any] | None = None,
) -> str:
    """Format a telemetry event into a human-readable message.

    Args:
        category: Event category.
        message: Original event message.
        entity: Affected entity.
        raw: Raw event data for context.

    Returns:
        Formatted human-readable message.
    """
    # If message is already human-readable, use it
    if message and not message.startswith("{") and len(message) < 200:
        # Just add entity context if helpful
        if entity:
            entity_str = format_entity(entity)
            if entity_str.lower() not in message.lower():
                return f"{message} ({entity_str})"
        return message

    # Otherwise, build from category and entity
    category_lower = category.lower()

    if category_lower == "oom":
        if raw:
            process = raw.get("process", raw.get("comm"))
            pid = raw.get("pid")
            return format_oom_event(process=process, pid=pid)
        return "Out of memory event detected"

    elif category_lower == "disk":
        if raw:
            mount = raw.get("mount", raw.get("path"))
            pct = raw.get("used_pct", raw.get("disk_pct"))
            avail = raw.get("avail_gb")
            return format_disk_issue(mount=mount, used_pct=pct, avail_gb=avail)
        return "Disk issue detected"

    elif category_lower == "service":
        if entity and "service:" in entity:
            service = entity.split(":", 1)[1]
            state = raw.get("state") if raw else None
            return format_service_issue(service, state=state)
        return "Service issue detected"

    elif category_lower == "net":
        if entity and "interface:" in entity:
            iface = entity.split(":", 1)[1]
            state = raw.get("state") if raw else None
            return format_network_issue(iface, state=state)
        return "Network issue detected"

    elif category_lower == "docker":
        if entity and "container:" in entity:
            container = entity.split(":", 1)[1]
            state = raw.get("state") if raw else None
            exit_code = raw.get("exit_code") if raw else None
            return format_docker_issue(container, state=state, exit_code=exit_code)
        return "Container issue detected"

    elif category_lower == "thermal":
        if raw:
            sensor = raw.get("sensor")
            temp = raw.get("temp_c", raw.get("temperature"))
            return format_temperature_issue(sensor=sensor, temp_c=temp)
        return "Temperature warning"

    # Default: clean up the message
    if message:
        # Truncate if too long
        if len(message) > 100:
            return message[:97] + "..."
        return message

    return f"{category.title()} event"
