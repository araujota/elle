"""Tests for Reactive Functions condition evaluator."""

import pytest

from elle.reactive.evaluator import (
    ConditionEvaluator,
    EvaluationError,
    evaluate_condition,
    resolve_template,
    resolve_templates_in_dict,
)
from elle.reactive.models import Condition, ConditionContext


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


class TestVariableResolution:
    """Tests for variable resolution."""

    def test_resolve_event_field(self, evaluator, event_context):
        """Test resolving event fields."""
        result = evaluator._resolve_value("{event.category}", event_context)
        assert result == "disk"

    def test_resolve_event_raw_field(self, evaluator, event_context):
        """Test resolving nested event.raw fields."""
        result = evaluator._resolve_value("{event.raw.used_pct}", event_context)
        assert result == 95

    def test_resolve_state_field(self, evaluator, event_context):
        """Test resolving state fields."""
        result = evaluator._resolve_value("{state.docker_running}", event_context)
        assert result is True

    def test_resolve_system_field(self, evaluator, event_context):
        """Test resolving system fields."""
        result = evaluator._resolve_value("{system.hostname}", event_context)
        assert result == "test-host"

    def test_resolve_missing_field(self, evaluator, event_context):
        """Test resolving missing field returns None."""
        result = evaluator._resolve_value("{event.nonexistent}", event_context)
        assert result is None

    def test_resolve_literal_string(self, evaluator, event_context):
        """Test that literal strings are returned as-is."""
        result = evaluator._resolve_value("literal", event_context)
        assert result == "literal"

    def test_resolve_literal_number(self, evaluator, event_context):
        """Test that literal numbers are returned as-is."""
        result = evaluator._resolve_value(90, event_context)
        assert result == 90

    def test_resolve_embedded_variable(self, evaluator, event_context):
        """Test resolving embedded variables in string."""
        result = evaluator._resolve_value("Host: {system.hostname}", event_context)
        assert result == "Host: test-host"


class TestComparisonOperators:
    """Tests for comparison operators."""

    def test_eq_true(self, evaluator, event_context):
        """Test equality operator returns true."""
        result = evaluator._op_eq(["{event.category}", "disk"], event_context)
        assert result is True

    def test_eq_false(self, evaluator, event_context):
        """Test equality operator returns false."""
        result = evaluator._op_eq(["{event.category}", "net"], event_context)
        assert result is False

    def test_ne_true(self, evaluator, event_context):
        """Test not-equal operator returns true."""
        result = evaluator._op_ne(["{event.category}", "net"], event_context)
        assert result is True

    def test_gt_true(self, evaluator, event_context):
        """Test greater-than operator returns true."""
        result = evaluator._op_gt(["{event.raw.used_pct}", 90], event_context)
        assert result is True

    def test_gt_false(self, evaluator, event_context):
        """Test greater-than operator returns false."""
        result = evaluator._op_gt(["{event.raw.used_pct}", 95], event_context)
        assert result is False

    def test_gte_true(self, evaluator, event_context):
        """Test greater-than-or-equal operator returns true."""
        result = evaluator._op_gte(["{event.raw.used_pct}", 95], event_context)
        assert result is True

    def test_lt_true(self, evaluator, event_context):
        """Test less-than operator returns true."""
        result = evaluator._op_lt(["{event.raw.used_pct}", 100], event_context)
        assert result is True

    def test_lte_true(self, evaluator, event_context):
        """Test less-than-or-equal operator returns true."""
        result = evaluator._op_lte(["{event.raw.used_pct}", 95], event_context)
        assert result is True


class TestStringOperators:
    """Tests for string operators."""

    def test_contains_true(self, evaluator, event_context):
        """Test contains operator returns true."""
        result = evaluator._op_contains(["{event.message}", "low"], event_context)
        assert result is True

    def test_contains_false(self, evaluator, event_context):
        """Test contains operator returns false."""
        result = evaluator._op_contains(["{event.message}", "high"], event_context)
        assert result is False

    def test_matches_true(self, evaluator, event_context):
        """Test matches operator returns true."""
        result = evaluator._op_matches(["{event.message}", r"[Dd]isk"], event_context)
        assert result is True

    def test_matches_false(self, evaluator, event_context):
        """Test matches operator returns false."""
        result = evaluator._op_matches(["{event.message}", r"^Error"], event_context)
        assert result is False

    def test_matches_invalid_regex(self, evaluator, event_context):
        """Test matches with invalid regex raises error."""
        with pytest.raises(EvaluationError):
            evaluator._op_matches(["{event.message}", r"[invalid"], event_context)


class TestLogicalOperators:
    """Tests for logical operators."""

    def test_all_true(self, evaluator, event_context):
        """Test all operator with all true conditions."""
        result = evaluator._op_all(
            [
                {"eq": ["{event.category}", "disk"]},
                {"gte": ["{event.raw.used_pct}", 90]},
            ],
            event_context,
        )
        assert result is True

    def test_all_false(self, evaluator, event_context):
        """Test all operator with one false condition."""
        result = evaluator._op_all(
            [
                {"eq": ["{event.category}", "disk"]},
                {"gte": ["{event.raw.used_pct}", 100]},
            ],
            event_context,
        )
        assert result is False

    def test_any_true(self, evaluator, event_context):
        """Test any operator with one true condition."""
        result = evaluator._op_any(
            [
                {"eq": ["{event.category}", "net"]},
                {"gte": ["{event.raw.used_pct}", 90]},
            ],
            event_context,
        )
        assert result is True

    def test_any_false(self, evaluator, event_context):
        """Test any operator with all false conditions."""
        result = evaluator._op_any(
            [
                {"eq": ["{event.category}", "net"]},
                {"gte": ["{event.raw.used_pct}", 100]},
            ],
            event_context,
        )
        assert result is False

    def test_not_true(self, evaluator, event_context):
        """Test not operator inverts false to true."""
        result = evaluator._op_not(
            [{"eq": ["{event.category}", "net"]}],
            event_context,
        )
        assert result is True

    def test_not_false(self, evaluator, event_context):
        """Test not operator inverts true to false."""
        result = evaluator._op_not(
            [{"eq": ["{event.category}", "disk"]}],
            event_context,
        )
        assert result is False


class TestExistsOperator:
    """Tests for exists operator."""

    def test_exists_true(self, evaluator, event_context):
        """Test exists operator for existing field."""
        result = evaluator._op_exists(["{event.category}"], event_context)
        assert result is True

    def test_exists_false(self, evaluator, event_context):
        """Test exists operator for missing field."""
        result = evaluator._op_exists(["{event.nonexistent}"], event_context)
        assert result is False


class TestConditionEvaluation:
    """Tests for full condition evaluation."""

    def test_evaluate_simple_condition(self, event_context):
        """Test evaluating simple condition."""
        condition = Condition(expression={"gte": ["{event.raw.used_pct}", 90]})
        result, explanation = evaluate_condition(condition, event_context)
        assert result is True
        assert "95" in explanation or "90" in explanation

    def test_evaluate_complex_condition(self, event_context):
        """Test evaluating complex nested condition."""
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

    def test_evaluate_failing_condition(self, event_context):
        """Test evaluating failing condition."""
        condition = Condition(expression={"gte": ["{event.raw.used_pct}", 100]})
        result, explanation = evaluate_condition(condition, event_context)
        assert result is False


class TestTemplateResolution:
    """Tests for template resolution functions."""

    def test_resolve_template(self, event_context):
        """Test resolve_template function."""
        result = resolve_template("{event.category}", event_context)
        assert result == "disk"

    def test_resolve_templates_in_dict(self, event_context):
        """Test resolve_templates_in_dict function."""
        data = {
            "title": "Alert on {system.hostname}",
            "body": "Disk at {event.raw.used_pct}%",
            "nested": {
                "value": "{state.docker_running}",
            },
        }
        result = resolve_templates_in_dict(data, event_context)
        assert result["title"] == "Alert on test-host"
        assert result["body"] == "Disk at 95%"
        assert result["nested"]["value"] is True

    def test_resolve_templates_in_dict_with_list(self, event_context):
        """Test resolve_templates_in_dict with list values."""
        data = {
            "tags": ["{event.category}", "alert"],
        }
        result = resolve_templates_in_dict(data, event_context)
        assert result["tags"] == ["disk", "alert"]


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_context(self, evaluator):
        """Test evaluation with empty context."""
        ctx = ConditionContext()
        result = evaluator._resolve_value("{event.category}", ctx)
        assert result is None

    def test_number_conversion(self, evaluator):
        """Test number conversion from string."""
        result = evaluator._to_number("42")
        assert result == 42.0

    def test_number_conversion_none(self, evaluator):
        """Test number conversion from None."""
        result = evaluator._to_number(None)
        assert result == 0.0

    def test_number_conversion_invalid(self, evaluator):
        """Test number conversion from invalid string."""
        with pytest.raises(EvaluationError):
            evaluator._to_number("not a number")

    def test_unknown_operator(self, evaluator, event_context):
        """Test unknown operator raises error."""
        with pytest.raises(EvaluationError):
            evaluator._eval({"unknown_op": [1, 2]}, event_context)
