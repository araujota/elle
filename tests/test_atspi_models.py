"""Tests for AT-SPI data models."""

import pytest

from elle.atspi.models import (
    AccessibleApp,
    ActionResult,
    MatchResult,
    UIAction,
    UIElement,
    UIRecipe,
    UITaskPlan,
)


class TestUIElement:
    """Tests for UIElement model."""

    def test_create_basic_element(self):
        """Test creating a basic UI element."""
        elem = UIElement(
            role="push button",
            name="Bluetooth",
            path=(0, 2, 1, 3),
        )

        assert elem.role == "push button"
        assert elem.name == "Bluetooth"
        assert elem.path == (0, 2, 1, 3)
        assert elem.states == ()
        assert elem.actions == ()

    def test_element_with_all_fields(self):
        """Test element with all fields populated."""
        elem = UIElement(
            role="toggle button",
            name="Wi-Fi",
            description="Toggle Wi-Fi on or off",
            states=("enabled", "visible", "checked"),
            actions=("click", "toggle"),
            path=(0, 1, 2),
            parent_role="panel",
            parent_name="Wireless Settings",
            label_patterns=(r"Wi-?Fi", r"Wireless"),
            sibling_context=("Bluetooth", "Airplane Mode"),
            bounds=(100, 200, 50, 30),
        )

        assert elem.description == "Toggle Wi-Fi on or off"
        assert "enabled" in elem.states
        assert "click" in elem.actions
        assert elem.parent_role == "panel"
        assert elem.bounds == (100, 200, 50, 30)

    def test_element_is_frozen(self):
        """Test that UIElement is immutable."""
        elem = UIElement(
            role="button",
            name="Test",
            path=(0,),
        )

        with pytest.raises(Exception):  # ValidationError for frozen model
            elem.name = "Changed"


class TestUIRecipe:
    """Tests for UIRecipe model."""

    def test_create_basic_recipe(self):
        """Test creating a basic recipe."""
        recipe = UIRecipe(
            recipe_id="test-recipe-123",
            app_name="gnome-settings",
            root_window="Settings",
        )

        assert recipe.recipe_id == "test-recipe-123"
        assert recipe.app_name == "gnome-settings"
        assert recipe.version == 1
        assert recipe.confidence == 1.0

    def test_recipe_with_elements(self):
        """Test recipe with elements."""
        elem1 = UIElement(role="button", name="OK", path=(0,))
        elem2 = UIElement(role="button", name="Cancel", path=(1,))

        recipe = UIRecipe(
            recipe_id="test-recipe",
            app_name="test-app",
            root_window="Main Window",
            elements=(elem1, elem2),
            navigation_paths={"ok_button": (0,), "cancel_button": (1,)},
        )

        assert len(recipe.elements) == 2
        assert "ok_button" in recipe.navigation_paths
        assert recipe.navigation_paths["ok_button"] == (0,)

    def test_recipe_confidence_update_success(self):
        """Test confidence update on success."""
        recipe = UIRecipe(
            recipe_id="test",
            app_name="test",
            root_window="Main",
            confidence=0.8,
        )

        updated = recipe.with_updated_confidence(success=True)

        assert updated.confidence > recipe.confidence
        assert updated.success_count == 1
        assert updated.failure_count == 0
        assert updated.last_success_at is not None

    def test_recipe_confidence_update_failure(self):
        """Test confidence update on failure."""
        recipe = UIRecipe(
            recipe_id="test",
            app_name="test",
            root_window="Main",
            confidence=0.8,
        )

        updated = recipe.with_updated_confidence(success=False)

        assert updated.confidence < recipe.confidence
        assert updated.success_count == 0
        assert updated.failure_count == 1


class TestUIAction:
    """Tests for UIAction model."""

    def test_create_click_action(self):
        """Test creating a click action."""
        action = UIAction(
            action_type="click",
            target_name="Bluetooth",
            target_role="toggle button",
        )

        assert action.action_type == "click"
        assert action.target_name == "Bluetooth"
        assert action.wait_ms == 100  # Default

    def test_create_type_action(self):
        """Test creating a type action."""
        action = UIAction(
            action_type="type",
            target_name="Search",
            text_input="hello world",
        )

        assert action.action_type == "type"
        assert action.text_input == "hello world"

    def test_action_with_verification(self):
        """Test action with state verification."""
        action = UIAction(
            action_type="toggle",
            target_name="Bluetooth",
            verify_state="unchecked",
            wait_ms=200,
        )

        assert action.verify_state == "unchecked"
        assert action.wait_ms == 200


class TestUITaskPlan:
    """Tests for UITaskPlan model."""

    def test_create_task_plan(self):
        """Test creating a task plan."""
        actions = (UIAction(action_type="click", target_name="Bluetooth"),)

        plan = UITaskPlan(
            task_id="task-123",
            user_request="disable bluetooth",
            app_name="gnome-settings",
            actions=actions,
            expected_outcome="Bluetooth is disabled",
        )

        assert plan.task_id == "task-123"
        assert plan.user_request == "disable bluetooth"
        assert len(plan.actions) == 1
        assert plan.expected_outcome == "Bluetooth is disabled"


class TestActionResult:
    """Tests for ActionResult model."""

    def test_successful_action_result(self):
        """Test successful action result."""
        action = UIAction(action_type="click", target_name="OK")

        result = ActionResult(
            action=action,
            action_index=0,
            success=True,
            element_found=True,
            element_path_used=(0, 1, 2),
            state_after=("enabled", "focused"),
            duration_ms=50,
        )

        assert result.success
        assert result.element_found
        assert result.state_after == ("enabled", "focused")

    def test_failed_action_result(self):
        """Test failed action result."""
        action = UIAction(action_type="click", target_name="Missing")

        result = ActionResult(
            action=action,
            action_index=0,
            success=False,
            error="Element not found",
            element_found=False,
        )

        assert not result.success
        assert result.error == "Element not found"

    def test_adapted_action_result(self):
        """Test action result with adaptation."""
        action = UIAction(action_type="click", target_name="Bluetooth")

        result = ActionResult(
            action=action,
            action_index=0,
            success=True,
            element_found=True,
            adapted=True,
            adaptation_method="fuzzy_name",
        )

        assert result.adapted
        assert result.adaptation_method == "fuzzy_name"


class TestMatchResult:
    """Tests for MatchResult model."""

    def test_successful_match(self):
        """Test successful element match."""
        elem = UIElement(role="button", name="OK", path=(0, 1))

        result = MatchResult(
            found=True,
            element=elem,
            method="exact_name",
            confidence=0.95,
            actual_path=(0, 1),
        )

        assert result.found
        assert result.element is not None
        assert result.method == "exact_name"

    def test_failed_match(self):
        """Test failed element match."""
        result = MatchResult(
            found=False,
            error="Element not found after all strategies",
            expected_path=(0, 2, 1),
            candidates_count=10,
        )

        assert not result.found
        assert result.error is not None
        assert result.candidates_count == 10


class TestAccessibleApp:
    """Tests for AccessibleApp model."""

    def test_create_accessible_app(self):
        """Test creating an accessible app info."""
        app = AccessibleApp(
            name="gnome-control-center",
            desktop_id="org.gnome.Settings.desktop",
            pid=1234,
            window_count=1,
            is_active=True,
        )

        assert app.name == "gnome-control-center"
        assert app.pid == 1234
        assert app.is_active
