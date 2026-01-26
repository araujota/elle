"""Recipe learning from applications.

Captures UI structure from applications to build recipes
that can be used for reliable automation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from elle.atspi.client import ATSPIClient, get_client
from elle.atspi.models import LearnMethod, UIElement, UIRecipe
from elle.atspi.store import create_recipe, get_recipe_by_app, update_recipe

logger = logging.getLogger(__name__)


# =============================================================================
# Navigation Path Extraction
# =============================================================================


# Roles that are typically interactive
ACTIONABLE_ROLES = frozenset(
    {
        "push button",
        "toggle button",
        "check box",
        "radio button",
        "menu item",
        "check menu item",
        "radio menu item",
        "combo box",
        "text",  # Editable text fields
        "entry",
        "password text",
        "spin button",
        "slider",
        "link",
        "switch",  # GTK4 switch
    }
)

# Roles that indicate container/section boundaries
CONTAINER_ROLES = frozenset(
    {
        "panel",
        "frame",
        "scroll pane",
        "viewport",
        "page tab",
        "split pane",
        "tool bar",
        "menu bar",
        "popup menu",
        "dialog",
        "alert",
        "list",
        "tree",
        "table",
    }
)


def _is_actionable(element: UIElement) -> bool:
    """Check if element is actionable.

    Args:
        element: UI element to check.

    Returns:
        True if element has actions or is interactive role.
    """
    # Has explicit actions
    if element.actions:
        return True

    # Is interactive role
    if element.role in ACTIONABLE_ROLES:
        return True

    # Is enabled and has a meaningful name
    return bool("enabled" in element.states and element.name and len(element.name) > 1)


def _normalize_target_name(name: str) -> str:
    """Normalize element name to snake_case target name.

    Args:
        name: Element display name.

    Returns:
        Normalized target name.
    """
    # Remove special characters
    name = re.sub(r"[^\w\s-]", "", name)
    # Replace spaces/hyphens with underscores
    name = re.sub(r"[\s-]+", "_", name)
    # Convert to lowercase
    name = name.lower()
    # Remove leading/trailing underscores
    name = name.strip("_")
    # Limit length
    return name[:50] if name else "unnamed"


def _extract_navigation_paths(
    elements: list[UIElement],
) -> dict[str, tuple[int, ...]]:
    """Extract meaningful navigation paths from elements.

    Args:
        elements: List of UI elements.

    Returns:
        Dict mapping target names to paths.
    """
    paths: dict[str, tuple[int, ...]] = {}
    seen_names: set[str] = set()

    for element in elements:
        if not _is_actionable(element):
            continue

        if not element.name:
            continue

        # Create target name
        base_name = _normalize_target_name(element.name)
        if not base_name:
            continue

        # Handle duplicates by adding role suffix
        target_name = base_name
        if target_name in seen_names:
            target_name = f"{base_name}_{element.role.replace(' ', '_')}"

        if target_name in seen_names:
            # Add path suffix for truly duplicate names
            path_str = "_".join(str(i) for i in element.path[-2:])
            target_name = f"{base_name}_{path_str}"

        if target_name not in seen_names:
            paths[target_name] = element.path
            seen_names.add(target_name)

    return paths


# =============================================================================
# Recipe Learner
# =============================================================================


class RecipeLearner:
    """Learns UI structure from applications.

    Supports two learning modes:
    - Passive: Automatically traverse the entire visible tree
    - Interactive: User navigates, we record their paths

    Usage:
        learner = RecipeLearner()
        recipe = await learner.learn_application("gnome-control-center")
    """

    def __init__(
        self,
        client: ATSPIClient | None = None,
        max_depth: int = 10,
    ) -> None:
        """Initialize the learner.

        Args:
            client: AT-SPI client.
            max_depth: Maximum tree traversal depth.
        """
        self._client = client
        self._max_depth = max_depth

    @property
    def client(self) -> ATSPIClient:
        """Get the AT-SPI client."""
        if self._client is None:
            self._client = get_client()
        return self._client

    async def learn_application(
        self,
        app_name: str,
        mode: LearnMethod = "passive",
        update_existing: bool = True,
    ) -> UIRecipe | None:
        """Learn an application's UI structure.

        Args:
            app_name: Application name to learn.
            mode: Learning mode (passive or interactive).
            update_existing: Whether to update existing recipe.

        Returns:
            Created or updated UIRecipe, or None if app not found.
        """
        if not self.client.is_available():
            logger.error("AT-SPI is not available")
            return None

        # Find the application
        app = self.client.get_application(app_name)
        if not app:
            logger.warning(f"Application '{app_name}' not found")
            return None

        # Get the main window
        window = self._get_main_window(app)
        if not window:
            logger.warning(f"No window found for '{app_name}'")
            return None

        # Get window title/role for identification
        root_window = self._get_window_identifier(window)

        logger.info(f"Learning '{app_name}' (mode: {mode}, window: {root_window})")

        # Capture the UI tree
        elements = self.client.get_tree(
            window,
            max_depth=self._max_depth,
            include_invisible=False,
        )

        logger.info(f"Captured {len(elements)} elements")

        # Extract navigation paths
        navigation_paths = _extract_navigation_paths(elements)
        logger.info(f"Identified {len(navigation_paths)} navigation targets")

        # Check for existing recipe
        existing = get_recipe_by_app(app_name)

        if existing and update_existing:
            # Update existing recipe
            updated = update_recipe(
                existing.recipe_id,
                elements=elements,
                navigation_paths=navigation_paths,
                version_reason=f"Re-learned via {mode} mode",
            )
            logger.info(f"Updated recipe for '{app_name}' (v{updated.version if updated else '?'})")
            return updated

        # Create new recipe
        recipe = create_recipe(
            app_name=app_name,
            root_window=root_window,
            elements=elements,
            navigation_paths=navigation_paths,
            learn_method=mode,
        )

        logger.info(f"Created new recipe for '{app_name}'")
        return recipe

    def learn_application_sync(
        self,
        app_name: str,
        mode: LearnMethod = "passive",
        update_existing: bool = True,
    ) -> UIRecipe | None:
        """Synchronous version of learn_application.

        Args:
            app_name: Application name to learn.
            mode: Learning mode.
            update_existing: Whether to update existing recipe.

        Returns:
            Created or updated UIRecipe.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self.learn_application(app_name, mode, update_existing))

    async def rebuild_recipe(
        self,
        recipe_id: str,
        changed_elements: list[str] | None = None,
    ) -> UIRecipe | None:
        """Rebuild portions of a recipe after UI changes.

        Args:
            recipe_id: Recipe to rebuild.
            changed_elements: Specific elements that changed (or all if None).

        Returns:
            Updated UIRecipe.
        """
        from elle.atspi.store import get_recipe

        recipe = get_recipe(recipe_id)
        if not recipe:
            logger.warning(f"Recipe '{recipe_id}' not found")
            return None

        # Learn again
        return await self.learn_application(
            recipe.app_name,
            mode="rebuild",
            update_existing=True,
        )

    def _get_main_window(self, app: Any) -> Any | None:
        """Get the main window of an application.

        Args:
            app: AT-SPI application accessible.

        Returns:
            Main window accessible, or None.
        """
        try:
            n_children = app.get_child_count() if hasattr(app, "get_child_count") else 0
            if n_children == 0 and hasattr(app, "childCount"):
                n_children = app.childCount

            for i in range(n_children):
                child = app.get_child_at_index(i) if hasattr(app, "get_child_at_index") else None
                if child is None and hasattr(app, "getChildAtIndex"):
                    child = app.getChildAtIndex(i)

                if child:
                    # Check if it's a window/frame
                    role = child.get_role() if hasattr(child, "get_role") else None
                    role_name = str(role).lower() if role else ""

                    if "window" in role_name or "frame" in role_name:
                        return child

            # Return first child if no window found
            if n_children > 0:
                return app.get_child_at_index(0) if hasattr(app, "get_child_at_index") else None

        except Exception as e:
            logger.debug(f"Error getting main window: {e}")

        return None

    def _get_window_identifier(self, window: Any) -> str:
        """Get a stable identifier for a window.

        Args:
            window: AT-SPI window accessible.

        Returns:
            Window identifier string.
        """
        try:
            name = window.get_name() if hasattr(window, "get_name") else ""
            if name is None and hasattr(window, "name"):
                name = window.name or ""

            if name:
                return name

            role = window.get_role() if hasattr(window, "get_role") else None
            if role:
                return str(role).replace("ROLE_", "").lower()

        except Exception:
            pass

        return "main_window"


# =============================================================================
# Interactive Learning (Future)
# =============================================================================


class InteractiveLearner:
    """Interactive learning mode where user demonstrates actions.

    This is a placeholder for future interactive learning
    that records user navigation through an application.
    """

    def __init__(self, learner: RecipeLearner | None = None) -> None:
        """Initialize interactive learner.

        Args:
            learner: Base recipe learner.
        """
        self._learner = learner or RecipeLearner()
        self._recording = False
        self._recorded_elements: list[UIElement] = []
        self._recorded_actions: list[dict] = []

    def start_recording(self, app_name: str) -> bool:
        """Start recording user interactions.

        Args:
            app_name: Application to record.

        Returns:
            True if recording started.
        """
        # TODO: Implement AT-SPI event listener for focus changes
        self._recording = True
        self._recorded_elements = []
        self._recorded_actions = []
        logger.info(f"Started recording interactions for '{app_name}'")
        return True

    def stop_recording(self) -> list[dict]:
        """Stop recording and return captured interactions.

        Returns:
            List of recorded action dicts.
        """
        self._recording = False
        actions = self._recorded_actions.copy()
        logger.info(f"Stopped recording, captured {len(actions)} actions")
        return actions

    def is_recording(self) -> bool:
        """Check if currently recording.

        Returns:
            True if recording.
        """
        return self._recording


# =============================================================================
# Module-level instances
# =============================================================================


_learner: RecipeLearner | None = None


def get_learner() -> RecipeLearner:
    """Get the shared RecipeLearner instance."""
    global _learner
    if _learner is None:
        _learner = RecipeLearner()
    return _learner
