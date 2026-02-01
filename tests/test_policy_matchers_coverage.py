"""Additional coverage tests for elle.policy.matchers.

Targets uncovered lines beyond what test_policy_matchers.py covers.
Focuses on:
- _glob_to_regex special regex chars escaping (line 114: chars in .^$+{}|())
- match_domain with ** combined with escaped dots (line 216-222)
- match_domain part mismatch with wildcard (line 234)
"""

from __future__ import annotations

from elle.policy.matchers import (
    _glob_to_regex,
    match_domain,
    match_glob,
)


class TestGlobToRegexSpecialChars:
    """Tests for _glob_to_regex escaping special regex chars (line 113-114)."""

    def test_dot_escaped(self):
        """Dots in glob patterns should be escaped to literal dots."""
        # Clear cache to ensure fresh run
        _glob_to_regex.cache_clear()
        regex = _glob_to_regex("/**/*.conf.bak")
        assert r"\." in regex

    def test_caret_escaped(self):
        """Caret in glob patterns should be escaped."""
        _glob_to_regex.cache_clear()
        regex = _glob_to_regex("/**/file^name")
        assert r"\^" in regex

    def test_dollar_escaped(self):
        """Dollar in glob patterns should be escaped."""
        _glob_to_regex.cache_clear()
        regex = _glob_to_regex("/**/file$end")
        assert r"\$" in regex

    def test_plus_escaped(self):
        """Plus in glob patterns should be escaped."""
        _glob_to_regex.cache_clear()
        regex = _glob_to_regex("/**/c++/**")
        assert r"\+" in regex

    def test_braces_escaped(self):
        """Braces in glob patterns should be escaped."""
        _glob_to_regex.cache_clear()
        regex = _glob_to_regex("/**/file{1}")
        assert r"\{" in regex
        assert r"\}" in regex

    def test_pipe_escaped(self):
        """Pipe in glob patterns should be escaped."""
        _glob_to_regex.cache_clear()
        regex = _glob_to_regex("/**/a|b")
        assert r"\|" in regex

    def test_parens_escaped(self):
        """Parentheses in glob patterns should be escaped."""
        _glob_to_regex.cache_clear()
        regex = _glob_to_regex("/**/func()")
        assert r"\(" in regex
        assert r"\)" in regex

    def test_glob_with_special_chars_matches(self):
        """Glob patterns with special chars should match correctly."""
        assert match_glob("/**/file.conf", "/etc/file.conf") is True
        assert match_glob("/**/file.conf", "/etc/fileXconf") is False


class TestDomainMatchingDoubleStarEdgeCases:
    """Tests for domain matching with ** patterns."""

    def test_doublestar_middle_of_pattern(self):
        """** in middle of domain pattern should match multiple parts."""
        assert match_domain("a.**.z", "a.b.c.z") is True
        assert match_domain("a.**.z", "a.z") is False  # ** needs at least one part

    def test_domain_wildcard_no_match(self):
        """Single * wildcard should not match different part (line 234)."""
        assert match_domain("network.*", "service.systemd") is False
        assert match_domain("*.config", "x.service") is False

    def test_domain_exact_parts_with_wildcard(self):
        """Wildcard should match any single part but not extra."""
        assert match_domain("a.*.c", "a.b.c") is True
        assert match_domain("a.*.c", "a.b.d") is False
