from __future__ import annotations

"""Tests for policy file discovery and composition (loader.py)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.policy.exceptions import PolicyCircularExtendError, PolicyLoadError
from elle.policy.models import (
    Condition,
    MatchType,
    PolicyDocument,
    PolicyEffect,
    PolicyRule,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_rule(rule_id: str, priority: int = 0) -> PolicyRule:
    """Create a minimal PolicyRule for testing."""
    return PolicyRule(
        id=rule_id,
        name=f"Rule {rule_id}",
        conditions=(Condition(command="*", match_type=MatchType.GLOB),),
        effect=PolicyEffect.ALLOW,
        priority=priority,
    )


def _make_policy(
    name: str = "Test",
    rules: tuple[PolicyRule, ...] = (),
    extends: tuple[str, ...] = (),
    default_effect: PolicyEffect = PolicyEffect.ALLOW,
    version: str = "1.0",
) -> PolicyDocument:
    """Create a minimal PolicyDocument for testing."""
    return PolicyDocument(
        version=version,
        name=name,
        rules=rules,
        extends=extends,
        default_effect=default_effect,
    )


_DEFAULT_POLICY = _make_policy(
    name="ELLE System Defaults",
    rules=(_make_rule("default-rule-1", priority=100),),
)


# =============================================================================
# PolicyLoader.__init__
# =============================================================================


class TestPolicyLoaderInit:
    """Tests for PolicyLoader constructor."""

    @patch("elle.policy.loader.SYSTEM_POLICY_PATH", Path("/etc/elle/policy.yaml"))
    @patch("elle.policy.loader.USER_POLICY_PATH", Path("/home/user/.config/elle/policy.yaml"))
    def test_default_paths(self):
        """Loader uses standard paths when no overrides given."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader()
        assert loader._system_path == Path("/etc/elle/policy.yaml")
        assert loader._user_path == Path("/home/user/.config/elle/policy.yaml")
        assert loader._cache == {}

    def test_custom_paths(self):
        """Loader respects explicit path overrides."""
        from elle.policy.loader import PolicyLoader

        sys_path = Path("/custom/system.yaml")
        usr_path = Path("/custom/user.yaml")
        loader = PolicyLoader(system_path=sys_path, user_path=usr_path)

        assert loader._system_path == sys_path
        assert loader._user_path == usr_path

    def test_only_system_path_override(self):
        """Only overriding system_path leaves user_path at default."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader(system_path=Path("/custom/sys.yaml"))
        assert loader._system_path == Path("/custom/sys.yaml")
        # user_path should remain the default (Path.home() / ...)
        assert loader._user_path is not None

    def test_only_user_path_override(self):
        """Only overriding user_path leaves system_path at default."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader(user_path=Path("/custom/user.yaml"))
        assert loader._user_path == Path("/custom/user.yaml")
        assert loader._system_path is not None


# =============================================================================
# PolicyLoader.load  -- discovery order
# =============================================================================


class TestPolicyLoaderLoad:
    """Tests for PolicyLoader.load method (policy discovery)."""

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    @patch("elle.policy.loader.parse_file")
    def test_user_policy_preferred(self, mock_parse, mock_defaults):
        """User policy is loaded when both user and system exist."""
        from elle.policy.loader import PolicyLoader

        user_path = MagicMock(spec=Path)
        user_path.exists.return_value = True
        user_path.__str__ = lambda _: "/home/user/.config/elle/policy.yaml"

        sys_path = MagicMock(spec=Path)
        sys_path.exists.return_value = True
        sys_path.__str__ = lambda _: "/etc/elle/policy.yaml"

        user_policy = _make_policy(name="User Policy")
        mock_parse.return_value = user_policy

        loader = PolicyLoader(system_path=sys_path, user_path=user_path)
        result = loader.load()

        assert result.name == "User Policy"
        mock_parse.assert_called_once()

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    @patch("elle.policy.loader.parse_file")
    def test_system_policy_when_no_user(self, mock_parse, mock_defaults):
        """System policy is loaded when user policy does not exist."""
        from elle.policy.loader import PolicyLoader

        user_path = MagicMock(spec=Path)
        user_path.exists.return_value = False

        sys_path = MagicMock(spec=Path)
        sys_path.exists.return_value = True
        sys_path.__str__ = lambda _: "/etc/elle/policy.yaml"

        sys_policy = _make_policy(name="System Policy")
        mock_parse.return_value = sys_policy

        loader = PolicyLoader(system_path=sys_path, user_path=user_path)
        result = loader.load()

        assert result.name == "System Policy"

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_default_policy_when_no_files(self, mock_defaults):
        """Default policy returned when neither user nor system file exists."""
        from elle.policy.loader import PolicyLoader

        user_path = MagicMock(spec=Path)
        user_path.exists.return_value = False

        sys_path = MagicMock(spec=Path)
        sys_path.exists.return_value = False

        loader = PolicyLoader(system_path=sys_path, user_path=user_path)
        result = loader.load()

        assert result is _DEFAULT_POLICY
        mock_defaults.assert_called()

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    @patch("elle.policy.loader.parse_file")
    def test_cache_cleared_on_load(self, mock_parse, mock_defaults):
        """Cache is cleared at the start of every load() call."""
        from elle.policy.loader import PolicyLoader

        user_path = MagicMock(spec=Path)
        user_path.exists.return_value = False
        sys_path = MagicMock(spec=Path)
        sys_path.exists.return_value = False

        loader = PolicyLoader(system_path=sys_path, user_path=user_path)
        loader._cache["stale"] = _make_policy(name="Stale")

        loader.load()

        assert "stale" not in loader._cache


# =============================================================================
# PolicyLoader.load_file
# =============================================================================


class TestPolicyLoaderLoadFile:
    """Tests for PolicyLoader.load_file method."""

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_load_specific_file(self, mock_defaults, mock_parse):
        """load_file delegates to _load_with_extends with correct args."""
        from elle.policy.loader import PolicyLoader

        my_policy = _make_policy(name="My File Policy")
        mock_parse.return_value = my_policy

        loader = PolicyLoader()
        result = loader.load_file(Path("/tmp/custom.yaml"))

        assert result.name == "My File Policy"

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_load_file_accepts_string(self, mock_defaults, mock_parse):
        """load_file also accepts a plain string path."""
        from elle.policy.loader import PolicyLoader

        mock_parse.return_value = _make_policy(name="String Path")
        loader = PolicyLoader()
        result = loader.load_file("/tmp/custom.yaml")

        assert result.name == "String Path"

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_load_file_clears_cache(self, mock_defaults, mock_parse):
        """Cache is cleared at the start of load_file()."""
        from elle.policy.loader import PolicyLoader

        mock_parse.return_value = _make_policy(name="Fresh")

        loader = PolicyLoader()
        loader._cache["old"] = _make_policy(name="Old")
        loader.load_file("/tmp/fresh.yaml")

        assert "old" not in loader._cache


# =============================================================================
# PolicyLoader._load_with_extends  -- cycle detection
# =============================================================================


class TestLoadWithExtendsCycles:
    """Tests for circular extends detection."""

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_circular_extends_detected(self, mock_defaults, mock_parse):
        """Circular extends raises PolicyCircularExtendError."""
        from elle.policy.loader import PolicyLoader

        # a.yaml extends b.yaml, b.yaml extends a.yaml
        policy_a = _make_policy(name="A", extends=("/tmp/b.yaml",))
        policy_b = _make_policy(name="B", extends=("/tmp/a.yaml",))

        def fake_parse(path):
            if str(path) == "/tmp/a.yaml":
                return policy_a
            if str(path) == "/tmp/b.yaml":
                return policy_b
            raise FileNotFoundError(path)

        mock_parse.side_effect = fake_parse

        loader = PolicyLoader()
        with pytest.raises(PolicyCircularExtendError) as exc_info:
            loader.load_file("/tmp/a.yaml")

        assert "/tmp/a.yaml" in exc_info.value.chain

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_self_referencing_extends(self, mock_defaults, mock_parse):
        """A file that extends itself raises a cycle error."""
        from elle.policy.loader import PolicyLoader

        policy = _make_policy(name="Self", extends=("/tmp/self.yaml",))
        mock_parse.return_value = policy

        loader = PolicyLoader()
        with pytest.raises(PolicyCircularExtendError):
            loader.load_file("/tmp/self.yaml")


# =============================================================================
# PolicyLoader._load_with_extends  -- caching
# =============================================================================


class TestLoadWithExtendsCache:
    """Tests for caching in _load_with_extends."""

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_cache_hit_returns_cached(self, mock_defaults, mock_parse):
        """If a policy is already cached, return it without re-parsing."""
        from elle.policy.loader import PolicyLoader

        cached_policy = _make_policy(name="Cached")

        loader = PolicyLoader()
        loader._cache["/tmp/cached.yaml"] = cached_policy

        # Call _load_with_extends directly; cache should be found.
        result = loader._load_with_extends("/tmp/cached.yaml", set())

        assert result is cached_policy
        mock_parse.assert_not_called()

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_result_stored_in_cache(self, mock_defaults, mock_parse):
        """Loaded policies are stored in cache for subsequent lookups."""
        from elle.policy.loader import PolicyLoader

        mock_parse.return_value = _make_policy(name="New")

        loader = PolicyLoader()
        result = loader._load_with_extends("/tmp/new.yaml", set())

        assert "/tmp/new.yaml" in loader._cache
        assert loader._cache["/tmp/new.yaml"] is result


# =============================================================================
# PolicyLoader._load_with_extends  -- extends resolution
# =============================================================================


class TestLoadWithExtendsResolution:
    """Tests for parent policy resolution via extends."""

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_single_parent_extends(self, mock_defaults, mock_parse):
        """Policy extending a single parent merges rules correctly."""
        from elle.policy.loader import PolicyLoader

        parent_rule = _make_rule("parent-r1", priority=10)
        child_rule = _make_rule("child-r1", priority=20)

        parent = _make_policy(name="Parent", rules=(parent_rule,))
        child = _make_policy(name="Child", rules=(child_rule,), extends=("/tmp/parent.yaml",))

        def fake_parse(path):
            if str(path) == "/tmp/child.yaml":
                return child
            if str(path) == "/tmp/parent.yaml":
                return parent
            raise FileNotFoundError(path)

        mock_parse.side_effect = fake_parse

        loader = PolicyLoader()
        result = loader.load_file("/tmp/child.yaml")

        rule_ids = {r.id for r in result.rules}
        assert "parent-r1" in rule_ids
        assert "child-r1" in rule_ids
        # Child metadata should be used
        assert result.name == "Child"

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_multiple_parent_extends(self, mock_defaults, mock_parse):
        """Policy extending multiple parents merges all their rules."""
        from elle.policy.loader import PolicyLoader

        p1_rule = _make_rule("p1-rule")
        p2_rule = _make_rule("p2-rule")
        child_rule = _make_rule("child-rule")

        parent1 = _make_policy(name="P1", rules=(p1_rule,))
        parent2 = _make_policy(name="P2", rules=(p2_rule,))
        child = _make_policy(
            name="Child",
            rules=(child_rule,),
            extends=("/tmp/p1.yaml", "/tmp/p2.yaml"),
        )

        def fake_parse(path):
            mapping = {
                "/tmp/child.yaml": child,
                "/tmp/p1.yaml": parent1,
                "/tmp/p2.yaml": parent2,
            }
            p = str(path)
            if p in mapping:
                return mapping[p]
            raise FileNotFoundError(path)

        mock_parse.side_effect = fake_parse

        loader = PolicyLoader()
        result = loader.load_file("/tmp/child.yaml")

        rule_ids = {r.id for r in result.rules}
        assert "p1-rule" in rule_ids
        assert "p2-rule" in rule_ids
        assert "child-rule" in rule_ids

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_extends_builtin_reference(self, mock_defaults, mock_parse):
        """A policy extending 'system:defaults' inherits default rules."""
        from elle.policy.loader import PolicyLoader

        child_rule = _make_rule("child-only", priority=50)
        child = _make_policy(
            name="Extends Defaults",
            rules=(child_rule,),
            extends=("system:defaults",),
        )
        mock_parse.return_value = child

        loader = PolicyLoader()
        result = loader.load_file("/tmp/child.yaml")

        rule_ids = {r.id for r in result.rules}
        assert "child-only" in rule_ids
        # Default rule from the mock default policy should be present
        assert "default-rule-1" in rule_ids

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_no_extends(self, mock_defaults, mock_parse):
        """Policy with no extends is returned as-is."""
        from elle.policy.loader import PolicyLoader

        policy = _make_policy(name="Standalone", rules=(_make_rule("solo"),))
        mock_parse.return_value = policy

        loader = PolicyLoader()
        result = loader.load_file("/tmp/standalone.yaml")

        assert result.name == "Standalone"
        assert len(result.rules) == 1


# =============================================================================
# PolicyLoader._load_single
# =============================================================================


class TestLoadSingle:
    """Tests for _load_single (single-file loading without extends)."""

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_builtin_ref_returns_defaults(self, mock_defaults):
        """'system:defaults' reference returns the built-in default policy."""
        from elle.policy.loader import BUILTIN_POLICY_REF, PolicyLoader

        loader = PolicyLoader()
        result = loader._load_single(BUILTIN_POLICY_REF)

        assert result is _DEFAULT_POLICY
        mock_defaults.assert_called_once()

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_unknown_system_ref_returns_defaults(self, mock_defaults):
        """Unknown system: reference logs warning and returns defaults."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader()
        result = loader._load_single("system:unknown_ref")

        assert result is _DEFAULT_POLICY

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_absolute_path_parsed(self, mock_defaults, mock_parse):
        """Absolute file path is passed directly to parse_file."""
        from elle.policy.loader import PolicyLoader

        expected = _make_policy(name="Abs")
        mock_parse.return_value = expected

        loader = PolicyLoader()
        result = loader._load_single("/etc/elle/custom.yaml")

        assert result is expected
        mock_parse.assert_called_once_with(Path("/etc/elle/custom.yaml"))

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_relative_path_resolved_to_user_config_dir(self, mock_defaults, mock_parse):
        """Relative path is resolved relative to user config directory."""
        from elle.policy.loader import PolicyLoader

        user_config = Path("/home/testuser/.config/elle/policy.yaml")
        expected = _make_policy(name="Relative")
        mock_parse.return_value = expected

        loader = PolicyLoader(user_path=user_config)
        result = loader._load_single("overrides.yaml")

        # Should resolve to parent of user_path / "overrides.yaml"
        expected_path = Path("/home/testuser/.config/elle") / "overrides.yaml"
        mock_parse.assert_called_once_with(expected_path)
        assert result is expected

    @patch("elle.policy.loader.parse_file", side_effect=FileNotFoundError("not found"))
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_parse_failure_raises_policy_load_error(self, mock_defaults, mock_parse):
        """Parse errors are wrapped in PolicyLoadError."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader()
        with pytest.raises(PolicyLoadError) as exc_info:
            loader._load_single("/tmp/missing.yaml")

        assert "missing.yaml" in str(exc_info.value)

    @patch("elle.policy.loader.parse_file", side_effect=PermissionError("denied"))
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_permission_error_wrapped(self, mock_defaults, mock_parse):
        """PermissionError from parse_file is wrapped in PolicyLoadError."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader()
        with pytest.raises(PolicyLoadError):
            loader._load_single("/etc/elle/restricted.yaml")


# =============================================================================
# PolicyLoader._merge_policies
# =============================================================================


class TestMergePolicies:
    """Tests for _merge_policies."""

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_empty_list_returns_defaults(self, mock_defaults):
        """Merging an empty list returns the default policy."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader()
        result = loader._merge_policies([])

        assert result is _DEFAULT_POLICY

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_single_policy_returned_unchanged(self, mock_defaults):
        """Merging a single policy returns it unchanged."""
        from elle.policy.loader import PolicyLoader

        p = _make_policy(name="Only One", rules=(_make_rule("r1"),))
        loader = PolicyLoader()
        result = loader._merge_policies([p])

        assert result is p

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_two_policies_merged(self, mock_defaults):
        """Two policies have their rules combined."""
        from elle.policy.loader import PolicyLoader

        p1 = _make_policy(name="P1", rules=(_make_rule("r1"),))
        p2 = _make_policy(name="P2", rules=(_make_rule("r2"),))

        loader = PolicyLoader()
        result = loader._merge_policies([p1, p2])

        rule_ids = {r.id for r in result.rules}
        assert "r1" in rule_ids
        assert "r2" in rule_ids

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_merged_name_reflects_constituents(self, mock_defaults):
        """Merged policy name mentions source policy names."""
        from elle.policy.loader import PolicyLoader

        p1 = _make_policy(name="Alpha")
        p2 = _make_policy(name="Beta")

        loader = PolicyLoader()
        result = loader._merge_policies([p1, p2])

        assert "Alpha" in result.name
        assert "Beta" in result.name

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_merged_uses_last_policy_metadata(self, mock_defaults):
        """Merged policy uses version and default_effect from last policy."""
        from elle.policy.loader import PolicyLoader

        p1 = _make_policy(name="P1", version="1.0", default_effect=PolicyEffect.ALLOW)
        p2 = _make_policy(name="P2", version="2.0", default_effect=PolicyEffect.DENY)

        loader = PolicyLoader()
        result = loader._merge_policies([p1, p2])

        assert result.version == "2.0"
        assert result.default_effect == PolicyEffect.DENY

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_merged_extends_cleared(self, mock_defaults):
        """Merged result has empty extends (already resolved)."""
        from elle.policy.loader import PolicyLoader

        p1 = _make_policy(name="P1", extends=("system:defaults",))
        p2 = _make_policy(name="P2", extends=("other.yaml",))

        loader = PolicyLoader()
        result = loader._merge_policies([p1, p2])

        assert result.extends == ()

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_three_policies_merged(self, mock_defaults):
        """Three policies are correctly combined."""
        from elle.policy.loader import PolicyLoader

        p1 = _make_policy(name="P1", rules=(_make_rule("r1"),))
        p2 = _make_policy(name="P2", rules=(_make_rule("r2"),))
        p3 = _make_policy(name="P3", rules=(_make_rule("r3"),))

        loader = PolicyLoader()
        result = loader._merge_policies([p1, p2, p3])

        rule_ids = {r.id for r in result.rules}
        assert rule_ids == {"r1", "r2", "r3"}


# =============================================================================
# PolicyLoader._apply_child_to_parent
# =============================================================================


class TestApplyChildToParent:
    """Tests for _apply_child_to_parent."""

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_child_overrides_parent_rule(self, mock_defaults):
        """Child rule with same ID replaces parent rule."""
        from elle.policy.loader import PolicyLoader

        parent_rule = PolicyRule(
            id="shared",
            name="Parent Shared",
            conditions=(Condition(command="*", match_type=MatchType.GLOB),),
            effect=PolicyEffect.DENY,
            priority=10,
        )
        child_rule = PolicyRule(
            id="shared",
            name="Child Shared",
            conditions=(Condition(command="ls *", match_type=MatchType.GLOB),),
            effect=PolicyEffect.ALLOW,
            priority=50,
        )

        parent = _make_policy(name="Parent", rules=(parent_rule,))
        child = _make_policy(name="Child", rules=(child_rule,))

        loader = PolicyLoader()
        result = loader._apply_child_to_parent(parent, child)

        # Only one rule with id "shared", and it should be the child version
        shared_rules = [r for r in result.rules if r.id == "shared"]
        assert len(shared_rules) == 1
        assert shared_rules[0].name == "Child Shared"
        assert shared_rules[0].effect == PolicyEffect.ALLOW

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_child_adds_new_rules(self, mock_defaults):
        """New child rules are added alongside parent rules."""
        from elle.policy.loader import PolicyLoader

        parent = _make_policy(name="Parent", rules=(_make_rule("p-rule"),))
        child = _make_policy(name="Child", rules=(_make_rule("c-rule"),))

        loader = PolicyLoader()
        result = loader._apply_child_to_parent(parent, child)

        rule_ids = {r.id for r in result.rules}
        assert "p-rule" in rule_ids
        assert "c-rule" in rule_ids

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_rules_sorted_by_priority_descending(self, mock_defaults):
        """Combined rules are sorted by priority, highest first."""
        from elle.policy.loader import PolicyLoader

        p_low = _make_rule("low", priority=10)
        c_high = _make_rule("high", priority=100)
        p_mid = _make_rule("mid", priority=50)

        parent = _make_policy(name="Parent", rules=(p_low, p_mid))
        child = _make_policy(name="Child", rules=(c_high,))

        loader = PolicyLoader()
        result = loader._apply_child_to_parent(parent, child)

        priorities = [r.priority for r in result.rules]
        assert priorities == sorted(priorities, reverse=True)

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_child_metadata_used(self, mock_defaults):
        """Result uses child's version, name, and default_effect."""
        from elle.policy.loader import PolicyLoader

        parent = _make_policy(
            name="Parent",
            version="1.0",
            default_effect=PolicyEffect.ALLOW,
        )
        child = _make_policy(
            name="Child",
            version="2.0",
            default_effect=PolicyEffect.DENY,
        )

        loader = PolicyLoader()
        result = loader._apply_child_to_parent(parent, child)

        assert result.name == "Child"
        assert result.version == "2.0"
        assert result.default_effect == PolicyEffect.DENY

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_extends_cleared_after_apply(self, mock_defaults):
        """Resulting document has empty extends (resolved)."""
        from elle.policy.loader import PolicyLoader

        parent = _make_policy(name="P", extends=("system:defaults",))
        child = _make_policy(name="C", extends=("parent.yaml",))

        loader = PolicyLoader()
        result = loader._apply_child_to_parent(parent, child)

        assert result.extends == ()

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_empty_parent_rules(self, mock_defaults):
        """Child applied to parent with no rules produces child rules only."""
        from elle.policy.loader import PolicyLoader

        parent = _make_policy(name="Empty Parent", rules=())
        child = _make_policy(name="Child", rules=(_make_rule("c1"),))

        loader = PolicyLoader()
        result = loader._apply_child_to_parent(parent, child)

        assert len(result.rules) == 1
        assert result.rules[0].id == "c1"

    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_empty_child_rules(self, mock_defaults):
        """Child with no rules inherits all parent rules."""
        from elle.policy.loader import PolicyLoader

        parent = _make_policy(name="Parent", rules=(_make_rule("p1"), _make_rule("p2")))
        child = _make_policy(name="Child", rules=())

        loader = PolicyLoader()
        result = loader._apply_child_to_parent(parent, child)

        rule_ids = {r.id for r in result.rules}
        assert rule_ids == {"p1", "p2"}


# =============================================================================
# Module-level functions: get_loader, load_policy, load_policy_file
# =============================================================================


class TestModuleLevelFunctions:
    """Tests for the module-level convenience functions."""

    @patch("elle.policy.loader._loader", None)
    def test_get_loader_creates_singleton(self):
        """get_loader creates a new PolicyLoader when none exists."""
        from elle.policy.loader import get_loader

        loader = get_loader()
        assert loader is not None

        # Second call returns the same instance
        from elle.policy import loader as loader_mod

        assert loader_mod._loader is loader

    @patch("elle.policy.loader._loader", None)
    def test_get_loader_returns_same_instance(self):
        """Consecutive get_loader calls return the same object."""
        from elle.policy.loader import get_loader

        first = get_loader()
        second = get_loader()
        assert first is second

    @patch("elle.policy.loader.get_loader")
    def test_load_policy_delegates(self, mock_get_loader):
        """load_policy() delegates to get_loader().load()."""
        from elle.policy.loader import load_policy

        mock_loader = MagicMock()
        expected = _make_policy(name="Delegated")
        mock_loader.load.return_value = expected
        mock_get_loader.return_value = mock_loader

        result = load_policy()

        mock_loader.load.assert_called_once()
        assert result is expected

    @patch("elle.policy.loader.get_loader")
    def test_load_policy_file_delegates(self, mock_get_loader):
        """load_policy_file() delegates to get_loader().load_file()."""
        from elle.policy.loader import load_policy_file

        mock_loader = MagicMock()
        expected = _make_policy(name="File Delegated")
        mock_loader.load_file.return_value = expected
        mock_get_loader.return_value = mock_loader

        result = load_policy_file("/tmp/test.yaml")

        mock_loader.load_file.assert_called_once_with("/tmp/test.yaml")
        assert result is expected

    @patch("elle.policy.loader.get_loader")
    def test_load_policy_file_accepts_path_object(self, mock_get_loader):
        """load_policy_file() works with Path objects."""
        from elle.policy.loader import load_policy_file

        mock_loader = MagicMock()
        mock_loader.load_file.return_value = _make_policy(name="Path Obj")
        mock_get_loader.return_value = mock_loader

        path = Path("/tmp/test.yaml")
        load_policy_file(path)

        mock_loader.load_file.assert_called_once_with(path)


# =============================================================================
# Integration-style tests with multi-level extends
# =============================================================================


class TestMultiLevelExtends:
    """Tests for deep inheritance chains (grandparent -> parent -> child)."""

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_three_level_extends_chain(self, mock_defaults, mock_parse):
        """Three-level inheritance chain merges all rules."""
        from elle.policy.loader import PolicyLoader

        gp_rule = _make_rule("gp-rule", priority=5)
        p_rule = _make_rule("p-rule", priority=10)
        c_rule = _make_rule("c-rule", priority=20)

        grandparent = _make_policy(name="GP", rules=(gp_rule,))
        parent = _make_policy(name="P", rules=(p_rule,), extends=("/tmp/gp.yaml",))
        child = _make_policy(name="C", rules=(c_rule,), extends=("/tmp/p.yaml",))

        def fake_parse(path):
            mapping = {
                "/tmp/gp.yaml": grandparent,
                "/tmp/p.yaml": parent,
                "/tmp/c.yaml": child,
            }
            p = str(path)
            if p in mapping:
                return mapping[p]
            raise FileNotFoundError(path)

        mock_parse.side_effect = fake_parse

        loader = PolicyLoader()
        result = loader.load_file("/tmp/c.yaml")

        rule_ids = {r.id for r in result.rules}
        assert "gp-rule" in rule_ids
        assert "p-rule" in rule_ids
        assert "c-rule" in rule_ids
        assert result.name == "C"

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_diamond_extends_detected_as_cycle(self, mock_defaults, mock_parse):
        """Diamond dependency (A extends B+C, B extends D, C extends D) works
        without cycle error because D is not revisited in the same branch."""
        from elle.policy.loader import PolicyLoader

        d = _make_policy(name="D", rules=(_make_rule("d-r"),))
        b = _make_policy(name="B", rules=(_make_rule("b-r"),), extends=("/tmp/d.yaml",))
        c = _make_policy(name="C", rules=(_make_rule("c-r"),), extends=("/tmp/d.yaml",))
        a = _make_policy(
            name="A",
            rules=(_make_rule("a-r"),),
            extends=("/tmp/b.yaml", "/tmp/c.yaml"),
        )

        def fake_parse(path):
            mapping = {
                "/tmp/a.yaml": a,
                "/tmp/b.yaml": b,
                "/tmp/c.yaml": c,
                "/tmp/d.yaml": d,
            }
            p = str(path)
            if p in mapping:
                return mapping[p]
            raise FileNotFoundError(path)

        mock_parse.side_effect = fake_parse

        loader = PolicyLoader()
        # Diamond should work - D is cached after first load via B
        result = loader.load_file("/tmp/a.yaml")

        rule_ids = {r.id for r in result.rules}
        assert "a-r" in rule_ids
        assert "b-r" in rule_ids
        assert "c-r" in rule_ids
        assert "d-r" in rule_ids


# =============================================================================
# Edge cases and error paths
# =============================================================================


class TestEdgeCases:
    """Edge cases and error paths."""

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_extends_with_system_defaults_ref(self, mock_defaults, mock_parse):
        """Extends with 'system:defaults' correctly resolves builtin."""
        from elle.policy.loader import PolicyLoader

        child = _make_policy(
            name="Extends Defaults",
            rules=(_make_rule("cr"),),
            extends=("system:defaults",),
        )
        mock_parse.return_value = child

        loader = PolicyLoader()
        result = loader.load_file("/tmp/child.yaml")

        assert "cr" in {r.id for r in result.rules}
        assert "default-rule-1" in {r.id for r in result.rules}

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_extends_with_unknown_system_ref(self, mock_defaults, mock_parse):
        """Extends with an unknown system: ref falls back to defaults."""
        from elle.policy.loader import PolicyLoader

        child = _make_policy(
            name="Unknown Ref",
            rules=(_make_rule("cr"),),
            extends=("system:nonexistent",),
        )
        mock_parse.return_value = child

        loader = PolicyLoader()
        result = loader.load_file("/tmp/child.yaml")

        # Should still work; unknown ref yields default policy
        rule_ids = {r.id for r in result.rules}
        assert "cr" in rule_ids

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_policy_with_empty_extends_tuple(self, mock_defaults, mock_parse):
        """Policy with an explicit empty extends tuple skips extends."""
        from elle.policy.loader import PolicyLoader

        policy = _make_policy(name="No Extends", extends=(), rules=(_make_rule("r1"),))
        mock_parse.return_value = policy

        loader = PolicyLoader()
        result = loader.load_file("/tmp/no-extends.yaml")

        assert len(result.rules) == 1
        assert result.rules[0].id == "r1"

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_load_propagates_policy_load_error(self, mock_defaults, mock_parse):
        """PolicyLoadError from nested extends propagates correctly."""
        from elle.policy.loader import PolicyLoader

        child = _make_policy(
            name="Child",
            extends=("/tmp/missing_parent.yaml",),
        )

        call_count = 0

        def fake_parse(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return child
            raise FileNotFoundError("not found")

        mock_parse.side_effect = fake_parse

        loader = PolicyLoader()
        with pytest.raises(PolicyLoadError):
            loader.load_file("/tmp/child.yaml")

    @patch("elle.policy.loader.parse_file")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_child_override_preserves_other_parent_rules(self, mock_defaults, mock_parse):
        """When child overrides one parent rule, other parent rules remain."""
        from elle.policy.loader import PolicyLoader

        pr1 = _make_rule("keep-me", priority=10)
        pr2 = PolicyRule(
            id="override-me",
            name="Parent Version",
            conditions=(Condition(command="old", match_type=MatchType.EXACT),),
            effect=PolicyEffect.DENY,
            priority=20,
        )
        cr = PolicyRule(
            id="override-me",
            name="Child Version",
            conditions=(Condition(command="new", match_type=MatchType.EXACT),),
            effect=PolicyEffect.ALLOW,
            priority=30,
        )

        parent = _make_policy(name="Parent", rules=(pr1, pr2))
        child = _make_policy(name="Child", rules=(cr,), extends=("/tmp/parent.yaml",))

        def fake_parse(path):
            if str(path) == "/tmp/child.yaml":
                return child
            if str(path) == "/tmp/parent.yaml":
                return parent
            raise FileNotFoundError(path)

        mock_parse.side_effect = fake_parse

        loader = PolicyLoader()
        result = loader.load_file("/tmp/child.yaml")

        rule_map = {r.id: r for r in result.rules}
        assert "keep-me" in rule_map
        assert rule_map["override-me"].name == "Child Version"
        assert rule_map["override-me"].effect == PolicyEffect.ALLOW


# =============================================================================
# Logging coverage
# =============================================================================


class TestLogging:
    """Verify that debug/warning log calls are exercised."""

    @patch("elle.policy.loader.logger")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    @patch("elle.policy.loader.parse_file")
    def test_user_path_logs_debug(self, mock_parse, mock_defaults, mock_logger):
        """Loading user policy produces a debug log message."""
        from elle.policy.loader import PolicyLoader

        mock_parse.return_value = _make_policy(name="User")
        user_path = MagicMock(spec=Path)
        user_path.exists.return_value = True
        user_path.__str__ = lambda _: "/home/u/.config/elle/policy.yaml"

        sys_path = MagicMock(spec=Path)
        sys_path.exists.return_value = False

        loader = PolicyLoader(system_path=sys_path, user_path=user_path)
        loader.load()

        mock_logger.debug.assert_called()

    @patch("elle.policy.loader.logger")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    @patch("elle.policy.loader.parse_file")
    def test_system_path_logs_debug(self, mock_parse, mock_defaults, mock_logger):
        """Loading system policy produces a debug log message."""
        from elle.policy.loader import PolicyLoader

        mock_parse.return_value = _make_policy(name="Sys")
        user_path = MagicMock(spec=Path)
        user_path.exists.return_value = False

        sys_path = MagicMock(spec=Path)
        sys_path.exists.return_value = True
        sys_path.__str__ = lambda _: "/etc/elle/policy.yaml"

        loader = PolicyLoader(system_path=sys_path, user_path=user_path)
        loader.load()

        mock_logger.debug.assert_called()

    @patch("elle.policy.loader.logger")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_no_files_logs_debug(self, mock_defaults, mock_logger):
        """Using defaults logs a debug message about no custom policy."""
        from elle.policy.loader import PolicyLoader

        user_path = MagicMock(spec=Path)
        user_path.exists.return_value = False
        sys_path = MagicMock(spec=Path)
        sys_path.exists.return_value = False

        loader = PolicyLoader(system_path=sys_path, user_path=user_path)
        loader.load()

        mock_logger.debug.assert_called()

    @patch("elle.policy.loader.logger")
    @patch("elle.policy.loader.get_default_policy", return_value=_DEFAULT_POLICY)
    def test_unknown_system_ref_logs_warning(self, mock_defaults, mock_logger):
        """Unknown system: reference produces a warning log."""
        from elle.policy.loader import PolicyLoader

        loader = PolicyLoader()
        loader._load_single("system:unknown")

        mock_logger.warning.assert_called()
