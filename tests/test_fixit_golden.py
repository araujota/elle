"""Golden tests for fixit error category detection.

Tests the fallback pattern matching against known error cases.
"""

import json
from pathlib import Path

import pytest

from elle.cli.fixit.fallback import detect_error_category


def load_test_cases():
    """Load test cases from fixit_cases.jsonl."""
    cases_path = Path(__file__).parent / "fixit_cases.jsonl"
    cases = []

    with open(cases_path) as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))

    return cases


TEST_CASES = load_test_cases()


class TestGoldenCases:
    """Test error category detection against golden cases."""

    @pytest.mark.parametrize(
        "case",
        TEST_CASES,
        ids=[c["description"] for c in TEST_CASES],
    )
    def test_error_category(self, case):
        """Test that error category is correctly detected."""
        stderr = case["stderr"]
        expected = case["expected_category"]

        detected_category, _ = detect_error_category(stderr)

        # Allow some flexibility for edge cases
        # "auth" errors may be detected as "permission_denied" since they're related
        equivalent_categories = {
            "auth": {"auth", "permission_denied"},
            "permission_denied": {"permission_denied", "auth"},
        }

        acceptable = equivalent_categories.get(expected, {expected})

        assert detected_category in acceptable, (
            f"Expected {expected} (or equivalent) for: {case['description']}\n"
            f"Stderr: {stderr}\n"
            f"Got: {detected_category}"
        )


class TestSpecificCases:
    """Test specific error patterns in detail."""

    def test_command_not_found_pattern(self):
        """Test command not found patterns."""
        test_cases = [
            "bash: foo: command not found",
            "foo: command not found",
            "/bin/sh: 1: foo: not found",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "command_not_found", f"Failed for: {stderr}"

    def test_permission_denied_pattern(self):
        """Test permission denied patterns."""
        test_cases = [
            "Permission denied",
            "permission denied",
            "EACCES: permission denied",
            "Access denied",
            "Operation not permitted",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "permission_denied", f"Failed for: {stderr}"

    def test_file_not_found_pattern(self):
        """Test file not found patterns."""
        test_cases = [
            "No such file or directory",
            "cannot stat 'foo': No such file or directory",
            "File does not exist",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "file_not_found", f"Failed for: {stderr}"

    def test_network_error_pattern(self):
        """Test network error patterns."""
        test_cases = [
            "Connection refused",
            "Connection timed out",
            "Network is unreachable",
            "Name or service not known",
            "Could not resolve host",
            "Temporary failure in name resolution",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "network_error", f"Failed for: {stderr}"

    def test_dependency_missing_pattern(self):
        """Test dependency missing patterns."""
        test_cases = [
            "E: Unable to locate package foo",
            "Package 'foo' is not available",
            "unmet dependencies",
            "Depends: libfoo but it is not installable",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "dependency_missing", f"Failed for: {stderr}"

    def test_resource_exhausted_pattern(self):
        """Test resource exhausted patterns."""
        test_cases = [
            "No space left on device",
            "Cannot allocate memory",
            "Too many open files",
            "Disk quota exceeded",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "resource_exhausted", f"Failed for: {stderr}"

    def test_syntax_error_pattern(self):
        """Test syntax error patterns."""
        test_cases = [
            "syntax error near unexpected token",
            "parse error",
            "Syntax error:",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "syntax_error", f"Failed for: {stderr}"

    def test_argument_error_pattern(self):
        """Test argument error patterns."""
        test_cases = [
            "invalid option -- 'x'",
            "unrecognized option '--foo'",
            "missing argument for -o",
            "option requires an argument",
            "too many arguments",
        ]

        for stderr in test_cases:
            category, _ = detect_error_category(stderr)
            assert category == "argument_error", f"Failed for: {stderr}"

    def test_other_category_fallback(self):
        """Test that unknown errors fall back to 'other'."""
        stderr = "Some completely random error message with no patterns"
        category, _ = detect_error_category(stderr)
        assert category == "other"


class TestExplanations:
    """Test that explanations are provided."""

    def test_explanation_not_empty(self):
        """Test that explanations are provided for all categories."""
        test_cases = [
            ("command not found", "command_not_found"),
            ("Permission denied", "permission_denied"),
            ("No such file or directory", "file_not_found"),
            ("Connection refused", "network_error"),
            ("Unable to locate package", "dependency_missing"),
            ("No space left on device", "resource_exhausted"),
            ("syntax error", "syntax_error"),
            ("invalid option", "argument_error"),
        ]

        for stderr, expected_cat in test_cases:
            category, explanation = detect_error_category(stderr)
            assert category == expected_cat
            assert explanation is not None
            assert len(explanation) > 10  # Meaningful explanation
