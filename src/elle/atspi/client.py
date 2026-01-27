"""AT-SPI client for accessibility tree operations.

Provides low-level access to the AT-SPI2 accessibility bus
for traversing UI trees, finding elements, and executing actions.

Requires the accessibility bus to be running:
    gsettings set org.gnome.desktop.interface toolkit-accessibility true
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from elle.atspi.models import AccessibleApp, UIElement

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# AT-SPI Availability Check
# =============================================================================


_ATSPI_AVAILABLE: bool | None = None
_ATSPI_MODULE: Any = None


def is_atspi_available() -> bool:
    """Check if AT-SPI is available on this system.

    Returns:
        True if AT-SPI can be used, False otherwise.
    """
    global _ATSPI_AVAILABLE, _ATSPI_MODULE

    if _ATSPI_AVAILABLE is not None:
        return _ATSPI_AVAILABLE

    try:
        # Try pyatspi2 first
        import pyatspi

        _ATSPI_MODULE = pyatspi
        _ATSPI_AVAILABLE = True
        logger.debug("AT-SPI available via pyatspi2")
        return True
    except ImportError:
        pass

    try:
        # Fall back to gi.repository.Atspi
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        _ATSPI_MODULE = Atspi
        _ATSPI_AVAILABLE = True
        logger.debug("AT-SPI available via gi.repository.Atspi")
        return True
    except (ImportError, ValueError) as e:
        logger.warning(f"AT-SPI not available: {e}")
        _ATSPI_AVAILABLE = False
        return False


def get_atspi_module() -> Any:
    """Get the AT-SPI module (pyatspi or Atspi).

    Returns:
        The AT-SPI module.

    Raises:
        RuntimeError: If AT-SPI is not available.
    """
    if not is_atspi_available():
        raise RuntimeError(
            "AT-SPI is not available. Install pyatspi2 or python3-gi and "
            "ensure accessibility is enabled: "
            "gsettings set org.gnome.desktop.interface toolkit-accessibility true"
        )
    return _ATSPI_MODULE


# =============================================================================
# Role Mapping
# =============================================================================


def _get_role_name(role: Any) -> str:
    """Get human-readable role name from AT-SPI role enum.

    Args:
        role: AT-SPI role enum value.

    Returns:
        Human-readable role name.
    """
    try:
        # pyatspi returns role as Atspi.Role enum
        if hasattr(role, "value_name"):
            name = str(role.value_name).replace("ATSPI_ROLE_", "").lower()
            return name.replace("_", " ")
        # Fallback to string conversion
        return str(role).lower().replace("role.", "").replace("_", " ")
    except Exception:
        return "unknown"


def _get_state_names(state_set: Any) -> tuple[str, ...]:
    """Get state names from AT-SPI state set.

    Args:
        state_set: AT-SPI state set.

    Returns:
        Tuple of state names.
    """
    states = []
    try:
        atspi = get_atspi_module()

        # Common states to check
        state_map = {
            "enabled": getattr(atspi, "STATE_ENABLED", None) or getattr(atspi.StateType, "ENABLED", None),
            "visible": getattr(atspi, "STATE_VISIBLE", None) or getattr(atspi.StateType, "VISIBLE", None),
            "showing": getattr(atspi, "STATE_SHOWING", None) or getattr(atspi.StateType, "SHOWING", None),
            "focusable": getattr(atspi, "STATE_FOCUSABLE", None) or getattr(atspi.StateType, "FOCUSABLE", None),
            "focused": getattr(atspi, "STATE_FOCUSED", None) or getattr(atspi.StateType, "FOCUSED", None),
            "checked": getattr(atspi, "STATE_CHECKED", None) or getattr(atspi.StateType, "CHECKED", None),
            "pressed": getattr(atspi, "STATE_PRESSED", None) or getattr(atspi.StateType, "PRESSED", None),
            "selected": getattr(atspi, "STATE_SELECTED", None) or getattr(atspi.StateType, "SELECTED", None),
            "sensitive": getattr(atspi, "STATE_SENSITIVE", None) or getattr(atspi.StateType, "SENSITIVE", None),
            "expanded": getattr(atspi, "STATE_EXPANDED", None) or getattr(atspi.StateType, "EXPANDED", None),
            "collapsed": getattr(atspi, "STATE_COLLAPSED", None) or getattr(atspi.StateType, "COLLAPSED", None),
            "editable": getattr(atspi, "STATE_EDITABLE", None) or getattr(atspi.StateType, "EDITABLE", None),
        }

        for name, state in state_map.items():
            if state is not None:
                try:
                    if hasattr(state_set, "contains"):
                        if state_set.contains(state):
                            states.append(name)
                    elif hasattr(state_set, "__contains__"):
                        if state in state_set:
                            states.append(name)
                except Exception:
                    pass

    except Exception as e:
        logger.debug(f"Error getting states: {e}")

    return tuple(states)


def _get_action_names(accessible: Any) -> tuple[str, ...]:
    """Get available action names from AT-SPI accessible.

    Args:
        accessible: AT-SPI accessible object.

    Returns:
        Tuple of action names.
    """
    actions = []
    try:
        action_iface = accessible.get_action_iface() if hasattr(accessible, "get_action_iface") else None
        if action_iface is None and hasattr(accessible, "queryAction"):
            action_iface = accessible.queryAction()

        if action_iface:
            n_actions = action_iface.get_n_actions() if hasattr(action_iface, "get_n_actions") else 0
            if n_actions == 0 and hasattr(action_iface, "nActions"):
                n_actions = action_iface.nActions

            for i in range(n_actions):
                name = action_iface.get_action_name(i) if hasattr(action_iface, "get_action_name") else None
                if name is None and hasattr(action_iface, "getName"):
                    name = action_iface.getName(i)
                if name:
                    actions.append(name.lower())

    except Exception as e:
        logger.debug(f"Error getting actions: {e}")

    return tuple(actions)


# =============================================================================
# AT-SPI Client
# =============================================================================


class ATSPIClient:
    """Low-level AT-SPI accessibility tree operations.

    Provides methods for:
    - Listing accessible applications
    - Finding applications by name
    - Traversing UI trees
    - Finding elements by various criteria
    - Executing actions on elements
    - Querying element states

    Usage:
        client = ATSPIClient()
        if client.is_available():
            apps = client.get_applications()
            app = client.get_application("gnome-control-center")
            if app:
                tree = client.get_tree(app)
    """

    def __init__(self) -> None:
        """Initialize the AT-SPI client."""
        self._atspi: Any = None
        self._desktop: Any = None

    def is_available(self) -> bool:
        """Check if AT-SPI is available.

        Returns:
            True if AT-SPI can be used.
        """
        return is_atspi_available()

    @property
    def atspi(self) -> Any:
        """Get the AT-SPI module.

        Returns:
            The AT-SPI module.

        Raises:
            RuntimeError: If AT-SPI is not available.
        """
        if self._atspi is None:
            self._atspi = get_atspi_module()
        return self._atspi

    @property
    def desktop(self) -> Any:
        """Get the desktop accessible (root of all applications).

        Returns:
            The desktop accessible object.
        """
        if self._desktop is None:
            atspi = self.atspi

            # pyatspi uses get_desktop
            if hasattr(atspi, "get_desktop"):
                self._desktop = atspi.get_desktop(0)
            # gi.repository.Atspi uses Atspi.get_desktop
            elif hasattr(atspi, "Desktop"):
                self._desktop = atspi.Desktop.get_desktop(0)
            else:
                raise RuntimeError("Cannot get desktop accessible")

        return self._desktop

    def get_applications(self) -> list[AccessibleApp]:
        """List all accessible applications.

        Returns:
            List of AccessibleApp objects.
        """
        apps = []
        desktop = self.desktop

        try:
            # Get child count
            n_children = desktop.get_child_count() if hasattr(desktop, "get_child_count") else 0
            if n_children == 0 and hasattr(desktop, "childCount"):
                n_children = desktop.childCount

            for i in range(n_children):
                try:
                    child = desktop.get_child_at_index(i) if hasattr(desktop, "get_child_at_index") else None
                    if child is None and hasattr(desktop, "getChildAtIndex"):
                        child = desktop.getChildAtIndex(i)

                    if child:
                        name = child.get_name() if hasattr(child, "get_name") else ""
                        if name is None and hasattr(child, "name"):
                            name = child.name or ""

                        # Get PID if available
                        pid = None
                        with contextlib.suppress(Exception):
                            pid = child.get_process_id() if hasattr(child, "get_process_id") else None

                        # Count windows (top-level children of the app)
                        window_count = 0
                        with contextlib.suppress(Exception):
                            window_count = child.get_child_count() if hasattr(child, "get_child_count") else 0

                        apps.append(
                            AccessibleApp(
                                name=name,
                                pid=pid,
                                window_count=window_count,
                            )
                        )

                except Exception as e:
                    logger.debug(f"Error getting app at index {i}: {e}")

        except Exception as e:
            logger.warning(f"Error listing applications: {e}")

        return apps

    def get_application(self, name: str) -> Any | None:
        """Find an application by name.

        Args:
            name: Application name to search for (case-insensitive partial match).

        Returns:
            AT-SPI accessible object for the application, or None.
        """
        desktop = self.desktop
        name_lower = name.lower()

        try:
            n_children = desktop.get_child_count() if hasattr(desktop, "get_child_count") else 0
            if n_children == 0 and hasattr(desktop, "childCount"):
                n_children = desktop.childCount

            for i in range(n_children):
                try:
                    child = desktop.get_child_at_index(i) if hasattr(desktop, "get_child_at_index") else None
                    if child is None and hasattr(desktop, "getChildAtIndex"):
                        child = desktop.getChildAtIndex(i)

                    if child:
                        app_name = child.get_name() if hasattr(child, "get_name") else ""
                        if app_name is None and hasattr(child, "name"):
                            app_name = child.name or ""

                        if app_name and name_lower in app_name.lower():
                            return child

                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"Error finding application: {e}")

        return None

    def get_tree(
        self,
        app_or_window: Any,
        max_depth: int = 10,
        include_invisible: bool = False,
    ) -> list[UIElement]:
        """Traverse and capture the UI tree.

        Args:
            app_or_window: AT-SPI accessible (app or window).
            max_depth: Maximum traversal depth.
            include_invisible: Whether to include non-visible elements.

        Returns:
            List of UIElement objects representing the tree.
        """
        elements = []

        def traverse(accessible: Any, path: tuple[int, ...], depth: int) -> None:
            if depth > max_depth:
                return

            try:
                # Get element info
                name = accessible.get_name() if hasattr(accessible, "get_name") else ""
                if name is None and hasattr(accessible, "name"):
                    name = accessible.name or ""

                role = accessible.get_role() if hasattr(accessible, "get_role") else None
                if role is None and hasattr(accessible, "role"):
                    role = accessible.role
                role_name = _get_role_name(role)

                description = accessible.get_description() if hasattr(accessible, "get_description") else None
                if description is None and hasattr(accessible, "description"):
                    description = accessible.description

                # Get states
                state_set = accessible.get_state_set() if hasattr(accessible, "get_state_set") else None
                if state_set is None and hasattr(accessible, "getState"):
                    state_set = accessible.getState()
                states = _get_state_names(state_set) if state_set else ()

                # Skip invisible elements unless requested
                if not include_invisible and "visible" not in states and "showing" not in states:
                    if depth > 0:  # Always include root
                        return

                # Get actions
                actions = _get_action_names(accessible)

                # Get parent info
                parent = accessible.get_parent() if hasattr(accessible, "get_parent") else None
                if parent is None and hasattr(accessible, "parent"):
                    parent = accessible.parent

                parent_role = None
                parent_name = None
                if parent:
                    try:
                        p_role = parent.get_role() if hasattr(parent, "get_role") else None
                        parent_role = _get_role_name(p_role) if p_role else None
                        parent_name = parent.get_name() if hasattr(parent, "get_name") else None
                    except Exception:
                        pass

                # Get sibling names for context
                sibling_context = []
                if parent:
                    try:
                        n_siblings = parent.get_child_count() if hasattr(parent, "get_child_count") else 0
                        for si in range(min(n_siblings, 5)):  # Limit siblings
                            sibling = parent.get_child_at_index(si) if hasattr(parent, "get_child_at_index") else None
                            if sibling and sibling != accessible:
                                sname = sibling.get_name() if hasattr(sibling, "get_name") else None
                                if sname:
                                    sibling_context.append(sname)
                    except Exception:
                        pass

                # Get bounds
                bounds = None
                try:
                    component = accessible.get_component_iface() if hasattr(accessible, "get_component_iface") else None
                    if component is None and hasattr(accessible, "queryComponent"):
                        component = accessible.queryComponent()
                    if component:
                        rect = component.get_extents(0) if hasattr(component, "get_extents") else None
                        if rect:
                            bounds = (rect.x, rect.y, rect.width, rect.height)
                except Exception:
                    pass

                # Create element
                element = UIElement(
                    role=role_name,
                    name=name or "",
                    description=description,
                    states=states,
                    actions=actions,
                    path=path,
                    parent_role=parent_role,
                    parent_name=parent_name,
                    sibling_context=tuple(sibling_context[:3]),
                    bounds=bounds,
                )
                elements.append(element)

                # Traverse children
                n_children = accessible.get_child_count() if hasattr(accessible, "get_child_count") else 0
                if n_children == 0 and hasattr(accessible, "childCount"):
                    n_children = accessible.childCount

                for i in range(n_children):
                    try:
                        child = accessible.get_child_at_index(i) if hasattr(accessible, "get_child_at_index") else None
                        if child is None and hasattr(accessible, "getChildAtIndex"):
                            child = accessible.getChildAtIndex(i)
                        if child:
                            traverse(child, path + (i,), depth + 1)
                    except Exception:
                        continue

            except Exception as e:
                logger.debug(f"Error traversing at path {path}: {e}")

        traverse(app_or_window, (), 0)
        return elements

    def find_element(
        self,
        root: Any,
        name: str | None = None,
        role: str | None = None,
        path: tuple[int, ...] | None = None,
    ) -> Any | None:
        """Find an element in the accessibility tree.

        Args:
            root: Root accessible to search from.
            name: Element name to match (partial, case-insensitive).
            role: Element role to match.
            path: Direct index path to follow.

        Returns:
            AT-SPI accessible if found, None otherwise.
        """
        # If path is provided, follow it directly
        if path is not None:
            return self._follow_path(root, path)

        # Otherwise search by name/role
        def search(accessible: Any, depth: int = 0) -> Any | None:
            if depth > 15:  # Max depth limit
                return None

            try:
                # Check current element
                elem_name = accessible.get_name() if hasattr(accessible, "get_name") else ""
                if elem_name is None and hasattr(accessible, "name"):
                    elem_name = accessible.name or ""

                elem_role = accessible.get_role() if hasattr(accessible, "get_role") else None
                if elem_role is None and hasattr(accessible, "role"):
                    elem_role = accessible.role
                elem_role_name = _get_role_name(elem_role)

                # Check match
                name_match = name is None or (elem_name and name.lower() in elem_name.lower())
                role_match = role is None or (elem_role_name and role.lower() in elem_role_name.lower())

                if name_match and role_match and (name or role):
                    return accessible

                # Search children
                n_children = accessible.get_child_count() if hasattr(accessible, "get_child_count") else 0
                if n_children == 0 and hasattr(accessible, "childCount"):
                    n_children = accessible.childCount

                for i in range(n_children):
                    try:
                        child = accessible.get_child_at_index(i) if hasattr(accessible, "get_child_at_index") else None
                        if child is None and hasattr(accessible, "getChildAtIndex"):
                            child = accessible.getChildAtIndex(i)
                        if child:
                            result = search(child, depth + 1)
                            if result:
                                return result
                    except Exception:
                        continue

            except Exception as e:
                logger.debug(f"Error searching: {e}")

            return None

        return search(root)

    def find_elements(
        self,
        root: Any,
        name: str | None = None,
        role: str | None = None,
        predicate: Callable[[Any], bool] | None = None,
        max_results: int = 10,
    ) -> list[Any]:
        """Find multiple elements matching criteria.

        Args:
            root: Root accessible to search from.
            name: Element name to match.
            role: Element role to match.
            predicate: Custom filter function.
            max_results: Maximum elements to return.

        Returns:
            List of matching AT-SPI accessibles.
        """
        results: list[Any] = []

        def search(accessible: Any, depth: int = 0) -> None:
            if len(results) >= max_results or depth > 15:
                return

            try:
                # Check current element
                elem_name = accessible.get_name() if hasattr(accessible, "get_name") else ""
                if elem_name is None and hasattr(accessible, "name"):
                    elem_name = accessible.name or ""

                elem_role = accessible.get_role() if hasattr(accessible, "get_role") else None
                if elem_role is None and hasattr(accessible, "role"):
                    elem_role = accessible.role
                elem_role_name = _get_role_name(elem_role)

                # Check matches
                name_match = name is None or (elem_name and name.lower() in elem_name.lower())
                role_match = role is None or (elem_role_name and role.lower() in elem_role_name.lower())
                pred_match = predicate is None or predicate(accessible)

                if name_match and role_match and pred_match and (name or role or predicate):
                    results.append(accessible)

                # Search children
                n_children = accessible.get_child_count() if hasattr(accessible, "get_child_count") else 0
                for i in range(n_children):
                    try:
                        child = accessible.get_child_at_index(i) if hasattr(accessible, "get_child_at_index") else None
                        if child:
                            search(child, depth + 1)
                    except Exception:
                        continue

            except Exception:
                pass

        search(root)
        return results

    def _follow_path(self, root: Any, path: tuple[int, ...]) -> Any | None:
        """Follow an index path to find an element.

        Args:
            root: Root accessible.
            path: Index path to follow.

        Returns:
            AT-SPI accessible if found, None otherwise.
        """
        current = root

        for index in path:
            try:
                child = current.get_child_at_index(index) if hasattr(current, "get_child_at_index") else None
                if child is None and hasattr(current, "getChildAtIndex"):
                    child = current.getChildAtIndex(index)

                if child is None:
                    return None

                current = child

            except Exception:
                return None

        return current

    def do_action(self, element: Any, action_name: str = "click") -> bool:
        """Execute an action on an element.

        Args:
            element: AT-SPI accessible element.
            action_name: Name of action to perform.

        Returns:
            True if action succeeded.
        """
        try:
            action_iface = element.get_action_iface() if hasattr(element, "get_action_iface") else None
            if action_iface is None and hasattr(element, "queryAction"):
                action_iface = element.queryAction()

            if not action_iface:
                logger.warning("Element has no action interface")
                return False

            # Find action index
            n_actions = action_iface.get_n_actions() if hasattr(action_iface, "get_n_actions") else 0
            if n_actions == 0 and hasattr(action_iface, "nActions"):
                n_actions = action_iface.nActions

            for i in range(n_actions):
                name = action_iface.get_action_name(i) if hasattr(action_iface, "get_action_name") else None
                if name is None and hasattr(action_iface, "getName"):
                    name = action_iface.getName(i)

                if name and name.lower() == action_name.lower():
                    result = action_iface.do_action(i) if hasattr(action_iface, "do_action") else False
                    if not result and hasattr(action_iface, "doAction"):
                        result = action_iface.doAction(i)
                    return bool(result)

            # If no exact match, try first action (usually "click" or "activate")
            if n_actions > 0:
                result = action_iface.do_action(0) if hasattr(action_iface, "do_action") else False
                if not result and hasattr(action_iface, "doAction"):
                    result = action_iface.doAction(0)
                return bool(result)

            return False

        except Exception as e:
            logger.error(f"Error executing action: {e}")
            return False

    def get_state(self, element: Any) -> tuple[str, ...]:
        """Get current states of an element.

        Args:
            element: AT-SPI accessible element.

        Returns:
            Tuple of state names.
        """
        try:
            state_set = element.get_state_set() if hasattr(element, "get_state_set") else None
            if state_set is None and hasattr(element, "getState"):
                state_set = element.getState()
            return _get_state_names(state_set) if state_set else ()
        except Exception as e:
            logger.debug(f"Error getting state: {e}")
            return ()

    def type_text(self, element: Any, text: str) -> bool:
        """Type text into an editable element.

        Args:
            element: AT-SPI accessible element (must be editable).
            text: Text to type.

        Returns:
            True if text was typed successfully.
        """
        try:
            # Get editable text interface
            edit_iface = element.get_editable_text_iface() if hasattr(element, "get_editable_text_iface") else None
            if edit_iface is None and hasattr(element, "queryEditableText"):
                edit_iface = element.queryEditableText()

            if edit_iface:
                # Clear existing text and insert new
                try:
                    # Get current text length
                    text_iface = element.get_text_iface() if hasattr(element, "get_text_iface") else None
                    if text_iface is None and hasattr(element, "queryText"):
                        text_iface = element.queryText()

                    if text_iface:
                        char_count = (
                            text_iface.get_character_count() if hasattr(text_iface, "get_character_count") else 0
                        )
                        if char_count > 0:
                            edit_iface.delete_text(0, char_count) if hasattr(edit_iface, "delete_text") else None

                    # Insert new text
                    result = edit_iface.insert_text(0, text, len(text)) if hasattr(edit_iface, "insert_text") else False
                    if not result and hasattr(edit_iface, "insertText"):
                        result = edit_iface.insertText(0, text, len(text))
                    return bool(result)

                except Exception as e:
                    logger.debug(f"Error inserting text: {e}")

            # Fallback: Try to use keyboard simulation (if available)
            # This would require additional libraries like python-xlib or pynput
            logger.warning("No editable text interface, keyboard simulation not implemented")
            return False

        except Exception as e:
            logger.error(f"Error typing text: {e}")
            return False

    def focus_element(self, element: Any) -> bool:
        """Focus an element.

        Args:
            element: AT-SPI accessible element.

        Returns:
            True if focus was set.
        """
        try:
            component = element.get_component_iface() if hasattr(element, "get_component_iface") else None
            if component is None and hasattr(element, "queryComponent"):
                component = element.queryComponent()

            if component and hasattr(component, "grab_focus"):
                return bool(component.grab_focus())
            elif component and hasattr(component, "grabFocus"):
                return bool(component.grabFocus())

            return False

        except Exception as e:
            logger.debug(f"Error focusing element: {e}")
            return False

    def get_element_path(self, element: Any) -> tuple[int, ...]:
        """Get the index path from root to an element.

        Args:
            element: AT-SPI accessible element.

        Returns:
            Tuple of indices from root to element.
        """
        path: list[int] = []
        current = element

        while current:
            try:
                parent = current.get_parent() if hasattr(current, "get_parent") else None
                if parent is None and hasattr(current, "parent"):
                    parent = current.parent

                if not parent:
                    break

                # Find index in parent
                index = current.get_index_in_parent() if hasattr(current, "get_index_in_parent") else -1
                if index == -1 and hasattr(current, "indexInParent"):
                    index = current.indexInParent

                if index >= 0:
                    path.insert(0, index)

                current = parent

            except Exception:
                break

        return tuple(path)

    def wait_for_element(
        self,
        root: Any,
        name: str | None = None,
        role: str | None = None,
        timeout_ms: int = 5000,
        poll_interval_ms: int = 100,
    ) -> Any | None:
        """Wait for an element to appear.

        Args:
            root: Root accessible to search from.
            name: Element name to match.
            role: Element role to match.
            timeout_ms: Maximum time to wait.
            poll_interval_ms: Time between checks.

        Returns:
            AT-SPI accessible if found, None if timeout.
        """
        start = time.time()
        timeout_sec = timeout_ms / 1000

        while (time.time() - start) < timeout_sec:
            element = self.find_element(root, name=name, role=role)
            if element:
                return element
            time.sleep(poll_interval_ms / 1000)

        return None


# =============================================================================
# Module-level client instance
# =============================================================================


_client: ATSPIClient | None = None


def get_client() -> ATSPIClient:
    """Get the shared AT-SPI client instance.

    Returns:
        The ATSPIClient singleton.
    """
    global _client
    if _client is None:
        _client = ATSPIClient()
    return _client
