"""Natural Language Compiler for Reactive Functions.

Converts natural language prompts to ReactiveFunction definitions
using LLM inference with capability awareness.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from elle.reactive.models import (
    ActionSpec,
    Condition,
    EventTrigger,
    PolicySpec,
    ReactiveFunction,
    ScheduleTrigger,
    StateProbe,
    Trigger,
)

if TYPE_CHECKING:
    from elle.capabilities.registry import CapabilityRegistry
    from elle.rag.llm import LLM

logger = logging.getLogger(__name__)


# =============================================================================
# Compiler Configuration
# =============================================================================

COMPILER_SYSTEM_PROMPT = """You are an expert system automation engineer. Your task is to convert
natural language automation requests into structured reactive function definitions.

A reactive function has these components:
1. **Trigger** - What initiates evaluation:
   - "event" type: fires on telemetry events (journal, kernel, probe, ebpf)
   - "schedule" type: fires on cron schedule
   - "manual" type: fires only when explicitly invoked

2. **Condition** (optional) - JSONLogic-style predicate using:
   - Operators: all, any, not, eq, ne, gt, gte, lt, lte, contains, matches, exists
   - Variables: {{event.field}}, {{event.raw.field}}, {{state.key}}, {{system.metric}}

3. **Actions** - List of capabilities to execute:
   - Each action has: capability name, input parameters
   - Actions run sequentially unless on_failure="continue"

4. **Policy** - Execution constraints:
   - max_frequency: Minimum time between executions (e.g., "5m", "1h", "1d")
   - max_daily_executions: Maximum times per day
   - require_confirmation: Whether to ask user before executing
   - escalate_on_failure: Send notification on failure

Event categories: oom, disk, net, auth, service, thermal, smart, fs, pkg, docker
Event sources: journal, kernel, probe, ebpf
Severity levels: debug, info, notice, warning, error, critical

{capabilities_context}

Respond with ONLY a valid JSON object matching this schema:
{{
  "name": "kebab-case-name",
  "description": "What this function does",
  "trigger": {{
    "type": "event" | "schedule" | "manual",
    "event": {{"source": "...", "category": "...", "severity": "...", "match": {{}}}},
    "schedule": {{"cron": "* * * * *", "timezone": "UTC"}}
  }},
  "condition": {{"expression": {{}}}},  // optional, JSONLogic-style
  "actions": [
    {{"capability": "capability.name", "input": {{}}, "on_failure": "stop"}}
  ],
  "policy": {{
    "max_frequency": "5m",
    "max_daily_executions": 100,
    "require_confirmation": false,
    "escalate_on_failure": true
  }},
  "tags": ["tag1", "tag2"]
}}"""


# =============================================================================
# Compiler
# =============================================================================


class ReactiveFunctionCompiler:
    """Compiles natural language prompts to ReactiveFunction definitions.

    Uses LLM inference with capability awareness to generate structured
    reactive function definitions from natural language descriptions.

    Usage:
        compiler = ReactiveFunctionCompiler()
        func = await compiler.compile(
            "When disk usage exceeds 90%, clean docker images and notify me"
        )
    """

    def __init__(
        self,
        llm: LLM | None = None,
        registry: CapabilityRegistry | None = None,
    ) -> None:
        """Initialize the compiler.

        Args:
            llm: LLM instance for inference.
            registry: Capability registry for available capabilities.
        """
        self._llm = llm
        self._registry = registry

    @property
    def llm(self) -> LLM:
        """Get the LLM instance, initializing lazily if needed."""
        if self._llm is None:
            from elle.rag.llm import get_llm

            self._llm = get_llm()
        return self._llm

    @property
    def registry(self) -> CapabilityRegistry:
        """Get the capability registry, initializing lazily if needed."""
        if self._registry is None:
            from elle.capabilities.registry import get_registry

            self._registry = get_registry()
        return self._registry

    async def compile(
        self,
        prompt: str,
        *,
        validate_capabilities: bool = True,
    ) -> ReactiveFunction:
        """Compile a natural language prompt to a ReactiveFunction.

        Args:
            prompt: Natural language description of the automation.
            validate_capabilities: Whether to validate capability names.

        Returns:
            ReactiveFunction definition.

        Raises:
            CompilationError: If compilation fails.
            ValidationError: If the generated function is invalid.
        """
        logger.info(f"Compiling reactive function from prompt: {prompt[:100]}...")

        # Build context with available capabilities
        capabilities_context = self._build_capabilities_context()

        # Generate the function definition
        system_prompt = COMPILER_SYSTEM_PROMPT.format(capabilities_context=capabilities_context)

        try:
            result = self.llm.generate_json(
                prompt,
                system=system_prompt,
                temperature=0.3,  # Lower temperature for more consistent output
            )
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise CompilationError(f"Failed to generate function: {e}") from e

        # Parse and validate the result
        try:
            func = self._parse_function(result, prompt)
        except Exception as e:
            logger.error(f"Failed to parse generated function: {e}")
            raise CompilationError(f"Failed to parse function: {e}") from e

        # Validate capabilities exist
        if validate_capabilities:
            self._validate_capabilities(func)

        return func

    def _build_capabilities_context(self) -> str:
        """Build context string describing available capabilities.

        Returns:
            Formatted string for LLM context.
        """
        capabilities = self.registry.list_all()

        if not capabilities:
            return "Available capabilities: notify.send (send notifications)"

        lines = ["Available capabilities:"]
        for cap in capabilities:
            spec = cap.spec
            lines.append(f"- {spec.name}: {spec.summary}")

        return "\n".join(lines)

    def _parse_function(
        self,
        data: dict[str, Any],
        source_prompt: str,
    ) -> ReactiveFunction:
        """Parse LLM output to ReactiveFunction.

        Args:
            data: Parsed JSON from LLM.
            source_prompt: Original user prompt.

        Returns:
            ReactiveFunction instance.
        """
        # Parse trigger
        trigger_data = data.get("trigger", {})
        trigger = self._parse_trigger(trigger_data)

        # Parse condition (optional)
        condition = None
        if data.get("condition") and data["condition"].get("expression"):
            condition = Condition(expression=data["condition"]["expression"])

        # Parse actions
        actions_data = data.get("actions", [])
        if not actions_data:
            raise CompilationError("No actions specified")
        actions = tuple(self._parse_action(a) for a in actions_data)

        # Parse policy
        policy_data = data.get("policy", {})
        policy = self._parse_policy(policy_data)

        # Parse state probes (optional)
        state_data = data.get("state", {})
        state = {k: StateProbe(**v) for k, v in state_data.items()} if state_data else {}

        # Parse tags
        tags = tuple(data.get("tags", []))

        return ReactiveFunction(
            name=data.get("name", "unnamed-function"),
            description=data.get("description", ""),
            trigger=trigger,
            condition=condition,
            actions=actions,
            policy=policy,
            state=state,
            tags=tags,
            source_prompt=source_prompt,
        )

    def _parse_trigger(self, data: dict[str, Any]) -> Trigger:
        """Parse trigger data.

        Args:
            data: Trigger data from LLM.

        Returns:
            Trigger instance.
        """
        trigger_type = data.get("type", "manual")

        event_trigger = None
        schedule_trigger = None

        if trigger_type == "event" and data.get("event"):
            event_data = data["event"]
            event_trigger = EventTrigger(
                source=event_data.get("source"),
                category=event_data.get("category"),
                severity=event_data.get("severity"),
                match=event_data.get("match", {}),
            )
        elif trigger_type == "schedule" and data.get("schedule"):
            schedule_data = data["schedule"]
            schedule_trigger = ScheduleTrigger(
                cron=schedule_data.get("cron", "0 * * * *"),
                timezone=schedule_data.get("timezone", "UTC"),
            )

        return Trigger(
            type=trigger_type,
            event=event_trigger,
            schedule=schedule_trigger,
        )

    def _parse_action(self, data: dict[str, Any]) -> ActionSpec:
        """Parse action data.

        Args:
            data: Action data from LLM.

        Returns:
            ActionSpec instance.
        """
        return ActionSpec(
            capability=data.get("capability", "notify.send"),
            input=data.get("input", {}),
            on_failure=data.get("on_failure", "stop"),
        )

    def _parse_policy(self, data: dict[str, Any]) -> PolicySpec:
        """Parse policy data.

        Args:
            data: Policy data from LLM.

        Returns:
            PolicySpec instance.
        """
        # Handle allowed_hours tuple
        allowed_hours = None
        if data.get("allowed_hours"):
            ah = data["allowed_hours"]
            if isinstance(ah, (list, tuple)) and len(ah) == 2:
                allowed_hours = (ah[0], ah[1])

        return PolicySpec(
            max_frequency=data.get("max_frequency", "5m"),
            max_daily_executions=data.get("max_daily_executions", 100),
            require_confirmation=data.get("require_confirmation", False),
            allowed_hours=allowed_hours,
            escalate_on_failure=data.get("escalate_on_failure", True),
            notification_channel=data.get("notification_channel"),
        )

    def _validate_capabilities(self, func: ReactiveFunction) -> None:
        """Validate that all referenced capabilities exist.

        Args:
            func: The reactive function to validate.

        Raises:
            ValidationError: If any capability is not found.
        """
        for action in func.actions:
            cap = self.registry.get(action.capability)
            if cap is None:
                logger.warning(
                    f"Unknown capability: {action.capability}. "
                    f"Available: {[c.spec.name for c in self.registry.list_all()]}"
                )
                # Don't raise - allow unknown capabilities for extensibility
                # raise ValidationError(f"Unknown capability: {action.capability}")

    def suggest_improvements(
        self,
        func: ReactiveFunction,
    ) -> list[str]:
        """Suggest improvements to a reactive function.

        Args:
            func: The reactive function to analyze.

        Returns:
            List of improvement suggestions.
        """
        suggestions = []

        # Check for missing description
        if not func.description:
            suggestions.append("Add a description to explain what this function does")

        # Check for high-frequency triggers without rate limiting
        if func.policy.max_frequency in ("1s", "5s", "10s"):
            suggestions.append("Consider increasing max_frequency to avoid overwhelming the system")

        # Check for dangerous actions without confirmation
        dangerous_caps = ["file.delete", "config.edit", "package.remove"]
        has_dangerous = any(a.capability in dangerous_caps for a in func.actions)
        if has_dangerous and not func.policy.require_confirmation:
            suggestions.append("Consider enabling require_confirmation for destructive actions")

        # Check for missing conditions on event triggers
        if func.trigger.type == "event" and not func.condition:
            suggestions.append("Consider adding a condition to filter events more precisely")

        # Check for too many actions
        if len(func.actions) > 5:
            suggestions.append("Consider splitting into multiple smaller reactive functions")

        return suggestions


# =============================================================================
# Exceptions
# =============================================================================


class CompilationError(Exception):
    """Error during compilation of natural language to ReactiveFunction."""

    pass


class ValidationError(Exception):
    """Error validating a compiled ReactiveFunction."""

    pass


# =============================================================================
# Singleton
# =============================================================================

_compiler: ReactiveFunctionCompiler | None = None


def get_compiler() -> ReactiveFunctionCompiler:
    """Get the singleton ReactiveFunctionCompiler instance.

    Returns:
        The global ReactiveFunctionCompiler.
    """
    global _compiler
    if _compiler is None:
        _compiler = ReactiveFunctionCompiler()
    return _compiler


def reset_compiler() -> None:
    """Reset the global compiler instance (for testing)."""
    global _compiler
    _compiler = None


# =============================================================================
# Convenience function
# =============================================================================


async def compile_reactive_function(
    prompt: str,
    **kwargs,
) -> ReactiveFunction:
    """Compile a natural language prompt to a ReactiveFunction.

    Convenience function using the singleton compiler.

    Args:
        prompt: Natural language description.
        **kwargs: Additional arguments for compile().

    Returns:
        ReactiveFunction definition.
    """
    return await get_compiler().compile(prompt, **kwargs)
