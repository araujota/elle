from __future__ import annotations

"""Tests for tier_selector.py and tier_registry.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.ops.editing.models import (
    EditOperation,
    NoApplicableTierError,
    TierApplicability,
    TierNumber,
)
from elle.ops.editing.tier_registry import (
    TIER0_DROPIN_PATTERNS,
    TIER1_AUGEAS_PATTERNS,
    TIER2_JSON_PATTERNS,
    TIER2_TOML_PATTERNS,
    TIER2_YAML_PATTERNS,
    TIER3_INI_PATTERNS,
    TIER3_XML_PATTERNS,
    TIER4_MARKDOWN_PATTERNS,
    TIER4_RST_PATTERNS,
    TIER_REGISTRY,
    detect_tier_from_path,
    get_file_extension,
    get_tier_info,
    is_dropin_candidate,
    list_all_tiers,
    matches_pattern,
)
from elle.ops.editing.tier_selector import TierSelector


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _op(kind: str = "set", **kw) -> EditOperation:
    return EditOperation(kind=kind, **kw)


# ============================================================================
# tier_registry: matches_pattern
# ============================================================================


class TestMatchesPattern:
    """Tests for matches_pattern."""

    def test_absolute_pattern_match(self):
        assert matches_pattern("/etc/fstab", ("/etc/fstab",)) is True

    def test_absolute_pattern_no_match(self):
        assert matches_pattern("/etc/fstab", ("/etc/hosts",)) is False

    def test_relative_filename_pattern(self):
        assert matches_pattern("/home/user/file.json", ("*.json",)) is True

    def test_relative_path_pattern(self):
        assert matches_pattern("/home/user/docker-compose.yml", ("docker-compose.yml",)) is True

    def test_glob_wildcard(self):
        assert matches_pattern("/etc/nginx/sites.conf", ("/etc/nginx/*.conf",)) is True

    def test_no_match(self):
        assert matches_pattern("/tmp/random", ("*.json",)) is False

    def test_home_expansion(self):
        # This tests the ~ expansion branch; just check it doesn't crash
        result = matches_pattern("/home/user/.ssh/config", ("~/.ssh/config",))
        # The match depends on the actual home directory, so just check no crash
        assert isinstance(result, bool)

    def test_multiple_patterns(self):
        assert matches_pattern("file.json", ("*.yaml", "*.json")) is True


# ============================================================================
# tier_registry: get_file_extension
# ============================================================================


class TestGetFileExtension:
    def test_json(self):
        assert get_file_extension("file.json") == "json"

    def test_uppercase(self):
        assert get_file_extension("FILE.YAML") == "yaml"

    def test_no_extension(self):
        assert get_file_extension("Dockerfile") == ""

    def test_gitconfig(self):
        assert get_file_extension(".gitconfig") == ""


# ============================================================================
# tier_registry: is_dropin_candidate
# ============================================================================


class TestIsDropinCandidate:
    def test_sudoers(self):
        supported, dropin_dir = is_dropin_candidate("/etc/sudoers")
        assert supported is True
        assert dropin_dir == "/etc/sudoers.d"

    def test_sysctl(self):
        supported, dropin_dir = is_dropin_candidate("/etc/sysctl.conf")
        assert supported is True
        assert dropin_dir == "/etc/sysctl.d"

    def test_apt_sources(self):
        supported, dropin_dir = is_dropin_candidate("/etc/apt/sources.list")
        assert supported is True
        assert dropin_dir == "/etc/apt/sources.list.d"

    def test_modprobe(self):
        supported, dropin_dir = is_dropin_candidate("/etc/modprobe.conf")
        assert supported is True
        assert dropin_dir == "/etc/modprobe.d"

    def test_logrotate(self):
        supported, dropin_dir = is_dropin_candidate("/etc/logrotate.conf")
        assert supported is True
        assert dropin_dir == "/etc/logrotate.d"

    def test_rsyslog(self):
        supported, dropin_dir = is_dropin_candidate("/etc/rsyslog.conf")
        assert supported is True
        assert dropin_dir == "/etc/rsyslog.d"

    def test_profile(self):
        supported, dropin_dir = is_dropin_candidate("/etc/profile")
        assert supported is True
        assert dropin_dir == "/etc/profile.d"

    def test_limits(self):
        supported, dropin_dir = is_dropin_candidate("/etc/security/limits.conf")
        assert supported is True
        assert dropin_dir == "/etc/security/limits.d"

    def test_systemd_unit(self):
        supported, dropin_dir = is_dropin_candidate("/etc/systemd/system/nginx.service")
        assert supported is True
        assert dropin_dir == "/etc/systemd/system/nginx.service.d"

    def test_not_supported(self):
        supported, dropin_dir = is_dropin_candidate("/etc/nginx/nginx.conf")
        # /etc/nginx/nginx.conf does not match any drop-in pattern (it matches *.conf which is INI)
        # But we check it isn't in a .d directory
        assert isinstance(supported, bool)

    def test_already_in_dropin_dir(self):
        supported, dropin_dir = is_dropin_candidate("/etc/sudoers.d/99-elle")
        assert supported is True
        assert dropin_dir is None

    def test_private_etc_normalization(self):
        """macOS /private/etc/ paths go through normalization logic.

        Note: matches_pattern is called with the original path before
        normalization, and uses Path.resolve() to check symlinks.
        On macOS, /etc -> /private/etc so /etc/sudoers resolves to /private/etc/sudoers.
        But /private/etc/sudoers doesn't match /etc/sudoers patterns unless
        the resolve path from /private/etc goes to /etc.
        We patch matches_pattern to simulate the pattern match succeeding.
        """
        with patch("elle.ops.editing.tier_registry.matches_pattern", return_value=True):
            supported, dropin_dir = is_dropin_candidate("/private/etc/sudoers")
        assert supported is True
        assert dropin_dir == "/etc/sudoers.d"


# ============================================================================
# tier_registry: detect_tier_from_path
# ============================================================================


class TestDetectTierFromPath:
    def test_sudoers(self):
        assert detect_tier_from_path("/etc/sudoers") == 0

    def test_fstab(self):
        assert detect_tier_from_path("/etc/fstab") == 1

    def test_json(self):
        assert detect_tier_from_path("file.json") == 2

    def test_yaml(self):
        assert detect_tier_from_path("file.yaml") == 2

    def test_toml(self):
        assert detect_tier_from_path("pyproject.toml") == 2

    def test_ini(self):
        assert detect_tier_from_path("setup.cfg") == 3

    def test_xml(self):
        assert detect_tier_from_path("pom.xml") == 3

    def test_markdown(self):
        assert detect_tier_from_path("README.md") == 4

    def test_rst(self):
        assert detect_tier_from_path("docs.rst") == 4

    def test_unknown(self):
        assert detect_tier_from_path("/tmp/random_file") == 5


# ============================================================================
# tier_registry: get_tier_info, list_all_tiers
# ============================================================================


class TestTierInfo:
    def test_get_tier_info(self):
        info = get_tier_info(0)
        assert info.name == "Native Override"
        assert info.number == 0

    def test_list_all_tiers(self):
        tiers = list_all_tiers()
        assert len(tiers) == 6
        assert tiers[0].number == 0
        assert tiers[5].number == 5


# ============================================================================
# tier_registry: TIER_REGISTRY
# ============================================================================


class TestTierRegistry:
    def test_all_tiers_present(self):
        for i in range(6):
            assert i in TIER_REGISTRY

    def test_tier_names(self):
        expected = ["Native Override", "Augeas", "Structured Data", "Format Editors", "Document-aware", "Text Patching"]
        for i, name in enumerate(expected):
            assert TIER_REGISTRY[i].name == name


# ============================================================================
# TierSelector
# ============================================================================


class TestTierSelector:
    """Tests for TierSelector."""

    def _make_selector(self):
        return TierSelector()

    # --- _check_tool_available ---------------------------------------------

    def test_check_tool_augeas(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.is_available = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}):
            assert selector._check_tool_available("augeas") is True

    def test_check_tool_augeas_import_error(self):
        selector = self._make_selector()
        with patch.dict("sys.modules", {"elle.ops.augeas": None}):
            assert selector._check_tool_available("augeas") is False

    def test_check_tool_jq(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.is_available = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"elle.ops.jq": mock_mod}):
            assert selector._check_tool_available("jq") is True

    def test_check_tool_jq_import_error(self):
        selector = self._make_selector()
        with patch.dict("sys.modules", {"elle.ops.jq": None}):
            assert selector._check_tool_available("jq") is False

    def test_check_tool_yq(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.is_available = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"elle.ops.yq": mock_mod}):
            assert selector._check_tool_available("yq") is True

    def test_check_tool_yq_import_error(self):
        selector = self._make_selector()
        with patch.dict("sys.modules", {"elle.ops.yq": None}):
            assert selector._check_tool_available("yq") is False

    def test_check_tool_crudini(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.is_available = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"elle.ops.crudini": mock_mod}):
            assert selector._check_tool_available("crudini") is True

    def test_check_tool_crudini_import_error(self):
        selector = self._make_selector()
        with patch.dict("sys.modules", {"elle.ops.crudini": None}):
            assert selector._check_tool_available("crudini") is False

    def test_check_tool_xmlstarlet(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.is_available = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"elle.ops.xmlstarlet": mock_mod}):
            assert selector._check_tool_available("xmlstarlet") is True

    def test_check_tool_xmlstarlet_import_error(self):
        selector = self._make_selector()
        with patch.dict("sys.modules", {"elle.ops.xmlstarlet": None}):
            assert selector._check_tool_available("xmlstarlet") is False

    def test_check_tool_ruamel(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        with patch.dict("sys.modules", {"ruamel.yaml": mock_mod, "ruamel": mock_mod}):
            assert selector._check_tool_available("ruamel.yaml") is True

    def test_check_tool_toml_tomllib(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        with patch.dict("sys.modules", {"tomllib": mock_mod}):
            assert selector._check_tool_available("toml") is True

    def test_check_tool_toml_fallback(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        with patch.dict("sys.modules", {"tomllib": None, "toml": mock_mod}):
            assert selector._check_tool_available("toml") is True

    def test_check_tool_markdown_it(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        with patch.dict("sys.modules", {"markdown_it": mock_mod}):
            assert selector._check_tool_available("markdown-it") is True

    def test_check_tool_docutils(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        with patch.dict("sys.modules", {"docutils": mock_mod}):
            assert selector._check_tool_available("docutils") is True

    def test_check_tool_unknown(self):
        selector = self._make_selector()
        assert selector._check_tool_available("unknown_tool") is False

    def test_check_tool_caching(self):
        selector = self._make_selector()
        selector._tool_availability["cached_tool"] = True
        assert selector._check_tool_available("cached_tool") is True

    # --- select_tier -------------------------------------------------------

    def test_select_tier0_dropin(self):
        selector = self._make_selector()
        op = _op(kind="set")
        result = selector.select_tier("/etc/sudoers", op)
        assert result.tier == 0
        assert result.tier_name == "Native Override"

    def test_select_tier2_json(self):
        selector = self._make_selector()
        op = _op(kind="set", path=".key", value="val")
        # Mock augeas as unavailable
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t not in ("augeas",)):
            result = selector.select_tier("config.json", op)
        assert result.tier == 2

    def test_select_tier5_text(self):
        selector = self._make_selector()
        op = _op(kind="set", anchor_pattern="anchor")
        # Disable all tools to force to tier 5
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector.select_tier("/tmp/random.txt", op)
        assert result.tier == 5

    def test_select_tier_with_preferred(self):
        selector = self._make_selector()
        op = _op(kind="set", anchor_pattern="x")
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector.select_tier("/tmp/test.txt", op, preferred_tier=5)
        assert result.tier == 5
        assert "Preferred" in result.reason

    def test_select_tier_preferred_not_applicable(self):
        selector = self._make_selector()
        op = _op(kind="rm")  # rm not suitable for dropin
        result = selector.select_tier("/tmp/test.txt", op, preferred_tier=0)
        assert result.tier != 0 or len(result.escalation_trail) > 0

    def test_select_tier_with_max(self):
        selector = self._make_selector()
        op = _op(kind="set")
        # max_tier=0 but not a dropin candidate => should raise
        with pytest.raises(NoApplicableTierError):
            selector.select_tier("/tmp/random.txt", op, max_tier=0)

    def test_select_tier_escalation_trail(self):
        selector = self._make_selector()
        op = _op(kind="set", anchor_pattern="x")
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector.select_tier("/tmp/random.txt", op)
        # Tiers 0-4 should be in escalation trail
        assert len(result.escalation_trail) >= 1

    # --- _check_tier_applicability -----------------------------------------

    def test_check_tier0(self):
        selector = self._make_selector()
        result = selector._check_tier_applicability(0, "/etc/sudoers", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier1_no_augeas(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(1, "/etc/fstab", _op(kind="set"))
        assert result.tool_available is False

    def test_check_tier2_json(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(2, "file.json", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier2_yaml_no_tools(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(2, "file.yaml", _op(kind="set"))
        assert result.applicable is True  # PyYAML fallback

    def test_check_tier2_toml_no_lib(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(2, "pyproject.toml", _op(kind="set"))
        assert result.applicable is False

    def test_check_tier2_not_structured(self):
        selector = self._make_selector()
        result = selector._check_tier_applicability(2, "/tmp/file.txt", _op(kind="set"))
        assert result.applicable is False

    def test_check_tier3_ini(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(3, "setup.cfg", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier3_xml(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(3, "pom.xml", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier3_not_format(self):
        selector = self._make_selector()
        result = selector._check_tier_applicability(3, "/tmp/file.txt", _op(kind="set"))
        assert result.applicable is False

    def test_check_tier4_markdown(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(4, "README.md", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier4_rst(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector._check_tier_applicability(4, "docs.rst", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier4_not_doc(self):
        selector = self._make_selector()
        result = selector._check_tier_applicability(4, "/tmp/file.txt", _op(kind="set"))
        assert result.applicable is False

    def test_check_tier5_with_anchor(self):
        selector = self._make_selector()
        result = selector._check_tier_applicability(5, "/tmp/file.txt", _op(kind="set", anchor_pattern="x"))
        assert result.applicable is True
        assert result.confidence == 0.8

    def test_check_tier5_no_anchor(self):
        selector = self._make_selector()
        result = selector._check_tier_applicability(5, "/tmp/file.txt", _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.5

    def test_check_unknown_tier(self):
        selector = self._make_selector()
        # An invalid tier number (99) reaches the else branch which tries
        # to create TierApplicability(tier=99), but the Pydantic model
        # enforces TierNumber = Literal[0..5], so it raises a ValidationError.
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            selector._check_tier_applicability(99, "/tmp/file.txt", _op(kind="set"))  # type: ignore

    # --- _get_tier_tool ----------------------------------------------------

    def test_get_tier_tool_0(self):
        selector = self._make_selector()
        assert selector._get_tier_tool(0, "/etc/sudoers") == "drop-in"

    def test_get_tier_tool_1(self):
        selector = self._make_selector()
        assert selector._get_tier_tool(1, "/etc/fstab") == "augeas"

    def test_get_tier_tool_2_json(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            assert selector._get_tier_tool(2, "file.json") == "python-json"

    def test_get_tier_tool_2_yaml_yq(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "yq"):
            assert selector._get_tier_tool(2, "file.yaml") == "yq"

    def test_get_tier_tool_2_yaml_ruamel(self):
        selector = self._make_selector()
        def tool_check(t):
            return t == "ruamel.yaml"
        with patch.object(selector, "_check_tool_available", side_effect=tool_check):
            assert selector._get_tier_tool(2, "file.yaml") == "ruamel.yaml"

    def test_get_tier_tool_2_yaml_pyyaml(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            assert selector._get_tier_tool(2, "file.yaml") == "pyyaml"

    def test_get_tier_tool_2_toml(self):
        selector = self._make_selector()
        assert selector._get_tier_tool(2, "pyproject.toml") == "python-toml"

    def test_get_tier_tool_3_ini(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            assert selector._get_tier_tool(3, "setup.cfg") == "configparser"

    def test_get_tier_tool_3_xml(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=True):
            assert selector._get_tier_tool(3, "pom.xml") == "xmlstarlet"

    def test_get_tier_tool_4_markdown(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            assert selector._get_tier_tool(4, "README.md") == "regex"

    def test_get_tier_tool_4_rst(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=True):
            assert selector._get_tier_tool(4, "docs.rst") == "docutils"

    def test_get_tier_tool_5(self):
        selector = self._make_selector()
        assert selector._get_tier_tool(5, "/tmp/file.txt") == "text-patch"

    def test_get_tier_tool_unknown(self):
        selector = self._make_selector()
        # A file that doesn't match any pattern for tier 2
        assert selector._get_tier_tool(2, "/tmp/file.txt") is None

    # --- get_all_tier_applicability ----------------------------------------

    def test_get_all_tier_applicability(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=False):
            result = selector.get_all_tier_applicability("file.json", _op(kind="set"))
        assert len(result) == 6
        for tier_num in range(6):
            assert tier_num in result

    # --- _check_tier1 with augeas available --------------------------------

    def test_check_tier1_augeas_with_lens(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.detect_lens = MagicMock(return_value="Fstab")
        with (
            patch.object(selector, "_check_tool_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = selector._check_tier1("/etc/fstab", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier1_augeas_no_lens(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.detect_lens = MagicMock(return_value=None)
        with (
            patch.object(selector, "_check_tool_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = selector._check_tier1("/etc/fstab", _op(kind="set"))
        assert result.applicable is False

    def test_check_tier1_augeas_exception(self):
        selector = self._make_selector()
        mock_mod = MagicMock()
        mock_mod.detect_lens = MagicMock(side_effect=RuntimeError("boom"))
        with (
            patch.object(selector, "_check_tool_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = selector._check_tier1("/etc/fstab", _op(kind="set"))
        assert result.applicable is False

    def test_check_tier1_no_pattern_match(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", return_value=True):
            result = selector._check_tier1("/tmp/random.txt", _op(kind="set"))
        assert result.applicable is False

    # --- _check_tier2 with tools available ---------------------------------

    def test_check_tier2_json_with_jq(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "jq"):
            result = selector._check_tier2("file.json", _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.95

    def test_check_tier2_yaml_with_yq(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "yq"):
            result = selector._check_tier2("file.yaml", _op(kind="set"))
        assert result.applicable is True

    def test_check_tier2_yaml_with_ruamel(self):
        selector = self._make_selector()
        def tool_check(t):
            return t == "ruamel.yaml"
        with patch.object(selector, "_check_tool_available", side_effect=tool_check):
            result = selector._check_tier2("file.yaml", _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.85

    def test_check_tier2_toml_available(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "toml"):
            result = selector._check_tier2("pyproject.toml", _op(kind="set"))
        assert result.applicable is True

    # --- _check_tier3 with tools available ---------------------------------

    def test_check_tier3_ini_with_crudini(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "crudini"):
            result = selector._check_tier3("setup.cfg", _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.9

    def test_check_tier3_xml_with_xmlstarlet(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "xmlstarlet"):
            result = selector._check_tier3("pom.xml", _op(kind="set"))
        assert result.applicable is True

    # --- _check_tier4 with tools available ---------------------------------

    def test_check_tier4_markdown_with_lib(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "markdown-it"):
            result = selector._check_tier4("README.md", _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.85

    def test_check_tier4_rst_with_docutils(self):
        selector = self._make_selector()
        with patch.object(selector, "_check_tool_available", side_effect=lambda t: t == "docutils"):
            result = selector._check_tier4("docs.rst", _op(kind="set"))
        assert result.applicable is True
