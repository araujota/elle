"""Pattern matching utilities for policy conditions.

Provides glob, regex, exact, and prefix matching for commands,
paths, and domain patterns.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime, time
from functools import lru_cache

from elle.policy.models import Condition, MatchType, PolicyEvaluationRequest, TimeWindow

# =============================================================================
# Pattern Matching Functions
# =============================================================================


def match_exact(pattern: str, value: str) -> bool:
    """Match exactly (case-sensitive).

    Args:
        pattern: The pattern to match.
        value: The value to test.

    Returns:
        True if pattern equals value exactly.
    """
    return pattern == value


def match_exact_ci(pattern: str, value: str) -> bool:
    """Match exactly (case-insensitive).

    Args:
        pattern: The pattern to match.
        value: The value to test.

    Returns:
        True if pattern equals value (case-insensitive).
    """
    return pattern.lower() == value.lower()


def match_glob(pattern: str, value: str) -> bool:
    """Match using glob/fnmatch patterns.

    Supports wildcards:
    - * matches any sequence of characters (except /)
    - ** matches any sequence including /
    - ? matches any single character
    - [abc] matches any character in set

    Args:
        pattern: Glob pattern (e.g., "/etc/**/*.conf").
        value: Value to test.

    Returns:
        True if value matches the glob pattern.
    """
    # Handle ** for recursive matching
    if "**" in pattern:
        # Convert ** to regex-style matching
        regex_pattern = _glob_to_regex(pattern)
        return bool(re.match(regex_pattern, value))
    else:
        return fnmatch.fnmatch(value, pattern)


@lru_cache(maxsize=256)
def _glob_to_regex(pattern: str) -> str:
    """Convert a glob pattern with ** to regex.

    Args:
        pattern: Glob pattern.

    Returns:
        Equivalent regex pattern.
    """
    # Escape special regex characters except glob wildcards
    regex = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # ** matches anything including / (zero or more path components)
                # Check if followed by /
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    # **/ -> optional path components: (.*/)? or (.*/)?
                    regex += "(.*/|)"
                    i += 3
                    continue
                else:
                    # ** at end or before non-/ -> match everything
                    regex += ".*"
                    i += 2
                    continue
            else:
                # * matches anything except /
                regex += "[^/]*"
        elif c == "?":
            regex += "[^/]"
        elif c == "[":
            # Character class - find closing ]
            j = i + 1
            while j < len(pattern) and pattern[j] != "]":
                j += 1
            regex += pattern[i : j + 1]
            i = j
        elif c in ".^$+{}|()":
            regex += "\\" + c
        else:
            regex += c
        i += 1
    return "^" + regex + "$"


@lru_cache(maxsize=256)
def _compile_regex(pattern: str) -> re.Pattern | None:
    """Compile a regex pattern with caching.

    Args:
        pattern: Regex pattern string.

    Returns:
        Compiled pattern or None if invalid.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def match_regex(pattern: str, value: str) -> bool:
    """Match using regular expression.

    Args:
        pattern: Regex pattern.
        value: Value to test.

    Returns:
        True if value matches the regex (search, not full match).
    """
    compiled = _compile_regex(pattern)
    if compiled is None:
        return False
    return bool(compiled.search(value))


def match_prefix(pattern: str, value: str) -> bool:
    """Match if value starts with pattern.

    Args:
        pattern: Prefix pattern.
        value: Value to test.

    Returns:
        True if value starts with pattern.
    """
    return value.startswith(pattern)


def match_pattern(pattern: str, value: str, match_type: MatchType) -> bool:
    """Match a pattern against a value using the specified match type.

    Args:
        pattern: The pattern to match.
        value: The value to test.
        match_type: Type of matching to use.

    Returns:
        True if value matches pattern.
    """
    match match_type:
        case MatchType.EXACT:
            return match_exact(pattern, value)
        case MatchType.GLOB:
            return match_glob(pattern, value)
        case MatchType.REGEX:
            return match_regex(pattern, value)
        case MatchType.PREFIX:
            return match_prefix(pattern, value)
        case _:
            return False


# =============================================================================
# Domain Matching
# =============================================================================


def match_domain(pattern: str, domain: str) -> bool:
    """Match a domain pattern against a domain string.

    Domain patterns use dot notation with glob wildcards:
    - "network.*" matches "network.interface", "network.route"
    - "*.config" matches "system.config", "user.config"
    - "service.**" matches "service.systemd.unit", "service.nginx"

    Args:
        pattern: Domain pattern (e.g., "network.*").
        domain: Domain string to test.

    Returns:
        True if domain matches pattern.
    """
    # Handle ** (recursive matching) first
    if "**" in pattern:
        # Convert to regex: ** matches one or more parts, * matches one part
        # First replace ** with a placeholder to avoid double replacement
        regex = pattern.replace("**", "\x00DOUBLESTAR\x00")
        # Escape dots
        regex = regex.replace(".", r"\.")
        # Replace single * with pattern for one domain part
        regex = regex.replace("*", r"[^.]+")
        # Replace ** placeholder with pattern for one or more parts
        regex = regex.replace("\x00DOUBLESTAR\x00", r".+")
        regex = "^" + regex + "$"
        return bool(re.match(regex, domain))

    # Simple matching without **
    parts_pattern = pattern.split(".")
    parts_domain = domain.split(".")

    if len(parts_pattern) != len(parts_domain):
        return False

    for p, d in zip(parts_pattern, parts_domain, strict=False):
        if p == "*":
            continue  # Single * matches any single part
        elif p != d:
            return False

    return True


# =============================================================================
# Time Window Matching
# =============================================================================


def match_time_window(
    windows: tuple[TimeWindow, ...],
    check_time: time | None = None,
) -> bool:
    """Check if current time falls within any time window.

    Args:
        windows: Time windows to check.
        check_time: Time to check (defaults to current time).

    Returns:
        True if current time is within any window.
    """
    if not windows:
        return True  # No windows means always matches

    if check_time is None:
        check_time = datetime.now().time()

    return any(window.contains(check_time) for window in windows)


# =============================================================================
# Condition Matching
# =============================================================================


def match_condition(
    condition: Condition,
    request: PolicyEvaluationRequest,
) -> bool:
    """Evaluate a single condition against a request.

    Args:
        condition: The condition to evaluate.
        request: The evaluation request with context.

    Returns:
        True if condition matches (accounting for negate flag).
    """
    result = _match_condition_inner(condition, request)

    # Apply negation if specified
    if condition.negate:
        return not result

    return result


def _match_condition_inner(
    condition: Condition,
    request: PolicyEvaluationRequest,
) -> bool:
    """Inner condition matching without negation handling.

    All specified conditions must match (implicit AND within a condition).
    """
    # Command matching
    if condition.command is not None:
        if request.command is None:
            return False
        if not match_pattern(condition.command, request.command, condition.match_type):
            return False

    # Path matching
    if condition.path is not None:
        if request.path is None:
            return False
        if not match_pattern(condition.path, request.path, condition.match_type):
            return False

    # Domain matching
    if condition.domain is not None:
        if request.domain is None:
            return False
        if not match_domain(condition.domain, request.domain):
            return False

    # Intent matching
    if condition.intent is not None:
        if request.intent is None:
            return False
        if not match_pattern(condition.intent, request.intent, condition.match_type):
            return False

    # Risk level matching
    if condition.risk_level is not None:
        if request.risk_level is None:
            return False
        # Match if request risk is >= condition risk
        risk_order = {"low": 0, "medium": 1, "high": 2}
        if risk_order.get(request.risk_level.value, 0) < risk_order.get(condition.risk_level.value, 0):
            return False

    # Privilege matching
    if condition.requires_privilege is not None:
        if request.requires_privilege != condition.requires_privilege:
            return False

    # Time window matching
    if condition.allowed_hours is not None:
        check_time = request.timestamp.time() if request.timestamp else None
        if not match_time_window(condition.allowed_hours, check_time):
            return False

    # All conditions matched
    return True
