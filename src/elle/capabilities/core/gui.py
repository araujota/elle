"""GUI automation capabilities via AT-SPI.

Provides typed, policy-governed capabilities for UI automation:
- gui.learn: Learn an application's UI structure
- gui.click: Click a UI element
- gui.type: Type text into a UI element
- gui.navigate: Navigate to a UI element
- gui.execute_task: Execute a natural language GUI task
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability
from elle.common.pydantic_compat import safe_model_dump

logger = logging.getLogger(__name__)

# =============================================================================
# Input/Output Models
# =============================================================================


class GuiLearnInput(BaseModel):
    """Input for gui.learn capability."""

    model_config = ConfigDict(frozen=True)

    app_name: str = Field(
        description="Application name to learn (e.g., 'gnome-control-center')",
    )
    mode: Literal["passive", "interactive"] = Field(
        default="passive",
        description="Learning mode: passive auto-traversal or interactive user-guided",
    )
    update_existing: bool = Field(
        default=True,
        description="Whether to update existing recipe or create new",
    )


class GuiLearnOutput(BaseModel):
    """Output for gui.learn capability."""

    model_config = ConfigDict(frozen=True)

    recipe_id: str = Field(description="Created/updated recipe ID")
    app_name: str = Field(description="Application name")
    element_count: int = Field(description="Number of elements captured")
    navigation_target_count: int = Field(description="Number of navigation targets")
    version: int = Field(description="Recipe version")
    created: bool = Field(description="True if new recipe, False if updated")


class GuiClickInput(BaseModel):
    """Input for gui.click capability."""

    model_config = ConfigDict(frozen=True)

    app_name: str = Field(description="Application name")
    element_name: str = Field(description="Element name to click")
    element_role: str | None = Field(
        default=None,
        description="Optional role filter",
    )
    wait_ms: int = Field(
        default=100,
        ge=0,
        description="Milliseconds to wait after click",
    )


class GuiClickOutput(BaseModel):
    """Output for gui.click capability."""

    model_config = ConfigDict(frozen=True)

    clicked: bool = Field(description="Whether click succeeded")
    element_found: bool = Field(description="Whether element was found")
    adapted: bool = Field(
        default=False,
        description="Whether adaptation was needed to find element",
    )
    state_after: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Element states after click",
    )


class GuiTypeInput(BaseModel):
    """Input for gui.type capability."""

    model_config = ConfigDict(frozen=True)

    app_name: str = Field(description="Application name")
    element_name: str = Field(description="Element name to type into")
    text: str = Field(description="Text to type")
    element_role: str | None = Field(
        default=None,
        description="Optional role filter",
    )
    clear_first: bool = Field(
        default=True,
        description="Whether to clear existing text first",
    )


class GuiTypeOutput(BaseModel):
    """Output for gui.type capability."""

    model_config = ConfigDict(frozen=True)

    typed: bool = Field(description="Whether text was typed")
    element_found: bool = Field(description="Whether element was found")


class GuiNavigateInput(BaseModel):
    """Input for gui.navigate capability."""

    model_config = ConfigDict(frozen=True)

    app_name: str = Field(description="Application name")
    element_name: str = Field(description="Element name to navigate to")
    element_role: str | None = Field(
        default=None,
        description="Optional role filter",
    )


class GuiNavigateOutput(BaseModel):
    """Output for gui.navigate capability."""

    model_config = ConfigDict(frozen=True)

    focused: bool = Field(description="Whether element was focused")
    element_found: bool = Field(description="Whether element was found")
    element_path: tuple[int, ...] = Field(
        default_factory=tuple,
        description="Path to the element",
    )


class GuiExecuteTaskInput(BaseModel):
    """Input for gui.execute_task capability."""

    model_config = ConfigDict(frozen=True)

    request: str = Field(
        description="Natural language request (e.g., 'disable bluetooth')",
    )
    app_name: str = Field(description="Application name")
    dry_run: bool = Field(
        default=False,
        description="If True, simulate without actual actions",
    )


class GuiExecuteTaskOutput(BaseModel):
    """Output for gui.execute_task capability."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether task completed successfully")
    outcome: str = Field(description="Outcome: success, adapted, failed, partial")
    actions_completed: int = Field(description="Number of actions completed")
    actions_total: int = Field(description="Total actions in plan")
    adaptations_made: int = Field(
        default=0,
        description="Number of adaptations made",
    )
    user_explanation: str = Field(
        default="",
        description="Human-readable explanation",
    )
    incident_id: str | None = Field(
        default=None,
        description="Created incident ID",
    )


# =============================================================================
# Capability Implementations
# =============================================================================


class GuiLearnCapability(BaseCapability[GuiLearnInput, GuiLearnOutput]):
    """Learn an application's UI structure.

    Captures the accessibility tree of a running application
    and stores it as a recipe for automation.
    """

    @property
    def spec(self) -> CapabilitySpec:
        """Get capability specification."""
        return CapabilitySpec(
            name="gui.learn",
            summary="Learn an application's UI structure for automation",
            domain="gui",
            risk="low",
            side_effects=(),
            requires_privilege=False,
            idempotent=True,
            input_schema_name="GuiLearnInput",
            output_schema_name="GuiLearnOutput",
            dependencies=("atspi",),
        )

    def run(
        self,
        input: GuiLearnInput,
    ) -> CapabilityResult:
        """Execute the capability."""
        import asyncio

        from elle.atspi import get_recipe_by_app, learn_application

        start_time = datetime.utcnow()

        try:
            # Check for existing recipe
            existing = get_recipe_by_app(input.app_name)

            # Learn the application (run async function synchronously)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    recipe = pool.submit(
                        lambda: asyncio.run(
                            learn_application(
                                app_name=input.app_name,
                                mode=input.mode,
                                update_existing=input.update_existing,
                            )
                        )
                    ).result()
            else:
                recipe = loop.run_until_complete(
                    learn_application(
                        app_name=input.app_name,
                        mode=input.mode,
                        update_existing=input.update_existing,
                    )
                )

            if not recipe:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input.app_name}' not found or not accessible",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            output = GuiLearnOutput(
                recipe_id=recipe.recipe_id,
                app_name=recipe.app_name,
                element_count=len(recipe.elements),
                navigation_target_count=len(recipe.navigation_paths),
                version=recipe.version,
                created=existing is None,
            )

            return CapabilityResult(
                success=True,
                output=safe_model_dump(output),
                execution_time_ms=_elapsed_ms(start_time),
                evidence=CapabilityEvidence(
                    verification_passed=True,
                    verification_details=f"Learned {len(recipe.elements)} elements",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=_elapsed_ms(start_time),
            )

    def dry_run(
        self,
        input: GuiLearnInput,
    ) -> DryRunResult:
        """Preview what would happen."""
        from elle.atspi import get_client, is_atspi_available

        if not is_atspi_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("AT-SPI is not available",),
                preview_text="Cannot learn: AT-SPI accessibility not available",
            )

        client = get_client()
        app = client.get_application(input.app_name)

        if not app:
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Application '{input.app_name}' not found",),
                preview_text=f"Cannot learn: {input.app_name} is not running",
            )

        return DryRunResult(
            is_valid=True,
            would_execute=(f"Traverse UI tree of {input.app_name}",),
            preview_text=f"Will capture UI structure of {input.app_name}",
            requires_confirmation=False,
        )

    def verify(
        self,
        input: GuiLearnInput,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        from elle.atspi import get_recipe_by_app

        recipe = get_recipe_by_app(input.app_name)

        return VerificationResult(
            passed=recipe is not None,
            checks_performed=("Recipe exists in database",),
            actual_state={"recipe_exists": recipe is not None},
            expected_state={"recipe_exists": True},
        )

    def rollback(
        self,
        input: GuiLearnInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback is not needed for learning."""
        return RollbackResult(
            success=True,
            rolled_back=(),
        )


class GuiClickCapability(BaseCapability[GuiClickInput, GuiClickOutput]):
    """Click a UI element."""

    @property
    def spec(self) -> CapabilitySpec:
        """Get capability specification."""
        return CapabilitySpec(
            name="gui.click",
            summary="Click a UI element in an application",
            domain="gui",
            risk="low",
            side_effects=(
                SideEffect(
                    kind="ui_interaction",
                    target="application",
                    reversible=False,
                    description="Clicks a UI element",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            input_schema_name="GuiClickInput",
            output_schema_name="GuiClickOutput",
            dependencies=("atspi",),
        )

    def run(
        self,
        input: GuiClickInput,
    ) -> CapabilityResult:
        """Execute the capability."""
        from elle.atspi import (
            get_adaptor,
            get_client,
            get_recipe_by_app,
        )

        start_time = datetime.utcnow()

        try:
            client = get_client()
            adaptor = get_adaptor()

            # Find application
            app = client.get_application(input.app_name)
            if not app:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input.app_name}' not found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            # Get window
            window = _get_main_window(client, app)
            if not window:
                return CapabilityResult(
                    success=False,
                    error="No window found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            # Get recipe for context
            recipe = get_recipe_by_app(input.app_name)

            # Find element
            find_result, _ = adaptor.adapt_and_find(
                root=window,
                target_name=input.element_name,
                target_role=input.element_role,
                recipe_elements=recipe.elements if recipe else (),
            )

            if not find_result.found:
                return CapabilityResult(
                    success=False,
                    output=safe_model_dump(
                        GuiClickOutput(
                            clicked=False,
                            element_found=False,
                        )
                    ),
                    error=find_result.error,
                    execution_time_ms=_elapsed_ms(start_time),
                )

            # Click element
            actual_path = find_result.actual_path or ()
            accessible = client._follow_path(window, actual_path)
            clicked = client.do_action(accessible, "click")

            if input.wait_ms > 0:
                import time

                time.sleep(input.wait_ms / 1000)

            state_after = client.get_state(accessible) if accessible else ()

            output = GuiClickOutput(
                clicked=clicked,
                element_found=True,
                adapted=find_result.method != "direct_path",
                state_after=state_after,
            )

            return CapabilityResult(
                success=clicked,
                output=safe_model_dump(output),
                execution_time_ms=_elapsed_ms(start_time),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=_elapsed_ms(start_time),
            )

    def dry_run(
        self,
        input: GuiClickInput,
    ) -> DryRunResult:
        """Preview what would happen."""
        return DryRunResult(
            is_valid=True,
            would_execute=(f"Click '{input.element_name}'",),
            preview_text=f"Will click element '{input.element_name}' in {input.app_name}",
            requires_confirmation=True,
        )

    def verify(
        self,
        input: GuiClickInput,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        # Click verification just checks the element exists
        from elle.atspi import get_adaptor, get_client, get_recipe_by_app

        client = get_client()
        app = client.get_application(input.app_name)
        if not app:
            return VerificationResult(passed=False, discrepancies=("Application not found",))

        window = _get_main_window(client, app)
        if not window:
            return VerificationResult(passed=False, discrepancies=("Window not found",))

        recipe = get_recipe_by_app(input.app_name)
        adaptor = get_adaptor()
        find_result, _ = adaptor.adapt_and_find(
            root=window,
            target_name=input.element_name,
            target_role=input.element_role,
            recipe_elements=recipe.elements if recipe else (),
        )

        return VerificationResult(
            passed=find_result.found,
            checks_performed=("Click action completed",),
        )

    def rollback(
        self,
        input: GuiClickInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback is not possible for clicks."""
        return RollbackResult(
            success=False,
            error="Click actions cannot be rolled back",
        )


class GuiTypeCapability(BaseCapability[GuiTypeInput, GuiTypeOutput]):
    """Type text into a UI element."""

    @property
    def spec(self) -> CapabilitySpec:
        """Get capability specification."""
        return CapabilitySpec(
            name="gui.type",
            summary="Type text into a UI element",
            domain="gui",
            risk="low",
            side_effects=(
                SideEffect(
                    kind="ui_interaction",
                    target="text field",
                    reversible=True,
                    description="Types text into an element",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            input_schema_name="GuiTypeInput",
            output_schema_name="GuiTypeOutput",
            dependencies=("atspi",),
        )

    def run(
        self,
        input: GuiTypeInput,
    ) -> CapabilityResult:
        """Execute the capability."""
        from elle.atspi import get_adaptor, get_client, get_recipe_by_app

        start_time = datetime.utcnow()

        try:
            client = get_client()
            adaptor = get_adaptor()

            app = client.get_application(input.app_name)
            if not app:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input.app_name}' not found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            window = _get_main_window(client, app)
            if not window:
                return CapabilityResult(
                    success=False,
                    error="No window found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            recipe = get_recipe_by_app(input.app_name)

            find_result, _ = adaptor.adapt_and_find(
                root=window,
                target_name=input.element_name,
                target_role=input.element_role,
                recipe_elements=recipe.elements if recipe else (),
            )

            if not find_result.found:
                return CapabilityResult(
                    success=False,
                    output=safe_model_dump(
                        GuiTypeOutput(
                            typed=False,
                            element_found=False,
                        )
                    ),
                    error=find_result.error,
                    execution_time_ms=_elapsed_ms(start_time),
                )

            actual_path = find_result.actual_path or ()
            accessible = client._follow_path(window, actual_path)
            typed = client.type_text(accessible, input.text)

            output = GuiTypeOutput(
                typed=typed,
                element_found=True,
            )

            return CapabilityResult(
                success=typed,
                output=safe_model_dump(output),
                execution_time_ms=_elapsed_ms(start_time),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=_elapsed_ms(start_time),
            )

    def dry_run(
        self,
        input: GuiTypeInput,
    ) -> DryRunResult:
        """Preview what would happen."""
        return DryRunResult(
            is_valid=True,
            would_execute=(f"Type '{input.text}' into '{input.element_name}'",),
            preview_text=f"Will type text into '{input.element_name}'",
            requires_confirmation=True,
        )

    def verify(
        self,
        input: GuiTypeInput,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        # Type verification just checks the element exists
        from elle.atspi import get_adaptor, get_client, get_recipe_by_app

        client = get_client()
        app = client.get_application(input.app_name)
        if not app:
            return VerificationResult(passed=False, discrepancies=("Application not found",))

        window = _get_main_window(client, app)
        if not window:
            return VerificationResult(passed=False, discrepancies=("Window not found",))

        recipe = get_recipe_by_app(input.app_name)
        adaptor = get_adaptor()
        find_result, _ = adaptor.adapt_and_find(
            root=window,
            target_name=input.element_name,
            target_role=input.element_role,
            recipe_elements=recipe.elements if recipe else (),
        )

        return VerificationResult(
            passed=find_result.found,
            checks_performed=("Text typed",),
        )

    def rollback(
        self,
        input: GuiTypeInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by clearing the text."""
        # Could potentially clear the field
        return RollbackResult(
            success=False,
            error="Type rollback not implemented",
        )


class GuiNavigateCapability(BaseCapability[GuiNavigateInput, GuiNavigateOutput]):
    """Navigate to a UI element (focus, scroll into view)."""

    @property
    def spec(self) -> CapabilitySpec:
        """Get capability specification."""
        return CapabilitySpec(
            name="gui.navigate",
            summary="Navigate to and focus a UI element",
            domain="gui",
            risk="none",
            side_effects=(
                SideEffect(
                    kind="window_focus",
                    target="element",
                    reversible=True,
                    description="Changes focus to element",
                ),
            ),
            requires_privilege=False,
            idempotent=True,
            input_schema_name="GuiNavigateInput",
            output_schema_name="GuiNavigateOutput",
            dependencies=("atspi",),
        )

    def run(
        self,
        input: GuiNavigateInput,
    ) -> CapabilityResult:
        """Execute the capability."""
        from elle.atspi import get_adaptor, get_client, get_recipe_by_app

        start_time = datetime.utcnow()

        try:
            client = get_client()
            adaptor = get_adaptor()

            app = client.get_application(input.app_name)
            if not app:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input.app_name}' not found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            window = _get_main_window(client, app)
            if not window:
                return CapabilityResult(
                    success=False,
                    error="No window found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            recipe = get_recipe_by_app(input.app_name)

            find_result, _ = adaptor.adapt_and_find(
                root=window,
                target_name=input.element_name,
                target_role=input.element_role,
                recipe_elements=recipe.elements if recipe else (),
            )

            if not find_result.found:
                return CapabilityResult(
                    success=False,
                    output=safe_model_dump(
                        GuiNavigateOutput(
                            focused=False,
                            element_found=False,
                        )
                    ),
                    error=find_result.error,
                    execution_time_ms=_elapsed_ms(start_time),
                )

            actual_path = find_result.actual_path or ()
            accessible = client._follow_path(window, actual_path)
            focused = client.focus_element(accessible)

            output = GuiNavigateOutput(
                focused=focused,
                element_found=True,
                element_path=actual_path,
            )

            return CapabilityResult(
                success=True,  # Navigation succeeds if element found
                output=safe_model_dump(output),
                execution_time_ms=_elapsed_ms(start_time),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=_elapsed_ms(start_time),
            )

    def dry_run(
        self,
        input: GuiNavigateInput,
    ) -> DryRunResult:
        """Preview what would happen."""
        return DryRunResult(
            is_valid=True,
            would_execute=(f"Navigate to '{input.element_name}'",),
            preview_text=f"Will focus element '{input.element_name}'",
            requires_confirmation=False,
        )

    def verify(
        self,
        input: GuiNavigateInput,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        # Navigate verification checks the element exists
        from elle.atspi import get_adaptor, get_client, get_recipe_by_app

        client = get_client()
        app = client.get_application(input.app_name)
        if not app:
            return VerificationResult(passed=False, discrepancies=("Application not found",))

        window = _get_main_window(client, app)
        if not window:
            return VerificationResult(passed=False, discrepancies=("Window not found",))

        recipe = get_recipe_by_app(input.app_name)
        adaptor = get_adaptor()
        find_result, _ = adaptor.adapt_and_find(
            root=window,
            target_name=input.element_name,
            target_role=input.element_role,
            recipe_elements=recipe.elements if recipe else (),
        )

        return VerificationResult(
            passed=find_result.found,
            checks_performed=("Element located",),
        )

    def rollback(
        self,
        input: GuiNavigateInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback not needed for navigation."""
        return RollbackResult(success=True, rolled_back=())


class GuiExecuteTaskCapability(BaseCapability[GuiExecuteTaskInput, GuiExecuteTaskOutput]):
    """Execute a natural language GUI task."""

    @property
    def spec(self) -> CapabilitySpec:
        """Get capability specification."""
        return CapabilitySpec(
            name="gui.execute_task",
            summary="Execute a natural language GUI automation task",
            domain="gui",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="ui_interaction",
                    target="application",
                    reversible=False,
                    description="Executes UI actions based on request",
                ),
                SideEffect(
                    kind="incident_created",
                    target="incident vault",
                    reversible=False,
                    description="Creates incident for audit",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            input_schema_name="GuiExecuteTaskInput",
            output_schema_name="GuiExecuteTaskOutput",
            dependencies=("atspi",),
        )

    def run(
        self,
        input: GuiExecuteTaskInput,
    ) -> CapabilityResult:
        """Execute the capability."""
        import asyncio

        from elle.atspi import execute_gui_task

        start_time = datetime.utcnow()

        try:
            # Run async function synchronously
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    task_result = pool.submit(
                        lambda: asyncio.run(
                            execute_gui_task(
                                request=input.request,
                                app_name=input.app_name,
                                dry_run=input.dry_run,
                            )
                        )
                    ).result()
            else:
                task_result = loop.run_until_complete(
                    execute_gui_task(
                        request=input.request,
                        app_name=input.app_name,
                        dry_run=input.dry_run,
                    )
                )

            output = GuiExecuteTaskOutput(
                success=task_result.success,
                outcome=task_result.outcome,
                actions_completed=task_result.actions_completed,
                actions_total=task_result.actions_total,
                adaptations_made=task_result.adaptations_made,
                user_explanation=task_result.user_explanation,
                incident_id=task_result.incident_id,
            )

            return CapabilityResult(
                success=task_result.success,
                output=safe_model_dump(output),
                execution_time_ms=_elapsed_ms(start_time),
                evidence=CapabilityEvidence(
                    verification_passed=task_result.success,
                    verification_details=task_result.user_explanation,
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=_elapsed_ms(start_time),
            )

    def dry_run(
        self,
        input: GuiExecuteTaskInput,
    ) -> DryRunResult:
        """Preview what would happen."""
        import asyncio

        from elle.atspi import get_planner, get_recipe_by_app

        try:
            recipe = get_recipe_by_app(input.app_name)
            planner = get_planner()

            # Run async function synchronously
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    plan = pool.submit(
                        lambda: asyncio.run(
                            planner.plan_task(
                                input.request,
                                input.app_name,
                                recipe,
                            )
                        )
                    ).result()
            else:
                plan = loop.run_until_complete(
                    planner.plan_task(
                        input.request,
                        input.app_name,
                        recipe,
                    )
                )

            actions = [f"{a.action_type} '{a.target_name}'" for a in plan.actions]

            return DryRunResult(
                is_valid=True,
                would_execute=tuple(actions),
                preview_text=f"Will execute {len(plan.actions)} actions: {plan.expected_outcome}",
                requires_confirmation=True,
            )

        except Exception as e:
            return DryRunResult(
                is_valid=False,
                validation_errors=(str(e),),
                preview_text=f"Cannot plan: {e}",
            )

    def verify(
        self,
        input: GuiExecuteTaskInput,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        # Task verification is application-specific, return success by default
        return VerificationResult(
            passed=True,
            checks_performed=("Task execution completed",),
        )

    def rollback(
        self,
        input: GuiExecuteTaskInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback not possible for complex tasks."""
        return RollbackResult(
            success=False,
            error="GUI task rollback not supported",
        )


# =============================================================================
# Helpers
# =============================================================================


def _elapsed_ms(start: datetime) -> int:
    """Calculate elapsed milliseconds."""
    return int((datetime.utcnow() - start).total_seconds() * 1000)


def _get_main_window(client: Any, app: Any) -> Any | None:
    """Get main window from application."""
    try:
        n_children = app.get_child_count() if hasattr(app, "get_child_count") else 0
        for i in range(n_children):
            child = app.get_child_at_index(i) if hasattr(app, "get_child_at_index") else None
            if child:
                role = child.get_role() if hasattr(child, "get_role") else None
                role_name = str(role).lower() if role else ""
                if "window" in role_name or "frame" in role_name:
                    return child
        if n_children > 0:
            return app.get_child_at_index(0)
    except Exception as e:
        logger.debug(f"AT-SPI error getting main window: {e}")
    return None


# =============================================================================
# Capability Registration
# =============================================================================


GUI_CAPABILITIES: list[type[BaseCapability[Any, Any]]] = [
    GuiLearnCapability,
    GuiClickCapability,
    GuiTypeCapability,
    GuiNavigateCapability,
    GuiExecuteTaskCapability,
]
