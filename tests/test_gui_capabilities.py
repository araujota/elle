"""Tests for GUI automation capabilities."""

from unittest.mock import patch

import pytest

from elle.capabilities.core.gui import (
    GUI_CAPABILITIES,
    GuiClickCapability,
    GuiClickInput,
    GuiExecuteTaskCapability,
    GuiExecuteTaskInput,
    GuiExecuteTaskOutput,
    GuiLearnCapability,
    GuiLearnInput,
    GuiLearnOutput,
    GuiNavigateCapability,
    GuiNavigateInput,
    GuiTypeCapability,
    GuiTypeInput,
)


class TestCapabilitySpecs:
    """Tests for capability specifications."""

    def test_gui_capabilities_list(self):
        """Test that GUI capabilities are registered."""
        assert len(GUI_CAPABILITIES) == 5

        names = [cap().spec.name for cap in GUI_CAPABILITIES]
        assert "gui.learn" in names
        assert "gui.click" in names
        assert "gui.type" in names
        assert "gui.navigate" in names
        assert "gui.execute_task" in names

    def test_gui_learn_spec(self):
        """Test gui.learn capability spec."""
        spec = GuiLearnCapability().spec

        assert spec.name == "gui.learn"
        assert spec.domain == "gui"
        assert spec.risk == "low"
        assert spec.idempotent is True

    def test_gui_click_spec(self):
        """Test gui.click capability spec."""
        spec = GuiClickCapability().spec

        assert spec.name == "gui.click"
        assert spec.domain == "gui"
        assert spec.risk == "low"
        assert len(spec.side_effects) > 0
        assert spec.side_effects[0].kind == "ui_interaction"

    def test_gui_type_spec(self):
        """Test gui.type capability spec."""
        spec = GuiTypeCapability().spec

        assert spec.name == "gui.type"
        assert spec.domain == "gui"
        assert spec.side_effects[0].reversible is True

    def test_gui_navigate_spec(self):
        """Test gui.navigate capability spec."""
        spec = GuiNavigateCapability().spec

        assert spec.name == "gui.navigate"
        assert spec.risk == "none"
        assert spec.idempotent is True

    def test_gui_execute_task_spec(self):
        """Test gui.execute_task capability spec."""
        spec = GuiExecuteTaskCapability().spec

        assert spec.name == "gui.execute_task"
        assert spec.risk == "medium"
        assert len(spec.side_effects) == 2


class TestGuiLearnInput:
    """Tests for GuiLearnInput model."""

    def test_create_learn_input(self):
        """Test creating learn input."""
        input_data = GuiLearnInput(
            app_name="gnome-control-center",
            mode="passive",
            update_existing=True,
        )

        assert input_data.app_name == "gnome-control-center"
        assert input_data.mode == "passive"
        assert input_data.update_existing is True

    def test_learn_input_defaults(self):
        """Test learn input default values."""
        input_data = GuiLearnInput(app_name="test-app")

        assert input_data.mode == "passive"
        assert input_data.update_existing is True


class TestGuiClickInput:
    """Tests for GuiClickInput model."""

    def test_create_click_input(self):
        """Test creating click input."""
        input_data = GuiClickInput(
            app_name="settings",
            element_name="Bluetooth",
            element_role="toggle button",
            wait_ms=200,
        )

        assert input_data.app_name == "settings"
        assert input_data.element_name == "Bluetooth"
        assert input_data.element_role == "toggle button"
        assert input_data.wait_ms == 200

    def test_click_input_defaults(self):
        """Test click input default values."""
        input_data = GuiClickInput(
            app_name="test",
            element_name="OK",
        )

        assert input_data.element_role is None
        assert input_data.wait_ms == 100


class TestGuiTypeInput:
    """Tests for GuiTypeInput model."""

    def test_create_type_input(self):
        """Test creating type input."""
        input_data = GuiTypeInput(
            app_name="text-editor",
            element_name="Search",
            text="hello world",
            clear_first=True,
        )

        assert input_data.app_name == "text-editor"
        assert input_data.text == "hello world"
        assert input_data.clear_first is True


class TestGuiExecuteTaskInput:
    """Tests for GuiExecuteTaskInput model."""

    def test_create_execute_task_input(self):
        """Test creating execute task input."""
        input_data = GuiExecuteTaskInput(
            request="disable bluetooth",
            app_name="gnome-control-center",
            dry_run=False,
        )

        assert input_data.request == "disable bluetooth"
        assert input_data.app_name == "gnome-control-center"
        assert input_data.dry_run is False


class TestGuiLearnOutput:
    """Tests for GuiLearnOutput model."""

    def test_create_learn_output(self):
        """Test creating learn output."""
        output = GuiLearnOutput(
            recipe_id="recipe-123",
            app_name="gnome-settings",
            element_count=50,
            navigation_target_count=10,
            version=1,
            created=True,
        )

        assert output.recipe_id == "recipe-123"
        assert output.element_count == 50
        assert output.created is True


class TestGuiExecuteTaskOutput:
    """Tests for GuiExecuteTaskOutput model."""

    def test_create_execute_task_output(self):
        """Test creating execute task output."""
        output = GuiExecuteTaskOutput(
            success=True,
            outcome="success",
            actions_completed=3,
            actions_total=3,
            adaptations_made=0,
            user_explanation="Completed: Bluetooth is disabled",
        )

        assert output.success is True
        assert output.outcome == "success"
        assert output.actions_completed == output.actions_total


class TestGuiLearnDryRun:
    """Tests for gui.learn dry run."""

    def test_dry_run_atspi_unavailable(self):
        """Test dry run when AT-SPI is not available."""
        with patch("elle.atspi.is_atspi_available", return_value=False):
            input_data = GuiLearnInput(app_name="test-app")
            result = GuiLearnCapability().dry_run(input_data)

            assert result.is_valid is False
            assert "not available" in result.preview_text.lower()


class TestGuiClickDryRun:
    """Tests for gui.click dry run."""

    def test_click_dry_run(self):
        """Test click dry run preview."""
        input_data = GuiClickInput(
            app_name="settings",
            element_name="Bluetooth",
        )

        result = GuiClickCapability().dry_run(input_data)

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert "Bluetooth" in str(result.would_execute)


class TestGuiNavigateDryRun:
    """Tests for gui.navigate dry run."""

    def test_navigate_dry_run(self):
        """Test navigate dry run preview."""
        input_data = GuiNavigateInput(
            app_name="settings",
            element_name="Bluetooth",
        )

        result = GuiNavigateCapability().dry_run(input_data)

        assert result.is_valid is True
        assert result.requires_confirmation is False  # Navigation is low risk


class TestCapabilityRollback:
    """Tests for capability rollback behavior."""

    def test_learn_rollback_not_needed(self):
        """Test that learn doesn't need rollback."""
        from elle.capabilities.models import CapabilityResult

        input_data = GuiLearnInput(app_name="test")
        result = CapabilityResult(success=True)

        rollback_result = GuiLearnCapability().rollback(input_data, result)

        assert rollback_result.success is True
        assert len(rollback_result.rolled_back) == 0

    def test_click_rollback_not_possible(self):
        """Test that click cannot be rolled back."""
        from elle.capabilities.models import CapabilityResult

        input_data = GuiClickInput(app_name="test", element_name="OK")
        result = CapabilityResult(success=True)

        rollback_result = GuiClickCapability().rollback(input_data, result)

        assert rollback_result.success is False
        assert "cannot be rolled back" in rollback_result.error.lower()

    def test_navigate_rollback_not_needed(self):
        """Test that navigate doesn't need rollback."""
        from elle.capabilities.models import CapabilityResult

        input_data = GuiNavigateInput(app_name="test", element_name="OK")
        result = CapabilityResult(success=True)

        rollback_result = GuiNavigateCapability().rollback(input_data, result)

        assert rollback_result.success is True
