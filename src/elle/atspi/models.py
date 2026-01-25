"""Pydantic models for AT-SPI GUI automation.

Defines data structures for UI elements, recipes, actions, and task plans.
All models are frozen (immutable) for safety and caching.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# UI Element
# =============================================================================


class UIElement(BaseModel):
    """Single accessible UI element.

    Represents an atomic component in the accessibility tree,
    with enough information for matching and interaction.
    """

    model_config = ConfigDict(frozen=True)

    # Core identity
    role: str = Field(
        description="AT-SPI role: 'push button', 'toggle button', 'menu item', etc."
    )
    name: str = Field(
        description="Accessible name: 'Bluetooth', 'Wi-Fi', 'Close', etc."
    )
    description: str | None = Field(
        default=None,
        description="Accessibility description if available",
    )

    # State and capabilities
    states: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Current states: 'enabled', 'visible', 'focusable', 'checked', etc.",
    )
    actions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Available actions: 'click', 'activate', 'toggle', etc.",
    )

    # Location in tree
    path: tuple[int, ...] = Field(
        description="Index path from root window: (0, 2, 1, 3)",
    )
    parent_role: str | None = Field(
        default=None,
        description="Parent element's role for context",
    )
    parent_name: str | None = Field(
        default=None,
        description="Parent element's name for context",
    )

    # Matching hints for fuzzy matching when UI changes
    label_patterns: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Regex patterns for name variants (e.g., 'Blue.*', 'BT')",
    )
    sibling_context: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Adjacent element names for disambiguation",
    )

    # Bounding box (optional, for visual matching)
    bounds: tuple[int, int, int, int] | None = Field(
        default=None,
        description="Screen coordinates: (x, y, width, height)",
    )


# =============================================================================
# UI Recipe
# =============================================================================


# Learning method types
LearnMethod = Literal["interactive", "passive", "rebuild"]


class UIRecipe(BaseModel):
    """Versioned recipe for navigating an application.

    Captures the complete UI structure learned from an application,
    enabling reliable automation even as the UI evolves.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    recipe_id: str = Field(description="UUID for this recipe")
    app_name: str = Field(
        description="Human-friendly app name: 'gnome-settings', 'firefox'",
    )
    app_desktop_id: str | None = Field(
        default=None,
        description="Desktop file ID: 'org.gnome.Settings.desktop'",
    )
    app_version: str | None = Field(
        default=None,
        description="Application version if detectable: '46.0'",
    )

    # Learning metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    learn_method: LearnMethod = Field(
        default="passive",
        description="How this recipe was learned",
    )

    # UI structure
    root_window: str = Field(
        description="Window title or role for the main window",
    )
    elements: tuple[UIElement, ...] = Field(
        default_factory=tuple,
        description="All discovered UI elements",
    )
    navigation_paths: dict[str, tuple[int, ...]] = Field(
        default_factory=dict,
        description="Named paths to important elements: 'bluetooth_toggle' -> (0,2,1,3)",
    )

    # Confidence and versioning
    version: int = Field(
        default=1,
        description="Recipe version, increments on update",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score, decays on failures",
    )
    last_success_at: datetime | None = Field(
        default=None,
        description="When recipe was last used successfully",
    )
    failure_count: int = Field(
        default=0,
        ge=0,
        description="Number of failed executions",
    )
    success_count: int = Field(
        default=0,
        ge=0,
        description="Number of successful executions",
    )

    def with_updated_confidence(self, success: bool) -> "UIRecipe":
        """Return a new recipe with updated confidence based on execution outcome.

        Args:
            success: Whether the execution was successful.

        Returns:
            New UIRecipe with adjusted confidence.
        """
        if success:
            new_confidence = min(1.0, self.confidence + 0.05)
            return UIRecipe(
                recipe_id=self.recipe_id,
                app_name=self.app_name,
                app_desktop_id=self.app_desktop_id,
                app_version=self.app_version,
                created_at=self.created_at,
                updated_at=datetime.utcnow(),
                learn_method=self.learn_method,
                root_window=self.root_window,
                elements=self.elements,
                navigation_paths=self.navigation_paths,
                version=self.version,
                confidence=new_confidence,
                last_success_at=datetime.utcnow(),
                failure_count=self.failure_count,
                success_count=self.success_count + 1,
            )
        else:
            new_confidence = max(0.0, self.confidence - 0.15)
            return UIRecipe(
                recipe_id=self.recipe_id,
                app_name=self.app_name,
                app_desktop_id=self.app_desktop_id,
                app_version=self.app_version,
                created_at=self.created_at,
                updated_at=datetime.utcnow(),
                learn_method=self.learn_method,
                root_window=self.root_window,
                elements=self.elements,
                navigation_paths=self.navigation_paths,
                version=self.version,
                confidence=new_confidence,
                last_success_at=self.last_success_at,
                failure_count=self.failure_count + 1,
                success_count=self.success_count,
            )


# =============================================================================
# UI Action
# =============================================================================


# Action types
ActionType = Literal["click", "toggle", "type", "select", "scroll", "wait", "focus"]

# Search scope for element finding
SearchScope = Literal["window", "application", "desktop"]


class UIAction(BaseModel):
    """Single action to perform on UI.

    Represents one step in a UI automation sequence,
    with fallback strategies for resilience.
    """

    model_config = ConfigDict(frozen=True)

    # Action specification
    action_type: ActionType = Field(
        description="Type of action to perform",
    )
    target_name: str = Field(
        description="Element name to find",
    )
    target_role: str | None = Field(
        default=None,
        description="Optional role filter for disambiguation",
    )
    target_path: tuple[int, ...] | None = Field(
        default=None,
        description="Direct path if known from recipe",
    )

    # Action parameters
    text_input: str | None = Field(
        default=None,
        description="Text to type for 'type' action",
    )
    wait_ms: int = Field(
        default=100,
        ge=0,
        description="Milliseconds to wait after action",
    )
    verify_state: str | None = Field(
        default=None,
        description="Expected state after action: 'checked', 'expanded', etc.",
    )

    # Fallback strategies
    fallback_patterns: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Alternative name patterns to try",
    )
    search_scope: SearchScope = Field(
        default="window",
        description="Where to search for the element",
    )


# =============================================================================
# UI Task Plan
# =============================================================================


class UITaskPlan(BaseModel):
    """Plan to fulfill a user's GUI request.

    Converts a natural language request into an executable
    sequence of UI actions with preconditions and verification.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    task_id: str = Field(description="UUID for this task")
    user_request: str = Field(
        description="Original user request: 'disable bluetooth'",
    )
    app_name: str = Field(
        description="Target application name",
    )
    recipe_id: str | None = Field(
        default=None,
        description="Matched recipe ID if using learned recipe",
    )

    # Execution plan
    actions: tuple[UIAction, ...] = Field(
        default_factory=tuple,
        description="Ordered sequence of actions to execute",
    )
    preconditions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Conditions that must be true: 'app must be running'",
    )
    expected_outcome: str = Field(
        default="",
        description="Expected result: 'Bluetooth toggle is off'",
    )

    # Incident tracking
    incident_id: str | None = Field(
        default=None,
        description="Linked incident for audit trail",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Execution Result
# =============================================================================


# Execution outcome types
ExecutionOutcome = Literal["pending", "success", "partial", "failed", "adapted"]


class ActionResult(BaseModel):
    """Result of executing a single UI action.

    Tracks what happened during action execution,
    including any adaptations made.
    """

    model_config = ConfigDict(frozen=True)

    # Action reference
    action: UIAction = Field(description="The action that was executed")
    action_index: int = Field(description="Index in the action sequence")

    # Outcome
    success: bool = Field(description="Whether the action succeeded")
    error: str | None = Field(
        default=None,
        description="Error message if failed",
    )

    # Element found
    element_found: bool = Field(
        default=False,
        description="Whether the target element was found",
    )
    element_path_used: tuple[int, ...] | None = Field(
        default=None,
        description="Actual path used to find element",
    )

    # State verification
    state_after: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Element states after action",
    )
    verification_passed: bool | None = Field(
        default=None,
        description="Whether verify_state check passed",
    )

    # Adaptation
    adapted: bool = Field(
        default=False,
        description="Whether adaptation was needed",
    )
    adaptation_method: str | None = Field(
        default=None,
        description="How the element was found: 'direct_path', 'fuzzy_name', etc.",
    )

    # Timing
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Execution time in milliseconds",
    )


class ExecutionResult(BaseModel):
    """Result of executing a complete task plan.

    Aggregates results from all actions and provides
    overall outcome assessment.
    """

    model_config = ConfigDict(frozen=True)

    # Task reference
    task_id: str = Field(description="The task that was executed")
    plan: UITaskPlan = Field(description="The plan that was executed")

    # Outcome
    outcome: ExecutionOutcome = Field(
        description="Overall execution outcome",
    )
    success: bool = Field(description="Whether the task completed successfully")

    # Action results
    action_results: tuple[ActionResult, ...] = Field(
        default_factory=tuple,
        description="Results for each action in order",
    )
    actions_completed: int = Field(
        default=0,
        ge=0,
        description="Number of actions successfully completed",
    )
    actions_total: int = Field(
        default=0,
        ge=0,
        description="Total number of actions in plan",
    )

    # Adaptation summary
    adaptations_made: int = Field(
        default=0,
        ge=0,
        description="Number of actions that required adaptation",
    )
    adaptation_notes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Descriptions of adaptations made",
    )

    # Error details
    error: str | None = Field(
        default=None,
        description="Overall error message if failed",
    )
    failed_at_action: int | None = Field(
        default=None,
        description="Index of first failed action",
    )

    # Timing
    total_duration_ms: int = Field(
        default=0,
        ge=0,
        description="Total execution time",
    )
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = Field(default=None)

    # Incident
    incident_id: str | None = Field(
        default=None,
        description="Created/linked incident ID",
    )

    # User explanation
    user_explanation: str = Field(
        default="",
        description="Human-readable explanation of what happened",
    )


# =============================================================================
# Recipe Execution History
# =============================================================================


class RecipeExecution(BaseModel):
    """Record of a recipe execution.

    Links recipe execution to incident vault for
    learning and audit.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: str = Field(description="UUID for this execution")
    recipe_id: str = Field(description="The recipe that was executed")
    incident_id: str | None = Field(
        default=None,
        description="Linked incident ID",
    )

    # Request
    task_request: str = Field(
        description="User's original request",
    )
    actions: tuple[UIAction, ...] = Field(
        default_factory=tuple,
        description="Actions that were executed",
    )

    # Timing
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = Field(default=None)

    # Outcome
    outcome: ExecutionOutcome = Field(description="Execution outcome")
    adaptation_notes: str | None = Field(
        default=None,
        description="Description of what changed and how we adapted",
    )


# =============================================================================
# Recipe Version
# =============================================================================


class RecipeVersion(BaseModel):
    """Historical version of a recipe.

    Stored for rollback and learning from
    UI evolution over time.
    """

    model_config = ConfigDict(frozen=True)

    version_id: str = Field(description="UUID for this version")
    recipe_id: str = Field(description="Parent recipe ID")
    version: int = Field(description="Version number")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Snapshot
    elements: tuple[UIElement, ...] = Field(
        description="Elements at this version",
    )
    navigation_paths: dict[str, tuple[int, ...]] = Field(
        default_factory=dict,
        description="Navigation paths at this version",
    )

    # Reason for update
    change_reason: str = Field(
        default="",
        description="Why this version was created",
    )


# =============================================================================
# Accessible Application Info
# =============================================================================


class AccessibleApp(BaseModel):
    """Information about an accessible application.

    Represents a running application that can be
    controlled via AT-SPI.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Application name")
    desktop_id: str | None = Field(
        default=None,
        description="Desktop file ID if available",
    )
    pid: int | None = Field(
        default=None,
        description="Process ID",
    )
    window_count: int = Field(
        default=0,
        ge=0,
        description="Number of windows",
    )
    is_active: bool = Field(
        default=False,
        description="Whether application has focus",
    )


# =============================================================================
# Match Result
# =============================================================================


# Match method types
MatchMethod = Literal[
    "direct_path",
    "exact_name",
    "fuzzy_name",
    "sibling_context",
    "role_search",
    "tree_search",
]


class MatchResult(BaseModel):
    """Result of attempting to find a UI element.

    Includes information about how the element was
    found for learning and debugging.
    """

    model_config = ConfigDict(frozen=True)

    # Outcome
    found: bool = Field(description="Whether element was found")
    element: UIElement | None = Field(
        default=None,
        description="The found element",
    )

    # How it was found
    method: MatchMethod | None = Field(
        default=None,
        description="Method used to find the element",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the match",
    )

    # Path information
    expected_path: tuple[int, ...] | None = Field(
        default=None,
        description="Path we expected to find element at",
    )
    actual_path: tuple[int, ...] | None = Field(
        default=None,
        description="Path where element was actually found",
    )

    # Candidates considered
    candidates_count: int = Field(
        default=0,
        ge=0,
        description="Number of candidate elements considered",
    )

    # Error
    error: str | None = Field(
        default=None,
        description="Error message if not found",
    )
