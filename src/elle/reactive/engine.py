"""Reactive Engine - core execution loop for reactive functions.

Processes telemetry events through matching reactive functions,
evaluates conditions, executes actions via capabilities, and
records everything to the incident vault.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from elle.reactive.evaluator import (
    ConditionContext,
    evaluate_condition,
    resolve_templates_in_dict,
)
from elle.reactive.models import (
    ActionResult,
    ActionSpec,
    ExecutionRecord,
    PolicySpec,
    ReactiveFunction,
)
from elle.reactive.store import (
    get_rate_limit_state,
    list_enabled_with_event_trigger,
    record_execution,
    update_rate_limit_state,
)

if TYPE_CHECKING:
    from elle.capabilities.executor import CapabilityExecutor
    from elle.daemon.telemetry.models import TelemetryEvent

logger = logging.getLogger(__name__)


class ReactiveEngine:
    """Core execution engine for reactive functions.

    Processes telemetry events through the reactive function lifecycle:
    1. Find matching functions (trigger pattern match)
    2. Check rate limits
    3. Probe dynamic state
    4. Evaluate conditions
    5. Execute actions via CapabilityExecutor
    6. Record execution to store and incident vault

    Usage:
        engine = ReactiveEngine(
            executor=get_executor(),
        )

        records = await engine.process_event(event)
    """

    def __init__(
        self,
        executor: CapabilityExecutor | None = None,
    ) -> None:
        """Initialize the reactive engine.

        Args:
            executor: Capability executor for running actions.
        """
        self._executor = executor
        self._state_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}

    @property
    def executor(self) -> CapabilityExecutor:
        """Get the capability executor, initializing lazily if needed."""
        if self._executor is None:
            from elle.capabilities.executor import get_executor

            self._executor = get_executor()
        return self._executor

    async def process_event(
        self,
        event: TelemetryEvent,
    ) -> list[ExecutionRecord]:
        """Process a telemetry event through all matching reactive functions.

        Args:
            event: The telemetry event to process.

        Returns:
            List of ExecutionRecords for each function that executed.
        """
        records: list[ExecutionRecord] = []

        # 1. Find functions that match this event's trigger
        matching = await self._find_matching_functions(event)
        if not matching:
            return records

        logger.debug(f"Found {len(matching)} matching functions for event {event.event_id}")

        for func in matching:
            try:
                record = await self._process_function(func, event)
                if record:
                    records.append(record)
            except Exception as e:
                logger.exception(f"Error processing function {func.name}: {e}")
                # Record the failure
                record = ExecutionRecord(
                    function_id=func.id,
                    function_name=func.name,
                    triggered_at=datetime.utcnow(),
                    trigger_event=_event_to_dict(event),
                    condition_result=False,
                    condition_explanation=f"Processing error: {e}",
                    success=False,
                    error=str(e),
                )
                record_execution(record)
                records.append(record)

        return records

    async def process_scheduled(
        self,
        func: ReactiveFunction,
    ) -> ExecutionRecord | None:
        """Process a scheduled reactive function (cron trigger).

        Args:
            func: The reactive function to execute.

        Returns:
            ExecutionRecord, or None if rate-limited or condition failed.
        """
        return await self._process_function(func, event=None)

    async def process_manual(
        self,
        func: ReactiveFunction,
        context_override: dict[str, Any] | None = None,
    ) -> ExecutionRecord | None:
        """Manually trigger a reactive function.

        Args:
            func: The reactive function to execute.
            context_override: Optional context data to inject.

        Returns:
            ExecutionRecord, or None if condition failed.
        """
        return await self._process_function(
            func,
            event=None,
            context_override=context_override,
            skip_rate_limit=True,
        )

    async def dry_run(
        self,
        func: ReactiveFunction,
        event: TelemetryEvent | None = None,
        context_override: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Dry run a reactive function without executing actions.

        Args:
            func: The reactive function to test.
            event: Optional event for context.
            context_override: Optional context data.

        Returns:
            Tuple of (condition_result, explanation, resolved_context).
        """
        # Build context
        context = await self._build_context(func, event, context_override)

        # Evaluate condition
        if func.condition:
            result, explanation = evaluate_condition(func.condition, context)
        else:
            result, explanation = True, "No condition defined"

        # Return the resolved context for inspection
        context_dict = {
            "event": context.event,
            "state": context.state,
            "system": context.system,
        }

        return result, explanation, context_dict

    async def _find_matching_functions(
        self,
        event: TelemetryEvent,
    ) -> list[ReactiveFunction]:
        """Find reactive functions whose triggers match this event.

        Args:
            event: The telemetry event.

        Returns:
            List of matching ReactiveFunctions.
        """
        # Get all enabled functions with event triggers
        functions = list_enabled_with_event_trigger()
        matching = []

        for func in functions:
            if self._event_matches_trigger(event, func):
                matching.append(func)

        return matching

    def _event_matches_trigger(
        self,
        event: TelemetryEvent,
        func: ReactiveFunction,
    ) -> bool:
        """Check if an event matches a function's trigger.

        Args:
            event: The telemetry event.
            func: The reactive function.

        Returns:
            True if the event matches the trigger pattern.
        """
        trigger = func.trigger

        if trigger.type != "event":
            return False

        if not trigger.event:
            return False

        et = trigger.event

        # Check source
        if et.source and et.source != event.source:
            return False

        # Check category
        if et.category and et.category != event.category:
            return False

        # Check severity (minimum severity)
        if et.severity:
            severity_order = ["debug", "info", "notice", "warning", "error", "critical"]
            try:
                min_idx = severity_order.index(et.severity)
                event_idx = severity_order.index(event.severity)
                if event_idx < min_idx:
                    return False
            except ValueError:
                pass

        # Check additional match patterns against event.raw
        if et.match:
            for key, pattern in et.match.items():
                # Navigate to the value in event.raw
                value = event.raw.get(key)
                if value is None:
                    return False

                # If pattern is a string, check for regex match or equality
                if isinstance(pattern, str):
                    if not (value == pattern or _regex_match(pattern, str(value))):
                        return False
                # If pattern is a number, check equality
                elif isinstance(pattern, (int, float)):
                    if value != pattern:
                        return False
                # If pattern is a list, check containment
                elif isinstance(pattern, list) and value not in pattern:
                    return False

        return True

    async def _process_function(
        self,
        func: ReactiveFunction,
        event: TelemetryEvent | None,
        context_override: dict[str, Any] | None = None,
        skip_rate_limit: bool = False,
    ) -> ExecutionRecord | None:
        """Process a single reactive function.

        Args:
            func: The reactive function.
            event: The trigger event (None for scheduled/manual).
            context_override: Optional context data.
            skip_rate_limit: Whether to skip rate limit checks.

        Returns:
            ExecutionRecord, or None if rate-limited or condition failed.
        """
        start_time = time.time()
        triggered_at = datetime.utcnow()

        # 2. Check rate limits
        if not skip_rate_limit:
            if not self._check_rate_limit(func):
                logger.debug(f"Function {func.name} rate-limited")
                return None

            # Check allowed hours
            if not self._check_allowed_hours(func.policy):
                logger.debug(f"Function {func.name} outside allowed hours")
                return None

        # 3. Build context (probes state, gathers system metrics)
        context = await self._build_context(func, event, context_override)

        # 4. Evaluate condition
        condition_result = True
        condition_explanation = "No condition"

        if func.condition:
            condition_result, condition_explanation = evaluate_condition(func.condition, context)

        if not condition_result:
            logger.debug(f"Function {func.name} condition not met: {condition_explanation}")
            return None

        # 5. Execute actions with incident recording
        actions_executed: list[str] = []
        actions_results: list[ActionResult] = []
        overall_success = True
        overall_error: str | None = None
        incident_id: str | None = None

        # Use IncidentContext to ensure all actions are recorded
        try:
            from elle.cli.agentic.incident_context import IncidentContext, RecordingMode

            async with IncidentContext.for_reactive_function(
                function_id=func.id,
                function_name=func.name,
                trigger_event=_event_to_dict(event) if event else None,
                recording_mode=RecordingMode.BEST_EFFORT,
            ) as ctx:
                incident_id = ctx.incident_id

                for action in func.actions:
                    try:
                        action_result = await self._execute_action(action, context, incident_id)
                        actions_executed.append(action.capability)
                        actions_results.append(action_result)

                        # Record the action to incident context
                        await ctx.record_action(
                            kind="capability",
                            command=action.capability,
                            payload=action.input,
                            success=action_result.success,
                            output=action_result.output or "",
                            error=action_result.error,
                            duration_ms=action_result.execution_time_ms or 0,
                        )

                        if not action_result.success:
                            overall_success = False
                            overall_error = action_result.error

                            if action.on_failure == "stop":
                                break
                            elif action.on_failure == "rollback":
                                # TODO: Implement rollback logic
                                break

                    except Exception as e:
                        logger.exception(f"Action {action.capability} failed: {e}")
                        actions_executed.append(action.capability)
                        actions_results.append(
                            ActionResult(
                                capability=action.capability,
                                success=False,
                                error=str(e),
                            )
                        )
                        overall_success = False
                        overall_error = str(e)

                        # Record the failure
                        await ctx.record_action(
                            kind="capability",
                            command=action.capability,
                            payload=action.input,
                            success=False,
                            error=str(e),
                        )

                        if action.on_failure == "stop":
                            break

                # Set outcome on the context
                ctx.set_outcome(
                    success=overall_success,
                    summary=f"Executed {len(actions_executed)} actions",
                )

        except ImportError:
            # Fallback if incident_context not available
            for action in func.actions:
                try:
                    action_result = await self._execute_action(action, context, incident_id)
                    actions_executed.append(action.capability)
                    actions_results.append(action_result)

                    if not action_result.success:
                        overall_success = False
                        overall_error = action_result.error

                        if action.on_failure in ("stop", "rollback"):
                            # TODO: Implement rollback logic for "rollback" case
                            break

                except Exception as e:
                    logger.exception(f"Action {action.capability} failed: {e}")
                    actions_executed.append(action.capability)
                    actions_results.append(
                        ActionResult(
                            capability=action.capability,
                            success=False,
                            error=str(e),
                        )
                    )
                    overall_success = False
                    overall_error = str(e)

                    if action.on_failure == "stop":
                        break

        # 6. Update rate limit state
        if not skip_rate_limit:
            self._update_rate_limit(func)

        # 7. Notify on failure if configured
        if not overall_success and func.policy.escalate_on_failure:
            await self._notify_failure(func, overall_error or "Unknown error")

        # 8. Create and store execution record
        execution_time_ms = int((time.time() - start_time) * 1000)

        record = ExecutionRecord(
            function_id=func.id,
            function_name=func.name,
            triggered_at=triggered_at,
            trigger_event=_event_to_dict(event) if event else None,
            condition_result=condition_result,
            condition_explanation=condition_explanation,
            actions_executed=tuple(actions_executed),
            actions_results=tuple(actions_results),
            success=overall_success,
            error=overall_error,
            execution_time_ms=execution_time_ms,
            incident_id=incident_id,
        )

        record_execution(record)

        return record

    async def _build_context(
        self,
        func: ReactiveFunction,
        event: TelemetryEvent | None,
        context_override: dict[str, Any] | None = None,
    ) -> ConditionContext:
        """Build the evaluation context for a function.

        Args:
            func: The reactive function.
            event: The trigger event.
            context_override: Optional context data.

        Returns:
            ConditionContext with event, state, and system data.
        """
        # Event data
        event_data = _event_to_dict(event) if event else {}

        # State data (from probes)
        state_data = await self._probe_state(func)

        # System metrics (basic for now)
        system_data = await self._get_system_metrics()

        # Apply overrides
        if context_override:
            if "event" in context_override:
                event_data.update(context_override["event"])
            if "state" in context_override:
                state_data.update(context_override["state"])
            if "system" in context_override:
                system_data.update(context_override["system"])

        return ConditionContext(
            event=event_data if event_data else None,
            state=state_data,
            system=system_data,
        )

    async def _probe_state(
        self,
        func: ReactiveFunction,
    ) -> dict[str, Any]:
        """Execute state probes defined in the function.

        Args:
            func: The reactive function.

        Returns:
            Dict of probe results.
        """
        state: dict[str, Any] = {}

        if not func.state:
            return state

        for key, probe_spec in func.state.items():
            # Check cache
            cache_key = f"{func.id}:{key}"
            if cache_key in self._state_cache:
                cached_time, cached_value = self._state_cache[cache_key]
                age = (datetime.utcnow() - cached_time).total_seconds()
                if age < probe_spec.cache_ttl:
                    state[key] = cached_value.get("value")
                    continue

            # Run probe (placeholder - would integrate with actual probe system)
            try:
                value = await self._run_probe(probe_spec.probe)
                state[key] = value
                self._state_cache[cache_key] = (
                    datetime.utcnow(),
                    {"value": value},
                )
            except Exception as e:
                logger.warning(f"Probe {probe_spec.probe} failed: {e}")
                state[key] = None

        return state

    async def _run_probe(self, probe_name: str) -> Any:
        """Run a named probe and return its value.

        Args:
            probe_name: The probe identifier.

        Returns:
            Probe result value.
        """
        # Placeholder - would integrate with daemon/telemetry/probes.py
        # For now, return None for unknown probes
        return None

    async def _get_system_metrics(self) -> dict[str, Any]:
        """Get current system metrics for context.

        Returns:
            Dict of system metrics.
        """
        # Placeholder - would gather basic system state
        # Could integrate with SystemSnapshot collection
        return {
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def _execute_action(
        self,
        action: ActionSpec,
        context: ConditionContext,
        incident_id: str | None = None,
    ) -> ActionResult:
        """Execute a single action via the capability executor.

        Args:
            action: The action specification.
            context: The evaluation context for template resolution.
            incident_id: Optional incident ID for recording.

        Returns:
            ActionResult with execution outcome.
        """
        start_time = time.time()

        # Resolve templates in action input
        resolved_input = resolve_templates_in_dict(action.input, context)

        try:
            # Get the capability's input model
            capability = self.executor.registry.get(action.capability)
            if not capability:
                return ActionResult(
                    capability=action.capability,
                    success=False,
                    error=f"Capability not found: {action.capability}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            # Create input model instance
            input_model = self._create_input_model(capability, resolved_input)

            # Execute via capability executor
            result = await self.executor.execute(
                action.capability,
                input_model,
                incident_id=incident_id,
                require_confirmation=False,  # Reactive functions don't wait for confirmation
            )

            return ActionResult(
                capability=action.capability,
                success=result.success,
                output=result.output,
                error=result.error,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            logger.exception(f"Failed to execute action {action.capability}: {e}")
            return ActionResult(
                capability=action.capability,
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _create_input_model(
        self,
        capability: Any,
        input_data: dict[str, Any],
    ) -> BaseModel:
        """Create an input model instance for a capability.

        Args:
            capability: The capability instance.
            input_data: Input data dict.

        Returns:
            Pydantic model instance.
        """
        # Try to get the input schema class
        if hasattr(capability, "input_schema"):
            schema_class = capability.input_schema
            result = schema_class(**input_data)
            # Cast to BaseModel since schema_class is dynamically typed
            return result if isinstance(result, BaseModel) else BaseModel()

        # Fallback: try to infer from spec
        spec = capability.spec
        if spec.input_schema_name:
            # Would need a schema registry to look up by name
            pass

        # Last resort: create a generic model
        class GenericInput(BaseModel):
            pass

        generic = GenericInput.model_construct()
        for k, v in input_data.items():
            setattr(generic, k, v)
        return generic

    def _check_rate_limit(self, func: ReactiveFunction) -> bool:
        """Check if function execution is allowed by rate limits.

        Args:
            func: The reactive function.

        Returns:
            True if execution is allowed.
        """
        state = get_rate_limit_state(func.id)
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")

        # Check daily limit
        if state.daily_reset_date != today:
            # Reset daily counter
            pass
        elif state.daily_executions >= func.policy.max_daily_executions:
            return False

        # Check frequency limit
        if state.last_execution:
            interval = _parse_interval(func.policy.max_frequency)
            if now - state.last_execution < interval:
                return False

        return True

    def _check_allowed_hours(self, policy: PolicySpec) -> bool:
        """Check if current time is within allowed hours.

        Args:
            policy: The policy specification.

        Returns:
            True if execution is allowed at current time.
        """
        if not policy.allowed_hours:
            return True

        start_hour, end_hour = policy.allowed_hours
        current_hour = datetime.utcnow().hour

        if start_hour <= end_hour:
            return start_hour <= current_hour < end_hour
        else:
            # Wraps around midnight
            return current_hour >= start_hour or current_hour < end_hour

    def _update_rate_limit(self, func: ReactiveFunction) -> None:
        """Update rate limit state after execution.

        Args:
            func: The reactive function.
        """
        state = get_rate_limit_state(func.id)
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")

        # Reset daily counter if new day
        daily_executions = 1 if state.daily_reset_date != today else state.daily_executions + 1

        update_rate_limit_state(
            func.id,
            last_execution=now,
            daily_executions=daily_executions,
            daily_reset_date=today,
        )

    async def _notify_failure(self, func: ReactiveFunction, error: str) -> None:
        """Send notification on action failure.

        Args:
            func: The reactive function.
            error: Error message.
        """
        try:
            from elle.daemon.notifications.service import notify

            notify(
                title=f"Reactive function failed: {func.name}",
                body=error[:200],  # Truncate
            )
        except Exception as e:
            logger.warning(f"Failed to send failure notification: {e}")


# =============================================================================
# Helper functions
# =============================================================================


def _event_to_dict(event: TelemetryEvent | None) -> dict[str, Any]:
    """Convert a TelemetryEvent to a dict for context.

    Args:
        event: The telemetry event.

    Returns:
        Dict representation.
    """
    if event is None:
        return {}

    return {
        "event_id": event.event_id,
        "ts": event.ts.isoformat(),
        "source": event.source,
        "severity": event.severity,
        "category": event.category,
        "message": event.message,
        "entity": event.entity,
        "fingerprint": event.fingerprint,
        "raw": event.raw,
    }


def _parse_interval(interval_str: str) -> timedelta:
    """Parse an interval string like '5m', '1h', '1d'.

    Args:
        interval_str: Interval string.

    Returns:
        timedelta object.
    """
    match = re.match(r"^(\d+)([smhd])$", interval_str.strip())
    if not match:
        # Default to 5 minutes
        return timedelta(minutes=5)

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "s":
        return timedelta(seconds=value)
    elif unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    else:
        return timedelta(minutes=5)


def _regex_match(pattern: str, text: str) -> bool:
    """Check if text matches a regex pattern.

    Args:
        pattern: Regex pattern.
        text: Text to match.

    Returns:
        True if pattern matches.
    """
    try:
        return bool(re.search(pattern, text))
    except re.error:
        return pattern in text


# =============================================================================
# Singleton
# =============================================================================

_engine: ReactiveEngine | None = None


def get_engine() -> ReactiveEngine:
    """Get the singleton ReactiveEngine instance.

    Returns:
        The global ReactiveEngine.
    """
    global _engine
    if _engine is None:
        _engine = ReactiveEngine()
    return _engine


def reset_engine() -> None:
    """Reset the global engine instance (for testing)."""
    global _engine
    _engine = None
