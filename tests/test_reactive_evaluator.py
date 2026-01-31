"""Tests for Reactive Functions condition evaluator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.reactive.evaluator import (
    ConditionEvaluator,
    EvaluationError,
    evaluate_condition,
    get_evaluator,
    resolve_template,
    resolve_templates_in_dict,
)
from elle.reactive.models import Condition, ConditionContext

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def evaluator():
    """Create a fresh evaluator instance."""
    return ConditionEvaluator()


@pytest.fixture
def event_context():
    """Create a context with event data."""
    return ConditionContext(
        event={
            "category": "disk",
            "severity": "warning",
            "message": "Disk space low",
            "raw": {
                "used_pct": 95,
                "path": "/",
                "mount": "/",
            },
        },
        state={
            "docker_running": True,
            "nginx_active": False,
        },
        system={
            "hostname": "test-host",
            "uptime_hours": 24,
        },
    )


# =============================================================================
# TestVariableResolution
# =============================================================================


class TestVariableResolution:
    """Tests for variable resolution from event, state, and system sources."""

    def test_resolve_event_field(self, evaluator, event_context):
        """Resolve a top-level event field."""
        result = evaluator._resolve_value("{event.category}", event_context)
        assert result == "disk"

    def test_resolve_event_raw_field(self, evaluator, event_context):
        """Resolve a nested event.raw field."""
        result = evaluator._resolve_value("{event.raw.used_pct}", event_context)
        assert result == 95

    def test_resolve_state_field(self, evaluator, event_context):
        """Resolve a state field."""
        result = evaluator._resolve_value("{state.docker_running}", event_context)
        assert result is True

    def test_resolve_system_field(self, evaluator, event_context):
        """Resolve a system metrics field."""
        result = evaluator._resolve_value("{system.hostname}", event_context)
        assert result == "test-host"

    def test_resolve_missing_field(self, evaluator, event_context):
        """Missing field resolves to None."""
        result = evaluator._resolve_value("{event.nonexistent}", event_context)
        assert result is None

    def test_resolve_literal_string(self, evaluator, event_context):
        """Literal strings without templates are returned unchanged."""
        result = evaluator._resolve_value("literal", event_context)
        assert result == "literal"

    def test_resolve_literal_number(self, evaluator, event_context):
        """Non-string values are returned as-is."""
        result = evaluator._resolve_value(90, event_context)
        assert result == 90

    def test_resolve_embedded_variable(self, evaluator, event_context):
        """Embedded variable inside a larger string is resolved."""
        result = evaluator._resolve_value("Host: {system.hostname}", event_context)
        assert result == "Host: test-host"

    def test_resolve_embedded_variable_none_becomes_empty(self, evaluator, event_context):
        """Embedded variable resolving to None is replaced with empty string."""
        result = evaluator._resolve_value("svc={event.missing_key}", event_context)
        assert result == "svc="

    def test_resolve_unknown_source_returns_none(self, evaluator, event_context):
        """Unknown variable source (not event/state/system) resolves to None."""
        result = evaluator._resolve_variable("unknown.field", event_context)
        assert result is None

    def test_resolve_none_source_object(self, evaluator):
        """When source object is None, resolution returns None."""
        ctx = ConditionContext(event=None, state={}, system={})
        result = evaluator._resolve_variable("event.field", ctx)
        assert result is None

    def test_resolve_missing_nested_key(self, evaluator, event_context):
        """Missing nested key in dict resolves to None."""
        result = evaluator._resolve_value("{event.raw.nonexistent}", event_context)
        assert result is None

    def test_resolve_attribute_on_nested_object(self, evaluator):
        """Variable resolution follows hasattr/getattr for non-dict nested objects."""
        # ConditionContext.event must be a dict, so we nest an object inside
        inner = MagicMock()
        inner.status = "active"
        ctx = ConditionContext(event={"svc": inner}, state={}, system={})
        result = evaluator._resolve_variable("event.svc.status", ctx)
        assert result == "active"

    def test_resolve_empty_context(self, evaluator):
        """Empty context (event=None) resolves event variables to None."""
        ctx = ConditionContext()
        result = evaluator._resolve_value("{event.category}", ctx)
        assert result is None

    def test_resolve_path_attribute_not_found_on_nested(self, evaluator):
        """When attribute does not exist on nested non-dict object, resolves to None."""
        inner = MagicMock(spec=[])  # spec=[] means no attributes
        ctx = ConditionContext(event={"svc": inner}, state={}, system={})
        result = evaluator._resolve_variable("event.svc.nonexistent", ctx)
        assert result is None


# =============================================================================
# TestComparisonOperators
# =============================================================================


class TestComparisonOperators:
    """Tests for eq, ne, gt, gte, lt, lte comparison operators."""

    def test_eq_true(self, evaluator, event_context):
        """eq returns True for equal values."""
        result = evaluator._op_eq(["{event.category}", "disk"], event_context)
        assert result is True

    def test_eq_false(self, evaluator, event_context):
        """eq returns False for unequal values."""
        result = evaluator._op_eq(["{event.category}", "net"], event_context)
        assert result is False

    def test_ne_true(self, evaluator, event_context):
        """ne returns True for unequal values."""
        result = evaluator._op_ne(["{event.category}", "net"], event_context)
        assert result is True

    def test_ne_false(self, evaluator, event_context):
        """ne returns False for equal values."""
        result = evaluator._op_ne(["{event.category}", "disk"], event_context)
        assert result is False

    def test_gt_true(self, evaluator, event_context):
        """gt returns True when left > right."""
        result = evaluator._op_gt(["{event.raw.used_pct}", 90], event_context)
        assert result is True

    def test_gt_false(self, evaluator, event_context):
        """gt returns False when left == right."""
        result = evaluator._op_gt(["{event.raw.used_pct}", 95], event_context)
        assert result is False

    def test_gte_true_at_boundary(self, evaluator, event_context):
        """gte returns True when left == right."""
        result = evaluator._op_gte(["{event.raw.used_pct}", 95], event_context)
        assert result is True

    def test_lt_true(self, evaluator, event_context):
        """lt returns True when left < right."""
        result = evaluator._op_lt(["{event.raw.used_pct}", 100], event_context)
        assert result is True

    def test_lt_false(self, evaluator, event_context):
        """lt returns False when left >= right."""
        result = evaluator._op_lt(["{event.raw.used_pct}", 95], event_context)
        assert result is False

    def test_lte_true_at_boundary(self, evaluator, event_context):
        """lte returns True when left == right."""
        result = evaluator._op_lte(["{event.raw.used_pct}", 95], event_context)
        assert result is True

    def test_eq_wrong_arg_count(self, evaluator, event_context):
        """eq with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_eq([1], event_context)

    def test_ne_wrong_arg_count(self, evaluator, event_context):
        """ne with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_ne([1, 2, 3], event_context)

    def test_gt_wrong_arg_count(self, evaluator, event_context):
        """gt with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_gt([1], event_context)

    def test_gte_wrong_arg_count(self, evaluator, event_context):
        """gte with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_gte([], event_context)

    def test_lt_wrong_arg_count(self, evaluator, event_context):
        """lt with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_lt([1, 2, 3], event_context)

    def test_lte_wrong_arg_count(self, evaluator, event_context):
        """lte with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_lte([1], event_context)


# =============================================================================
# TestStringOperators
# =============================================================================


class TestStringOperators:
    """Tests for contains and matches string operators."""

    def test_contains_true(self, evaluator, event_context):
        """contains returns True when substring is present."""
        result = evaluator._op_contains(["{event.message}", "low"], event_context)
        assert result is True

    def test_contains_false(self, evaluator, event_context):
        """contains returns False when substring is absent."""
        result = evaluator._op_contains(["{event.message}", "high"], event_context)
        assert result is False

    def test_contains_none_haystack(self, evaluator):
        """contains gracefully handles None resolved haystack."""
        ctx = ConditionContext(event={"other": "val"}, state={}, system={})
        result = evaluator._op_contains(["{event.missing}", "text"], ctx)
        assert result is False

    def test_contains_wrong_arg_count(self, evaluator, event_context):
        """contains with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_contains(["{event.message}"], event_context)

    def test_matches_true(self, evaluator, event_context):
        """matches returns True when regex matches."""
        result = evaluator._op_matches(["{event.message}", r"[Dd]isk"], event_context)
        assert result is True

    def test_matches_false(self, evaluator, event_context):
        """matches returns False when regex does not match."""
        result = evaluator._op_matches(["{event.message}", r"^Error"], event_context)
        assert result is False

    def test_matches_invalid_regex(self, evaluator, event_context):
        """matches with invalid regex raises EvaluationError."""
        with pytest.raises(EvaluationError, match="Invalid regex"):
            evaluator._op_matches(["{event.message}", r"[invalid"], event_context)

    def test_matches_wrong_arg_count(self, evaluator, event_context):
        """matches with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly two"):
            evaluator._op_matches(["{event.message}"], event_context)


# =============================================================================
# TestLogicalOperators
# =============================================================================


class TestLogicalOperators:
    """Tests for all, any, not logical operators."""

    def test_all_true(self, evaluator, event_context):
        """all returns True when every sub-condition is true."""
        result = evaluator._op_all(
            [
                {"eq": ["{event.category}", "disk"]},
                {"gte": ["{event.raw.used_pct}", 90]},
            ],
            event_context,
        )
        assert result is True

    def test_all_false(self, evaluator, event_context):
        """all returns False when any sub-condition is false."""
        result = evaluator._op_all(
            [
                {"eq": ["{event.category}", "disk"]},
                {"gte": ["{event.raw.used_pct}", 100]},
            ],
            event_context,
        )
        assert result is False

    def test_any_true(self, evaluator, event_context):
        """any returns True when at least one sub-condition is true."""
        result = evaluator._op_any(
            [
                {"eq": ["{event.category}", "net"]},
                {"gte": ["{event.raw.used_pct}", 90]},
            ],
            event_context,
        )
        assert result is True

    def test_any_false(self, evaluator, event_context):
        """any returns False when all sub-conditions are false."""
        result = evaluator._op_any(
            [
                {"eq": ["{event.category}", "net"]},
                {"gte": ["{event.raw.used_pct}", 100]},
            ],
            event_context,
        )
        assert result is False

    def test_not_true(self, evaluator, event_context):
        """not inverts a false condition to True."""
        result = evaluator._op_not(
            [{"eq": ["{event.category}", "net"]}],
            event_context,
        )
        assert result is True

    def test_not_false(self, evaluator, event_context):
        """not inverts a true condition to False."""
        result = evaluator._op_not(
            [{"eq": ["{event.category}", "disk"]}],
            event_context,
        )
        assert result is False

    def test_not_wrong_arg_count(self, evaluator, event_context):
        """not with more than one argument raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly one"):
            evaluator._op_not(
                [{"eq": [1, 1]}, {"eq": [2, 2]}],
                event_context,
            )


# =============================================================================
# TestExistsOperator
# =============================================================================


class TestExistsOperator:
    """Tests for exists operator."""

    def test_exists_true(self, evaluator, event_context):
        """exists returns True for a present field."""
        result = evaluator._op_exists(["{event.category}"], event_context)
        assert result is True

    def test_exists_false(self, evaluator, event_context):
        """exists returns False for a missing field."""
        result = evaluator._op_exists(["{event.nonexistent}"], event_context)
        assert result is False

    def test_exists_wrong_arg_count(self, evaluator, event_context):
        """exists with wrong argument count raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly one"):
            evaluator._op_exists(["{event.a}", "{event.b}"], event_context)


# =============================================================================
# TestToNumberConversion
# =============================================================================


class TestToNumberConversion:
    """Tests for _to_number conversion with various types."""

    def test_convert_none(self, evaluator):
        """None converts to 0.0."""
        assert evaluator._to_number(None) == 0.0

    def test_convert_int(self, evaluator):
        """Integer converts to float."""
        assert evaluator._to_number(42) == 42.0

    def test_convert_float(self, evaluator):
        """Float passes through."""
        assert evaluator._to_number(3.14) == 3.14

    def test_convert_numeric_string(self, evaluator):
        """Numeric string converts to float."""
        assert evaluator._to_number("42.5") == 42.5

    def test_convert_invalid_string(self, evaluator):
        """Non-numeric string raises EvaluationError."""
        with pytest.raises(EvaluationError, match="Cannot convert"):
            evaluator._to_number("not a number")

    def test_convert_bool_true(self, evaluator):
        """Boolean True converts to 1.0."""
        # Note: bool check is after int/float in source, so bool goes through
        # the isinstance(value, (int, float)) branch since bool is a subclass of int
        result = evaluator._to_number(True)
        assert result == 1.0

    def test_convert_bool_false(self, evaluator):
        """Boolean False converts to 0.0."""
        result = evaluator._to_number(False)
        assert result == 0.0

    def test_convert_unconvertible_type(self, evaluator):
        """Unconvertible type (e.g. list) raises EvaluationError."""
        with pytest.raises(EvaluationError, match="Cannot convert"):
            evaluator._to_number([1, 2, 3])


# =============================================================================
# TestExpressionEvaluation
# =============================================================================


class TestExpressionEvaluation:
    """Tests for _eval recursive expression evaluation."""

    def test_eval_dict_expression(self, evaluator, event_context):
        """Dict expression is dispatched to the correct operator."""
        result = evaluator._eval({"eq": ["{event.category}", "disk"]}, event_context)
        assert result is True

    def test_eval_string_resolves_variable(self, evaluator, event_context):
        """String expression is resolved as a variable template."""
        result = evaluator._eval("{event.category}", event_context)
        assert result == "disk"

    def test_eval_list_evaluates_each_element(self, evaluator, event_context):
        """List expression evaluates each element recursively."""
        result = evaluator._eval([1, "{event.raw.used_pct}", 3], event_context)
        assert result == [1, 95, 3]

    def test_eval_scalar_passthrough(self, evaluator, event_context):
        """Scalar values pass through unchanged."""
        assert evaluator._eval(42, event_context) == 42
        assert evaluator._eval(3.14, event_context) == 3.14
        assert evaluator._eval(True, event_context) is True

    def test_eval_multiple_operators_error(self, evaluator, event_context):
        """Expression dict with multiple keys raises EvaluationError."""
        with pytest.raises(EvaluationError, match="exactly one operator"):
            evaluator._eval({"eq": [1, 1], "ne": [2, 3]}, event_context)

    def test_eval_unknown_operator_error(self, evaluator, event_context):
        """Unknown operator name raises EvaluationError."""
        with pytest.raises(EvaluationError, match="Unknown operator"):
            evaluator._eval({"between": [1, 0, 10]}, event_context)

    def test_eval_non_list_args_wrapped(self, evaluator, event_context):
        """Non-list operator args are wrapped in a list."""
        # "not" with a single dict arg (not wrapped in list)
        result = evaluator._eval({"not": {"eq": [1, 2]}}, event_context)
        assert result is True  # not(eq(1,2)) -> not(False) -> True


# =============================================================================
# TestFullEvaluation
# =============================================================================


class TestFullEvaluation:
    """Tests for evaluate() method covering error handling and explanations."""

    def test_evaluate_simple_condition(self, event_context):
        """Evaluate a simple comparison condition."""
        condition = Condition(expression={"gte": ["{event.raw.used_pct}", 90]})
        result, explanation = evaluate_condition(condition, event_context)
        assert result is True
        assert ">=" in explanation

    def test_evaluate_complex_nested(self, event_context):
        """Evaluate a complex nested all/gte/eq condition."""
        condition = Condition(
            expression={
                "all": [
                    {"eq": ["{event.category}", "disk"]},
                    {"gte": ["{event.raw.used_pct}", 85]},
                    {"eq": ["{state.docker_running}", True]},
                ]
            }
        )
        result, explanation = evaluate_condition(condition, event_context)
        assert result is True
        assert "passed" in explanation.lower()

    def test_evaluate_failing_condition(self, event_context):
        """Evaluate a condition that fails."""
        condition = Condition(expression={"gte": ["{event.raw.used_pct}", 100]})
        result, explanation = evaluate_condition(condition, event_context)
        assert result is False

    def test_evaluate_catches_evaluation_error(self, event_context):
        """EvaluationError during evaluate is caught and returns False."""
        condition = Condition(expression={"eq": [1]})  # Wrong arg count
        result, explanation = evaluate_condition(condition, event_context)
        assert result is False
        assert "evaluation error" in explanation.lower()

    def test_evaluate_catches_unexpected_error(self):
        """Unexpected exceptions during evaluate are caught and return False."""
        evaluator = ConditionEvaluator()
        condition = Condition(expression={"eq": [1, 1]})
        ctx = ConditionContext(event={}, state={}, system={})

        # Patch _eval to raise a generic exception
        with patch.object(evaluator, "_eval", side_effect=RuntimeError("boom")):
            result, explanation = evaluator.evaluate(condition, ctx)
        assert result is False
        assert "unexpected error" in explanation.lower()


# =============================================================================
# TestExplainBranches
# =============================================================================


class TestExplainBranches:
    """Tests for _explain covering all operator explanation branches."""

    def test_explain_non_dict_expression(self, evaluator, event_context):
        """Non-dict expression gets generic 'Result: <val>' explanation."""
        explanation = evaluator._explain("not_a_dict", event_context, True)
        assert explanation == "Result: True"

    def test_explain_dict_with_multiple_keys(self, evaluator, event_context):
        """Dict with multiple keys gets generic explanation."""
        explanation = evaluator._explain({"eq": [1, 1], "ne": [2, 3]}, event_context, True)
        assert explanation == "Result: True"

    def test_explain_all_passed(self, evaluator, event_context):
        """all operator with True result shows 'passed'."""
        explanation = evaluator._explain({"all": [{"eq": [1, 1]}]}, event_context, True)
        assert "passed" in explanation.lower()

    def test_explain_all_not_passed(self, evaluator, event_context):
        """all operator with False result shows 'did not pass'."""
        explanation = evaluator._explain({"all": [{"eq": [1, 2]}]}, event_context, False)
        assert "did not pass" in explanation.lower()

    def test_explain_any_true(self, evaluator, event_context):
        """any operator with True result shows 'at least one'."""
        explanation = evaluator._explain({"any": [{"eq": [1, 1]}]}, event_context, True)
        assert "at least one" in explanation.lower()

    def test_explain_any_false(self, evaluator, event_context):
        """any operator with False result shows 'No condition'."""
        explanation = evaluator._explain({"any": [{"eq": [1, 2]}]}, event_context, False)
        assert "no" in explanation.lower()

    def test_explain_not(self, evaluator, event_context):
        """not operator explanation includes 'Negation'."""
        explanation = evaluator._explain({"not": [{"eq": [1, 1]}]}, event_context, False)
        assert "negation" in explanation.lower()

    def test_explain_comparison_operator(self, evaluator, event_context):
        """Comparison operator explanation shows left op right -> result."""
        explanation = evaluator._explain({"gte": ["{event.raw.used_pct}", 90]}, event_context, True)
        assert ">=" in explanation
        assert "True" in explanation

    def test_explain_contains_operator(self, evaluator, event_context):
        """contains explanation shows haystack contains needle -> result."""
        explanation = evaluator._explain({"contains": ["{event.message}", "low"]}, event_context, True)
        assert "contains" in explanation

    def test_explain_matches_operator(self, evaluator, event_context):
        """matches explanation shows text matches pattern -> result."""
        explanation = evaluator._explain({"matches": ["{event.message}", "Disk"]}, event_context, True)
        assert "matches" in explanation

    def test_explain_exists_operator(self, evaluator, event_context):
        """exists explanation shows exists(var) -> result."""
        explanation = evaluator._explain({"exists": ["{event.category}"]}, event_context, True)
        assert "exists" in explanation
        assert "True" in explanation

    def test_explain_comparison_insufficient_args(self, evaluator, event_context):
        """Comparison operator with < 2 args gets fallback explanation."""
        explanation = evaluator._explain({"eq": ["only_one"]}, event_context, False)
        # Falls through to fallback: "eq: False"
        assert "eq" in explanation

    def test_explain_exists_insufficient_args(self, evaluator, event_context):
        """exists with no args gets fallback explanation."""
        explanation = evaluator._explain({"exists": []}, event_context, False)
        assert "exists" in explanation

    def test_explain_fallback_unknown_operator(self, evaluator, event_context):
        """Unknown operator in _explain gets fallback 'op: result'."""
        explanation = evaluator._explain({"custom": [1, 2]}, event_context, True)
        assert "custom" in explanation
        assert "True" in explanation


# =============================================================================
# TestConvenienceFunctions
# =============================================================================


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_evaluate_condition_uses_singleton(self):
        """evaluate_condition convenience function delegates to singleton."""
        condition = Condition(expression={"eq": [1, 1]})
        ctx = ConditionContext(event={}, state={}, system={})
        result, explanation = evaluate_condition(condition, ctx)
        assert result is True

    def test_get_evaluator_returns_singleton(self):
        """get_evaluator returns the same instance on repeated calls."""
        import elle.reactive.evaluator as mod

        mod._evaluator = None
        first = get_evaluator()
        second = get_evaluator()
        assert first is second
        assert isinstance(first, ConditionEvaluator)

    def test_resolve_template_simple(self, event_context):
        """resolve_template resolves a single variable."""
        result = resolve_template("{event.category}", event_context)
        assert result == "disk"

    def test_resolve_template_embedded(self, event_context):
        """resolve_template resolves embedded variables in a string."""
        result = resolve_template("Alert on {system.hostname}", event_context)
        assert result == "Alert on test-host"

    def test_resolve_template_no_variables(self):
        """resolve_template returns plain string unchanged."""
        ctx = ConditionContext(event={}, state={}, system={})
        result = resolve_template("plain text", ctx)
        assert result == "plain text"


# =============================================================================
# TestResolveTemplatesInDict
# =============================================================================


class TestResolveTemplatesInDict:
    """Tests for resolve_templates_in_dict recursive resolution."""

    def test_resolve_flat_string_values(self, event_context):
        """Resolves template strings in a flat dict."""
        data = {
            "title": "Alert on {system.hostname}",
            "body": "Disk at {event.raw.used_pct}%",
        }
        result = resolve_templates_in_dict(data, event_context)
        assert result["title"] == "Alert on test-host"
        assert result["body"] == "Disk at 95%"

    def test_resolve_nested_dicts(self, event_context):
        """Resolves templates in nested dictionaries."""
        data = {
            "nested": {
                "value": "{state.docker_running}",
            },
        }
        result = resolve_templates_in_dict(data, event_context)
        assert result["nested"]["value"] is True

    def test_resolve_list_with_strings(self, event_context):
        """Resolves template strings inside lists."""
        data = {"tags": ["{event.category}", "alert"]}
        result = resolve_templates_in_dict(data, event_context)
        assert result["tags"] == ["disk", "alert"]

    def test_resolve_list_with_dicts(self, event_context):
        """Resolves templates in dict items inside lists."""
        data = {"steps": [{"host": "{system.hostname}"}]}
        result = resolve_templates_in_dict(data, event_context)
        assert result["steps"][0]["host"] == "test-host"

    def test_non_string_non_dict_values_pass_through(self):
        """Non-string, non-dict, non-list values pass through unchanged."""
        ctx = ConditionContext(event={}, state={}, system={})
        data = {"count": 42, "enabled": True, "ratio": 3.14}
        result = resolve_templates_in_dict(data, ctx)
        assert result["count"] == 42
        assert result["enabled"] is True
        assert result["ratio"] == 3.14

    def test_list_with_non_string_non_dict_items(self):
        """Non-string, non-dict items in lists pass through unchanged."""
        ctx = ConditionContext(event={}, state={}, system={})
        data = {"mixed": ["text", 123, True]}
        result = resolve_templates_in_dict(data, ctx)
        assert result["mixed"][0] == "text"
        assert result["mixed"][1] == 123
        assert result["mixed"][2] is True

    def test_empty_dict(self):
        """Empty dict returns empty dict."""
        ctx = ConditionContext(event={}, state={}, system={})
        result = resolve_templates_in_dict({}, ctx)
        assert result == {}


# =============================================================================
# TestEdgeCases
# =============================================================================


class TestEdgeCases:
    """Tests for additional edge cases not covered elsewhere."""

    def test_deeply_nested_all_any(self, evaluator, event_context):
        """Deeply nested all > any > comparison evaluates correctly."""
        condition = Condition(
            expression={
                "all": [
                    {
                        "any": [
                            {"contains": ["{event.message}", "error"]},
                            {"contains": ["{event.message}", "low"]},
                        ]
                    },
                    {"gte": ["{event.raw.used_pct}", 90]},
                ]
            }
        )
        result, explanation = evaluator.evaluate(condition, event_context)
        assert result is True

    def test_comparison_with_string_numeric_values(self, evaluator):
        """Numeric string values are converted for comparison."""
        ctx = ConditionContext(event={"pct": "75.5"}, state={}, system={})
        result = evaluator._op_gt(["{event.pct}", 50], ctx)
        assert result is True

    def test_non_numeric_comparison_raises_error(self, evaluator):
        """Non-numeric values in numeric comparison raise EvaluationError."""
        ctx = ConditionContext(event={"val": "abc"}, state={}, system={})
        with pytest.raises(EvaluationError, match="Cannot convert"):
            evaluator._op_gt(["{event.val}", 10], ctx)

    def test_none_in_numeric_comparison(self, evaluator):
        """None value in numeric comparison converts to 0.0."""
        ctx = ConditionContext(event={}, state={}, system={})
        # {event.missing} resolves to None -> 0.0
        result = evaluator._op_gte(["{event.missing}", 0], ctx)
        assert result is True

    def test_evaluation_error_class(self):
        """EvaluationError is a proper Exception subclass."""
        err = EvaluationError("test error")
        assert str(err) == "test error"
        assert isinstance(err, Exception)
