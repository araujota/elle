"""Tool definitions for the agentic loop.

Defines the tools available to the LLM during ReAct-style reasoning:
- search_man_vault: Search documentation in Man Vault
- search_incidents: Search similar past incidents
- execute_capability: Execute a registered capability
- get_system_info: Get current system information
- shell_command: Execute read-only shell commands
- list_capabilities: List available capabilities
- search_capabilities: Semantic search for capabilities by task description

Each tool has:
- Pydantic input/output models for type safety
- JSON schema for LLM tool calling
- Async execution function
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Type Aliases
# =============================================================================

# Callback for user confirmation
ConfirmCallback = Callable[[str, str], Awaitable[bool]]


# =============================================================================
# Tool Registry
# =============================================================================


class ToolSpec(BaseModel):
    """Specification for a tool available to the LLM."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Tool name for invocation")
    description: str = Field(description="Human-readable description for LLM")
    parameters: dict[str, Any] = Field(
        description="JSON Schema for tool parameters"
    )
    is_mutating: bool = Field(
        default=False,
        description="Whether this tool can mutate system state"
    )


class ToolResult(BaseModel):
    """Result from executing a tool."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    success: bool
    output: str = Field(description="Tool output as string")
    error: str | None = None
    duration_ms: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# =============================================================================
# Tool 1: search_man_vault
# =============================================================================


class SearchManVaultInput(BaseModel):
    """Input for search_man_vault tool."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(description="Search query for documentation")
    k: int = Field(default=5, ge=1, le=20, description="Number of results to return")
    command: str | None = Field(
        default=None,
        description="Filter by specific command name (e.g., 'ls', 'systemctl')"
    )
    search_type: Literal["lexical", "semantic", "hybrid"] = Field(
        default="hybrid",
        description="Type of search: lexical (fast), semantic (contextual), or hybrid (best)"
    )


class ManVaultSnippet(BaseModel):
    """A snippet from Man Vault search."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Command/topic name")
    section: str = Field(description="Man section (1, 5, 8, etc.)")
    snippet: str = Field(description="Relevant documentation snippet")
    score: float = Field(description="Relevance score")
    match_section: str | None = Field(
        default=None,
        description="Section where match was found (OPTIONS, DESCRIPTION, etc.)"
    )


class SearchManVaultOutput(BaseModel):
    """Output from search_man_vault tool."""

    model_config = ConfigDict(frozen=True)

    snippets: tuple[ManVaultSnippet, ...] = Field(
        description="Matching documentation snippets"
    )
    total_found: int = Field(description="Number of results found")


SEARCH_MAN_VAULT_SPEC = ToolSpec(
    name="search_man_vault",
    description=(
        "Search the Man Vault for Linux documentation. Use this to find command "
        "options, configuration syntax, or usage examples. Returns relevant snippets "
        "from man pages."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for documentation",
            },
            "k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
                "description": "Number of results to return",
            },
            "command": {
                "type": "string",
                "description": "Filter by specific command name (e.g., 'ls', 'systemctl')",
            },
            "search_type": {
                "type": "string",
                "enum": ["lexical", "semantic", "hybrid"],
                "default": "hybrid",
                "description": "Type of search",
            },
        },
        "required": ["query"],
    },
    is_mutating=False,
)


async def search_man_vault(args: SearchManVaultInput) -> ToolResult:
    """Execute search_man_vault tool."""
    start_time = time.time()

    try:
        from elle.daemon.manvault.retriever import search

        results = search(
            query=args.query,
            k=args.k,
            name=args.command,
            search_type=args.search_type,
        )

        snippets = tuple(
            ManVaultSnippet(
                name=r.name,
                section=r.section,
                snippet=r.snippet,
                score=r.score,
                match_section=r.match_section,
            )
            for r in results
        )

        # Format output for LLM
        if not snippets:
            output_str = "No documentation found matching the query."
        else:
            lines = [f"Found {len(snippets)} relevant documentation snippets:\n"]
            for i, s in enumerate(snippets, 1):
                lines.append(f"--- {i}. {s.name}({s.section}) [{s.match_section or 'general'}] ---")
                lines.append(s.snippet[:1500])  # Limit snippet length
                lines.append("")
            output_str = "\n".join(lines)

        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="search_man_vault",
            success=True,
            output=output_str,
            duration_ms=duration_ms,
        )

    except Exception as e:
        logger.exception("search_man_vault failed")
        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="search_man_vault",
            success=False,
            output="",
            error=str(e),
            duration_ms=duration_ms,
        )


# =============================================================================
# Tool 2: search_incidents
# =============================================================================


class SearchIncidentsInput(BaseModel):
    """Input for search_incidents tool."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(description="Search query describing the problem")
    domain: str | None = Field(
        default=None,
        description="Filter by domain: net, disk, oom, docker, auth, pkg, service, fs"
    )
    k: int = Field(default=3, ge=1, le=10, description="Number of results to return")
    include_actions: bool = Field(
        default=True,
        description="Include successful actions from prior incidents"
    )


class PriorIncident(BaseModel):
    """A prior incident from search."""

    model_config = ConfigDict(frozen=True)

    incident_id: str
    title: str
    summary: str
    outcome: str
    outcome_weight: float
    decision: str | None
    root_cause: str | None
    successful_actions: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    days_ago: int


class SearchIncidentsOutput(BaseModel):
    """Output from search_incidents tool."""

    model_config = ConfigDict(frozen=True)

    prior_art: tuple[PriorIncident, ...] = Field(
        description="Similar past incidents"
    )
    total_found: int = Field(description="Number of results found")


SEARCH_INCIDENTS_SPEC = ToolSpec(
    name="search_incidents",
    description=(
        "Search the Incident Vault for similar past problems and their solutions. "
        "Returns prior incidents with their outcomes, root causes, and what actions "
        "worked. Use this to learn from past experience before taking action."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query describing the problem",
            },
            "domain": {
                "type": "string",
                "enum": ["net", "disk", "oom", "docker", "auth", "pkg", "service", "fs"],
                "description": "Filter by incident domain",
            },
            "k": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "maximum": 10,
                "description": "Number of results to return",
            },
            "include_actions": {
                "type": "boolean",
                "default": True,
                "description": "Include successful actions from prior incidents",
            },
        },
        "required": ["query"],
    },
    is_mutating=False,
)


async def search_incidents(args: SearchIncidentsInput) -> ToolResult:
    """Execute search_incidents tool."""
    start_time = time.time()

    try:
        from elle.daemon.incidents.retriever import get_prior_art

        prior_art = get_prior_art(
            query=args.query,
            domain=args.domain,
            k=args.k,
            include_actions=args.include_actions,
        )

        incidents = tuple(
            PriorIncident(
                incident_id=p["incident_id"],
                title=p["title"],
                summary=p["summary"],
                outcome=p["outcome"],
                outcome_weight=p["outcome_weight"],
                decision=p.get("decision"),
                root_cause=p.get("root_cause"),
                successful_actions=tuple(p.get("successful_actions", [])),
                days_ago=p.get("days_ago", 0),
            )
            for p in prior_art
        )

        # Format output for LLM
        if not incidents:
            output_str = "No similar past incidents found."
        else:
            lines = [f"Found {len(incidents)} similar past incidents:\n"]
            for i, inc in enumerate(incidents, 1):
                lines.append(f"--- {i}. {inc.title} ({inc.days_ago} days ago, outcome: {inc.outcome}) ---")
                lines.append(f"Summary: {inc.summary}")
                if inc.root_cause:
                    lines.append(f"Root Cause: {inc.root_cause}")
                if inc.decision:
                    lines.append(f"Decision: {inc.decision}")
                if inc.successful_actions:
                    lines.append("Actions that worked:")
                    for action in inc.successful_actions[:5]:
                        lines.append(f"  - {action.get('command', 'N/A')}")
                lines.append("")
            output_str = "\n".join(lines)

        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="search_incidents",
            success=True,
            output=output_str,
            duration_ms=duration_ms,
        )

    except Exception as e:
        logger.exception("search_incidents failed")
        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="search_incidents",
            success=False,
            output="",
            error=str(e),
            duration_ms=duration_ms,
        )


# =============================================================================
# Tool 3: execute_capability
# =============================================================================


class ExecuteCapabilityInput(BaseModel):
    """Input for execute_capability tool."""

    model_config = ConfigDict(frozen=True)

    capability_name: str = Field(
        description="Capability name (e.g., 'service.restart', 'file.read')"
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments for the capability"
    )
    skip_confirmation: bool = Field(
        default=False,
        description="Skip user confirmation (use with caution)"
    )


class ExecuteCapabilityOutput(BaseModel):
    """Output from execute_capability tool."""

    model_config = ConfigDict(frozen=True)

    success: bool
    output: Any = None
    output_text: str | None = None
    error: str | None = None
    duration_ms: int = 0
    verification_passed: bool | None = None
    incident_id: str | None = None


EXECUTE_CAPABILITY_SPEC = ToolSpec(
    name="execute_capability",
    description=(
        "Execute a registered capability to perform system operations. Capabilities "
        "are typed, policy-governed operations like 'service.restart', 'file.read', "
        "'docker.logs', etc. Use list_capabilities to see available operations. "
        "Mutating operations may require user confirmation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "capability_name": {
                "type": "string",
                "description": "Capability name (e.g., 'service.restart')",
            },
            "args": {
                "type": "object",
                "description": "Arguments for the capability",
            },
            "skip_confirmation": {
                "type": "boolean",
                "default": False,
                "description": "Skip user confirmation (use with caution)",
            },
        },
        "required": ["capability_name"],
    },
    is_mutating=True,
)


async def execute_capability(
    args: ExecuteCapabilityInput,
    confirm_callback: ConfirmCallback | None = None,
) -> ToolResult:
    """Execute execute_capability tool."""
    start_time = time.time()

    try:
        # Try to use the PlanExecutor for consistent execution
        from elle.cli.agentic.models import (
            AgenticIntent,
            CapabilityCall,
            ExecutionPlan,
            ParallelGroup,
        )
        from elle.cli.agentic.plan_executor import get_plan_executor

        # Create a simple plan with one capability call
        call = CapabilityCall(
            capability=args.capability_name,
            args=dict(args.args),
            purpose=f"Execute {args.capability_name}",
            is_read_only=args.capability_name.endswith(
                (".status", ".logs", ".info", ".list", ".read", ".stat")
            ),
        )

        intent = AgenticIntent(
            information_needs=(),
            action_requests=(),
            goal_summary=f"Execute {args.capability_name}",
        )

        plan = ExecutionPlan(
            parallel_groups=(ParallelGroup(calls=(call,)),),
            intent=intent,
        )

        executor = get_plan_executor()
        result = await executor.execute(
            plan,
            confirm_callback=confirm_callback,
            skip_confirmation=args.skip_confirmation,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        if result.evidence:
            ev = result.evidence[0]
            output_str = ev.output_text or str(ev.output) if ev.output else "Completed successfully"
            if ev.error:
                output_str = f"Error: {ev.error}"

            return ToolResult(
                tool_name="execute_capability",
                success=ev.success,
                output=output_str,
                error=ev.error,
                duration_ms=duration_ms,
            )
        else:
            return ToolResult(
                tool_name="execute_capability",
                success=False,
                output="",
                error="No execution result",
                duration_ms=duration_ms,
            )

    except Exception as e:
        logger.exception("execute_capability failed")
        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="execute_capability",
            success=False,
            output="",
            error=str(e),
            duration_ms=duration_ms,
        )


# =============================================================================
# Tool 4: get_system_info
# =============================================================================


class SystemInfoAspect(str, Enum):
    """Aspects of system information that can be queried."""

    RESOURCES = "resources"  # CPU, memory, disk usage
    SERVICES = "services"  # Systemd service states
    LISTENERS = "listeners"  # Network listeners
    CONTAINERS = "containers"  # Docker containers
    PACKAGES = "packages"  # Recent package changes
    KERNEL = "kernel"  # Kernel info and modules
    RECENT_EVENTS = "recent_events"  # Recent system events


class GetSystemInfoInput(BaseModel):
    """Input for get_system_info tool."""

    model_config = ConfigDict(frozen=True)

    aspect: SystemInfoAspect = Field(
        description="What aspect of system info to retrieve"
    )
    filter: str | None = Field(
        default=None,
        description="Optional filter (e.g., service name, container name)"
    )


GET_SYSTEM_INFO_SPEC = ToolSpec(
    name="get_system_info",
    description=(
        "Get current system information for diagnostics. Use this to understand "
        "the system state before taking action. Aspects: resources (CPU/RAM/disk), "
        "services (systemd units), listeners (network ports), containers (Docker), "
        "packages (recent installs), kernel (system info), recent_events (logs)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "aspect": {
                "type": "string",
                "enum": ["resources", "services", "listeners", "containers", "packages", "kernel", "recent_events"],
                "description": "What system info to retrieve",
            },
            "filter": {
                "type": "string",
                "description": "Optional filter (e.g., service name)",
            },
        },
        "required": ["aspect"],
    },
    is_mutating=False,
)


async def get_system_info(args: GetSystemInfoInput) -> ToolResult:
    """Execute get_system_info tool."""
    from elle.cli.subprocess_runner import RunMode, run_safe

    start_time = time.time()
    aspect = args.aspect
    filter_val = args.filter

    try:
        output_str = ""

        if aspect == SystemInfoAspect.RESOURCES:
            result = run_safe(
                "free -h && echo '---DISK---' && df -h / /home /var 2>/dev/null && echo '---LOAD---' && uptime && echo '---TOP---' && top -bn1 | head -15",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            output_str = result.stdout if result.success else f"Error: {result.stderr}"

        elif aspect == SystemInfoAspect.SERVICES:
            cmd = "systemctl list-units --type=service --state=running,failed"
            if filter_val:
                cmd = f"systemctl status {filter_val} --no-pager"
            result = run_safe(cmd, timeout=10.0, mode=RunMode.CAPTURE)
            output_str = result.stdout if result.success else f"Error: {result.stderr}"

        elif aspect == SystemInfoAspect.LISTENERS:
            result = run_safe(
                "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            output_str = result.stdout if result.success else f"Error: {result.stderr}"

        elif aspect == SystemInfoAspect.CONTAINERS:
            cmd = "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'"
            if filter_val:
                cmd = f"docker inspect {filter_val} --format '{{{{json .}}}}' | head -100"
            result = run_safe(cmd, timeout=10.0, mode=RunMode.CAPTURE)
            output_str = result.stdout if result.success else f"Error: {result.stderr}"

        elif aspect == SystemInfoAspect.PACKAGES:
            result = run_safe(
                "grep -E 'install|upgrade' /var/log/dpkg.log 2>/dev/null | tail -20 || echo 'No package log found'",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            output_str = result.stdout if result.success else f"Error: {result.stderr}"

        elif aspect == SystemInfoAspect.KERNEL:
            result = run_safe(
                "uname -a && echo '---RELEASE---' && cat /etc/os-release 2>/dev/null | head -10 && echo '---MODULES---' && lsmod | head -20",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            output_str = result.stdout if result.success else f"Error: {result.stderr}"

        elif aspect == SystemInfoAspect.RECENT_EVENTS:
            cmd = "journalctl --no-pager -p warning -n 30 --since '1 hour ago'"
            if filter_val:
                cmd = f"journalctl --no-pager -u {filter_val} -n 50 --since '1 hour ago'"
            result = run_safe(cmd, timeout=10.0, mode=RunMode.CAPTURE)
            output_str = result.stdout if result.success else f"Error: {result.stderr}"

        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="get_system_info",
            success=True,
            output=output_str,
            duration_ms=duration_ms,
        )

    except Exception as e:
        logger.exception("get_system_info failed")
        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="get_system_info",
            success=False,
            output="",
            error=str(e),
            duration_ms=duration_ms,
        )


# =============================================================================
# Tool 5: shell_command (read-only)
# =============================================================================


class ShellCommandInput(BaseModel):
    """Input for shell_command tool."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(
        description="Shell command to execute (read-only operations only)"
    )
    timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        description="Timeout in seconds"
    )


# Commands that are blocked for safety
BLOCKED_COMMANDS = frozenset([
    "rm -rf",
    "mkfs",
    "dd if=",
    "dd of=/dev",
    "> /dev/sd",
    "chmod -R 777 /",
    "shutdown",
    "reboot",
    "init 0",
    "init 6",
    ":()",  # fork bomb
    "sudo",
])


SHELL_COMMAND_SPEC = ToolSpec(
    name="shell_command",
    description=(
        "Execute a read-only shell command for diagnostics. Use this for quick "
        "checks that don't have a dedicated capability. Commands that modify system "
        "state are blocked - use execute_capability for mutations. Examples: "
        "'cat /etc/hosts', 'ls -la /var/log', 'grep error /var/log/syslog'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute (read-only only)",
            },
            "timeout": {
                "type": "number",
                "default": 30.0,
                "minimum": 1.0,
                "maximum": 120.0,
                "description": "Timeout in seconds",
            },
        },
        "required": ["command"],
    },
    is_mutating=False,
)


async def shell_command(args: ShellCommandInput) -> ToolResult:
    """Execute shell_command tool."""
    from elle.cli.subprocess_runner import RunMode, run_safe

    start_time = time.time()

    # Safety check
    cmd_lower = args.command.lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return ToolResult(
                tool_name="shell_command",
                success=False,
                output="",
                error=f"Command blocked for safety: {blocked}",
                duration_ms=0,
            )

    try:
        result = run_safe(
            args.command,
            timeout=args.timeout,
            mode=RunMode.CAPTURE,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        output_str = result.stdout or ""
        if result.stderr:
            output_str += f"\n[stderr]: {result.stderr}"

        return ToolResult(
            tool_name="shell_command",
            success=result.success,
            output=output_str,
            error=result.stderr if not result.success else None,
            duration_ms=duration_ms,
        )

    except Exception as e:
        logger.exception("shell_command failed")
        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="shell_command",
            success=False,
            output="",
            error=str(e),
            duration_ms=duration_ms,
        )


# =============================================================================
# Tool 6: list_capabilities
# =============================================================================


class ListCapabilitiesInput(BaseModel):
    """Input for list_capabilities tool."""

    model_config = ConfigDict(frozen=True)

    domain: str | None = Field(
        default=None,
        description="Filter by domain: service, file, config, network, package, docker, auth, gui"
    )
    search: str | None = Field(
        default=None,
        description="Search by capability name or description"
    )


class CapabilityInfo(BaseModel):
    """Information about a capability."""

    model_config = ConfigDict(frozen=True)

    name: str
    domain: str
    risk_level: str
    description: str
    is_read_only: bool


LIST_CAPABILITIES_SPEC = ToolSpec(
    name="list_capabilities",
    description=(
        "List available capabilities in the system. Capabilities are typed operations "
        "that can be executed via execute_capability. Filter by domain (service, file, "
        "docker, etc.) or search by name. Shows risk level and whether the capability "
        "is read-only or mutating."
    ),
    parameters={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "enum": ["service", "file", "config", "network", "package", "docker", "auth", "gui"],
                "description": "Filter by capability domain",
            },
            "search": {
                "type": "string",
                "description": "Search by name or description",
            },
        },
        "required": [],
    },
    is_mutating=False,
)


async def list_capabilities(args: ListCapabilitiesInput) -> ToolResult:
    """Execute list_capabilities tool."""
    start_time = time.time()

    try:
        from elle.capabilities import get_registry

        registry = get_registry()
        all_caps = []

        # Get capabilities, optionally filtered by domain
        if args.domain:
            caps = registry.list_by_domain(args.domain)
        else:
            caps = registry.list_all()

        for cap in caps:
            spec = cap.spec
            info = CapabilityInfo(
                name=spec.name,
                domain=spec.domain,
                risk_level=spec.risk.value if hasattr(spec.risk, 'value') else str(spec.risk),
                description=spec.description or "",
                is_read_only=spec.name.endswith((".status", ".logs", ".info", ".list", ".read", ".stat")),
            )
            # Apply search filter
            if args.search:
                search_lower = args.search.lower()
                if search_lower not in info.name.lower() and search_lower not in info.description.lower():
                    continue
            all_caps.append(info)

        # Format output
        if not all_caps:
            output_str = "No capabilities found matching the criteria."
        else:
            lines = [f"Found {len(all_caps)} capabilities:\n"]
            for info in sorted(all_caps, key=lambda x: x.name):
                ro_marker = "[R]" if info.is_read_only else "[W]"
                lines.append(f"  {ro_marker} {info.name} ({info.domain}, risk: {info.risk_level})")
                if info.description:
                    lines.append(f"      {info.description[:80]}")
            output_str = "\n".join(lines)

        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            tool_name="list_capabilities",
            success=True,
            output=output_str,
            duration_ms=duration_ms,
        )

    except Exception:
        logger.exception("list_capabilities failed")
        duration_ms = int((time.time() - start_time) * 1000)

        # Provide fallback list of common capabilities
        fallback = """Common capabilities (registry unavailable):
  [R] service.status - Get systemd service status
  [R] service.logs - Get service logs
  [W] service.restart - Restart a service
  [W] service.start - Start a service
  [W] service.stop - Stop a service
  [R] file.read - Read file contents
  [R] file.stat - Get file metadata
  [W] file.write - Write file contents
  [R] docker.list - List Docker containers
  [R] docker.logs - Get container logs
  [W] docker.restart - Restart container
  [R] network.listeners - List network listeners
  [R] system.resources - Get resource usage
"""
        return ToolResult(
            tool_name="list_capabilities",
            success=True,
            output=fallback,
            duration_ms=duration_ms,
        )


# =============================================================================
# Tool 7: search_capabilities (semantic search)
# =============================================================================


class SearchCapabilitiesInput(BaseModel):
    """Input for search_capabilities tool."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(
        description="Natural language description of what you want to do (e.g., 'restart the web server')"
    )
    domain: str | None = Field(
        default=None,
        description="Filter by domain: service, file, config, network, package, docker, auth, gui"
    )
    k: int = Field(default=5, ge=1, le=15, description="Number of results to return")
    include_unapproved: bool = Field(
        default=False,
        description="Include capabilities that haven't been approved yet"
    )


class CapabilityMatchInfo(BaseModel):
    """Information about a matched capability from semantic search."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Capability name")
    domain: str = Field(description="Capability domain")
    description: str = Field(description="What this capability does")
    risk_level: str = Field(description="Risk level (none, low, medium, high, critical)")
    match_score: float = Field(description="Relevance score (0-1)")
    success_rate: float = Field(description="Historical success rate")
    is_approved: bool = Field(description="Whether approved for use")
    source_command: str | None = Field(default=None, description="Source command if auto-generated")


SEARCH_CAPABILITIES_SPEC = ToolSpec(
    name="search_capabilities",
    description=(
        "Search for capabilities by natural language description. Use this to find "
        "the right capability for a task, e.g., 'restart the web server' finds "
        "'service.restart'. Returns capabilities ranked by semantic similarity, "
        "trust level, and past success rate. Prefer this over list_capabilities "
        "when you know what you want to accomplish."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of what you want to do",
            },
            "domain": {
                "type": "string",
                "enum": ["service", "file", "config", "network", "package", "docker", "auth", "gui"],
                "description": "Filter by capability domain",
            },
            "k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 15,
                "description": "Number of results to return",
            },
            "include_unapproved": {
                "type": "boolean",
                "default": False,
                "description": "Include capabilities that haven't been approved yet",
            },
        },
        "required": ["query"],
    },
    is_mutating=False,
)


async def search_capabilities(args: SearchCapabilitiesInput) -> ToolResult:
    """Execute search_capabilities tool."""
    start_time = time.time()

    try:
        from elle.capabilities.autogen.retriever import search_capabilities as do_search

        results = do_search(
            query=args.query,
            domain=args.domain,
            k=args.k,
            search_type="hybrid",
            include_unapproved=args.include_unapproved,
        )

        if not results:
            # Fallback to list_capabilities style output
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                tool_name="search_capabilities",
                success=True,
                output=f"No capabilities found matching: {args.query}\n\nTry list_capabilities to see all available capabilities.",
                duration_ms=duration_ms,
            )

        # Format output for LLM
        lines = [f"Found {len(results)} capabilities matching '{args.query}':\n"]
        for i, r in enumerate(results, 1):
            approved_marker = "[approved]" if r.is_approved else "[pending approval]"
            lines.append(
                f"{i}. **{r.capability_name}** (score: {r.score:.2f}, success: {r.success_rate:.0%}) {approved_marker}"
            )
            lines.append(f"   Domain: {r.domain}, Risk: {r.risk_level}")
            lines.append(f"   {r.description[:150]}")
            if r.source_command:
                lines.append(f"   Source: {r.source_command}")
            lines.append("")

        output_str = "\n".join(lines)
        duration_ms = int((time.time() - start_time) * 1000)

        return ToolResult(
            tool_name="search_capabilities",
            success=True,
            output=output_str,
            duration_ms=duration_ms,
        )

    except Exception as e:
        logger.exception("search_capabilities failed")
        duration_ms = int((time.time() - start_time) * 1000)

        # Provide helpful fallback
        fallback = f"""Capability search failed: {e}

Try using list_capabilities to see available capabilities, or specify the capability directly:
- service.restart, service.status, service.logs
- file.read, file.write, file.stat
- docker.list, docker.logs, docker.restart
- network.listeners, system.resources
"""
        return ToolResult(
            tool_name="search_capabilities",
            success=False,
            output=fallback,
            error=str(e),
            duration_ms=duration_ms,
        )


# =============================================================================
# Tool Registry
# =============================================================================


class ToolRegistry:
    """Registry of available tools for the agentic loop."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Callable[..., Awaitable[ToolResult]]]] = {}

        # Register built-in tools
        self.register(SEARCH_MAN_VAULT_SPEC, search_man_vault)
        self.register(SEARCH_INCIDENTS_SPEC, search_incidents)
        self.register(EXECUTE_CAPABILITY_SPEC, execute_capability)
        self.register(GET_SYSTEM_INFO_SPEC, get_system_info)
        self.register(SHELL_COMMAND_SPEC, shell_command)
        self.register(LIST_CAPABILITIES_SPEC, list_capabilities)
        self.register(SEARCH_CAPABILITIES_SPEC, search_capabilities)

    def register(
        self,
        spec: ToolSpec,
        handler: Callable[..., Awaitable[ToolResult]],
    ) -> None:
        """Register a tool."""
        self._tools[spec.name] = (spec, handler)

    def get(self, name: str) -> tuple[ToolSpec, Callable[..., Awaitable[ToolResult]]] | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_specs(self) -> list[ToolSpec]:
        """List all tool specifications."""
        return [spec for spec, _ in self._tools.values()]

    def to_ollama_tools(self) -> list[dict[str, Any]]:
        """Convert tools to Ollama tool format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self.list_specs()
        ]

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        confirm_callback: ConfirmCallback | None = None,
    ) -> ToolResult:
        """Execute a tool by name."""
        tool_info = self.get(tool_name)
        if not tool_info:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Unknown tool: {tool_name}",
            )

        spec, handler = tool_info

        # Build input model for the tool
        try:
            if tool_name == "search_man_vault":
                input_obj = SearchManVaultInput(**args)
                return await handler(input_obj)
            elif tool_name == "search_incidents":
                input_obj = SearchIncidentsInput(**args)
                return await handler(input_obj)
            elif tool_name == "execute_capability":
                input_obj = ExecuteCapabilityInput(**args)
                return await handler(input_obj, confirm_callback)
            elif tool_name == "get_system_info":
                input_obj = GetSystemInfoInput(**args)
                return await handler(input_obj)
            elif tool_name == "shell_command":
                input_obj = ShellCommandInput(**args)
                return await handler(input_obj)
            elif tool_name == "list_capabilities":
                input_obj = ListCapabilitiesInput(**args)
                return await handler(input_obj)
            elif tool_name == "search_capabilities":
                input_obj = SearchCapabilitiesInput(**args)
                return await handler(input_obj)
            else:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output="",
                    error=f"No handler for tool: {tool_name}",
                )
        except Exception as e:
            logger.exception(f"Tool execution failed: {tool_name}")
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=str(e),
            )


# Module-level singleton
_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Get the shared tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def reset_tool_registry() -> None:
    """Reset the shared tool registry."""
    global _registry
    _registry = None
