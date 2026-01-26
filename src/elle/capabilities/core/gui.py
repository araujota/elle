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
from elle.capabilities.protocol import Capability
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


class GuiLearnCapability(Capability):
    """Learn an application's UI structure.

    Captures the accessibility tree of a running application
    and stores it as a recipe for automation.
    """

    @classmethod
    def spec(cls) -> CapabilitySpec:
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

    @classmethod
    async def execute(
        cls,
        input_data: GuiLearnInput,
        **kwargs: Any,
    ) -> CapabilityResult:
        """Execute the capability."""
        from elle.atspi import get_recipe_by_app, learn_application

        start_time = datetime.utcnow()

        try:
            # Check for existing recipe
            existing = get_recipe_by_app(input_data.app_name)

            # Learn the application
            recipe = await learn_application(
                app_name=input_data.app_name,
                mode=input_data.mode,
                update_existing=input_data.update_existing,
            )

            if not recipe:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input_data.app_name}' not found or not accessible",
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

    @classmethod
    async def dry_run(
        cls,
        input_data: GuiLearnInput,
        **kwargs: Any,
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
        app = client.get_application(input_data.app_name)

        if not app:
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Application '{input_data.app_name}' not found",),
                preview_text=f"Cannot learn: {input_data.app_name} is not running",
            )

        return DryRunResult(
            is_valid=True,
            would_execute=(f"Traverse UI tree of {input_data.app_name}",),
            preview_text=f"Will capture UI structure of {input_data.app_name}",
            requires_confirmation=False,
        )

    @classmethod
    async def verify(
        cls,
        input_data: GuiLearnInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        if not result.success:
            return VerificationResult(
                passed=False,
                discrepancies=(result.error or "Unknown error",),
            )

        from elle.atspi import get_recipe_by_app

        recipe = get_recipe_by_app(input_data.app_name)

        return VerificationResult(
            passed=recipe is not None,
            checks_performed=("Recipe exists in database",),
            actual_state={"recipe_exists": recipe is not None},
            expected_state={"recipe_exists": True},
        )

    @classmethod
    async def rollback(
        cls,
        input_data: GuiLearnInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> RollbackResult:
        """Rollback is not needed for learning."""
        return RollbackResult(
            success=True,
            rolled_back=(),
        )


class GuiClickCapability(Capability):
    """Click a UI element."""

    @classmethod
    def spec(cls) -> CapabilitySpec:
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

    @classmethod
    async def execute(
        cls,
        input_data: GuiClickInput,
        **kwargs: Any,
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
            app = client.get_application(input_data.app_name)
            if not app:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input_data.app_name}' not found",
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
            recipe = get_recipe_by_app(input_data.app_name)

            # Find element
            result, _ = adaptor.adapt_and_find(
                root=window,
                target_name=input_data.element_name,
                target_role=input_data.element_role,
                recipe_elements=recipe.elements if recipe else (),
            )

            if not result.found:
                return CapabilityResult(
                    success=False,
                    output=safe_model_dump(GuiClickOutput(
                        clicked=False,
                        element_found=False,
                    )),
                    error=result.error,
                    execution_time_ms=_elapsed_ms(start_time),
                )

            # Click element
            accessible = client._follow_path(window, result.actual_path)
            clicked = client.do_action(accessible, "click")

            if input_data.wait_ms > 0:
                import time
                time.sleep(input_data.wait_ms / 1000)

            state_after = client.get_state(accessible) if accessible else ()

            output = GuiClickOutput(
                clicked=clicked,
                element_found=True,
                adapted=result.method != "direct_path",
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

    @classmethod
    async def dry_run(
        cls,
        input_data: GuiClickInput,
        **kwargs: Any,
    ) -> DryRunResult:
        """Preview what would happen."""
        return DryRunResult(
            is_valid=True,
            would_execute=(f"Click '{input_data.element_name}'",),
            preview_text=f"Will click element '{input_data.element_name}' in {input_data.app_name}",
            requires_confirmation=True,
        )

    @classmethod
    async def verify(
        cls,
        input_data: GuiClickInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        return VerificationResult(
            passed=result.success,
            checks_performed=("Click action completed",),
        )

    @classmethod
    async def rollback(
        cls,
        input_data: GuiClickInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> RollbackResult:
        """Rollback is not possible for clicks."""
        return RollbackResult(
            success=False,
            error="Click actions cannot be rolled back",
        )


class GuiTypeCapability(Capability):
    """Type text into a UI element."""

    @classmethod
    def spec(cls) -> CapabilitySpec:
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

    @classmethod
    async def execute(
        cls,
        input_data: GuiTypeInput,
        **kwargs: Any,
    ) -> CapabilityResult:
        """Execute the capability."""
        from elle.atspi import get_adaptor, get_client, get_recipe_by_app

        start_time = datetime.utcnow()

        try:
            client = get_client()
            adaptor = get_adaptor()

            app = client.get_application(input_data.app_name)
            if not app:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input_data.app_name}' not found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            window = _get_main_window(client, app)
            if not window:
                return CapabilityResult(
                    success=False,
                    error="No window found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            recipe = get_recipe_by_app(input_data.app_name)

            result, _ = adaptor.adapt_and_find(
                root=window,
                target_name=input_data.element_name,
                target_role=input_data.element_role,
                recipe_elements=recipe.elements if recipe else (),
            )

            if not result.found:
                return CapabilityResult(
                    success=False,
                    output=safe_model_dump(GuiTypeOutput(
                        typed=False,
                        element_found=False,
                    )),
                    error=result.error,
                    execution_time_ms=_elapsed_ms(start_time),
                )

            accessible = client._follow_path(window, result.actual_path)
            typed = client.type_text(accessible, input_data.text)

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

    @classmethod
    async def dry_run(
        cls,
        input_data: GuiTypeInput,
        **kwargs: Any,
    ) -> DryRunResult:
        """Preview what would happen."""
        return DryRunResult(
            is_valid=True,
            would_execute=(f"Type '{input_data.text}' into '{input_data.element_name}'",),
            preview_text=f"Will type text into '{input_data.element_name}'",
            requires_confirmation=True,
        )

    @classmethod
    async def verify(
        cls,
        input_data: GuiTypeInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        return VerificationResult(
            passed=result.success,
            checks_performed=("Text typed",),
        )

    @classmethod
    async def rollback(
        cls,
        input_data: GuiTypeInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> RollbackResult:
        """Rollback by clearing the text."""
        # Could potentially clear the field
        return RollbackResult(
            success=False,
            error="Type rollback not implemented",
        )


class GuiNavigateCapability(Capability):
    """Navigate to a UI element (focus, scroll into view)."""

    @classmethod
    def spec(cls) -> CapabilitySpec:
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

    @classmethod
    async def execute(
        cls,
        input_data: GuiNavigateInput,
        **kwargs: Any,
    ) -> CapabilityResult:
        """Execute the capability."""
        from elle.atspi import get_adaptor, get_client, get_recipe_by_app

        start_time = datetime.utcnow()

        try:
            client = get_client()
            adaptor = get_adaptor()

            app = client.get_application(input_data.app_name)
            if not app:
                return CapabilityResult(
                    success=False,
                    error=f"Application '{input_data.app_name}' not found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            window = _get_main_window(client, app)
            if not window:
                return CapabilityResult(
                    success=False,
                    error="No window found",
                    execution_time_ms=_elapsed_ms(start_time),
                )

            recipe = get_recipe_by_app(input_data.app_name)

            result, _ = adaptor.adapt_and_find(
                root=window,
                target_name=input_data.element_name,
                target_role=input_data.element_role,
                recipe_elements=recipe.elements if recipe else (),
            )

            if not result.found:
                return CapabilityResult(
                    success=False,
                    output=safe_model_dump(GuiNavigateOutput(
                        focused=False,
                        element_found=False,
                    )),
                    error=result.error,
                    execution_time_ms=_elapsed_ms(start_time),
                )

            accessible = client._follow_path(window, result.actual_path)
            focused = client.focus_element(accessible)

            output = GuiNavigateOutput(
                focused=focused,
                element_found=True,
                element_path=result.actual_path or (),
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

    @classmethod
    async def dry_run(
        cls,
        input_data: GuiNavigateInput,
        **kwargs: Any,
    ) -> DryRunResult:
        """Preview what would happen."""
        return DryRunResult(
            is_valid=True,
            would_execute=(f"Navigate to '{input_data.element_name}'",),
            preview_text=f"Will focus element '{input_data.element_name}'",
            requires_confirmation=False,
        )

    @classmethod
    async def verify(
        cls,
        input_data: GuiNavigateInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        return VerificationResult(
            passed=result.success,
            checks_performed=("Element located",),
        )

    @classmethod
    async def rollback(
        cls,
        input_data: GuiNavigateInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> RollbackResult:
        """Rollback not needed for navigation."""
        return RollbackResult(success=True, rolled_back=())


class GuiExecuteTaskCapability(Capability):
    """Execute a natural language GUI task."""

    @classmethod
    def spec(cls) -> CapabilitySpec:
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

    @classmethod
    async def execute(
        cls,
        input_data: GuiExecuteTaskInput,
        **kwargs: Any,
    ) -> CapabilityResult:
        """Execute the capability."""
        from elle.atspi import execute_gui_task

        start_time = datetime.utcnow()

        try:
            result = await execute_gui_task(
                request=input_data.request,
                app_name=input_data.app_name,
                dry_run=input_data.dry_run,
            )

            output = GuiExecuteTaskOutput(
                success=result.success,
                outcome=result.outcome,
                actions_completed=result.actions_completed,
                actions_total=result.actions_total,
                adaptations_made=result.adaptations_made,
                user_explanation=result.user_explanation,
                incident_id=result.incident_id,
            )

            return CapabilityResult(
                success=result.success,
                output=safe_model_dump(output),
                execution_time_ms=_elapsed_ms(start_time),
                evidence=CapabilityEvidence(
                    verification_passed=result.success,
                    verification_details=result.user_explanation,
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=_elapsed_ms(start_time),
            )

    @classmethod
    async def dry_run(
        cls,
        input_data: GuiExecuteTaskInput,
        **kwargs: Any,
    ) -> DryRunResult:
        """Preview what would happen."""
        from elle.atspi import get_planner, get_recipe_by_app

        try:
            recipe = get_recipe_by_app(input_data.app_name)
            planner = get_planner()
            plan = await planner.plan_task(
                input_data.request,
                input_data.app_name,
                recipe,
            )

            actions = [
                f"{a.action_type} '{a.target_name}'"
                for a in plan.actions
            ]

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

    @classmethod
    async def verify(
        cls,
        input_data: GuiExecuteTaskInput,
        result: CapabilityResult,
        **kwargs: Any,
    ) -> VerificationResult:
        """Verify the operation succeeded."""
        return VerificationResult(
            passed=result.success,
            checks_performed=("Task execution completed",),
        )

    @classmethod
    async def rollback(
        cls,
        input_data: GuiExecuteTaskInput,
        result: CapabilityResult,
        **kwargs: Any,
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


GUI_CAPABILITIES: list[type[Capability]] = [
    GuiLearnCapability,
    GuiClickCapability,
    GuiTypeCapability,
    GuiNavigateCapability,
    GuiExecuteTaskCapability,
]
