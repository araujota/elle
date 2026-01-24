"""Condition evaluator for Reactive Functions.

Implements a JSONLogic-style expression language for evaluating
conditions over event payloads, probed state, and system metrics.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from elle.reactive.models import Condition, ConditionContext


class EvaluationError(Exception):
    """Error evaluating a condition."""

    pass


class ConditionEvaluator:
    """Evaluate JSONLogic-style conditions.

    Supports operators:
    - Logical: all, any, not
    - Comparison: eq, ne, gt, gte, lt, lte
    - String: contains, matches (regex)

    Variables use template syntax {source.path}:
    - {event.field} - Event fields (ts, source, category, etc.)
    - {event.raw.field} - Raw event data
    - {state.key} - Probed state values
    - {system.metric} - System snapshot metrics

    Example expressions:
        {"all": [
            {"gte": ["{event.raw.used_pct}", 90]},
            {"eq": ["{state.docker_running}", true]}
        ]}

        {"any": [
            {"contains": ["{event.message}", "error"]},
            {"matches": ["{event.message}", "fail(ed|ure)"]}
        ]}
    """

    # Variable pattern for {source.path.to.value}
    VAR_PATTERN = re.compile(r"\{([a-z_]+(?:\.[a-z_0-9]+)*)\}", re.IGNORECASE)

    def __init__(self) -> None:
        """Initialize the evaluator with default operators."""
        self._operators: dict[str, Callable[[list[Any], ConditionContext], Any]] = {
            # Logical operators
            "all": self._op_all,
            "any": self._op_any,
            "not": self._op_not,
            # Comparison operators
            "eq": self._op_eq,
            "ne": self._op_ne,
            "gt": self._op_gt,
            "gte": self._op_gte,
            "lt": self._op_lt,
            "lte": self._op_lte,
            # String operators
            "contains": self._op_contains,
            "matches": self._op_matches,
            # Existence check
            "exists": self._op_exists,
        }

    def evaluate(
        self,
        condition: Condition,
        context: ConditionContext,
    ) -> tuple[bool, str]:
        """Evaluate a condition against a context.

        Args:
            condition: The condition to evaluate.
            context: The context containing event, state, and system data.

        Returns:
            Tuple of (result, explanation).
        """
        try:
            result = self._eval(condition.expression, context)
            explanation = self._explain(condition.expression, context, result)
            return bool(result), explanation
        except EvaluationError as e:
            return False, f"Evaluation error: {e}"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def _eval(self, expr: Any, ctx: ConditionContext) -> Any:
        """Recursively evaluate an expression.

        Args:
            expr: Expression to evaluate (dict, list, or scalar).
            ctx: Evaluation context.

        Returns:
            Evaluated result.
        """
        # Handle dict expressions (operators)
        if isinstance(expr, dict):
            if len(expr) != 1:
                raise EvaluationError(f"Expression must have exactly one operator: {expr}")

            op_name, args = next(iter(expr.items()))

            if op_name not in self._operators:
                raise EvaluationError(f"Unknown operator: {op_name}")

            # Ensure args is a list
            if not isinstance(args, list):
                args = [args]

            return self._operators[op_name](args, ctx)

        # Handle variable templates
        if isinstance(expr, str):
            return self._resolve_value(expr, ctx)

        # Handle lists (evaluate each element)
        if isinstance(expr, list):
            return [self._eval(item, ctx) for item in expr]

        # Return scalars as-is
        return expr

    def _resolve_value(self, value: Any, ctx: ConditionContext) -> Any:
        """Resolve a value, substituting variables.

        Args:
            value: Value to resolve (may contain {var} templates).
            ctx: Evaluation context.

        Returns:
            Resolved value.
        """
        if not isinstance(value, str):
            return value

        # Check if the entire string is a variable
        match = self.VAR_PATTERN.fullmatch(value)
        if match:
            return self._resolve_variable(match.group(1), ctx)

        # Check for embedded variables in a string
        def replace_var(m: re.Match[str]) -> str:
            resolved = self._resolve_variable(m.group(1), ctx)
            return str(resolved) if resolved is not None else ""

        return self.VAR_PATTERN.sub(replace_var, value)

    def _resolve_variable(self, path: str, ctx: ConditionContext) -> Any:
        """Resolve a variable path to its value.

        Args:
            path: Dotted path like "event.raw.used_pct".
            ctx: Evaluation context.

        Returns:
            Resolved value, or None if not found.
        """
        parts = path.split(".")
        if not parts:
            return None

        source = parts[0].lower()
        remaining = parts[1:] if len(parts) > 1 else []

        # Get the source object
        if source == "event":
            obj = ctx.event
        elif source == "state":
            obj = ctx.state
        elif source == "system":
            obj = ctx.system
        else:
            return None

        if obj is None:
            return None

        # Navigate the path
        current = obj
        for part in remaining:
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None

            if current is None:
                return None

        return current

    def _explain(
        self,
        expr: dict[str, Any],
        ctx: ConditionContext,
        result: bool,
    ) -> str:
        """Generate a human-readable explanation of the evaluation.

        Args:
            expr: The expression that was evaluated.
            ctx: Evaluation context.
            result: Evaluation result.

        Returns:
            Human-readable explanation.
        """
        if not isinstance(expr, dict) or len(expr) != 1:
            return f"Result: {result}"

        op_name, args = next(iter(expr.items()))

        if op_name == "all":
            return f"All conditions {'passed' if result else 'did not pass'}"
        elif op_name == "any":
            return f"{'At least one' if result else 'No'} condition passed"
        elif op_name == "not":
            return f"Negation: {result}"
        elif op_name in ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "matches"):
            if isinstance(args, list) and len(args) >= 2:
                left_val = self._resolve_value(args[0], ctx)
                right_val = self._resolve_value(args[1], ctx)
                op_symbol = {
                    "eq": "==",
                    "ne": "!=",
                    "gt": ">",
                    "gte": ">=",
                    "lt": "<",
                    "lte": "<=",
                    "contains": "contains",
                    "matches": "matches",
                }.get(op_name, op_name)
                return f"{left_val} {op_symbol} {right_val} -> {result}"
        elif op_name == "exists" and isinstance(args, list) and len(args) >= 1:
            var = args[0]
            return f"exists({var}) -> {result}"

        return f"{op_name}: {result}"

    # =========================================================================
    # Operator implementations
    # =========================================================================

    def _op_all(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Evaluate all conditions (AND)."""
        return all(self._eval(arg, ctx) for arg in args)

    def _op_any(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Evaluate any condition (OR)."""
        return any(self._eval(arg, ctx) for arg in args)

    def _op_not(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Negate a condition."""
        if len(args) != 1:
            raise EvaluationError("'not' operator requires exactly one argument")
        return not self._eval(args[0], ctx)

    def _op_eq(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test equality."""
        if len(args) != 2:
            raise EvaluationError("'eq' operator requires exactly two arguments")
        left = self._resolve_value(args[0], ctx)
        right = self._resolve_value(args[1], ctx)
        return left == right

    def _op_ne(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test inequality."""
        if len(args) != 2:
            raise EvaluationError("'ne' operator requires exactly two arguments")
        left = self._resolve_value(args[0], ctx)
        right = self._resolve_value(args[1], ctx)
        return left != right

    def _op_gt(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test greater than."""
        if len(args) != 2:
            raise EvaluationError("'gt' operator requires exactly two arguments")
        left = self._to_number(self._resolve_value(args[0], ctx))
        right = self._to_number(self._resolve_value(args[1], ctx))
        return left > right

    def _op_gte(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test greater than or equal."""
        if len(args) != 2:
            raise EvaluationError("'gte' operator requires exactly two arguments")
        left = self._to_number(self._resolve_value(args[0], ctx))
        right = self._to_number(self._resolve_value(args[1], ctx))
        return left >= right

    def _op_lt(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test less than."""
        if len(args) != 2:
            raise EvaluationError("'lt' operator requires exactly two arguments")
        left = self._to_number(self._resolve_value(args[0], ctx))
        right = self._to_number(self._resolve_value(args[1], ctx))
        return left < right

    def _op_lte(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test less than or equal."""
        if len(args) != 2:
            raise EvaluationError("'lte' operator requires exactly two arguments")
        left = self._to_number(self._resolve_value(args[0], ctx))
        right = self._to_number(self._resolve_value(args[1], ctx))
        return left <= right

    def _op_contains(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test if string contains substring."""
        if len(args) != 2:
            raise EvaluationError("'contains' operator requires exactly two arguments")
        haystack = str(self._resolve_value(args[0], ctx) or "")
        needle = str(self._resolve_value(args[1], ctx) or "")
        return needle in haystack

    def _op_matches(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test if string matches regex pattern."""
        if len(args) != 2:
            raise EvaluationError("'matches' operator requires exactly two arguments")
        text = str(self._resolve_value(args[0], ctx) or "")
        pattern = str(self._resolve_value(args[1], ctx) or "")
        try:
            return bool(re.search(pattern, text))
        except re.error as e:
            raise EvaluationError(f"Invalid regex pattern: {e}") from e

    def _op_exists(self, args: list[Any], ctx: ConditionContext) -> bool:
        """Test if a variable exists and is not None."""
        if len(args) != 1:
            raise EvaluationError("'exists' operator requires exactly one argument")
        value = self._resolve_value(args[0], ctx)
        return value is not None

    def _to_number(self, value: Any) -> float:
        """Convert a value to a number for comparison.

        Args:
            value: Value to convert.

        Returns:
            Numeric value.

        Raises:
            EvaluationError: If value cannot be converted.
        """
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError as e:
                raise EvaluationError(f"Cannot convert to number: {value}") from e
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        raise EvaluationError(f"Cannot convert to number: {type(value)}")


# Singleton evaluator instance
_evaluator: ConditionEvaluator | None = None


def get_evaluator() -> ConditionEvaluator:
    """Get the singleton ConditionEvaluator instance.

    Returns:
        The global ConditionEvaluator.
    """
    global _evaluator
    if _evaluator is None:
        _evaluator = ConditionEvaluator()
    return _evaluator


def evaluate_condition(
    condition: Condition,
    context: ConditionContext,
) -> tuple[bool, str]:
    """Evaluate a condition against a context.

    Convenience function using the singleton evaluator.

    Args:
        condition: The condition to evaluate.
        context: The context containing event, state, and system data.

    Returns:
        Tuple of (result, explanation).
    """
    return get_evaluator().evaluate(condition, context)


def resolve_template(
    template: str,
    context: ConditionContext,
) -> str:
    """Resolve variable templates in a string.

    Args:
        template: String containing {var} templates.
        context: Evaluation context.

    Returns:
        String with variables resolved.
    """
    evaluator = get_evaluator()
    return str(evaluator._resolve_value(template, context))


def resolve_templates_in_dict(
    data: dict[str, Any],
    context: ConditionContext,
) -> dict[str, Any]:
    """Resolve variable templates in a dictionary recursively.

    Used to resolve action input templates like {event.raw.service}.

    Args:
        data: Dictionary potentially containing {var} templates.
        context: Evaluation context.

    Returns:
        Dictionary with all templates resolved.
    """
    evaluator = get_evaluator()
    result: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, str):
            result[key] = evaluator._resolve_value(value, context)
        elif isinstance(value, dict):
            result[key] = resolve_templates_in_dict(value, context)
        elif isinstance(value, list):
            result[key] = [
                evaluator._resolve_value(item, context)
                if isinstance(item, str)
                else resolve_templates_in_dict(item, context)
                if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            result[key] = value

    return result
