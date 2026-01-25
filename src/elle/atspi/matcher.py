"""Fuzzy element matching for UI automation.

Provides multiple strategies for finding UI elements when
the expected path or name doesn't match exactly. Enables
self-healing automation that adapts to UI changes.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any, Callable

from elle.atspi.models import MatchMethod, MatchResult, UIElement

logger = logging.getLogger(__name__)


# =============================================================================
# String Similarity
# =============================================================================


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein (edit) distance between two strings.

    Args:
        s1: First string.
        s2: Second string.

    Returns:
        Number of edits needed to transform s1 into s2.
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)

    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def similarity_ratio(s1: str, s2: str) -> float:
    """Calculate similarity ratio between two strings.

    Uses Python's difflib SequenceMatcher for a balance between
    speed and quality.

    Args:
        s1: First string.
        s2: Second string.

    Returns:
        Similarity ratio between 0.0 and 1.0.
    """
    if not s1 or not s2:
        return 0.0
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def fuzzy_match(
    query: str,
    target: str,
    threshold: float = 0.6,
) -> bool:
    """Check if query fuzzy-matches target.

    Args:
        query: Search query.
        target: Target string to match against.
        threshold: Minimum similarity ratio.

    Returns:
        True if strings match within threshold.
    """
    if not query or not target:
        return False

    query_lower = query.lower()
    target_lower = target.lower()

    # Exact substring match
    if query_lower in target_lower or target_lower in query_lower:
        return True

    # Ratio match
    return similarity_ratio(query, target) >= threshold


def pattern_match(
    text: str,
    patterns: tuple[str, ...],
) -> bool:
    """Check if text matches any regex patterns.

    Args:
        text: Text to match.
        patterns: Regex patterns to try.

    Returns:
        True if any pattern matches.
    """
    for pattern in patterns:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


# =============================================================================
# Element Matcher
# =============================================================================


class ElementMatcher:
    """Finds UI elements using multiple matching strategies.

    Strategies are tried in order of reliability:
    1. Direct path - Follow exact index path
    2. Exact name - Match name exactly
    3. Fuzzy name - Levenshtein/ratio match
    4. Sibling context - Find by nearby elements
    5. Role search - Find all elements with role
    6. Tree search - Full tree traversal with LLM

    Usage:
        matcher = ElementMatcher(client)
        result = matcher.find(
            root=app_window,
            target_name="Bluetooth",
            target_role="toggle button",
            expected_path=(0, 2, 1, 3),
        )
    """

    def __init__(
        self,
        client: Any = None,
        similarity_threshold: float = 0.7,
    ) -> None:
        """Initialize the matcher.

        Args:
            client: ATSPIClient instance.
            similarity_threshold: Minimum fuzzy match similarity.
        """
        self._client = client
        self._similarity_threshold = similarity_threshold

    @property
    def client(self) -> Any:
        """Get the AT-SPI client."""
        if self._client is None:
            from elle.atspi.client import get_client

            self._client = get_client()
        return self._client

    def find(
        self,
        root: Any,
        target_name: str,
        target_role: str | None = None,
        expected_path: tuple[int, ...] | None = None,
        fallback_patterns: tuple[str, ...] = (),
        sibling_context: tuple[str, ...] = (),
        recipe_elements: tuple[UIElement, ...] = (),
    ) -> MatchResult:
        """Find a UI element using multiple strategies.

        Tries strategies in order of reliability, returning
        as soon as a match is found.

        Args:
            root: Root accessible to search from.
            target_name: Element name to find.
            target_role: Optional role filter.
            expected_path: Known path from recipe.
            fallback_patterns: Regex patterns for name variants.
            sibling_context: Adjacent element names.
            recipe_elements: Known elements from recipe.

        Returns:
            MatchResult with found element and match details.
        """
        # Strategy 1: Direct path
        if expected_path:
            result = self._try_direct_path(root, expected_path, target_name, target_role)
            if result.found:
                return result

        # Strategy 2: Exact name match
        result = self._try_exact_name(root, target_name, target_role)
        if result.found:
            return result

        # Strategy 3: Fuzzy name match
        result = self._try_fuzzy_name(root, target_name, target_role)
        if result.found:
            return result

        # Strategy 4: Pattern match
        if fallback_patterns:
            result = self._try_pattern_match(root, fallback_patterns, target_role)
            if result.found:
                return result

        # Strategy 5: Sibling context
        if sibling_context and recipe_elements:
            result = self._try_sibling_context(
                root, target_name, target_role, sibling_context, recipe_elements
            )
            if result.found:
                return result

        # Strategy 6: Role-based search
        if target_role:
            result = self._try_role_search(root, target_name, target_role)
            if result.found:
                return result

        # Strategy 7: Full tree search (expensive)
        result = self._try_tree_search(root, target_name, target_role)
        if result.found:
            return result

        # Not found
        return MatchResult(
            found=False,
            expected_path=expected_path,
            error=f"Element '{target_name}' not found after all strategies",
        )

    def _try_direct_path(
        self,
        root: Any,
        path: tuple[int, ...],
        name: str,
        role: str | None,
    ) -> MatchResult:
        """Try to find element at exact path.

        Args:
            root: Root accessible.
            path: Index path to follow.
            name: Expected element name.
            role: Expected element role.

        Returns:
            MatchResult.
        """
        try:
            element = self.client._follow_path(root, path)
            if element:
                elem_name = element.get_name() if hasattr(element, "get_name") else ""
                if elem_name is None and hasattr(element, "name"):
                    elem_name = element.name or ""

                # Check if it's still the right element
                if name.lower() in (elem_name or "").lower():
                    return MatchResult(
                        found=True,
                        element=self._accessible_to_element(element, path),
                        method="direct_path",
                        confidence=1.0,
                        expected_path=path,
                        actual_path=path,
                        candidates_count=1,
                    )

        except Exception as e:
            logger.debug(f"Direct path failed: {e}")

        return MatchResult(found=False, expected_path=path)

    def _try_exact_name(
        self,
        root: Any,
        name: str,
        role: str | None,
    ) -> MatchResult:
        """Try to find element with exact name match.

        Args:
            root: Root accessible.
            name: Element name.
            role: Optional role filter.

        Returns:
            MatchResult.
        """
        candidates = self.client.find_elements(root, name=name, role=role, max_results=5)

        for candidate in candidates:
            elem_name = candidate.get_name() if hasattr(candidate, "get_name") else ""
            if elem_name and elem_name.lower() == name.lower():
                path = self.client.get_element_path(candidate)
                return MatchResult(
                    found=True,
                    element=self._accessible_to_element(candidate, path),
                    method="exact_name",
                    confidence=0.95,
                    actual_path=path,
                    candidates_count=len(candidates),
                )

        return MatchResult(found=False, candidates_count=len(candidates))

    def _try_fuzzy_name(
        self,
        root: Any,
        name: str,
        role: str | None,
    ) -> MatchResult:
        """Try to find element with fuzzy name match.

        Args:
            root: Root accessible.
            name: Target element name.
            role: Optional role filter.

        Returns:
            MatchResult.
        """
        # Get all elements with the role (or all actionable elements)
        def has_name(accessible: Any) -> bool:
            elem_name = accessible.get_name() if hasattr(accessible, "get_name") else ""
            return bool(elem_name)

        candidates = self.client.find_elements(root, role=role, predicate=has_name, max_results=50)

        best_match = None
        best_score = 0.0
        best_path = None

        for candidate in candidates:
            elem_name = candidate.get_name() if hasattr(candidate, "get_name") else ""
            if not elem_name:
                continue

            score = similarity_ratio(name, elem_name)
            if score > best_score and score >= self._similarity_threshold:
                best_match = candidate
                best_score = score
                best_path = self.client.get_element_path(candidate)

        if best_match:
            return MatchResult(
                found=True,
                element=self._accessible_to_element(best_match, best_path),
                method="fuzzy_name",
                confidence=best_score,
                actual_path=best_path,
                candidates_count=len(candidates),
            )

        return MatchResult(found=False, candidates_count=len(candidates))

    def _try_pattern_match(
        self,
        root: Any,
        patterns: tuple[str, ...],
        role: str | None,
    ) -> MatchResult:
        """Try to find element matching regex patterns.

        Args:
            root: Root accessible.
            patterns: Regex patterns to match.
            role: Optional role filter.

        Returns:
            MatchResult.
        """

        def matches_pattern(accessible: Any) -> bool:
            elem_name = accessible.get_name() if hasattr(accessible, "get_name") else ""
            return bool(elem_name) and pattern_match(elem_name, patterns)

        candidates = self.client.find_elements(
            root, role=role, predicate=matches_pattern, max_results=10
        )

        if candidates:
            match = candidates[0]
            path = self.client.get_element_path(match)
            return MatchResult(
                found=True,
                element=self._accessible_to_element(match, path),
                method="fuzzy_name",  # Pattern match is a type of fuzzy
                confidence=0.8,
                actual_path=path,
                candidates_count=len(candidates),
            )

        return MatchResult(found=False)

    def _try_sibling_context(
        self,
        root: Any,
        name: str,
        role: str | None,
        sibling_names: tuple[str, ...],
        recipe_elements: tuple[UIElement, ...],
    ) -> MatchResult:
        """Try to find element by locating known siblings.

        Args:
            root: Root accessible.
            name: Target element name.
            role: Optional role filter.
            sibling_names: Names of adjacent elements.
            recipe_elements: Elements from recipe for context.

        Returns:
            MatchResult.
        """
        # Find siblings first
        for sibling_name in sibling_names[:3]:  # Limit search
            sibling = self.client.find_element(root, name=sibling_name)
            if sibling:
                # Get parent and search among siblings
                parent = sibling.get_parent() if hasattr(sibling, "get_parent") else None
                if parent:
                    candidates = self.client.find_elements(
                        parent, name=name, role=role, max_results=5
                    )
                    if candidates:
                        match = candidates[0]
                        path = self.client.get_element_path(match)
                        return MatchResult(
                            found=True,
                            element=self._accessible_to_element(match, path),
                            method="sibling_context",
                            confidence=0.85,
                            actual_path=path,
                            candidates_count=len(candidates),
                        )

        return MatchResult(found=False)

    def _try_role_search(
        self,
        root: Any,
        name: str,
        role: str,
    ) -> MatchResult:
        """Try to find element by role, then match by position.

        Args:
            root: Root accessible.
            name: Target element name.
            role: Element role.

        Returns:
            MatchResult.
        """
        # Get all elements with this role
        candidates = self.client.find_elements(root, role=role, max_results=20)

        # Try fuzzy match on each
        best_match = None
        best_score = 0.0
        best_path = None

        for candidate in candidates:
            elem_name = candidate.get_name() if hasattr(candidate, "get_name") else ""
            if not elem_name:
                continue

            score = similarity_ratio(name, elem_name)
            if score > best_score:
                best_match = candidate
                best_score = score
                best_path = self.client.get_element_path(candidate)

        if best_match and best_score >= 0.5:  # Lower threshold for role search
            return MatchResult(
                found=True,
                element=self._accessible_to_element(best_match, best_path),
                method="role_search",
                confidence=best_score * 0.9,  # Reduce confidence slightly
                actual_path=best_path,
                candidates_count=len(candidates),
            )

        return MatchResult(found=False, candidates_count=len(candidates))

    def _try_tree_search(
        self,
        root: Any,
        name: str,
        role: str | None,
    ) -> MatchResult:
        """Full tree search as last resort.

        Args:
            root: Root accessible.
            name: Target element name.
            role: Optional role filter.

        Returns:
            MatchResult.
        """
        # Get all visible elements
        elements = self.client.get_tree(root, max_depth=10, include_invisible=False)

        best_match = None
        best_score = 0.0
        best_element = None

        for elem in elements:
            # Skip if role doesn't match
            if role and role.lower() not in elem.role.lower():
                continue

            # Score by name similarity
            score = similarity_ratio(name, elem.name)
            if score > best_score:
                best_score = score
                best_element = elem

        if best_element and best_score >= 0.5:
            return MatchResult(
                found=True,
                element=best_element,
                method="tree_search",
                confidence=best_score * 0.8,  # Lower confidence for tree search
                actual_path=best_element.path,
                candidates_count=len(elements),
            )

        return MatchResult(found=False, candidates_count=len(elements))

    def _accessible_to_element(self, accessible: Any, path: tuple[int, ...]) -> UIElement:
        """Convert AT-SPI accessible to UIElement.

        Args:
            accessible: AT-SPI accessible object.
            path: Index path to element.

        Returns:
            UIElement.
        """
        from elle.atspi.client import _get_action_names, _get_role_name, _get_state_names

        name = accessible.get_name() if hasattr(accessible, "get_name") else ""
        if name is None and hasattr(accessible, "name"):
            name = accessible.name or ""

        role = accessible.get_role() if hasattr(accessible, "get_role") else None
        role_name = _get_role_name(role) if role else "unknown"

        description = accessible.get_description() if hasattr(accessible, "get_description") else None

        state_set = accessible.get_state_set() if hasattr(accessible, "get_state_set") else None
        states = _get_state_names(state_set) if state_set else ()

        actions = _get_action_names(accessible)

        return UIElement(
            role=role_name,
            name=name or "",
            description=description,
            states=states,
            actions=actions,
            path=path,
        )


# =============================================================================
# Adaptor
# =============================================================================


class UIAdaptor:
    """Self-healing adapter for UI changes.

    When elements aren't found at expected locations,
    this class attempts recovery using fuzzy matching
    and updates recipes with new paths.
    """

    def __init__(
        self,
        matcher: ElementMatcher | None = None,
    ) -> None:
        """Initialize the adaptor.

        Args:
            matcher: ElementMatcher instance.
        """
        self._matcher = matcher

    @property
    def matcher(self) -> ElementMatcher:
        """Get the element matcher."""
        if self._matcher is None:
            self._matcher = ElementMatcher()
        return self._matcher

    def adapt_and_find(
        self,
        root: Any,
        target_name: str,
        target_role: str | None = None,
        expected_path: tuple[int, ...] | None = None,
        fallback_patterns: tuple[str, ...] = (),
        sibling_context: tuple[str, ...] = (),
        recipe_elements: tuple[UIElement, ...] = (),
    ) -> tuple[MatchResult, str]:
        """Find element with adaptation and explanation.

        Args:
            root: Root accessible.
            target_name: Element name to find.
            target_role: Optional role filter.
            expected_path: Known path from recipe.
            fallback_patterns: Regex patterns for name.
            sibling_context: Adjacent element names.
            recipe_elements: Known elements from recipe.

        Returns:
            Tuple of (MatchResult, explanation string).
        """
        result = self.matcher.find(
            root=root,
            target_name=target_name,
            target_role=target_role,
            expected_path=expected_path,
            fallback_patterns=fallback_patterns,
            sibling_context=sibling_context,
            recipe_elements=recipe_elements,
        )

        explanation = self._generate_explanation(result, expected_path)
        return result, explanation

    def _generate_explanation(
        self,
        result: MatchResult,
        expected_path: tuple[int, ...] | None,
    ) -> str:
        """Generate user-friendly explanation of what happened.

        Args:
            result: Match result.
            expected_path: Original expected path.

        Returns:
            Explanation string.
        """
        if not result.found:
            return f"Could not find element after trying all strategies"

        method = result.method

        if method == "direct_path":
            return "Found at expected location"

        if method == "exact_name":
            if expected_path and result.actual_path != expected_path:
                return (
                    f"Element has moved from position {expected_path} "
                    f"to {result.actual_path}. Updated understanding."
                )
            return "Found by exact name match"

        if method == "fuzzy_name":
            confidence_pct = int(result.confidence * 100)
            if result.element:
                return (
                    f"Found similar element '{result.element.name}' "
                    f"({confidence_pct}% match)"
                )
            return f"Found via fuzzy name match ({confidence_pct}% confidence)"

        if method == "sibling_context":
            return "Found by locating nearby elements"

        if method == "role_search":
            if result.element:
                return (
                    f"Found '{result.element.name}' among all "
                    f"'{result.element.role}' elements"
                )
            return "Found by searching all elements with matching role"

        if method == "tree_search":
            return "Found via full tree search (UI may have changed significantly)"

        return "Element found"


# =============================================================================
# Module-level instances
# =============================================================================


_matcher: ElementMatcher | None = None
_adaptor: UIAdaptor | None = None


def get_matcher() -> ElementMatcher:
    """Get the shared ElementMatcher instance."""
    global _matcher
    if _matcher is None:
        _matcher = ElementMatcher()
    return _matcher


def get_adaptor() -> UIAdaptor:
    """Get the shared UIAdaptor instance."""
    global _adaptor
    if _adaptor is None:
        _adaptor = UIAdaptor()
    return _adaptor
