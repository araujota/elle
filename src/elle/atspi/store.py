"""CRUD operations for the UI Recipe database.

Provides database operations for recipes, elements, executions,
and versions. All operations are auditable and versioned.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from elle.atspi.models import (
    ExecutionOutcome,
    LearnMethod,
    RecipeExecution,
    RecipeVersion,
    UIAction,
    UIElement,
    UIRecipe,
)
from elle.atspi.schema import ensure_schema, get_connection


@contextmanager
def _ensure_connection(
    conn: sqlite3.Connection | None = None,
) -> Iterator[sqlite3.Connection]:
    """Context manager that ensures a valid connection with schema."""
    own_conn = conn is None
    if own_conn:
        actual_conn = get_connection()
        ensure_schema(actual_conn)
    else:
        # conn is guaranteed non-None here by the condition above
        actual_conn = conn  # type: ignore[assignment]
    try:
        yield actual_conn
    finally:
        if own_conn:
            actual_conn.close()


# =============================================================================
# Serialization Helpers
# =============================================================================


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format string."""
    return dt.isoformat()


def _parse_datetime(s: str | None) -> datetime | None:
    """Parse ISO format string to datetime."""
    if not s:
        return None
    return datetime.fromisoformat(s)


def _json_dumps(obj: Any) -> str:
    """Serialize object to JSON string."""
    return json.dumps(obj, default=str)


def _json_loads(s: str | None) -> Any:
    """Parse JSON string, returning empty list/dict on None."""
    if not s:
        return []
    return json.loads(s)


def _element_to_dict(element: UIElement) -> dict[str, Any]:
    """Convert UIElement to JSON-serializable dict."""
    return {
        "role": element.role,
        "name": element.name,
        "description": element.description,
        "states": list(element.states),
        "actions": list(element.actions),
        "path": list(element.path),
        "parent_role": element.parent_role,
        "parent_name": element.parent_name,
        "label_patterns": list(element.label_patterns),
        "sibling_context": list(element.sibling_context),
        "bounds": list(element.bounds) if element.bounds else None,
    }


def _dict_to_element(data: dict[str, Any]) -> UIElement:
    """Convert dict to UIElement."""
    return UIElement(
        role=data.get("role", ""),
        name=data.get("name", ""),
        description=data.get("description"),
        states=tuple(data.get("states", [])),
        actions=tuple(data.get("actions", [])),
        path=tuple(data.get("path", [])),
        parent_role=data.get("parent_role"),
        parent_name=data.get("parent_name"),
        label_patterns=tuple(data.get("label_patterns", [])),
        sibling_context=tuple(data.get("sibling_context", [])),
        bounds=tuple(data["bounds"]) if data.get("bounds") else None,
    )


def _action_to_dict(action: UIAction) -> dict[str, Any]:
    """Convert UIAction to JSON-serializable dict."""
    return {
        "action_type": action.action_type,
        "target_name": action.target_name,
        "target_role": action.target_role,
        "target_path": list(action.target_path) if action.target_path else None,
        "text_input": action.text_input,
        "wait_ms": action.wait_ms,
        "verify_state": action.verify_state,
        "fallback_patterns": list(action.fallback_patterns),
        "search_scope": action.search_scope,
    }


def _dict_to_action(data: dict[str, Any]) -> UIAction:
    """Convert dict to UIAction."""
    return UIAction(
        action_type=data.get("action_type", "click"),
        target_name=data.get("target_name", ""),
        target_role=data.get("target_role"),
        target_path=tuple(data["target_path"]) if data.get("target_path") else None,
        text_input=data.get("text_input"),
        wait_ms=data.get("wait_ms", 100),
        verify_state=data.get("verify_state"),
        fallback_patterns=tuple(data.get("fallback_patterns", [])),
        search_scope=data.get("search_scope", "window"),
    )


# =============================================================================
# Recipe CRUD
# =============================================================================


def create_recipe(
    app_name: str,
    root_window: str,
    elements: list[UIElement] | None = None,
    navigation_paths: dict[str, tuple[int, ...]] | None = None,
    app_desktop_id: str | None = None,
    app_version: str | None = None,
    learn_method: LearnMethod = "passive",
    conn: sqlite3.Connection | None = None,
) -> UIRecipe:
    """Create a new UI recipe.

    Args:
        app_name: Human-friendly application name.
        root_window: Window title/role for main window.
        elements: List of UI elements discovered.
        navigation_paths: Named paths to important elements.
        app_desktop_id: Desktop file ID.
        app_version: Application version.
        learn_method: How this recipe was learned.
        conn: SQLite connection.

    Returns:
        The created UIRecipe.
    """
    with _ensure_connection(conn) as c:
        recipe_id = str(uuid.uuid4())
        now = datetime.utcnow()

        elements = elements or []
        navigation_paths = navigation_paths or {}

        # Convert navigation paths to JSON-compatible format
        nav_paths_json = {k: list(v) for k, v in navigation_paths.items()}

        recipe = UIRecipe(
            recipe_id=recipe_id,
            app_name=app_name,
            app_desktop_id=app_desktop_id,
            app_version=app_version,
            created_at=now,
            updated_at=now,
            learn_method=learn_method,
            root_window=root_window,
            elements=tuple(elements),
            navigation_paths=navigation_paths,
            version=1,
            confidence=1.0,
        )

        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO recipes (
                recipe_id, app_name, app_desktop_id, app_version,
                root_window, created_at, updated_at, learn_method,
                version, confidence, elements_json, navigation_paths_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recipe.recipe_id,
                recipe.app_name,
                recipe.app_desktop_id,
                recipe.app_version,
                recipe.root_window,
                _serialize_datetime(recipe.created_at),
                _serialize_datetime(recipe.updated_at),
                recipe.learn_method,
                recipe.version,
                recipe.confidence,
                _json_dumps([_element_to_dict(e) for e in elements]),
                _json_dumps(nav_paths_json),
            ),
        )

        # Index elements for FTS
        _index_elements(cursor, recipe_id, elements)

        c.commit()
        return recipe


def get_recipe(
    recipe_id: str,
    conn: sqlite3.Connection | None = None,
) -> UIRecipe | None:
    """Get a recipe by ID.

    Args:
        recipe_id: The recipe UUID.
        conn: SQLite connection.

    Returns:
        UIRecipe if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute("SELECT * FROM recipes WHERE recipe_id = ?", (recipe_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_recipe(row)


def get_recipe_by_app(
    app_name: str,
    conn: sqlite3.Connection | None = None,
) -> UIRecipe | None:
    """Get a recipe by application name.

    Returns the highest-confidence recipe for the app.

    Args:
        app_name: Application name to search for.
        conn: SQLite connection.

    Returns:
        UIRecipe if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM recipes
            WHERE app_name = ?
            ORDER BY confidence DESC, updated_at DESC
            LIMIT 1
            """,
            (app_name,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_recipe(row)


def list_recipes(
    limit: int = 100,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> list[UIRecipe]:
    """List all recipes.

    Args:
        limit: Maximum number of results.
        offset: Offset for pagination.
        conn: SQLite connection.

    Returns:
        List of UIRecipes.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM recipes
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )

        return [_row_to_recipe(row) for row in cursor.fetchall()]


def update_recipe(
    recipe_id: str,
    *,
    elements: list[UIElement] | None = None,
    navigation_paths: dict[str, tuple[int, ...]] | None = None,
    confidence: float | None = None,
    last_success_at: datetime | None = None,
    failure_count: int | None = None,
    success_count: int | None = None,
    version_reason: str = "",
    conn: sqlite3.Connection | None = None,
) -> UIRecipe | None:
    """Update an existing recipe.

    Creates a version snapshot before updating if elements change.

    Args:
        recipe_id: ID of recipe to update.
        elements: New elements list.
        navigation_paths: New navigation paths.
        confidence: New confidence score.
        last_success_at: When last successful.
        failure_count: New failure count.
        success_count: New success count.
        version_reason: Reason for this update.
        conn: SQLite connection.

    Returns:
        Updated UIRecipe, or None if not found.
    """
    with _ensure_connection(conn) as c:
        # Get current recipe
        current = get_recipe(recipe_id, conn=c)
        if not current:
            return None

        cursor = c.cursor()

        # Build update statement
        updates = ["updated_at = ?"]
        values: list[Any] = [_serialize_datetime(datetime.utcnow())]

        if elements is not None:
            # Create version snapshot before updating elements
            _create_version_snapshot(cursor, current, version_reason)

            updates.append("elements_json = ?")
            values.append(_json_dumps([_element_to_dict(e) for e in elements]))
            updates.append("version = version + 1")

            # Re-index elements
            cursor.execute(
                "DELETE FROM recipe_elements WHERE recipe_id = ?",
                (recipe_id,),
            )
            _index_elements(cursor, recipe_id, elements)

        if navigation_paths is not None:
            nav_paths_json = {k: list(v) for k, v in navigation_paths.items()}
            updates.append("navigation_paths_json = ?")
            values.append(_json_dumps(nav_paths_json))

        if confidence is not None:
            updates.append("confidence = ?")
            values.append(confidence)

        if last_success_at is not None:
            updates.append("last_success_at = ?")
            values.append(_serialize_datetime(last_success_at))

        if failure_count is not None:
            updates.append("failure_count = ?")
            values.append(failure_count)

        if success_count is not None:
            updates.append("success_count = ?")
            values.append(success_count)

        values.append(recipe_id)

        cursor.execute(
            f"UPDATE recipes SET {', '.join(updates)} WHERE recipe_id = ?",
            values,
        )
        c.commit()

        return get_recipe(recipe_id, conn=c)


def delete_recipe(
    recipe_id: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Delete a recipe and all related data.

    Args:
        recipe_id: The recipe UUID.
        conn: SQLite connection.

    Returns:
        True if deleted, False if not found.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()

        # Delete from FTS index
        cursor.execute(
            "DELETE FROM recipe_elements WHERE recipe_id = ?",
            (recipe_id,),
        )

        # Delete recipe (cascades to versions and executions)
        cursor.execute("DELETE FROM recipes WHERE recipe_id = ?", (recipe_id,))
        c.commit()

        return cursor.rowcount > 0


def _row_to_recipe(row: sqlite3.Row) -> UIRecipe:
    """Convert a database row to a UIRecipe."""
    elements_data = _json_loads(row["elements_json"])
    elements = tuple(_dict_to_element(e) for e in elements_data)

    nav_paths_data = _json_loads(row["navigation_paths_json"])
    navigation_paths = {k: tuple(v) for k, v in nav_paths_data.items()} if nav_paths_data else {}

    return UIRecipe(
        recipe_id=row["recipe_id"],
        app_name=row["app_name"],
        app_desktop_id=row["app_desktop_id"],
        app_version=row["app_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        learn_method=row["learn_method"],
        root_window=row["root_window"],
        elements=elements,
        navigation_paths=navigation_paths,
        version=row["version"],
        confidence=row["confidence"],
        last_success_at=_parse_datetime(row["last_success_at"]),
        failure_count=row["failure_count"],
        success_count=row["success_count"],
    )


def _index_elements(
    cursor: sqlite3.Cursor,
    recipe_id: str,
    elements: list[UIElement],
) -> None:
    """Index elements for full-text search.

    Args:
        cursor: SQLite cursor.
        recipe_id: Parent recipe ID.
        elements: Elements to index.
    """
    for element in elements:
        cursor.execute(
            """
            INSERT INTO recipe_elements (
                recipe_id, element_name, element_role, element_path
            ) VALUES (?, ?, ?, ?)
            """,
            (
                recipe_id,
                element.name,
                element.role,
                "/".join(str(i) for i in element.path),
            ),
        )


def _create_version_snapshot(
    cursor: sqlite3.Cursor,
    recipe: UIRecipe,
    reason: str,
) -> None:
    """Create a version snapshot of the current recipe state.

    Args:
        cursor: SQLite cursor.
        recipe: Current recipe.
        reason: Reason for creating snapshot.
    """
    version_id = str(uuid.uuid4())
    nav_paths_json = {k: list(v) for k, v in recipe.navigation_paths.items()}

    cursor.execute(
        """
        INSERT INTO recipe_versions (
            version_id, recipe_id, version, created_at,
            elements_json, navigation_paths_json, change_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            recipe.recipe_id,
            recipe.version,
            _serialize_datetime(datetime.utcnow()),
            _json_dumps([_element_to_dict(e) for e in recipe.elements]),
            _json_dumps(nav_paths_json),
            reason,
        ),
    )


# =============================================================================
# Recipe Search
# =============================================================================


def search_elements(
    query: str,
    recipe_id: str | None = None,
    limit: int = 20,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[str, UIElement]]:
    """Search elements using full-text search.

    Args:
        query: Search query.
        recipe_id: Limit to specific recipe.
        limit: Maximum results.
        conn: SQLite connection.

    Returns:
        List of (recipe_id, UIElement) tuples.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()

        if recipe_id:
            cursor.execute(
                """
                SELECT re.recipe_id, re.element_path, r.elements_json
                FROM recipe_elements re
                JOIN recipes r ON re.recipe_id = r.recipe_id
                WHERE recipe_elements MATCH ? AND re.recipe_id = ?
                LIMIT ?
                """,
                (query, recipe_id, limit),
            )
        else:
            cursor.execute(
                """
                SELECT re.recipe_id, re.element_path, r.elements_json
                FROM recipe_elements re
                JOIN recipes r ON re.recipe_id = r.recipe_id
                WHERE recipe_elements MATCH ?
                LIMIT ?
                """,
                (query, limit),
            )

        results = []
        for row in cursor.fetchall():
            # Parse element path
            path = tuple(int(i) for i in row["element_path"].split("/") if i)

            # Find matching element in recipe
            elements_data = _json_loads(row["elements_json"])
            for elem_data in elements_data:
                if tuple(elem_data.get("path", [])) == path:
                    results.append((row["recipe_id"], _dict_to_element(elem_data)))
                    break

        return results


# =============================================================================
# Execution CRUD
# =============================================================================


def create_execution(
    recipe_id: str,
    task_request: str,
    actions: list[UIAction] | None = None,
    incident_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> RecipeExecution:
    """Create a new recipe execution record.

    Args:
        recipe_id: The recipe being executed.
        task_request: User's original request.
        actions: Actions to execute.
        incident_id: Linked incident ID.
        conn: SQLite connection.

    Returns:
        The created RecipeExecution.
    """
    with _ensure_connection(conn) as c:
        execution_id = str(uuid.uuid4())
        now = datetime.utcnow()
        actions = actions or []

        execution = RecipeExecution(
            execution_id=execution_id,
            recipe_id=recipe_id,
            incident_id=incident_id,
            task_request=task_request,
            actions=tuple(actions),
            started_at=now,
            outcome="pending",
        )

        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO recipe_executions (
                execution_id, recipe_id, incident_id, task_request,
                actions_json, started_at, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution.execution_id,
                execution.recipe_id,
                execution.incident_id,
                execution.task_request,
                _json_dumps([_action_to_dict(a) for a in actions]),
                _serialize_datetime(execution.started_at),
                "pending",
            ),
        )
        c.commit()

        return execution


def finalize_execution(
    execution_id: str,
    outcome: ExecutionOutcome,
    adaptation_notes: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> RecipeExecution | None:
    """Finalize an execution with outcome.

    Args:
        execution_id: The execution UUID.
        outcome: Final outcome.
        adaptation_notes: Notes about adaptations made.
        conn: SQLite connection.

    Returns:
        Updated RecipeExecution.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        now = datetime.utcnow()

        cursor.execute(
            """
            UPDATE recipe_executions
            SET completed_at = ?, outcome = ?, adaptation_notes = ?
            WHERE execution_id = ?
            """,
            (
                _serialize_datetime(now),
                outcome,
                adaptation_notes,
                execution_id,
            ),
        )
        c.commit()

        if cursor.rowcount == 0:
            return None

        return get_execution(execution_id, conn=c)


def get_execution(
    execution_id: str,
    conn: sqlite3.Connection | None = None,
) -> RecipeExecution | None:
    """Get an execution by ID.

    Args:
        execution_id: The execution UUID.
        conn: SQLite connection.

    Returns:
        RecipeExecution if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            "SELECT * FROM recipe_executions WHERE execution_id = ?",
            (execution_id,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_execution(row)


def list_executions(
    recipe_id: str | None = None,
    limit: int = 50,
    conn: sqlite3.Connection | None = None,
) -> list[RecipeExecution]:
    """List executions, optionally filtered by recipe.

    Args:
        recipe_id: Filter by recipe.
        limit: Maximum results.
        conn: SQLite connection.

    Returns:
        List of RecipeExecutions.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()

        if recipe_id:
            cursor.execute(
                """
                SELECT * FROM recipe_executions
                WHERE recipe_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (recipe_id, limit),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM recipe_executions
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [_row_to_execution(row) for row in cursor.fetchall()]


def _row_to_execution(row: sqlite3.Row) -> RecipeExecution:
    """Convert a database row to a RecipeExecution."""
    actions_data = _json_loads(row["actions_json"])
    actions = tuple(_dict_to_action(a) for a in actions_data)

    return RecipeExecution(
        execution_id=row["execution_id"],
        recipe_id=row["recipe_id"],
        incident_id=row["incident_id"],
        task_request=row["task_request"],
        actions=actions,
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=_parse_datetime(row["completed_at"]),
        outcome=row["outcome"],
        adaptation_notes=row["adaptation_notes"],
    )


# =============================================================================
# Version CRUD
# =============================================================================


def get_versions(
    recipe_id: str,
    limit: int = 10,
    conn: sqlite3.Connection | None = None,
) -> list[RecipeVersion]:
    """Get version history for a recipe.

    Args:
        recipe_id: The recipe UUID.
        limit: Maximum versions to return.
        conn: SQLite connection.

    Returns:
        List of RecipeVersions, newest first.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM recipe_versions
            WHERE recipe_id = ?
            ORDER BY version DESC
            LIMIT ?
            """,
            (recipe_id, limit),
        )

        return [_row_to_version(row) for row in cursor.fetchall()]


def get_version(
    recipe_id: str,
    version: int,
    conn: sqlite3.Connection | None = None,
) -> RecipeVersion | None:
    """Get a specific version of a recipe.

    Args:
        recipe_id: The recipe UUID.
        version: Version number.
        conn: SQLite connection.

    Returns:
        RecipeVersion if found, None otherwise.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT * FROM recipe_versions
            WHERE recipe_id = ? AND version = ?
            """,
            (recipe_id, version),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return _row_to_version(row)


def _row_to_version(row: sqlite3.Row) -> RecipeVersion:
    """Convert a database row to a RecipeVersion."""
    elements_data = _json_loads(row["elements_json"])
    elements = tuple(_dict_to_element(e) for e in elements_data)

    nav_paths_data = _json_loads(row["navigation_paths_json"])
    navigation_paths = {k: tuple(v) for k, v in nav_paths_data.items()} if nav_paths_data else {}

    return RecipeVersion(
        version_id=row["version_id"],
        recipe_id=row["recipe_id"],
        version=row["version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        elements=elements,
        navigation_paths=navigation_paths,
        change_reason=row["change_reason"] or "",
    )


# =============================================================================
# Navigation Target CRUD
# =============================================================================


def add_navigation_target(
    recipe_id: str,
    target_name: str,
    element_path: tuple[int, ...],
    element_role: str | None = None,
    element_name: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Add or update a named navigation target.

    Args:
        recipe_id: The recipe UUID.
        target_name: Name for the target (e.g., 'bluetooth_toggle').
        element_path: Path to the element.
        element_role: Element role.
        element_name: Element name.
        conn: SQLite connection.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        path_str = "/".join(str(i) for i in element_path)

        cursor.execute(
            """
            INSERT INTO navigation_targets (
                recipe_id, target_name, element_path,
                element_role, element_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(recipe_id, target_name) DO UPDATE SET
                element_path = excluded.element_path,
                element_role = excluded.element_role,
                element_name = excluded.element_name
            """,
            (
                recipe_id,
                target_name,
                path_str,
                element_role,
                element_name,
                _serialize_datetime(datetime.utcnow()),
            ),
        )
        c.commit()


def get_navigation_target(
    recipe_id: str,
    target_name: str,
    conn: sqlite3.Connection | None = None,
) -> tuple[int, ...] | None:
    """Get a named navigation target path.

    Args:
        recipe_id: The recipe UUID.
        target_name: Name of the target.
        conn: SQLite connection.

    Returns:
        Element path, or None if not found.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            SELECT element_path FROM navigation_targets
            WHERE recipe_id = ? AND target_name = ?
            """,
            (recipe_id, target_name),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return tuple(int(i) for i in row["element_path"].split("/") if i)


def increment_target_usage(
    recipe_id: str,
    target_name: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Increment usage count for a navigation target.

    Args:
        recipe_id: The recipe UUID.
        target_name: Name of the target.
        conn: SQLite connection.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            """
            UPDATE navigation_targets
            SET usage_count = usage_count + 1,
                last_used_at = ?
            WHERE recipe_id = ? AND target_name = ?
            """,
            (
                _serialize_datetime(datetime.utcnow()),
                recipe_id,
                target_name,
            ),
        )
        c.commit()


# =============================================================================
# Statistics
# =============================================================================


def get_recipe_stats(
    recipe_id: str,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Get statistics for a recipe.

    Args:
        recipe_id: The recipe UUID.
        conn: SQLite connection.

    Returns:
        Dictionary with stats.
    """
    with _ensure_connection(conn) as c:
        cursor = c.cursor()

        stats: dict[str, Any] = {"recipe_id": recipe_id}

        # Execution stats
        cursor.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN outcome = 'adapted' THEN 1 ELSE 0 END) as adapted
            FROM recipe_executions
            WHERE recipe_id = ?
            """,
            (recipe_id,),
        )
        row = cursor.fetchone()
        stats["executions"] = {
            "total": row["total"],
            "success": row["success"] or 0,
            "failed": row["failed"] or 0,
            "adapted": row["adapted"] or 0,
        }

        # Version count
        cursor.execute(
            "SELECT COUNT(*) FROM recipe_versions WHERE recipe_id = ?",
            (recipe_id,),
        )
        version_row = cursor.fetchone()
        stats["version_count"] = int(version_row[0]) if version_row else 0

        # Navigation target count
        cursor.execute(
            "SELECT COUNT(*) FROM navigation_targets WHERE recipe_id = ?",
            (recipe_id,),
        )
        target_row = cursor.fetchone()
        stats["target_count"] = int(target_row[0]) if target_row else 0

        return stats
