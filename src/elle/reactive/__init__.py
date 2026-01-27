"""ELLE Reactive Functions - Event-driven automation system.

Reactive functions are "if-this-then-that" automations that:
- Trigger on telemetry events or schedules
- Evaluate conditions over system state
- Execute ELLE capabilities as actions
- Record everything to the incident vault

Example usage:
    from elle.reactive import (
        ReactiveFunction,
        Trigger,
        EventTrigger,
        Condition,
        ActionSpec,
        PolicySpec,
    )

    func = ReactiveFunction(
        name="docker-cleanup",
        description="Clean Docker when disk full",
        trigger=Trigger(
            type="event",
            event=EventTrigger(category="disk"),
        ),
        condition=Condition(
            expression={"gte": ["{event.raw.used_pct}", 85]},
        ),
        actions=(
            ActionSpec(capability="docker.prune", input={}),
        ),
        policy=PolicySpec(max_frequency="1h"),
    )
"""

from typing import TYPE_CHECKING

from elle.reactive.models import (
    ActionResult,
    # Action models
    ActionSpec,
    # Condition models
    Condition,
    ConditionContext,
    # Trigger models
    EventTrigger,
    ExecutionRecord,
    OnFailureMode,
    # Policy models
    PolicySpec,
    RateLimitState,
    # Core models
    ReactiveFunction,
    ScheduleTrigger,
    StateProbe,
    TelemetrySeverityType,
    TelemetrySourceType,
    Trigger,
    # Type aliases
    TriggerType,
)

if TYPE_CHECKING:
    from elle.reactive.compiler import ReactiveFunctionCompiler
    from elle.reactive.engine import ReactiveEngine
    from elle.reactive.evaluator import ConditionEvaluator
    from elle.reactive.scheduler import ReactiveScheduler


# Lazy imports for heavy modules
def get_engine() -> "ReactiveEngine":
    """Get the singleton ReactiveEngine instance."""
    from elle.reactive.engine import get_engine as _get_engine

    return _get_engine()


def get_evaluator() -> "ConditionEvaluator":
    """Get the singleton ConditionEvaluator instance."""
    from elle.reactive.evaluator import get_evaluator as _get_evaluator

    return _get_evaluator()


def get_compiler() -> "ReactiveFunctionCompiler":
    """Get the singleton ReactiveFunctionCompiler instance."""
    from elle.reactive.compiler import get_compiler as _get_compiler

    return _get_compiler()


def get_scheduler() -> "ReactiveScheduler":
    """Get the singleton ReactiveScheduler instance."""
    from elle.reactive.scheduler import get_scheduler as _get_scheduler

    return _get_scheduler()


__all__ = [
    # Type aliases
    "TriggerType",
    "OnFailureMode",
    "TelemetrySourceType",
    "TelemetrySeverityType",
    # Trigger models
    "EventTrigger",
    "ScheduleTrigger",
    "Trigger",
    # Condition models
    "Condition",
    "ConditionContext",
    # Action models
    "ActionSpec",
    "ActionResult",
    # Policy models
    "PolicySpec",
    "StateProbe",
    # Core models
    "ReactiveFunction",
    "ExecutionRecord",
    "RateLimitState",
    # Singletons
    "get_engine",
    "get_evaluator",
    "get_compiler",
    "get_scheduler",
]
