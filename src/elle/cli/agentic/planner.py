"""CapabilityPlanner - Maps intent to execution plan with dependencies.

Takes an AgenticIntent and produces an ExecutionPlan with capability calls
organized into parallel groups based on their dependencies.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    CapabilityCall,
    ExecutionPlan,
    InformationNeed,
    ParallelGroup,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Capability Mappings
# =============================================================================

# Map information needs to read-only capabilities
INFO_NEED_TO_CAPABILITY: dict[str, dict[str, tuple[str, dict[str, str]]]] = {
    "service": {
        "status": ("service.status", {"service": "{target}"}),
        "logs": ("service.logs", {"service": "{target}", "lines": "50"}),
        "config": ("service.status", {"service": "{target}"}),
    },
    "file": {
        "content": ("file.read", {"path": "{target}"}),
        "stat": ("file.stat", {"path": "{target}"}),
        "diff": ("file.diff", {"path": "{target}"}),
    },
    "package": {
        "info": ("package.info", {"package": "{target}"}),
    },
    "docker": {
        "list": ("docker.list", {"all": "true"}),
        "status": ("docker.inspect", {"name": "{target}"}),
        "logs": ("docker.logs", {"name": "{target}", "lines": "50"}),
    },
    "network": {
        "listeners": ("network.listeners", {}),
        "connections": ("network.connections", {}),
    },
    "config": {
        "content": ("config.preview", {"path": "{target}"}),
    },
    "system": {
        "info": ("system.info", {}),
        "resources": ("system.resources", {}),
    },
}

# Map action requests to mutating capabilities
ACTION_TO_CAPABILITY: dict[str, dict[ActionType, str]] = {
    "service": {
        ActionType.RESTART: "service.restart",
        ActionType.START: "service.start",
        ActionType.STOP: "service.stop",
        ActionType.ENABLE: "service.enable",
        ActionType.DISABLE: "service.disable",
        ActionType.FIX: "service.restart",  # Fallback to restart for fix
    },
    "package": {
        ActionType.INSTALL: "package.install",
        ActionType.REMOVE: "package.remove",
        ActionType.UPDATE: "package.upgrade",
    },
    "docker": {
        ActionType.START: "docker.start",
        ActionType.STOP: "docker.stop",
        ActionType.RESTART: "docker.restart",
        ActionType.REMOVE: "docker.remove",
    },
    "file": {
        ActionType.CREATE: "file.write",
        ActionType.DELETE: "file.delete",
        ActionType.CONFIGURE: "config.apply",
    },
}

# Capabilities that require confirmation
REQUIRES_CONFIRMATION: frozenset[str] = frozenset(
    {
        "service.restart",
        "service.stop",
        "service.start",
        "service.enable",
        "service.disable",
        "package.install",
        "package.remove",
        "package.upgrade",
        "docker.restart",
        "docker.stop",
        "docker.remove",
        "file.write",
        "file.delete",
        "config.apply",
    }
)

# Read-only capabilities (no confirmation needed)
READ_ONLY_CAPABILITIES: frozenset[str] = frozenset(
    {
        "service.status",
        "service.logs",
        "file.read",
        "file.stat",
        "file.diff",
        "package.info",
        "docker.list",
        "docker.inspect",
        "docker.logs",
        "network.listeners",
        "network.connections",
        "config.preview",
        "system.info",
        "system.resources",
    }
)

# Estimated duration per capability (ms)
CAPABILITY_DURATION_MS: dict[str, int] = {
    "service.status": 500,
    "service.logs": 1000,
    "service.restart": 3000,
    "service.start": 2000,
    "service.stop": 2000,
    "file.read": 100,
    "file.stat": 50,
    "package.info": 500,
    "package.install": 30000,
    "docker.list": 500,
    "docker.inspect": 200,
    "docker.logs": 1000,
    "network.listeners": 500,
    "system.info": 200,
    "system.resources": 500,
}


# =============================================================================
# LLM Planning Prompt
# =============================================================================

PLANNING_PROMPT = """Plan capability calls to fulfill this user intent.

User request: "{user_input}"
Intent summary: {goal_summary}

Information needs:
{info_needs}

Action requests:
{action_requests}

Available capabilities:
- service.status(service): Get service status
- service.logs(service, lines): Get service logs
- service.restart(service): Restart a service
- service.start(service): Start a service
- service.stop(service): Stop a service
- file.read(path): Read file contents
- package.info(package): Get package information
- package.install(package): Install a package
- docker.list(): List containers
- docker.inspect(name): Inspect container
- docker.logs(name, lines): Get container logs
- docker.restart(name): Restart container
- network.listeners(): List listening ports
- system.info(): Get system information
- system.resources(): Get resource usage

Return a JSON plan with capability calls. Include dependencies (by index) when one call needs the result of another.
Example: status check before restart would have restart depend_on the status check.

{{
  "calls": [
    {{"capability": "service.status", "args": {{"service": "nginx"}}, "purpose": "Check current status", "depends_on": []}},
    {{"capability": "service.restart", "args": {{"service": "nginx"}}, "purpose": "Restart the service", "depends_on": [0]}}
  ]
}}

Return only valid JSON."""


# =============================================================================
# CapabilityPlanner
# =============================================================================


class CapabilityPlanner:
    """Plans capability execution from intent.

    Maps AgenticIntent to ExecutionPlan with parallel groups based
    on dependencies between capability calls.
    """

    def __init__(
        self,
        use_llm_planning: bool = False,
    ) -> None:
        """Initialize the planner.

        Args:
            use_llm_planning: Whether to use LLM for complex planning.
                Defaults to False (rule-based planning is faster and predictable).
        """
        self.use_llm_planning = use_llm_planning

    async def plan(
        self,
        intent: AgenticIntent,
        user_input: str = "",
    ) -> ExecutionPlan:
        """Create an execution plan from intent.

        Args:
            intent: The analyzed intent.
            user_input: Original user input (for LLM planning context).

        Returns:
            ExecutionPlan with parallel groups.
        """
        if not intent.information_needs and not intent.action_requests:
            return ExecutionPlan(
                parallel_groups=(),
                intent=intent,
                estimated_duration_ms=0,
                requires_confirmation=False,
            )

        # Use rule-based planning (fast and predictable)
        calls = self._plan_with_rules(intent)

        # Optionally enhance with LLM for complex cases
        if self.use_llm_planning and intent.is_mixed:
            try:
                llm_calls = await self._plan_with_llm(intent, user_input)
                if llm_calls:
                    calls = llm_calls
            except Exception as e:
                logger.warning(f"LLM planning failed, using rules: {e}")

        # Build parallel groups from dependency graph
        groups = self._build_parallel_groups(calls)

        # Calculate total estimated duration
        total_duration = sum(CAPABILITY_DURATION_MS.get(c.capability, 1000) for c in calls)

        # Check if any capability requires confirmation
        requires_confirmation = any(c.capability in REQUIRES_CONFIRMATION for c in calls)

        return ExecutionPlan(
            parallel_groups=tuple(groups),
            intent=intent,
            estimated_duration_ms=total_duration,
            requires_confirmation=requires_confirmation,
        )

    def _plan_with_rules(self, intent: AgenticIntent) -> list[CapabilityCall]:
        """Plan capabilities using rule-based mapping.

        Args:
            intent: The analyzed intent.

        Returns:
            List of capability calls.
        """
        calls: list[CapabilityCall] = []
        call_index = 0

        # First, plan information gathering (read-only)
        for need in intent.information_needs:
            call = self._map_info_need_to_call(need, call_index)
            if call:
                calls.append(call)
                call_index += 1

        # Then, plan actions (with dependencies on info gathering if conditional)
        read_call_count = len(calls)
        for action in intent.action_requests:
            # If action has a condition, it depends on info gathering
            depends_on: tuple[int, ...] = ()
            if action.condition and read_call_count > 0:
                # Find a relevant info call to depend on
                for i, call in enumerate(calls[:read_call_count]):
                    if self._action_depends_on_call(action, call):
                        depends_on = (i,)
                        break

            call = self._map_action_to_call(action, call_index, depends_on)
            if call:
                calls.append(call)
                call_index += 1

        return calls

    def _map_info_need_to_call(
        self,
        need: InformationNeed,
        index: int,
    ) -> CapabilityCall | None:
        """Map an information need to a capability call.

        Args:
            need: The information need.
            index: Index for this call.

        Returns:
            CapabilityCall or None if no mapping exists.
        """
        category_map = INFO_NEED_TO_CAPABILITY.get(need.category, {})

        # Try each aspect
        for aspect in need.aspects:
            if aspect in category_map:
                capability, args_template = category_map[aspect]

                # Substitute target into args
                args = {k: v.replace("{target}", need.target) for k, v in args_template.items()}

                return CapabilityCall(
                    capability=capability,
                    args=args,
                    purpose=f"Get {aspect} for {need.target or need.category}",
                    depends_on=(),
                    requires_confirmation=False,
                    is_read_only=True,
                )

        # Default capability for category
        defaults = {
            "service": ("service.status", {"service": need.target}),
            "file": ("file.read", {"path": need.target}),
            "package": ("package.info", {"package": need.target}),
            "docker": ("docker.list", {}),
            "network": ("network.listeners", {}),
            "system": ("system.info", {}),
        }

        if need.category in defaults:
            capability, args = defaults[need.category]
            return CapabilityCall(
                capability=capability,
                args=args,
                purpose=f"Get {need.category} info for {need.target or 'system'}",
                depends_on=(),
                requires_confirmation=False,
                is_read_only=True,
            )

        return None

    def _map_action_to_call(
        self,
        action: ActionRequest,
        index: int,
        depends_on: tuple[int, ...],
    ) -> CapabilityCall | None:
        """Map an action request to a capability call.

        Args:
            action: The action request.
            index: Index for this call.
            depends_on: Indices of calls this depends on.

        Returns:
            CapabilityCall or None if no mapping exists.
        """
        domain_map = ACTION_TO_CAPABILITY.get(action.domain, {})
        capability = domain_map.get(action.action_type)

        if not capability:
            # Try custom capability name
            capability = f"{action.domain}.{action.action_type.value}"

        # Build args
        args: dict[str, Any] = {}
        if action.target:
            # Use appropriate arg name based on domain
            arg_name = {
                "service": "service",
                "package": "package",
                "docker": "name",
                "file": "path",
            }.get(action.domain, "target")
            args[arg_name] = action.target

        # Add any additional parameters
        args.update(action.parameters)

        return CapabilityCall(
            capability=capability,
            args=args,
            purpose=f"{action.action_type.value} {action.target}",
            depends_on=depends_on,
            requires_confirmation=capability in REQUIRES_CONFIRMATION,
            is_read_only=False,
        )

    def _action_depends_on_call(
        self,
        action: ActionRequest,
        call: CapabilityCall,
    ) -> bool:
        """Check if an action should depend on an info call.

        Args:
            action: The action request.
            call: A potential dependency call.

        Returns:
            True if the action should depend on this call.
        """
        # Action and call should be for the same target
        action_target = action.target.lower()

        for arg_value in call.args.values():
            if isinstance(arg_value, str) and arg_value.lower() == action_target:
                return True

        return False

    def _build_parallel_groups(
        self,
        calls: list[CapabilityCall],
    ) -> list[ParallelGroup]:
        """Build parallel execution groups from calls with dependencies.

        Uses a topological sort approach - calls with no dependencies
        go in the first group, calls depending on those go in the next, etc.

        Args:
            calls: List of capability calls with depends_on fields.

        Returns:
            List of ParallelGroups for sequential execution.
        """
        if not calls:
            return []

        # Track which calls have been assigned to groups
        assigned: set[int] = set()
        groups: list[ParallelGroup] = []

        while len(assigned) < len(calls):
            # Find all calls whose dependencies are satisfied
            group_calls: list[CapabilityCall] = []

            for i, call in enumerate(calls):
                if i in assigned:
                    continue

                # Check if all dependencies are in previous groups
                deps_satisfied = all(dep in assigned for dep in call.depends_on)

                if deps_satisfied:
                    group_calls.append(call)
                    assigned.add(i)

            if not group_calls:
                # Circular dependency or bug - break to avoid infinite loop
                logger.warning("Could not resolve all dependencies, adding remaining calls")
                for i, call in enumerate(calls):
                    if i not in assigned:
                        group_calls.append(call)
                        assigned.add(i)

            if group_calls:
                groups.append(ParallelGroup(calls=tuple(group_calls)))

        return groups

    async def _plan_with_llm(
        self,
        intent: AgenticIntent,
        user_input: str,
    ) -> list[CapabilityCall] | None:
        """Plan capabilities using LLM for complex cases.

        Args:
            intent: The analyzed intent.
            user_input: Original user input.

        Returns:
            List of capability calls or None if LLM planning failed.
        """
        from elle.rag.llm import get_llm

        llm = get_llm()
        if not llm.is_available():
            return None

        # Format intent for prompt
        info_needs_str = (
            "\n".join(f"- {n.category}: {n.target} ({', '.join(n.aspects)})" for n in intent.information_needs)
            or "None"
        )

        action_requests_str = (
            "\n".join(
                f"- {a.action_type.value} {a.target} ({a.domain}){' [' + a.condition + ']' if a.condition else ''}"
                for a in intent.action_requests
            )
            or "None"
        )

        prompt = PLANNING_PROMPT.format(
            user_input=user_input,
            goal_summary=intent.goal_summary,
            info_needs=info_needs_str,
            action_requests=action_requests_str,
        )

        try:
            response = llm.generate(prompt, max_tokens=500, temperature=0.1)
            if not response or not response.content:
                return None

            return self._parse_llm_plan(response.content)

        except Exception as e:
            logger.warning(f"LLM planning failed: {e}")
            return None

    def _parse_llm_plan(self, content: str) -> list[CapabilityCall] | None:
        """Parse capability calls from LLM response.

        Args:
            content: LLM response content.

        Returns:
            List of capability calls or None if parsing failed.
        """
        content = content.strip()

        # Extract JSON
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return None
            else:
                return None

        calls: list[CapabilityCall] = []
        for call_data in data.get("calls", []):
            try:
                capability = call_data.get("capability", "")
                if not capability:
                    continue

                calls.append(
                    CapabilityCall(
                        capability=capability,
                        args=call_data.get("args", {}),
                        purpose=call_data.get("purpose", f"Execute {capability}"),
                        depends_on=tuple(call_data.get("depends_on", [])),
                        requires_confirmation=capability in REQUIRES_CONFIRMATION,
                        is_read_only=capability in READ_ONLY_CAPABILITIES,
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to parse call: {e}")

        return calls if calls else None

    async def replan(
        self,
        intent: AgenticIntent,
        previous_evidence: tuple[Any, ...],
        gaps: tuple[str, ...],
    ) -> ExecutionPlan:
        """Create a new plan to fill gaps from previous execution.

        Args:
            intent: The original intent.
            previous_evidence: Evidence from previous execution.
            gaps: What's still missing.

        Returns:
            New ExecutionPlan to fill the gaps.
        """
        # For now, just try to re-plan any failed items
        # In the future, this could be more intelligent about
        # what specifically failed and how to recover

        # Filter intent to only unfulfilled parts
        # This is a simplified version - a full implementation would
        # analyze which specific needs/actions weren't satisfied

        return await self.plan(intent)


# =============================================================================
# Module-level singleton
# =============================================================================

_planner: CapabilityPlanner | None = None


def get_capability_planner() -> CapabilityPlanner:
    """Get the shared planner instance.

    Returns:
        The CapabilityPlanner singleton.
    """
    global _planner
    if _planner is None:
        _planner = CapabilityPlanner()
    return _planner


def reset_capability_planner() -> None:
    """Reset the shared planner instance."""
    global _planner
    _planner = None
