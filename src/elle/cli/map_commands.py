"""REPL commands for GUI mapping and recipe management.

Provides /map command handlers for:
- Mapping applications (GUI tree traces)
- Listing recipes
- Showing recipe details
- Rebuilding recipes
- Managing recipes

Renamed from /learn to /map to make room for package capability learning.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


# =============================================================================
# Command Handlers
# =============================================================================


async def handle_map_command(args: str) -> str:
    """Handle /map command.

    Usage:
        /map                      - Show help
        /map <appname>            - Map application interactively
        /map <appname> --passive  - Map via automatic traversal
        /map list                 - List mapped applications
        /map show <appname>       - Show recipe details
        /map rebuild <appname>    - Force rebuild recipe
        /map delete <appname>     - Delete recipe
        /map export <appname>     - Export recipe as JSON
        /map import <file>        - Import recipe from JSON

    Args:
        args: Command arguments.

    Returns:
        Response string.
    """
    args = args.strip()

    if not args:
        return _get_help()

    parts = args.split(None, 1)
    subcommand = parts[0].lower()
    subargs = parts[1] if len(parts) > 1 else ""

    async def _help_handler(_: str) -> str:
        return _get_help()

    handlers = {
        "list": _handle_list,
        "show": _handle_show,
        "rebuild": _handle_rebuild,
        "delete": _handle_delete,
        "export": _handle_export,
        "import": _handle_import,
        "help": _help_handler,
    }

    if subcommand in handlers:
        return await handlers[subcommand](subargs)

    # Default: map application
    return await _handle_map(args)


def _get_help() -> str:
    """Get help text for /map command."""
    return """
/map - GUI automation mapping commands

Usage:
  /map <appname>           Map an application (passive mode)
  /map <appname> --passive Map via automatic tree traversal
  /map <appname> --interactive  Map interactively (future)

  /map list                List mapped applications
  /map show <appname>      Show recipe details
  /map rebuild <appname>   Force rebuild recipe
  /map delete <appname>    Delete a recipe
  /map export <appname>    Export recipe as JSON
  /map import <file>       Import recipe from JSON

Examples:
  /map gnome-control-center    Map GNOME Settings
  /map show gnome-control-center   Show recipe details
  /map list                    List all mapped apps

Note: Requires AT-SPI accessibility to be enabled:
  gsettings set org.gnome.desktop.interface toolkit-accessibility true
""".strip()


async def _handle_map(args: str) -> str:
    """Map an application.

    Args:
        args: Application name and optional flags.

    Returns:
        Response string.
    """
    from elle.atspi import is_atspi_available, learn_application

    if not is_atspi_available():
        return (
            "AT-SPI is not available. Please install pyatspi2 or python3-gi "
            "and enable accessibility:\n\n"
            "  gsettings set org.gnome.desktop.interface toolkit-accessibility true"
        )

    # Parse args
    parts = args.split()
    app_name = parts[0] if parts else ""
    mode: Literal["interactive", "passive", "rebuild"] = "passive"

    if "--interactive" in parts:
        mode = "interactive"
    elif "--passive" in parts:
        mode = "passive"

    if not app_name or app_name.startswith("--"):
        return "Usage: /map <appname> [--passive|--interactive]"

    # Map the application
    try:
        recipe = await learn_application(app_name, mode=mode)

        if not recipe:
            return f"Could not find application '{app_name}'.\nMake sure the application is running and accessible."

        return (
            f"Mapped '{recipe.app_name}':\n"
            f"  Recipe ID: {recipe.recipe_id[:8]}...\n"
            f"  Elements: {len(recipe.elements)}\n"
            f"  Navigation targets: {len(recipe.navigation_paths)}\n"
            f"  Version: {recipe.version}\n"
            f"  Confidence: {recipe.confidence:.0%}"
        )

    except Exception as e:
        logger.error(f"Error mapping application: {e}")
        return f"Error mapping application: {e}"


async def _handle_list(args: str) -> str:
    """List mapped applications.

    Args:
        args: Unused.

    Returns:
        Response string.
    """
    from elle.atspi import list_recipes

    try:
        recipes = list_recipes(limit=50)

        if not recipes:
            return "No mapped applications. Use '/map <appname>' to map one."

        lines = ["Mapped applications:\n"]
        for recipe in recipes:
            confidence_str = f"{recipe.confidence:.0%}"
            success_rate = (
                f"{recipe.success_count}/{recipe.success_count + recipe.failure_count}"
                if recipe.success_count + recipe.failure_count > 0
                else "n/a"
            )
            lines.append(f"  {recipe.app_name:30} v{recipe.version} ({confidence_str} conf, {success_rate} success)")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error listing recipes: {e}")
        return f"Error listing recipes: {e}"


async def _handle_show(args: str) -> str:
    """Show recipe details.

    Args:
        args: Application name.

    Returns:
        Response string.
    """
    from elle.atspi import get_recipe_by_app

    app_name = args.strip()
    if not app_name:
        return "Usage: /map show <appname>"

    try:
        recipe = get_recipe_by_app(app_name)

        if not recipe:
            return f"No recipe found for '{app_name}'"

        # Count elements by role
        role_counts: dict[str, int] = {}
        for elem in recipe.elements:
            role_counts[elem.role] = role_counts.get(elem.role, 0) + 1

        top_roles = sorted(role_counts.items(), key=lambda x: -x[1])[:5]

        lines = [
            f"Recipe: {recipe.app_name}",
            f"  ID: {recipe.recipe_id}",
            f"  Version: {recipe.version}",
            f"  Map method: {recipe.learn_method}",
            f"  Window: {recipe.root_window}",
            f"  Desktop ID: {recipe.app_desktop_id or 'unknown'}",
            "",
            "Statistics:",
            f"  Elements: {len(recipe.elements)}",
            f"  Navigation targets: {len(recipe.navigation_paths)}",
            f"  Confidence: {recipe.confidence:.0%}",
            f"  Success count: {recipe.success_count}",
            f"  Failure count: {recipe.failure_count}",
            "",
            "Element types:",
        ]

        for role, count in top_roles:
            lines.append(f"  {role}: {count}")

        if recipe.navigation_paths:
            lines.append("")
            lines.append("Navigation targets:")
            for name in sorted(recipe.navigation_paths.keys())[:10]:
                lines.append(f"  {name}")
            if len(recipe.navigation_paths) > 10:
                lines.append(f"  ... and {len(recipe.navigation_paths) - 10} more")

        lines.append("")
        lines.append(f"Created: {recipe.created_at.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"Updated: {recipe.updated_at.strftime('%Y-%m-%d %H:%M')}")
        if recipe.last_success_at:
            lines.append(f"Last success: {recipe.last_success_at.strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error showing recipe: {e}")
        return f"Error showing recipe: {e}"


async def _handle_rebuild(args: str) -> str:
    """Force rebuild a recipe.

    Args:
        args: Application name.

    Returns:
        Response string.
    """
    from elle.atspi import get_recipe_by_app, is_atspi_available, learn_application

    if not is_atspi_available():
        return "AT-SPI is not available"

    app_name = args.strip()
    if not app_name:
        return "Usage: /map rebuild <appname>"

    # Check existing recipe
    existing = get_recipe_by_app(app_name)
    if not existing:
        return f"No existing recipe for '{app_name}'. Use '/map {app_name}' instead."

    try:
        recipe = await learn_application(app_name, mode="rebuild", update_existing=True)

        if not recipe:
            return f"Could not rebuild recipe for '{app_name}'"

        return (
            f"Rebuilt recipe for '{recipe.app_name}':\n"
            f"  Version: {existing.version} -> {recipe.version}\n"
            f"  Elements: {len(existing.elements)} -> {len(recipe.elements)}\n"
            f"  Navigation targets: {len(existing.navigation_paths)} -> {len(recipe.navigation_paths)}"
        )

    except Exception as e:
        logger.error(f"Error rebuilding recipe: {e}")
        return f"Error rebuilding recipe: {e}"


async def _handle_delete(args: str) -> str:
    """Delete a recipe.

    Args:
        args: Application name.

    Returns:
        Response string.
    """
    from elle.atspi import delete_recipe, get_recipe_by_app

    app_name = args.strip()
    if not app_name:
        return "Usage: /map delete <appname>"

    try:
        recipe = get_recipe_by_app(app_name)
        if not recipe:
            return f"No recipe found for '{app_name}'"

        deleted = delete_recipe(recipe.recipe_id)

        if deleted:
            return f"Deleted recipe for '{app_name}'"
        else:
            return f"Failed to delete recipe for '{app_name}'"

    except Exception as e:
        logger.error(f"Error deleting recipe: {e}")
        return f"Error deleting recipe: {e}"


async def _handle_export(args: str) -> str:
    """Export recipe as JSON.

    Args:
        args: Application name.

    Returns:
        Response string.
    """
    from elle.atspi import get_recipe_by_app

    app_name = args.strip()
    if not app_name:
        return "Usage: /map export <appname>"

    try:
        recipe = get_recipe_by_app(app_name)
        if not recipe:
            return f"No recipe found for '{app_name}'"

        # Export to file
        filename = f"{app_name.replace(' ', '_')}_recipe.json"
        filepath = Path.home() / ".local" / "share" / "elle" / "exports" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict
        data = {
            "recipe_id": recipe.recipe_id,
            "app_name": recipe.app_name,
            "app_desktop_id": recipe.app_desktop_id,
            "app_version": recipe.app_version,
            "root_window": recipe.root_window,
            "learn_method": recipe.learn_method,
            "version": recipe.version,
            "elements": [
                {
                    "role": e.role,
                    "name": e.name,
                    "description": e.description,
                    "states": list(e.states),
                    "actions": list(e.actions),
                    "path": list(e.path),
                }
                for e in recipe.elements
            ],
            "navigation_paths": {k: list(v) for k, v in recipe.navigation_paths.items()},
            "exported_at": datetime.utcnow().isoformat(),
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        return f"Exported recipe to:\n  {filepath}"

    except Exception as e:
        logger.error(f"Error exporting recipe: {e}")
        return f"Error exporting recipe: {e}"


async def _handle_import(args: str) -> str:
    """Import recipe from JSON.

    Args:
        args: File path.

    Returns:
        Response string.
    """
    from elle.atspi import create_recipe
    from elle.atspi.models import UIElement

    filepath = args.strip()
    if not filepath:
        return "Usage: /map import <filepath>"

    path = Path(filepath).expanduser()
    if not path.exists():
        return f"File not found: {filepath}"

    try:
        with open(path) as f:
            data = json.load(f)

        # Convert elements
        elements = [
            UIElement(
                role=e.get("role", ""),
                name=e.get("name", ""),
                description=e.get("description"),
                states=tuple(e.get("states", [])),
                actions=tuple(e.get("actions", [])),
                path=tuple(e.get("path", [])),
            )
            for e in data.get("elements", [])
        ]

        # Convert navigation paths
        nav_paths = {k: tuple(v) for k, v in data.get("navigation_paths", {}).items()}

        recipe = create_recipe(
            app_name=data.get("app_name", "imported"),
            root_window=data.get("root_window", "main_window"),
            elements=elements,
            navigation_paths=nav_paths,
            app_desktop_id=data.get("app_desktop_id"),
            app_version=data.get("app_version"),
            learn_method="passive",
        )

        return (
            f"Imported recipe for '{recipe.app_name}':\n"
            f"  Elements: {len(recipe.elements)}\n"
            f"  Navigation targets: {len(recipe.navigation_paths)}"
        )

    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    except Exception as e:
        logger.error(f"Error importing recipe: {e}")
        return f"Error importing recipe: {e}"


# =============================================================================
# Synchronous wrapper
# =============================================================================


def handle_map_command_sync(args: str) -> str:
    """Synchronous version of handle_map_command."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(handle_map_command(args))
