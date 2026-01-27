"""Task planning from natural language requests.

Converts user requests like "disable bluetooth" into
executable sequences of UI actions using LLM.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from elle.atspi.models import UIAction, UIElement, UIRecipe, UITaskPlan

logger = logging.getLogger(__name__)


# =============================================================================
# LLM Prompt Templates
# =============================================================================


PLANNER_SYSTEM_PROMPT = """You are a UI automation planner. Convert user requests into UI action sequences.

Given a user request and available UI elements, output a JSON plan with this structure:
{
    "actions": [
        {
            "action_type": "click|toggle|type|select|focus",
            "target_name": "exact element name to interact with",
            "target_role": "element role if known",
            "text_input": "text to type (for 'type' action only)",
            "verify_state": "expected state after action (checked, unchecked, etc.)",
            "wait_ms": 200
        }
    ],
    "preconditions": ["app must be running", "window must be visible"],
    "expected_outcome": "description of final state"
}

RULES:
- Use exact element names from the available elements list
- For toggle actions, include verify_state to confirm the change
- Add short waits (100-300ms) between actions
- Keep actions minimal - only what's needed for the request
- If the request is ambiguous, include clarifying preconditions
- For "disable X", use toggle or click action with verify_state="unchecked"
- For "enable X", use toggle or click action with verify_state="checked"
"""


PLANNER_PROMPT_TEMPLATE = """USER REQUEST: {request}

APPLICATION: {app_name}

AVAILABLE UI ELEMENTS:
{elements_list}

NAVIGATION TARGETS:
{navigation_targets}

Create a minimal action plan to fulfill the user's request.
Return ONLY valid JSON, no explanation."""


# =============================================================================
# Element Formatting
# =============================================================================


def _format_elements_for_prompt(
    elements: tuple[UIElement, ...],
    max_elements: int = 50,
) -> str:
    """Format elements for LLM prompt.

    Args:
        elements: UI elements from recipe.
        max_elements: Maximum elements to include.

    Returns:
        Formatted string listing elements.
    """
    lines = []

    # Filter to actionable elements
    actionable = [
        e
        for e in elements
        if e.actions
        or e.role
        in (
            "push button",
            "toggle button",
            "check box",
            "radio button",
            "menu item",
            "combo box",
            "text",
            "entry",
            "slider",
            "switch",
            "link",
        )
    ]

    for elem in actionable[:max_elements]:
        states_str = ", ".join(elem.states) if elem.states else "none"
        actions_str = ", ".join(elem.actions) if elem.actions else "none"
        lines.append(f"- name='{elem.name}' role='{elem.role}' states=[{states_str}] actions=[{actions_str}]")

    if len(actionable) > max_elements:
        lines.append(f"... and {len(actionable) - max_elements} more elements")

    return "\n".join(lines) if lines else "(no actionable elements found)"


def _format_navigation_targets(
    paths: dict[str, tuple[int, ...]],
) -> str:
    """Format navigation targets for LLM prompt.

    Args:
        paths: Navigation path dict.

    Returns:
        Formatted string listing targets.
    """
    if not paths:
        return "(no named targets)"

    lines = []
    for name in sorted(paths.keys())[:30]:
        lines.append(f"- {name}")

    return "\n".join(lines)


# =============================================================================
# Task Planner
# =============================================================================


class UITaskPlanner:
    """Plans UI actions from natural language requests.

    Uses LLM to convert requests like "disable bluetooth" into
    sequences of UI actions that can be executed.

    Usage:
        planner = UITaskPlanner()
        plan = await planner.plan_task(
            request="disable bluetooth",
            app_name="gnome-control-center",
            recipe=recipe,
        )
    """

    def __init__(self) -> None:
        """Initialize the planner."""
        self._llm_available: bool | None = None

    def _check_llm_available(self) -> bool:
        """Check if LLM is available for planning."""
        if self._llm_available is not None:
            return self._llm_available

        try:
            from elle.rag.ollama_client import get_client

            client = get_client()
            self._llm_available = client.is_available()
        except Exception:
            self._llm_available = False

        return self._llm_available

    async def plan_task(
        self,
        request: str,
        app_name: str,
        recipe: UIRecipe | None = None,
    ) -> UITaskPlan:
        """Plan UI actions for a user request.

        Args:
            request: User's natural language request.
            app_name: Target application name.
            recipe: Optional recipe for the application.

        Returns:
            UITaskPlan with action sequence.
        """
        task_id = str(uuid.uuid4())

        # Try LLM planning first
        if self._check_llm_available() and recipe:
            plan_data = await self._plan_with_llm(request, app_name, recipe)
            if plan_data:
                return self._build_plan(task_id, request, app_name, recipe, plan_data)

        # Fall back to rule-based planning
        plan_data = self._plan_with_rules(request, app_name, recipe)
        return self._build_plan(task_id, request, app_name, recipe, plan_data)

    def plan_task_sync(
        self,
        request: str,
        app_name: str,
        recipe: UIRecipe | None = None,
    ) -> UITaskPlan:
        """Synchronous version of plan_task.

        Args:
            request: User's natural language request.
            app_name: Target application name.
            recipe: Optional recipe.

        Returns:
            UITaskPlan.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self.plan_task(request, app_name, recipe))

    async def _plan_with_llm(
        self,
        request: str,
        app_name: str,
        recipe: UIRecipe,
    ) -> dict[str, Any] | None:
        """Generate plan using LLM.

        Args:
            request: User request.
            app_name: Application name.
            recipe: Application recipe.

        Returns:
            Parsed plan data, or None if LLM fails.
        """
        try:
            from elle.rag.ollama_client import get_client

            client = get_client()

            # Build prompt
            elements_list = _format_elements_for_prompt(recipe.elements)
            nav_targets = _format_navigation_targets(recipe.navigation_paths)

            prompt = PLANNER_PROMPT_TEMPLATE.format(
                request=request,
                app_name=app_name,
                elements_list=elements_list,
                navigation_targets=nav_targets,
            )

            # Get model
            models = client.list_models()
            model = next(
                (m for m in models if "phi" in m.lower() or "llama" in m.lower()),
                models[0] if models else None,
            )

            if not model:
                return None

            # Generate plan
            response = client.generate_json(
                model=model,
                prompt=prompt,
                system=PLANNER_SYSTEM_PROMPT,
                max_tokens=500,
                temperature=0.3,
            )

            return response

        except Exception as e:
            logger.warning(f"LLM planning failed: {e}")
            return None

    def _plan_with_rules(
        self,
        request: str,
        app_name: str,
        recipe: UIRecipe | None,
    ) -> dict[str, Any]:
        """Generate plan using rule-based matching.

        Args:
            request: User request.
            app_name: Application name.
            recipe: Optional recipe.

        Returns:
            Plan data dict.
        """
        request_lower = request.lower()
        actions = []
        preconditions = [f"{app_name} must be running"]
        expected_outcome = ""

        # Parse request for action keywords
        action_type = "click"
        verify_state = None

        if re.search(r"\b(disable|turn\s+off|deactivate)\b", request_lower):
            action_type = "toggle"
            verify_state = "unchecked"
        elif re.search(r"\b(enable|turn\s+on|activate)\b", request_lower):
            action_type = "toggle"
            verify_state = "checked"
        elif re.search(r"\b(click|press|select)\b", request_lower):
            action_type = "click"

        # Extract target from request
        target_name = self._extract_target(request)

        if recipe:
            # Try to find matching element
            matched = self._match_request_to_elements(target_name, recipe.elements)
            if matched:
                actions.append(
                    {
                        "action_type": action_type,
                        "target_name": matched.name,
                        "target_role": matched.role,
                        "verify_state": verify_state,
                        "wait_ms": 200,
                    }
                )
                expected_outcome = f"{matched.name} is {verify_state or 'clicked'}"
            else:
                # Use raw target name
                actions.append(
                    {
                        "action_type": action_type,
                        "target_name": target_name,
                        "verify_state": verify_state,
                        "wait_ms": 200,
                    }
                )
                expected_outcome = f"{target_name} is {verify_state or 'clicked'}"
        else:
            # No recipe, use raw target
            actions.append(
                {
                    "action_type": action_type,
                    "target_name": target_name,
                    "verify_state": verify_state,
                    "wait_ms": 200,
                }
            )
            expected_outcome = f"{target_name} is {verify_state or 'clicked'}"

        return {
            "actions": actions,
            "preconditions": preconditions,
            "expected_outcome": expected_outcome,
        }

    def _extract_target(self, request: str) -> str:
        """Extract target element name from request.

        Args:
            request: User request.

        Returns:
            Extracted target name.
        """
        request_lower = request.lower()

        # Remove common action words
        for word in [
            "disable",
            "enable",
            "turn off",
            "turn on",
            "click",
            "press",
            "toggle",
            "activate",
            "deactivate",
            "the",
            "a",
            "an",
        ]:
            request_lower = request_lower.replace(word, "")

        # Remove common prepositions
        for word in ["in", "on", "at", "to", "from"]:
            request_lower = re.sub(rf"\b{word}\b", "", request_lower)

        # Clean up
        target = request_lower.strip()
        target = re.sub(r"\s+", " ", target)

        return target.title() if target else request

    def _match_request_to_elements(
        self,
        target: str,
        elements: tuple[UIElement, ...],
    ) -> UIElement | None:
        """Match request target to recipe elements.

        Args:
            target: Target from request.
            elements: Available elements.

        Returns:
            Best matching element, or None.
        """
        from elle.atspi.matcher import similarity_ratio

        target_lower = target.lower()
        best_match = None
        best_score = 0.0

        for elem in elements:
            if not elem.name:
                continue

            # Check direct substring match
            if target_lower in elem.name.lower():
                score = 0.9
            elif elem.name.lower() in target_lower:
                score = 0.8
            else:
                score = similarity_ratio(target, elem.name)

            if score > best_score:
                best_score = score
                best_match = elem

        return best_match if best_score >= 0.5 else None

    def _build_plan(
        self,
        task_id: str,
        request: str,
        app_name: str,
        recipe: UIRecipe | None,
        plan_data: dict[str, Any],
    ) -> UITaskPlan:
        """Build UITaskPlan from plan data.

        Args:
            task_id: Task UUID.
            request: Original request.
            app_name: Application name.
            recipe: Optional recipe.
            plan_data: Plan data dict.

        Returns:
            UITaskPlan.
        """
        # Parse actions
        actions_data = plan_data.get("actions", [])
        actions = []

        for action_data in actions_data:
            # Look up path from recipe if available
            target_path = None
            if recipe and action_data.get("target_name"):
                target_name = action_data["target_name"]
                # Check navigation paths
                for path_name, path in recipe.navigation_paths.items():
                    if target_name.lower() in path_name.lower():
                        target_path = path
                        break
                # Or find in elements
                if not target_path:
                    for elem in recipe.elements:
                        if elem.name and target_name.lower() in elem.name.lower():
                            target_path = elem.path
                            break

            action = UIAction(
                action_type=action_data.get("action_type", "click"),
                target_name=action_data.get("target_name", ""),
                target_role=action_data.get("target_role"),
                target_path=target_path,
                text_input=action_data.get("text_input"),
                wait_ms=action_data.get("wait_ms", 100),
                verify_state=action_data.get("verify_state"),
            )
            actions.append(action)

        preconditions = tuple(plan_data.get("preconditions", []))
        expected_outcome = plan_data.get("expected_outcome", "")

        return UITaskPlan(
            task_id=task_id,
            user_request=request,
            app_name=app_name,
            recipe_id=recipe.recipe_id if recipe else None,
            actions=tuple(actions),
            preconditions=preconditions,
            expected_outcome=expected_outcome,
        )


# =============================================================================
# Module-level instance
# =============================================================================


_planner: UITaskPlanner | None = None


def get_planner() -> UITaskPlanner:
    """Get the shared UITaskPlanner instance."""
    global _planner
    if _planner is None:
        _planner = UITaskPlanner()
    return _planner
