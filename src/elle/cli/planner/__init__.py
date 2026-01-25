"""System Task Planner - LLM-driven safe command plan generation.

The planner module provides intelligent task planning when users request
system modifications:
1. Builds context from Man Vault and Incident Vault
2. Generates verified command plans via LLM
3. Validates commands for safety (flags, rollback, checks)
4. Presents plans with risk assessment in unicode boxes
5. Executes with rollback support on failure
6. Records outcomes to Incident Vault for learning

Usage (REPL mode):
    /do open ports 80 and 443
    -> Shows plan preview
    -> User confirms
    -> Executes with progress
    -> Shows summary

Usage (programmatic):
    from elle.cli.planner import get_planner_service

    service = get_planner_service()
    result = service.run_planning_pipeline(request, session)

    # For interactive mode:
    from elle.cli.planner import run_interactive_planner
    final_result = run_interactive_planner(result, session)

    # For text output:
    from elle.cli.planner import render_plan_result
    print(render_plan_result(result))
"""

# Models
# Interactive UI
from elle.cli.planner.interactive import (
    PlannerInteractive,
    run_interactive_planner,
    run_noninteractive_planner,
)
from elle.cli.planner.models import (
    CheckResult,
    CommandPlan,
    ManDocContext,
    PlanContext,
    PlanOutcome,
    PlanResult,
    PlanStep,
    PlanVerification,
    PriorPlanContext,
    RiskLevel,
    RollbackStep,
    StepResult,
    StepVerification,
    TaskRequest,
    ValidationCheck,
)

# Rendering
from elle.cli.planner.renderer import (
    render_box,
    render_check_result,
    render_confirmation_prompt,
    render_execution_summary,
    render_plan,
    render_plan_result,
    render_step_progress,
    render_step_result,
    risk_badge,
    risk_color,
)

# Service
from elle.cli.planner.service import (
    PlannerService,
    get_planner_service,
    reset_planner_service,
)

# Verification
from elle.cli.planner.verifier import (
    can_auto_approve,
    verify_plan,
    verify_step,
)

__all__ = [
    # Models
    "CheckResult",
    "CommandPlan",
    "ManDocContext",
    "PlanContext",
    "PlanOutcome",
    "PlanResult",
    "PlanStep",
    "PlanVerification",
    "PriorPlanContext",
    "RiskLevel",
    "RollbackStep",
    "StepResult",
    "StepVerification",
    "TaskRequest",
    "ValidationCheck",
    # Service
    "PlannerService",
    "get_planner_service",
    "reset_planner_service",
    # Rendering
    "render_box",
    "render_check_result",
    "render_confirmation_prompt",
    "render_execution_summary",
    "render_plan",
    "render_plan_result",
    "render_step_progress",
    "render_step_result",
    "risk_badge",
    "risk_color",
    # Interactive
    "PlannerInteractive",
    "run_interactive_planner",
    "run_noninteractive_planner",
    # Verification
    "can_auto_approve",
    "verify_plan",
    "verify_step",
]
