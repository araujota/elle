"""AT-SPI GUI automation for ELLE.

Provides learning, planning, and execution of UI automation
with self-healing capabilities and incident integration.

Key Components:
- ATSPIClient: Low-level AT-SPI accessibility tree operations
- RecipeLearner: Learns UI structure from applications
- UITaskPlanner: Converts natural language to action sequences
- UIExecutor: Executes actions with adaptation and logging
- ElementMatcher: Fuzzy element matching with multiple strategies
- RecipeStore: CRUD operations for UI recipes

Usage:
    from elle.atspi import learn_application, execute_gui_task

    # Learn an application
    recipe = await learn_application("gnome-control-center")

    # Execute a task
    result = await execute_gui_task(
        request="disable bluetooth",
        app_name="gnome-control-center",
    )

Requirements:
    - AT-SPI2 accessibility bus running
    - pyatspi2 or python3-gi with Atspi

Enable accessibility:
    gsettings set org.gnome.desktop.interface toolkit-accessibility true
"""

from __future__ import annotations

# Client
from elle.atspi.client import (
    ATSPIClient,
    get_client,
    is_atspi_available,
)

# Executor
from elle.atspi.executor import (
    UIExecutor,
    get_executor,
)

# Learner
from elle.atspi.learner import (
    RecipeLearner,
    get_learner,
)

# Matcher
from elle.atspi.matcher import (
    ElementMatcher,
    UIAdaptor,
    get_adaptor,
    get_matcher,
)

# Models
from elle.atspi.models import (
    AccessibleApp,
    ActionResult,
    ActionType,
    ExecutionOutcome,
    ExecutionResult,
    LearnMethod,
    MatchMethod,
    MatchResult,
    RecipeExecution,
    RecipeVersion,
    SearchScope,
    UIAction,
    UIElement,
    UIRecipe,
    UITaskPlan,
)

# Planner
from elle.atspi.planner import (
    UITaskPlanner,
    get_planner,
)

# Store
from elle.atspi.store import (
    create_execution,
    create_recipe,
    delete_recipe,
    finalize_execution,
    get_execution,
    get_recipe,
    get_recipe_by_app,
    list_executions,
    list_recipes,
    search_elements,
    update_recipe,
)

# =============================================================================
# High-level API
# =============================================================================


async def learn_application(
    app_name: str,
    mode: LearnMethod = "passive",
    update_existing: bool = True,
) -> UIRecipe | None:
    """Learn an application's UI structure.

    Captures the accessibility tree of a running application
    and stores it as a recipe for future automation.

    Args:
        app_name: Application name (e.g., "gnome-control-center").
        mode: Learning mode ("passive" for auto-traverse, "interactive" for user-guided).
        update_existing: Whether to update existing recipe.

    Returns:
        Created or updated UIRecipe, or None if app not found.

    Example:
        recipe = await learn_application("gnome-control-center")
        print(f"Learned {len(recipe.elements)} elements")
    """
    learner = get_learner()
    return await learner.learn_application(app_name, mode, update_existing)


def learn_application_sync(
    app_name: str,
    mode: LearnMethod = "passive",
    update_existing: bool = True,
) -> UIRecipe | None:
    """Synchronous version of learn_application."""
    learner = get_learner()
    return learner.learn_application_sync(app_name, mode, update_existing)


async def execute_gui_task(
    request: str,
    app_name: str,
    adapt_on_failure: bool = True,
    dry_run: bool = False,
) -> ExecutionResult:
    """Execute a natural language GUI task.

    Converts the request into UI actions and executes them
    with full incident tracking.

    Args:
        request: Natural language request (e.g., "disable bluetooth").
        app_name: Target application name.
        adapt_on_failure: Whether to try adaptation when elements move.
        dry_run: If True, simulate without actual actions.

    Returns:
        ExecutionResult with outcome and details.

    Example:
        result = await execute_gui_task(
            request="disable bluetooth",
            app_name="gnome-control-center",
        )
        print(result.user_explanation)
    """
    # Get or learn recipe
    recipe = get_recipe_by_app(app_name)

    # Plan the task
    planner = get_planner()
    plan = await planner.plan_task(request, app_name, recipe)

    # Execute
    executor = get_executor()
    return await executor.execute_plan(plan, adapt_on_failure, dry_run)


def execute_gui_task_sync(
    request: str,
    app_name: str,
    adapt_on_failure: bool = True,
    dry_run: bool = False,
) -> ExecutionResult:
    """Synchronous version of execute_gui_task."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(execute_gui_task(request, app_name, adapt_on_failure, dry_run))


__all__ = [
    # Models
    "AccessibleApp",
    "ActionResult",
    "ActionType",
    "ExecutionOutcome",
    "ExecutionResult",
    "LearnMethod",
    "MatchMethod",
    "MatchResult",
    "RecipeExecution",
    "RecipeVersion",
    "SearchScope",
    "UIAction",
    "UIElement",
    "UIRecipe",
    "UITaskPlan",
    # Client
    "ATSPIClient",
    "get_client",
    "is_atspi_available",
    # Store
    "create_recipe",
    "delete_recipe",
    "get_recipe",
    "get_recipe_by_app",
    "list_recipes",
    "update_recipe",
    "create_execution",
    "finalize_execution",
    "get_execution",
    "list_executions",
    "search_elements",
    # Matcher
    "ElementMatcher",
    "UIAdaptor",
    "get_matcher",
    "get_adaptor",
    # Learner
    "RecipeLearner",
    "get_learner",
    # Planner
    "UITaskPlanner",
    "get_planner",
    # Executor
    "UIExecutor",
    "get_executor",
    # High-level API
    "learn_application",
    "learn_application_sync",
    "execute_gui_task",
    "execute_gui_task_sync",
]
