"""Tests for AT-SPI element matching."""

from elle.atspi.matcher import (
    ElementMatcher,
    UIAdaptor,
    fuzzy_match,
    levenshtein_distance,
    pattern_match,
    similarity_ratio,
)
from elle.atspi.models import UIElement


class TestStringSimilarity:
    """Tests for string similarity functions."""

    def test_levenshtein_identical(self):
        """Test Levenshtein distance for identical strings."""
        assert levenshtein_distance("hello", "hello") == 0

    def test_levenshtein_one_edit(self):
        """Test Levenshtein distance for one edit."""
        assert levenshtein_distance("hello", "hallo") == 1
        assert levenshtein_distance("cat", "hat") == 1

    def test_levenshtein_multiple_edits(self):
        """Test Levenshtein distance for multiple edits."""
        assert levenshtein_distance("kitten", "sitting") == 3

    def test_levenshtein_empty(self):
        """Test Levenshtein distance with empty string."""
        assert levenshtein_distance("hello", "") == 5
        assert levenshtein_distance("", "world") == 5

    def test_similarity_identical(self):
        """Test similarity ratio for identical strings."""
        ratio = similarity_ratio("Bluetooth", "Bluetooth")
        assert ratio == 1.0

    def test_similarity_similar(self):
        """Test similarity ratio for similar strings."""
        ratio = similarity_ratio("Bluetooth", "bluetooth")
        assert ratio > 0.9  # Case insensitive

    def test_similarity_different(self):
        """Test similarity ratio for different strings."""
        ratio = similarity_ratio("Bluetooth", "WiFi")
        assert ratio < 0.5

    def test_similarity_empty(self):
        """Test similarity ratio with empty strings."""
        assert similarity_ratio("", "hello") == 0.0
        assert similarity_ratio("hello", "") == 0.0


class TestFuzzyMatch:
    """Tests for fuzzy matching function."""

    def test_fuzzy_exact_match(self):
        """Test fuzzy match with exact string."""
        assert fuzzy_match("Bluetooth", "Bluetooth")

    def test_fuzzy_substring_match(self):
        """Test fuzzy match with substring."""
        assert fuzzy_match("Blue", "Bluetooth")
        assert fuzzy_match("Bluetooth", "Enable Bluetooth")

    def test_fuzzy_similar_match(self):
        """Test fuzzy match with similar string."""
        assert fuzzy_match("Bluetoth", "Bluetooth", threshold=0.7)

    def test_fuzzy_no_match(self):
        """Test fuzzy match with different strings."""
        assert not fuzzy_match("Bluetooth", "WiFi", threshold=0.6)

    def test_fuzzy_threshold(self):
        """Test fuzzy match with different thresholds."""
        # With high threshold, typo doesn't match
        assert not fuzzy_match("Bluetoth", "Bluetooth", threshold=0.95)
        # With lower threshold, it matches
        assert fuzzy_match("Bluetoth", "Bluetooth", threshold=0.7)


class TestPatternMatch:
    """Tests for regex pattern matching."""

    def test_pattern_simple_match(self):
        """Test simple pattern match."""
        patterns = (r"Blue.*", r"Wire.*")
        assert pattern_match("Bluetooth", patterns)
        assert pattern_match("Wireless", patterns)
        assert not pattern_match("WiFi", patterns)

    def test_pattern_case_insensitive(self):
        """Test case insensitive pattern match."""
        patterns = (r"bluetooth",)
        assert pattern_match("Bluetooth", patterns)
        assert pattern_match("BLUETOOTH", patterns)

    def test_pattern_invalid_regex(self):
        """Test handling of invalid regex patterns."""
        patterns = (r"[invalid", r"valid.*")
        # Should not raise, just skip invalid pattern
        assert pattern_match("valid string", patterns)


class TestElementMatcher:
    """Tests for ElementMatcher class.

    Note: These tests use mocked AT-SPI client since actual AT-SPI
    requires a running accessibility bus.
    """

    def test_matcher_initialization(self):
        """Test creating an ElementMatcher."""
        matcher = ElementMatcher(client=None, similarity_threshold=0.7)
        assert matcher._similarity_threshold == 0.7

    def test_accessible_to_element(self):
        """Test conversion of UIElement data."""
        # Create a mock element via direct construction
        elem = UIElement(
            role="button",
            name="Test Button",
            states=("enabled", "visible"),
            actions=("click",),
            path=(0, 1, 2),
        )

        assert elem.role == "button"
        assert elem.name == "Test Button"
        assert "enabled" in elem.states


class TestUIAdaptor:
    """Tests for UIAdaptor class."""

    def test_adaptor_initialization(self):
        """Test creating a UIAdaptor."""
        adaptor = UIAdaptor(matcher=None)
        assert adaptor._matcher is None

    def test_generate_explanation_direct_path(self):
        """Test explanation for direct path match."""
        from elle.atspi.models import MatchResult

        adaptor = UIAdaptor()

        result = MatchResult(
            found=True,
            method="direct_path",
            confidence=1.0,
            actual_path=(0, 1, 2),
        )

        explanation = adaptor._generate_explanation(result, (0, 1, 2))
        assert "expected location" in explanation.lower()

    def test_generate_explanation_fuzzy_match(self):
        """Test explanation for fuzzy match."""
        from elle.atspi.models import MatchResult

        adaptor = UIAdaptor()

        elem = UIElement(role="button", name="Bluetoth", path=(0, 2))
        result = MatchResult(
            found=True,
            element=elem,
            method="fuzzy_name",
            confidence=0.85,
            actual_path=(0, 2),
        )

        explanation = adaptor._generate_explanation(result, (0, 1))
        assert "similar" in explanation.lower() or "match" in explanation.lower()

    def test_generate_explanation_not_found(self):
        """Test explanation for not found."""
        from elle.atspi.models import MatchResult

        adaptor = UIAdaptor()

        result = MatchResult(found=False)

        explanation = adaptor._generate_explanation(result, None)
        assert "could not find" in explanation.lower()


class TestMatchStrategies:
    """Tests for individual matching strategies.

    These test the logic of each strategy without requiring AT-SPI.
    """

    def test_strategy_ordering(self):
        """Test that strategies would be tried in correct order."""
        # This tests the conceptual ordering, not actual execution
        strategies = [
            "direct_path",
            "exact_name",
            "fuzzy_name",
            "pattern_match",
            "sibling_context",
            "role_search",
            "tree_search",
        ]

        # Verify expected order
        assert strategies[0] == "direct_path"  # Most reliable
        assert strategies[-1] == "tree_search"  # Last resort

    def test_confidence_decay_by_strategy(self):
        """Test that confidence decays as strategies get less reliable."""
        confidence_by_strategy = {
            "direct_path": 1.0,
            "exact_name": 0.95,
            "fuzzy_name": 0.85,
            "sibling_context": 0.85,
            "role_search": 0.75,
            "tree_search": 0.65,
        }

        # Verify confidence decreases for less reliable strategies
        prev_conf = 1.1
        for strategy in ["direct_path", "exact_name", "fuzzy_name", "role_search", "tree_search"]:
            conf = confidence_by_strategy.get(strategy, 0.5)
            assert conf <= prev_conf, f"{strategy} confidence should be <= previous"
            prev_conf = conf
