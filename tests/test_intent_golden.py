"""Golden test evaluation for intent classifier.

Runs the classifier against intent_cases.jsonl and reports accuracy metrics.
This test is designed to be run periodically to track classifier quality.

Usage:
    pytest tests/test_intent_golden.py -v
    pytest tests/test_intent_golden.py -v --tb=no  # Quiet mode, just show pass/fail
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from elle.cli.terminal.classifier import IntentClassifier
from elle.cli.terminal.intent import Intent
from elle.common.session import Session

# =============================================================================
# Test Case Loading
# =============================================================================


@dataclass
class TestCase:
    """A single test case from the golden set."""

    input: str
    expected_intent: str
    category: str
    needs_failed_cmd: bool = False
    should_flag: bool = False


@dataclass
class EvaluationResult:
    """Result of evaluating the classifier on the golden set."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    failures: list[tuple[TestCase, str]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        """Overall accuracy percentage."""
        if self.total == 0:
            return 0.0
        return (self.passed / self.total) * 100

    def add_result(self, case: TestCase, predicted: str, passed: bool) -> None:
        """Record a test result."""
        self.total += 1
        if passed:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append((case, predicted))

        # Track by category
        if case.category not in self.by_category:
            self.by_category[case.category] = {"total": 0, "passed": 0}
        self.by_category[case.category]["total"] += 1
        if passed:
            self.by_category[case.category]["passed"] += 1

    def summary(self) -> str:
        """Generate a summary report."""
        lines = [
            f"Overall: {self.passed}/{self.total} ({self.accuracy:.1f}%)",
            "",
            "By Category:",
        ]
        for category, stats in sorted(self.by_category.items()):
            cat_accuracy = (stats["passed"] / stats["total"]) * 100 if stats["total"] > 0 else 0
            lines.append(f"  {category}: {stats['passed']}/{stats['total']} ({cat_accuracy:.1f}%)")

        if self.failures:
            lines.extend(["", f"Failures ({len(self.failures)}):"])
            for case, predicted in self.failures[:10]:  # Show first 10
                lines.append(f"  '{case.input}' -> {predicted} (expected {case.expected_intent})")
            if len(self.failures) > 10:
                lines.append(f"  ... and {len(self.failures) - 10} more")

        return "\n".join(lines)


def load_test_cases() -> list[TestCase]:
    """Load test cases from intent_cases.jsonl."""
    cases_file = Path(__file__).parent / "intent_cases.jsonl"
    cases = []

    with open(cases_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            cases.append(
                TestCase(
                    input=data["input"],
                    expected_intent=data["expected_intent"],
                    category=data["category"],
                    needs_failed_cmd=data.get("needs_failed_cmd", False),
                    should_flag=data.get("should_flag", False),
                )
            )

    return cases


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def classifier() -> IntentClassifier:
    """Create classifier with mocked Ollama (for consistent testing)."""
    mock_client = MagicMock()
    mock_client.is_available.return_value = False
    return IntentClassifier(client=mock_client, log_path=Path("/tmp/elle-golden-test"))


@pytest.fixture
def default_session() -> Session:
    """Default session for most tests."""
    return Session(cwd=Path("/home/user"))


@pytest.fixture
def failed_session() -> Session:
    """Session with a failed command."""
    return Session(
        cwd=Path("/home/user"),
        last_cmd="git push",
        last_stderr="error: failed to push",
        last_exit=1,
    )


# =============================================================================
# Parametrized Tests (for individual case visibility)
# =============================================================================


# Generate test IDs from input strings (truncated for readability)
def generate_test_id(case: TestCase) -> str:
    """Generate a readable test ID."""
    input_short = case.input[:30].replace(" ", "_") if case.input else "empty"
    return f"{case.category}_{input_short}"


# Load all test cases
ALL_CASES = load_test_cases()


@pytest.mark.parametrize(
    "case",
    ALL_CASES,
    ids=[generate_test_id(c) for c in ALL_CASES],
)
def test_intent_classification(
    classifier: IntentClassifier,
    default_session: Session,
    failed_session: Session,
    case: TestCase,
) -> None:
    """Test individual intent classification cases."""
    # Use appropriate session
    session = failed_session if case.needs_failed_cmd else default_session

    # Classify the input
    result = classifier.classify(case.input, session)

    # Check the intent
    expected = Intent.from_label(case.expected_intent)

    # For dangerous commands, we check that it's flagged
    if case.should_flag:
        assert result.requires_clarification, (
            f"Dangerous command not flagged: '{case.input}' (confidence={result.confidence})"
        )
    else:
        # Normal intent check
        assert result.intent == expected, (
            f"Intent mismatch for '{case.input}': expected {expected.value}, got {result.intent.value}"
        )


# =============================================================================
# Aggregate Evaluation
# =============================================================================


def test_overall_accuracy(
    classifier: IntentClassifier,
    default_session: Session,
    failed_session: Session,
) -> None:
    """Test that overall accuracy meets threshold.

    This test runs all cases and reports aggregate statistics.
    """
    result = EvaluationResult()

    for case in ALL_CASES:
        session = failed_session if case.needs_failed_cmd else default_session
        classification = classifier.classify(case.input, session)
        expected = Intent.from_label(case.expected_intent)

        if case.should_flag:
            # For dangerous commands, check if flagged
            passed = classification.requires_clarification
        else:
            passed = classification.intent == expected

        result.add_result(case, classification.intent.value, passed)

    # Print summary (visible in pytest output with -v)
    print("\n" + result.summary())

    # Assert minimum accuracy threshold
    # Rule-based classification should be nearly perfect for hard routes
    assert result.accuracy >= 80.0, f"Accuracy too low: {result.accuracy:.1f}%"

    # Category-specific thresholds
    for category, stats in result.by_category.items():
        if stats["total"] > 0:
            cat_accuracy = (stats["passed"] / stats["total"]) * 100
            if category in ("hard_route", "prefix"):
                # Hard routes and prefixes should be near-perfect
                assert cat_accuracy >= 95.0, f"Category '{category}' accuracy too low: {cat_accuracy:.1f}%"
            elif category == "shell_command":
                # Shell command detection should be high
                assert cat_accuracy >= 85.0, f"Category '{category}' accuracy too low: {cat_accuracy:.1f}%"


# =============================================================================
# Category-Specific Tests
# =============================================================================


class TestHardRouteAccuracy:
    """Test accuracy on hard route cases specifically."""

    def test_meta_commands(
        self,
        classifier: IntentClassifier,
        default_session: Session,
    ) -> None:
        """All meta commands should match perfectly."""
        meta_cases = [c for c in ALL_CASES if c.category == "hard_route" and c.expected_intent == "meta"]

        for case in meta_cases:
            result = classifier.classify(case.input, default_session)
            assert result.intent == Intent.META, f"Failed for: {case.input}"
            assert result.classified_by == "rule", f"Should be rule-based: {case.input}"

    def test_navigation_commands(
        self,
        classifier: IntentClassifier,
        default_session: Session,
    ) -> None:
        """All navigation commands should match perfectly."""
        nav_cases = [c for c in ALL_CASES if c.category == "hard_route" and c.expected_intent == "navigation"]

        for case in nav_cases:
            result = classifier.classify(case.input, default_session)
            assert result.intent == Intent.NAVIGATION, f"Failed for: {case.input}"


class TestDangerousCommandDetection:
    """Test that dangerous commands are properly flagged."""

    def test_all_dangerous_flagged(
        self,
        classifier: IntentClassifier,
        default_session: Session,
    ) -> None:
        """All dangerous commands should require clarification."""
        dangerous_cases = [c for c in ALL_CASES if c.should_flag]

        for case in dangerous_cases:
            result = classifier.classify(case.input, default_session)
            assert result.requires_clarification, f"Dangerous command not flagged: '{case.input}'"


class TestPrefixCommands:
    """Test prefix command parsing accuracy."""

    def test_all_prefixes_work(
        self,
        classifier: IntentClassifier,
        default_session: Session,
    ) -> None:
        """All prefix commands should be correctly identified."""
        prefix_cases = [c for c in ALL_CASES if c.category == "prefix"]

        for case in prefix_cases:
            result = classifier.classify(case.input, default_session)
            expected = Intent.from_label(case.expected_intent)
            assert result.intent == expected, (
                f"Prefix mismatch: '{case.input}' -> {result.intent.value} (expected {expected.value})"
            )
            assert result.classified_by == "rule", f"Prefix should be rule-based: {case.input}"
