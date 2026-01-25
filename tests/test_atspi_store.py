"""Tests for AT-SPI recipe store operations."""

import pytest
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime

from elle.atspi.models import UIElement, UIRecipe, UIAction
from elle.atspi.schema import get_connection, ensure_schema
from elle.atspi import store


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    ensure_schema(conn)

    yield conn, db_path

    conn.close()
    db_path.unlink(missing_ok=True)


class TestRecipeCRUD:
    """Tests for recipe CRUD operations."""

    def test_create_recipe(self, temp_db):
        """Test creating a recipe."""
        conn, _ = temp_db

        elements = [
            UIElement(role="button", name="OK", path=(0,)),
            UIElement(role="button", name="Cancel", path=(1,)),
        ]

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main Window",
            elements=elements,
            navigation_paths={"ok": (0,), "cancel": (1,)},
            conn=conn,
        )

        assert recipe.recipe_id is not None
        assert recipe.app_name == "test-app"
        assert len(recipe.elements) == 2
        assert recipe.version == 1
        assert recipe.confidence == 1.0

    def test_get_recipe(self, temp_db):
        """Test getting a recipe by ID."""
        conn, _ = temp_db

        created = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        fetched = store.get_recipe(created.recipe_id, conn=conn)

        assert fetched is not None
        assert fetched.recipe_id == created.recipe_id
        assert fetched.app_name == "test-app"

    def test_get_recipe_by_app(self, temp_db):
        """Test getting a recipe by app name."""
        conn, _ = temp_db

        store.create_recipe(
            app_name="gnome-settings",
            root_window="Settings",
            conn=conn,
        )

        recipe = store.get_recipe_by_app("gnome-settings", conn=conn)

        assert recipe is not None
        assert recipe.app_name == "gnome-settings"

    def test_list_recipes(self, temp_db):
        """Test listing recipes."""
        conn, _ = temp_db

        store.create_recipe(app_name="app1", root_window="Win1", conn=conn)
        store.create_recipe(app_name="app2", root_window="Win2", conn=conn)
        store.create_recipe(app_name="app3", root_window="Win3", conn=conn)

        recipes = store.list_recipes(limit=10, conn=conn)

        assert len(recipes) == 3

    def test_update_recipe(self, temp_db):
        """Test updating a recipe."""
        conn, _ = temp_db

        created = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        new_elements = [
            UIElement(role="button", name="New Button", path=(0, 0)),
        ]

        updated = store.update_recipe(
            created.recipe_id,
            elements=new_elements,
            confidence=0.9,
            version_reason="Updated elements",
            conn=conn,
        )

        assert updated is not None
        assert len(updated.elements) == 1
        assert updated.confidence == 0.9
        assert updated.version == 2  # Incremented

    def test_delete_recipe(self, temp_db):
        """Test deleting a recipe."""
        conn, _ = temp_db

        created = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        deleted = store.delete_recipe(created.recipe_id, conn=conn)

        assert deleted is True

        fetched = store.get_recipe(created.recipe_id, conn=conn)
        assert fetched is None


class TestElementSearch:
    """Tests for element search functionality."""

    def test_search_elements(self, temp_db):
        """Test searching elements by name."""
        conn, _ = temp_db

        elements = [
            UIElement(role="button", name="Bluetooth Settings", path=(0,)),
            UIElement(role="toggle", name="Bluetooth Toggle", path=(1,)),
            UIElement(role="button", name="WiFi", path=(2,)),
        ]

        store.create_recipe(
            app_name="settings",
            root_window="Settings",
            elements=elements,
            conn=conn,
        )

        results = store.search_elements("Bluetooth", conn=conn)

        assert len(results) >= 1


class TestExecutionCRUD:
    """Tests for execution record operations."""

    def test_create_execution(self, temp_db):
        """Test creating an execution record."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        actions = [
            UIAction(action_type="click", target_name="OK"),
        ]

        execution = store.create_execution(
            recipe_id=recipe.recipe_id,
            task_request="click ok button",
            actions=actions,
            conn=conn,
        )

        assert execution.execution_id is not None
        assert execution.task_request == "click ok button"
        assert len(execution.actions) == 1

    def test_finalize_execution(self, temp_db):
        """Test finalizing an execution."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        execution = store.create_execution(
            recipe_id=recipe.recipe_id,
            task_request="test",
            conn=conn,
        )

        finalized = store.finalize_execution(
            execution.execution_id,
            outcome="success",
            adaptation_notes="No adaptations needed",
            conn=conn,
        )

        assert finalized is not None
        assert finalized.outcome == "success"
        assert finalized.completed_at is not None

    def test_list_executions(self, temp_db):
        """Test listing executions."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        store.create_execution(
            recipe_id=recipe.recipe_id,
            task_request="task1",
            conn=conn,
        )
        store.create_execution(
            recipe_id=recipe.recipe_id,
            task_request="task2",
            conn=conn,
        )

        executions = store.list_executions(recipe_id=recipe.recipe_id, conn=conn)

        assert len(executions) == 2


class TestNavigationTargets:
    """Tests for navigation target operations."""

    def test_add_navigation_target(self, temp_db):
        """Test adding a navigation target."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        store.add_navigation_target(
            recipe_id=recipe.recipe_id,
            target_name="bluetooth_toggle",
            element_path=(0, 2, 1, 3),
            element_role="toggle button",
            element_name="Bluetooth",
            conn=conn,
        )

        path = store.get_navigation_target(
            recipe.recipe_id,
            "bluetooth_toggle",
            conn=conn,
        )

        assert path == (0, 2, 1, 3)

    def test_increment_target_usage(self, temp_db):
        """Test incrementing target usage count."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        store.add_navigation_target(
            recipe_id=recipe.recipe_id,
            target_name="test_target",
            element_path=(0,),
            conn=conn,
        )

        store.increment_target_usage(
            recipe.recipe_id,
            "test_target",
            conn=conn,
        )

        # Verify usage was incremented (would need to query directly)
        # This is a smoke test to ensure no errors


class TestRecipeVersions:
    """Tests for recipe versioning."""

    def test_version_created_on_update(self, temp_db):
        """Test that version snapshot is created on element update."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            elements=[UIElement(role="button", name="Old", path=(0,))],
            conn=conn,
        )

        store.update_recipe(
            recipe.recipe_id,
            elements=[UIElement(role="button", name="New", path=(1,))],
            version_reason="Updated button",
            conn=conn,
        )

        versions = store.get_versions(recipe.recipe_id, conn=conn)

        assert len(versions) >= 1

    def test_get_specific_version(self, temp_db):
        """Test getting a specific version."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            elements=[UIElement(role="button", name="V1", path=(0,))],
            conn=conn,
        )

        store.update_recipe(
            recipe.recipe_id,
            elements=[UIElement(role="button", name="V2", path=(1,))],
            conn=conn,
        )

        version = store.get_version(recipe.recipe_id, version=1, conn=conn)

        assert version is not None
        assert version.version == 1


class TestRecipeStats:
    """Tests for recipe statistics."""

    def test_get_recipe_stats(self, temp_db):
        """Test getting recipe statistics."""
        conn, _ = temp_db

        recipe = store.create_recipe(
            app_name="test-app",
            root_window="Main",
            conn=conn,
        )

        store.create_execution(
            recipe_id=recipe.recipe_id,
            task_request="test1",
            conn=conn,
        )

        stats = store.get_recipe_stats(recipe.recipe_id, conn=conn)

        assert stats["recipe_id"] == recipe.recipe_id
        assert stats["executions"]["total"] >= 1
