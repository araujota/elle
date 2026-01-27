"""Incident recording for agentic loop executions.

This module bridges the agentic loop with the incident vault, ensuring
that every agent execution creates a complete incident record.

The incident record includes:
- Full execution trace (stages, hypotheses, tool calls)
- Provenance (what sources informed the decision)
- Actions taken (capabilities executed)
- Outcome (success/failure, verification status)

This is ESSENTIAL for the Spine architecture:
    DAEMON -> SIGNALS -> INCIDENT REPORT -> AGENT LOOP -> CAPABILITIES -> OUTCOME -> INCIDENT MEMORY

Key functions:
- record_execution_to_incident(): Full execution recording
- record_arm_action(): Simplified recording for standalone modules
- RecordingResult: Structured result with error handling
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


# =============================================================================
# Recording Result Types
# =============================================================================


@dataclass
class RecordingResult:
    """Result from a recording operation."""

    success: bool
    incident_id: str | None = None
    error: str | None = None
    recoverable: bool = True

    def __bool__(self) -> bool:
        return self.success


def _with_retry(
    func: Any,
    max_retries: int = 2,
    base_delay: float = 0.1,
) -> RecordingResult:
    """Execute a recording function with retry and exponential backoff.

    Args:
        func: Callable that returns an incident_id or raises.
        max_retries: Maximum number of retries.
        base_delay: Base delay between retries (doubles each retry).

    Returns:
        RecordingResult with outcome.
    """
    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            result = func()
            return RecordingResult(
                success=True,
                incident_id=result,
            )
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.debug(f"Recording attempt {attempt + 1} failed, retrying in {delay}s: {e}")
                time.sleep(delay)
            else:
                logger.warning(f"Recording failed after {max_retries + 1} attempts: {e}")

    return RecordingResult(
        success=False,
        error=last_error,
        recoverable=True,  # Database errors are typically recoverable
    )


def record_execution_to_incident(
    execution_id: str,
    user_input: str,
    classified_intent: str,
    response: str,
    success: bool,
    iterations: int,
    total_duration_ms: int,
    tool_calls: list[dict[str, Any]],
    retrieval_context: Any | None = None,
    hypotheses: list[dict[str, Any]] | None = None,
    stage_transitions: list[dict[str, Any]] | None = None,
    capabilities_executed: list[str] | None = None,
    error: str | None = None,
    existing_incident_id: str | None = None,
) -> str | None:
    """Record an agentic loop execution to the incident vault.

    This creates or updates an incident with the full execution record,
    ensuring complete provenance and auditability.

    Args:
        execution_id: The loop's execution ID.
        user_input: The original user input.
        classified_intent: How the input was classified.
        response: The final response text.
        success: Whether the execution succeeded.
        iterations: Number of loop iterations.
        total_duration_ms: Total execution time.
        tool_calls: List of tool call records.
        retrieval_context: Retrieval results (for provenance).
        hypotheses: Hypotheses considered during execution.
        stage_transitions: Stage transition records.
        capabilities_executed: Names of capabilities that were executed.
        error: Error message if failed.
        existing_incident_id: If provided, update this incident instead of creating new.

    Returns:
        The incident ID (created or updated), or None if recording failed.
    """
    try:
        from elle.daemon.incidents.store import (
            append_action,
            create_incident_draft,
            get_incident,
            update_incident,
        )

        # Determine if we should create or update an incident
        incident_id = existing_incident_id

        # Determine domain and severity from intent and outcome
        domain = _infer_domain(user_input, classified_intent, capabilities_executed or [])
        severity = "error" if not success else "info"
        trigger_source = "user_task" if classified_intent == "system_task" else "manual"

        # Create incident if needed
        if incident_id:
            incident = get_incident(incident_id)
            if not incident:
                logger.warning(f"Incident {incident_id} not found, creating new")
                incident_id = None

        if not incident_id:
            # Create title from user input
            title = _generate_title(user_input, classified_intent)

            incident = create_incident_draft(
                title=title,
                domain=domain,
                severity=severity,
                trigger_source=trigger_source,
            )
            incident_id = incident.incident_id
            logger.info(f"Created incident {incident_id} for execution {execution_id}")

        # Build confidence breakdown
        confidence = _calculate_confidence(retrieval_context, success, iterations)

        # Build summary
        summary = _build_summary(
            user_input=user_input,
            response=response,
            success=success,
            tool_calls=tool_calls,
            capabilities_executed=capabilities_executed or [],
        )

        # Update incident with execution details
        update_incident(
            incident_id,
            status="resolved" if success else "open",
            summary=summary,
            outcome="improved" if success else "no_change",
            confidence=confidence.total_confidence() if hasattr(confidence, "total_confidence") else 0.5,
            decision={
                "execution_id": execution_id,
                "classified_intent": classified_intent,
                "iterations": iterations,
                "duration_ms": total_duration_ms,
                "hypotheses": hypotheses or [],
                "stage_transitions": stage_transitions or [],
                "error": error,
            },
        )

        # Record tool calls as incident actions
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "unknown")
            arguments = tc.get("arguments", {})
            result = tc.get("result", {})

            # Determine action kind
            if tool_name == "execute_capability":
                kind = "capability"
                cap_name = arguments.get("capability_name", "")
                command = f"capability:{cap_name}"
            elif tool_name == "shell_command":
                kind = "shell"
                command = arguments.get("command", "")
            else:
                kind = "verify"  # Search/lookup operations are verification
                command = f"{tool_name}({json.dumps(arguments)[:100]})"

            # Extract result details
            result_output = result.get("output", "") if isinstance(result, dict) else str(result)
            result_success = result.get("success", True) if isinstance(result, dict) else True
            result_duration = result.get("duration_ms", 0) if isinstance(result, dict) else 0

            append_action(
                incident_id=incident_id,
                kind=kind,
                command=command,
                payload={
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "execution_id": execution_id,
                    "iteration": tc.get("iteration", 1),
                    "verified": tc.get("verified", False),
                },
                stdout=result_output[:5000] if result_output else None,
                success=result_success,
                duration_ms=result_duration,
            )

        logger.info(
            f"Recorded execution {execution_id} to incident {incident_id}: "
            f"{len(tool_calls)} actions, success={success}"
        )

        return incident_id

    except Exception as e:
        logger.exception(f"Failed to record execution to incident: {e}")
        return None


def _infer_domain(
    user_input: str,
    classified_intent: str,
    capabilities_executed: list[str],
) -> str:
    """Infer incident domain from context."""
    lower_input = user_input.lower()

    # Check capabilities first
    for cap in capabilities_executed:
        if cap.startswith("service."):
            return "service"
        if cap.startswith("docker."):
            return "docker"
        if cap.startswith("network."):
            return "net"
        if cap.startswith("file.") or cap.startswith("config."):
            return "fs"
        if cap.startswith("package."):
            return "pkg"

    # Check input keywords
    domain_keywords = {
        "net": ["network", "connection", "dns", "firewall", "port", "ip", "ping", "curl"],
        "docker": ["docker", "container", "compose", "image"],
        "service": ["service", "systemd", "restart", "stop", "start", "status"],
        "disk": ["disk", "storage", "mount", "space", "full"],
        "oom": ["memory", "oom", "ram", "swap"],
        "pkg": ["package", "install", "apt", "dpkg", "upgrade"],
        "auth": ["permission", "sudo", "password", "login", "ssh"],
    }

    for domain, keywords in domain_keywords.items():
        if any(kw in lower_input for kw in keywords):
            return domain

    return "other"


def _generate_title(user_input: str, classified_intent: str) -> str:
    """Generate incident title from user input."""
    # Truncate and clean
    title = user_input.strip()
    if len(title) > 100:
        title = title[:97] + "..."

    # Add prefix based on intent
    if classified_intent == "system_question":
        if not title.endswith("?"):
            title = f"Question: {title}"
    elif classified_intent == "system_task":
        title = f"Task: {title}"

    return title


def _build_provenance(retrieval_context: Any | None, tool_calls: list[dict[str, Any]]) -> Any:
    """Build provenance from retrieval context."""
    try:
        from elle.daemon.incidents.models import (
            IncidentCitation,
            ManPageCitation,
            PrimarySource,
            Provenance,
        )

        man_pages: list[ManPageCitation] = []
        prior_incidents: list[IncidentCitation] = []
        primary_source: PrimarySource = "llm_only"

        if retrieval_context:
            # Extract man vault citations
            if hasattr(retrieval_context, "man_vault_sources"):
                for source in retrieval_context.man_vault_sources[:5]:
                    man_pages.append(
                        ManPageCitation(
                            name=source if isinstance(source, str) else str(source),
                            section="",
                            snippet="",
                            relevance_score=0.8,
                        )
                    )
                if man_pages:
                    primary_source = "man_vault"

            # Extract incident citations
            if hasattr(retrieval_context, "incident_ids"):
                for inc_id in retrieval_context.incident_ids[:3]:
                    prior_incidents.append(
                        IncidentCitation(
                            incident_id=inc_id,
                            title="",
                            outcome="unknown",
                            similarity_score=0.7,
                            match_type="semantic",
                        )
                    )
                if prior_incidents and primary_source == "llm_only":
                    primary_source = "incident_vault"

        # Check tool calls for additional sources
        for tc in tool_calls:
            if tc.get("tool_name") == "search_man_vault":
                primary_source = "man_vault"
            elif tc.get("tool_name") == "search_incidents":
                if primary_source == "llm_only":
                    primary_source = "incident_vault"

        return Provenance(
            man_pages=tuple(man_pages),
            prior_incidents=tuple(prior_incidents),
            primary_source=primary_source,
        )

    except Exception as e:
        logger.debug(f"Failed to build provenance: {e}")
        from elle.daemon.incidents.models import Provenance
        return Provenance(primary_source="llm_only")


def _calculate_confidence(
    retrieval_context: Any | None,
    success: bool,
    iterations: int,
) -> Any:
    """Calculate confidence breakdown."""
    try:
        from elle.daemon.incidents.models import ConfidenceBreakdown

        # Base confidence from retrieval
        from_man_vault = 0.0
        from_incident_vault = 0.0
        from_telemetry = 0.0
        from_llm = 0.5  # Base LLM confidence

        if retrieval_context:
            if hasattr(retrieval_context, "man_vault_hits") and retrieval_context.man_vault_hits > 0:
                from_man_vault = min(0.3, retrieval_context.man_vault_hits * 0.1)
            if hasattr(retrieval_context, "incident_vault_hits") and retrieval_context.incident_vault_hits > 0:
                from_incident_vault = min(0.25, retrieval_context.incident_vault_hits * 0.1)

        # Adjust based on outcome
        if success:
            from_llm = min(0.5, from_llm + 0.1)
        else:
            from_llm = max(0.2, from_llm - 0.2)

        # Penalize high iteration count
        if iterations > 5:
            from_llm = max(0.1, from_llm - 0.1)

        # Calculate overall confidence
        overall = min(1.0, from_man_vault + from_incident_vault + from_telemetry + from_llm)

        # Determine dominant tier
        scores = {
            "fingerprint": from_man_vault,  # Man vault uses fingerprint matching
            "lexical": from_incident_vault,  # Incident vault uses lexical search
            "semantic": from_telemetry,  # Telemetry correlation
            "llm": from_llm,
        }
        dominant_tier = max(scores, key=lambda k: scores[k])

        return ConfidenceBreakdown(
            overall=overall,
            from_man_vault=from_man_vault,
            from_incident_vault=from_incident_vault,
            from_telemetry=from_telemetry,
            from_llm=from_llm,
            dominant_tier=dominant_tier,  # type: ignore[arg-type]
        )

    except Exception as e:
        logger.debug(f"Failed to calculate confidence: {e}")
        from elle.daemon.incidents.models import ConfidenceBreakdown
        return ConfidenceBreakdown(overall=0.5, from_llm=0.5)


def _build_rationale(
    hypotheses: list[dict[str, Any]] | None,
    tool_calls: list[dict[str, Any]],
) -> str:
    """Build rationale from hypotheses and tool calls."""
    parts = []

    if hypotheses:
        chosen = [h for h in hypotheses if h.get("chosen")]
        if chosen:
            parts.append(f"Chosen hypothesis: {chosen[0].get('text', 'N/A')[:200]}")
        parts.append(f"Considered {len(hypotheses)} hypotheses.")

    if tool_calls:
        tool_names = [tc.get("tool_name", "unknown") for tc in tool_calls]
        unique_tools = list(dict.fromkeys(tool_names))  # Preserve order, remove duplicates
        parts.append(f"Used tools: {', '.join(unique_tools[:5])}")

    return " ".join(parts) if parts else "Executed via agentic loop."


def _build_summary(
    user_input: str,
    response: str,
    success: bool,
    tool_calls: list[dict[str, Any]],
    capabilities_executed: list[str],
) -> str:
    """Build incident summary."""
    parts = []

    # Input summary
    parts.append(f"Query: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")

    # Execution summary
    if capabilities_executed:
        parts.append(f"Executed {len(capabilities_executed)} capabilities: {', '.join(capabilities_executed[:3])}")

    if tool_calls:
        parts.append(f"Made {len(tool_calls)} tool calls.")

    # Outcome
    if success:
        parts.append("Execution completed successfully.")
    else:
        parts.append("Execution encountered errors.")

    return " ".join(parts)


def get_or_create_incident_for_input(
    user_input: str,
    classified_intent: str,
) -> str:
    """Get an existing matching incident or create a new one.

    This is called at the START of execution to get an incident ID
    that will be updated as execution progresses.

    Args:
        user_input: The user's input.
        classified_intent: The classified intent.

    Returns:
        Incident ID to use for this execution.
    """
    try:
        from elle.daemon.incidents.store import create_incident_draft

        title = _generate_title(user_input, classified_intent)
        domain = _infer_domain(user_input, classified_intent, [])
        trigger_source = "user_task" if classified_intent == "system_task" else "manual"

        incident = create_incident_draft(
            title=title,
            domain=domain,
            severity="info",
            trigger_source=trigger_source,
        )

        return incident.incident_id

    except Exception as e:
        logger.exception(f"Failed to create incident: {e}")
        return str(uuid4())  # Return a UUID anyway for tracking


# =============================================================================
# ARM Action Recording
# =============================================================================


def record_arm_action(
    arm_name: str,
    action: str,
    target: str,
    success: bool,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> str | None:
    """Record an action from an "arm" (standalone module) to the incident vault.

    This is a simplified interface for arms that don't go through the full
    agentic loop but still need to record their actions for:
    - Audit trails
    - Pattern matching (future similar actions)
    - Compliance

    Arms that use this:
    - /learn (package capability learning)
    - /map (GUI automation learning)
    - /react (reactive function management)
    - /mobile (mobile gateway management)

    Args:
        arm_name: Name of the arm (e.g., "package_learning", "gui_mapping")
        action: Action performed (e.g., "learn_package", "approve_capability")
        target: Target of the action (e.g., package name, capability name)
        success: Whether the action succeeded
        details: Additional details about the action
        error: Error message if failed

    Returns:
        The incident ID, or None if recording failed.

    Example:
        record_arm_action(
            arm_name="package_learning",
            action="learn_package",
            target="ffmpeg",
            success=True,
            details={
                "capabilities_generated": 5,
                "extraction_sources": ["man", "completion", "help"],
            },
        )
    """
    def _do_record() -> str:
        from elle.daemon.incidents.store import (
            append_action,
            create_incident_draft,
            update_incident,
        )

        # Map arm names to domains
        arm_domains = {
            "package_learning": "pkg",
            "gui_mapping": "gui",
            "reactive_functions": "service",
            "mobile_gateway": "auth",
        }
        domain = arm_domains.get(arm_name, "other")

        # Create incident
        title = f"[{arm_name}] {action}: {target}"
        incident = create_incident_draft(
            title=title[:100],
            domain=domain,
            severity="error" if not success else "info",
            trigger_source="arm_action",
        )
        incident_id = incident.incident_id

        # Build summary
        summary = f"{arm_name} performed '{action}' on '{target}'. "
        if success:
            summary += "Operation completed successfully."
        else:
            summary += f"Operation failed: {error or 'Unknown error'}"

        # Update incident with outcome
        update_incident(
            incident_id,
            status="resolved" if success else "open",
            summary=summary,
            outcome="improved" if success else "no_change",
            confidence=0.9 if success else 0.3,
            decision={
                "arm_name": arm_name,
                "action": action,
                "target": target,
                "details": details or {},
                "success": success,
                "error": error,
            },
        )

        # Record the action
        append_action(
            incident_id=incident_id,
            kind="capability" if action.startswith("execute_") else "arm",
            command=f"{arm_name}.{action}",
            payload={
                "target": target,
                "details": details or {},
            },
            stdout=json.dumps(details) if details else None,
            success=success,
            duration_ms=0,
        )

        logger.debug(f"Recorded arm action to incident {incident_id}: {arm_name}.{action}")
        return incident_id

    result = _with_retry(_do_record, max_retries=2)
    if result.success:
        return result.incident_id
    else:
        logger.warning(f"Failed to record arm action after retries: {result.error}")
        return None
