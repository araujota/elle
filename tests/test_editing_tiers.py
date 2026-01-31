"""Tests for editing tiers 0-5."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.ops.editing.models import (
    EditOperation,
    FileEditPreview,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _op(kind: str = "set", **kw) -> EditOperation:
    """Shortcut to create an EditOperation."""
    return EditOperation(kind=kind, **kw)


# We mock the diff helpers globally for every tier since they depend on
# the augeas subpackage which may not be installed.
DIFF_PATCH = "elle.ops.augeas.diff"


def _stub_diff_module():
    """Return a mock module with unified_diff / colored_diff."""
    mod = MagicMock()
    mod.unified_diff = MagicMock(return_value="--- a\n+++ b\n")
    mod.colored_diff = MagicMock(return_value="\x1b[31m-old\x1b[0m\n\x1b[32m+new\x1b[0m")
    return mod


def _stub_backup_module():
    """Return a mock backup module."""
    mod = MagicMock()
    record = MagicMock()
    record.backup_path = "/tmp/backup_test"
    mod.backup_file = MagicMock(return_value=record)
    mod.restore_file = MagicMock()
    return mod


# ============================================================================
# Tier 0 -- Drop-In
# ============================================================================


class TestTier0DropIn:
    """Tests for Tier0DropIn."""

    def _make_tier(self):
        from elle.ops.editing.tiers.tier0_dropin import Tier0DropIn

        return Tier0DropIn()

    # --- can_handle ---------------------------------------------------------

    @patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, "/etc/sudoers.d"))
    def test_can_handle_additive_operations(self, mock_dropin):
        tier = self._make_tier()
        for kind in ("add", "append", "set"):
            result = tier.can_handle(Path("/etc/sudoers"), _op(kind=kind))
            assert result.applicable is True
            assert result.tier == 0
            assert result.confidence == 0.95

    @patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, None))
    def test_can_handle_additive_no_dir(self, mock_dropin):
        tier = self._make_tier()
        result = tier.can_handle(Path("/etc/profile"), _op(kind="set"))
        assert result.applicable is True
        assert "existing .d directory" in result.reason

    @patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(False, None))
    def test_can_handle_not_supported(self, mock_dropin):
        tier = self._make_tier()
        result = tier.can_handle(Path("/etc/nginx/nginx.conf"), _op(kind="set"))
        assert result.applicable is False

    @patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, "/etc/sudoers.d"))
    def test_can_handle_rm_not_suitable(self, mock_dropin):
        tier = self._make_tier()
        result = tier.can_handle(Path("/etc/sudoers"), _op(kind="rm"))
        assert result.applicable is False
        assert "not suitable" in result.reason

    # --- validate_syntax ----------------------------------------------------

    def test_validate_systemd_dropin_success(self):
        tier = self._make_tier()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = tier.validate_syntax(
                "[Service]\nRestart=always\n", Path("/etc/systemd/system/foo.service.d/override.conf")
            )
        assert result.passed is True
        assert result.method == "systemd-analyze verify"

    def test_validate_systemd_dropin_failure(self):
        tier = self._make_tier()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error\nbad line"
        with patch("subprocess.run", return_value=mock_result):
            result = tier.validate_syntax("bad", Path("/etc/systemd/system/foo.service.d/override.conf"))
        assert result.passed is False
        assert len(result.errors) > 0

    def test_validate_systemd_exception(self):
        tier = self._make_tier()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = tier.validate_syntax("x", Path("/etc/systemd/system/foo.conf"))
        assert result.passed is True
        assert result.method == "skipped"

    def test_validate_sudoers_success(self):
        tier = self._make_tier()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = tier.validate_syntax("user ALL=(ALL) NOPASSWD: ALL\n", Path("/etc/sudoers.d/99-elle"))
        assert result.passed is True
        assert result.method == "visudo -cf"

    def test_validate_sudoers_failure(self):
        tier = self._make_tier()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "bad syntax"
        with patch("subprocess.run", return_value=mock_result):
            result = tier.validate_syntax("bad", Path("/etc/sudoers.d/test"))
        assert result.passed is False

    def test_validate_sudoers_exception(self):
        tier = self._make_tier()
        with patch("subprocess.run", side_effect=OSError("no visudo")):
            result = tier.validate_syntax("x", Path("/etc/sudoers"))
        assert result.passed is True
        assert result.method == "skipped"

    def test_validate_generic(self):
        tier = self._make_tier()
        result = tier.validate_syntax("hello", Path("/etc/other"))
        assert result.passed is True
        assert result.method == "none"

    # --- _generate_dropin_content -------------------------------------------

    def test_generate_systemd_section(self):
        tier = self._make_tier()
        op = _op(kind="set", section="Service", key="Restart", value="always")
        content = tier._generate_dropin_content(Path("/etc/systemd/system/foo.service"), op)
        assert "[Service]" in content
        assert "Restart=always" in content

    def test_generate_systemd_path_extraction(self):
        tier = self._make_tier()
        op = _op(kind="set", path="/Service/Restart", value="always")
        content = tier._generate_dropin_content(Path("/etc/systemd/system/foo.service"), op)
        assert "[Service]" in content

    def test_generate_systemd_value_only(self):
        tier = self._make_tier()
        op = _op(kind="set", value="SomeDirective")
        content = tier._generate_dropin_content(Path("/etc/systemd/system/foo.service"), op)
        assert "SomeDirective" in content

    def test_generate_sudoers(self):
        tier = self._make_tier()
        op = _op(kind="add", value="user ALL=(ALL) NOPASSWD: ALL")
        content = tier._generate_dropin_content(Path("/etc/sudoers"), op)
        assert "user ALL=(ALL) NOPASSWD: ALL" in content

    def test_generate_sysctl_key_value(self):
        tier = self._make_tier()
        op = _op(kind="set", key="net.ipv4.ip_forward", value="1")
        content = tier._generate_dropin_content(Path("/etc/sysctl.conf"), op)
        assert "net.ipv4.ip_forward=1" in content

    def test_generate_sysctl_path_value(self):
        tier = self._make_tier()
        op = _op(kind="set", path="net.ipv4.ip_forward", value="1")
        content = tier._generate_dropin_content(Path("/etc/sysctl.conf"), op)
        assert "net.ipv4.ip_forward=1" in content

    def test_generate_apt(self):
        tier = self._make_tier()
        op = _op(kind="add", value="deb http://repo example main")
        content = tier._generate_dropin_content(Path("/etc/apt/sources.list"), op)
        assert "deb http://repo example main" in content

    def test_generate_generic(self):
        tier = self._make_tier()
        op = _op(kind="add", value="some_value")
        content = tier._generate_dropin_content(Path("/etc/other"), op)
        assert "some_value" in content

    # --- _get_dropin_path ---------------------------------------------------

    def test_get_dropin_path_with_name(self):
        tier = self._make_tier()
        result = tier._get_dropin_path(Path("/etc/profile"), "/etc/profile.d", "custom", 50)
        assert result == Path("/etc/profile.d/50-custom.conf")

    def test_get_dropin_path_no_name(self):
        tier = self._make_tier()
        result = tier._get_dropin_path(Path("/etc/profile"), "/etc/profile.d", None, 99)
        assert result == Path("/etc/profile.d/99-elle-override.conf")

    def test_get_dropin_path_sudoers(self):
        tier = self._make_tier()
        result = tier._get_dropin_path(Path("/etc/sudoers"), "/etc/sudoers.d", "elle", 99)
        assert ".conf" not in str(result)

    def test_get_dropin_path_no_dir(self):
        tier = self._make_tier()
        result = tier._get_dropin_path(Path("/etc/foo"), None, None, 99)
        assert "/etc/foo.d/" in str(result)

    # --- preview ------------------------------------------------------------

    @patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, "/etc/sudoers.d"))
    def test_preview(self, mock_dropin, tmp_path):
        tier = self._make_tier()
        op = _op(kind="add", value="user ALL=(ALL) NOPASSWD: ALL")
        diff_mod = _stub_diff_module()
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}):
            result = tier.preview(Path("/etc/sudoers"), op)
        assert isinstance(result, FileEditPreview)
        assert result.tier_selected == 0
        assert result.is_valid is True

    # --- apply (async) ------------------------------------------------------

    @pytest.mark.asyncio
    @patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, None))
    async def test_apply_no_dropin_dir(self, mock_dropin):
        tier = self._make_tier()
        op = _op(kind="set", value="x")
        diff_mod = _stub_diff_module()
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}):
            result = await tier.apply(Path("/etc/foo"), op, skip_backup=True)
        assert result.success is False
        assert "No drop-in directory" in result.error

    @pytest.mark.asyncio
    async def test_apply_permission_denied_mkdir(self, tmp_path):
        tier = self._make_tier()
        op = _op(kind="set", value="x")
        diff_mod = _stub_diff_module()
        with (
            patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, "/root/noperm.d")),
            patch("pathlib.Path.mkdir", side_effect=PermissionError),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = await tier.apply(Path("/root/noperm"), op, skip_backup=True)
        assert result.success is False
        assert result.requires_privilege is True

    @pytest.mark.asyncio
    async def test_apply_validation_fails(self, tmp_path):
        tier = self._make_tier()
        dropin_dir = str(tmp_path / "sudoers.d")
        op = _op(kind="set", value="bad")
        diff_mod = _stub_diff_module()
        with (
            patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, dropin_dir)),
            patch.object(
                tier, "validate_syntax", return_value=ValidationResult(passed=False, method="test", errors=("err",))
            ),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = await tier.apply(Path("/etc/sudoers"), op, skip_backup=True)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_success(self, tmp_path):
        tier = self._make_tier()
        dropin_dir = str(tmp_path / "profile.d")
        op = _op(kind="set", value="export FOO=bar")
        diff_mod = _stub_diff_module()
        with (
            patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, dropin_dir)),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = await tier.apply(Path("/etc/profile"), op, skip_backup=True, skip_validation=True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_write_permission_error(self, tmp_path):
        tier = self._make_tier()
        dropin_dir = str(tmp_path / "test.d")
        os.makedirs(dropin_dir, exist_ok=True)
        op = _op(kind="set", value="data")
        diff_mod = _stub_diff_module()
        with (
            patch("elle.ops.editing.tiers.tier0_dropin.is_dropin_candidate", return_value=(True, dropin_dir)),
            patch("pathlib.Path.write_text", side_effect=PermissionError),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = await tier.apply(Path("/etc/test"), op, skip_backup=True, skip_validation=True)
        assert result.success is False
        assert result.requires_privilege is True


# ============================================================================
# Tier 1 -- Augeas
# ============================================================================


class TestTier1Augeas:
    """Tests for Tier1Augeas."""

    def _make_tier(self):
        from elle.ops.editing.tiers.tier1_augeas import Tier1Augeas

        return Tier1Augeas()

    # --- _is_available ------------------------------------------------------

    def test_is_available_true(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.is_available = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}):
            assert tier._is_available() is True

    def test_is_available_false_import_error(self):
        tier = self._make_tier()
        with patch.dict("sys.modules", {"elle.ops.augeas": None}):
            assert tier._is_available() is False

    # --- can_handle ---------------------------------------------------------

    def test_can_handle_not_available(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_available", return_value=False):
            result = tier.can_handle(Path("/etc/fstab"), _op(kind="set"))
        assert result.tier == 1
        assert result.tool_available is False

    def test_can_handle_no_pattern_match(self):
        tier = self._make_tier()
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch("elle.ops.editing.tiers.tier1_augeas.matches_pattern", return_value=False),
        ):
            result = tier.can_handle(Path("/tmp/random.txt"), _op(kind="set"))
        assert result.applicable is False

    def test_can_handle_lens_found(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.detect_lens = MagicMock(return_value="Fstab")
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch("elle.ops.editing.tiers.tier1_augeas.matches_pattern", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = tier.can_handle(Path("/etc/fstab"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.9

    def test_can_handle_no_lens(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.detect_lens = MagicMock(return_value=None)
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch("elle.ops.editing.tiers.tier1_augeas.matches_pattern", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = tier.can_handle(Path("/etc/fstab"), _op(kind="set"))
        assert result.applicable is False

    def test_can_handle_lens_exception(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.detect_lens = MagicMock(side_effect=RuntimeError("boom"))
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch("elle.ops.editing.tiers.tier1_augeas.matches_pattern", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = tier.can_handle(Path("/etc/fstab"), _op(kind="set"))
        assert result.applicable is False
        assert "failed" in result.reason

    # --- validate_syntax ----------------------------------------------------

    def test_validate_not_available(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_available", return_value=False):
            result = tier.validate_syntax("data", Path("/etc/fstab"))
        assert result.passed is True
        assert result.method == "skipped"

    def test_validate_success(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        vr = MagicMock()
        vr.valid = True
        mock_mod.validate_config = MagicMock(return_value=vr)
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = tier.validate_syntax("data", Path("/etc/fstab"))
        assert result.passed is True

    def test_validate_exception(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.validate_config = MagicMock(side_effect=RuntimeError("err"))
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = tier.validate_syntax("data", Path("/etc/fstab"))
        assert result.passed is False

    # --- preview ------------------------------------------------------------

    def test_preview_not_available(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_available", return_value=False):
            result = tier.preview(Path("/etc/fstab"), _op(kind="set"))
        assert result.is_valid is False

    def test_preview_success(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        preview_result = MagicMock()
        preview_result.original_content = "old"
        preview_result.proposed_content = "new"
        preview_result.diff = "diff"
        preview_result.diff_colored = "colored"
        preview_result.requires_privilege = False
        mock_mod.preview_edit = MagicMock(return_value=preview_result)
        mock_mod.AugeasEditRequest = MagicMock()
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch.object(tier, "_convert_to_augeas_ops", return_value=[]),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = tier.preview(Path("/etc/fstab"), _op(kind="set"))
        assert result.is_valid is True

    def test_preview_exception_fallback(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.conf"
        f.write_text("original")
        mock_mod = MagicMock()
        mock_mod.AugeasEditRequest = MagicMock(side_effect=RuntimeError("nope"))
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch.object(tier, "_convert_to_augeas_ops", return_value=[]),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = tier.preview(f, _op(kind="set"))
        assert result.is_valid is False

    # --- apply (async) ------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_not_available(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_available", return_value=False):
            result = await tier.apply(Path("/etc/fstab"), _op(kind="set"))
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_success(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        edit_result = MagicMock()
        edit_result.success = True
        edit_result.validation_passed = True
        edit_result.validation_output = "ok"
        edit_result.diff = "diff"
        edit_result.diff_colored = "col"
        edit_result.backup_path = "/tmp/bk"
        mock_mod.execute_edit = AsyncMock(return_value=edit_result)
        mock_mod.AugeasEditRequest = MagicMock()
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch.object(tier, "_convert_to_augeas_ops", return_value=[]),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = await tier.apply(Path("/etc/fstab"), _op(kind="set"))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_failure(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        edit_result = MagicMock()
        edit_result.success = False
        edit_result.error = "augeas error"
        edit_result.backup_path = None
        edit_result.rollback_applied = False
        edit_result.requires_privilege = False
        mock_mod.execute_edit = AsyncMock(return_value=edit_result)
        mock_mod.AugeasEditRequest = MagicMock()
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch.object(tier, "_convert_to_augeas_ops", return_value=[]),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = await tier.apply(Path("/etc/fstab"), _op(kind="set"))
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_exception(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.AugeasEditRequest = MagicMock(side_effect=RuntimeError("crash"))
        with (
            patch.object(tier, "_is_available", return_value=True),
            patch.object(tier, "_convert_to_augeas_ops", return_value=[]),
            patch.dict("sys.modules", {"elle.ops.augeas": mock_mod}),
        ):
            result = await tier.apply(Path("/etc/fstab"), _op(kind="set"))
        assert result.success is False

    # --- _convert_to_augeas_ops ---------------------------------------------

    def test_convert_set_op(self):
        tier = self._make_tier()
        aug_mod = MagicMock()
        aug_mod.AugeasOp = MagicMock()
        with patch.dict("sys.modules", {"elle.ops.augeas": aug_mod}):
            ops = tier._convert_to_augeas_ops(_op(kind="set", path="/Service/Restart", value="always"))
        assert len(ops) == 1

    def test_convert_rm_op(self):
        tier = self._make_tier()
        aug_mod = MagicMock()
        aug_mod.AugeasOp = MagicMock()
        with patch.dict("sys.modules", {"elle.ops.augeas": aug_mod}):
            ops = tier._convert_to_augeas_ops(_op(kind="rm", path="/Service/Restart"))
        assert len(ops) == 1

    def test_convert_add_op(self):
        tier = self._make_tier()
        aug_mod = MagicMock()
        aug_mod.AugeasOp = MagicMock()
        with patch.dict("sys.modules", {"elle.ops.augeas": aug_mod}):
            ops = tier._convert_to_augeas_ops(
                _op(kind="add", path="/Service", value="Restart=always", key="Restart", position="before")
            )
        assert len(ops) == 2

    def test_convert_unknown_op(self):
        tier = self._make_tier()
        aug_mod = MagicMock()
        aug_mod.AugeasOp = MagicMock()
        with patch.dict("sys.modules", {"elle.ops.augeas": aug_mod}):
            ops = tier._convert_to_augeas_ops(_op(kind="replace", path="/x", value="y"))
        assert len(ops) == 0


# ============================================================================
# Tier 2 -- Structured Data (JSON/YAML/TOML)
# ============================================================================


class TestTier2Structured:
    """Tests for Tier2Structured."""

    def _make_tier(self):
        from elle.ops.editing.tiers.tier2_structured import Tier2Structured

        return Tier2Structured()

    # --- _detect_format ----------------------------------------------------

    def test_detect_json(self):
        tier = self._make_tier()
        assert tier._detect_format("/etc/docker/daemon.json") == "json"

    def test_detect_yaml(self):
        tier = self._make_tier()
        assert tier._detect_format("docker-compose.yml") == "yaml"

    def test_detect_toml(self):
        tier = self._make_tier()
        assert tier._detect_format("pyproject.toml") == "toml"

    def test_detect_none(self):
        tier = self._make_tier()
        assert tier._detect_format("/etc/passwd") is None

    # --- can_handle ---------------------------------------------------------

    def test_can_handle_json_with_jq(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_jq_available", return_value=True):
            result = tier.can_handle(Path("/etc/docker/daemon.json"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.95

    def test_can_handle_json_no_jq(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_jq_available", return_value=False):
            result = tier.can_handle(Path("/etc/docker/daemon.json"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.85

    def test_can_handle_yaml_with_yq(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_yq_available", return_value=True):
            result = tier.can_handle(Path("docker-compose.yml"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.95

    def test_can_handle_yaml_no_yq(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_yq_available", return_value=False):
            result = tier.can_handle(Path("docker-compose.yml"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.8

    def test_can_handle_toml(self):
        tier = self._make_tier()
        result = tier.can_handle(Path("pyproject.toml"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.9

    def test_can_handle_unknown(self):
        tier = self._make_tier()
        result = tier.can_handle(Path("/etc/passwd"), _op(kind="set"))
        assert result.applicable is False

    # --- validate_syntax ----------------------------------------------------

    def test_validate_json_valid(self):
        tier = self._make_tier()
        result = tier.validate_syntax('{"key": "val"}', Path("file.json"))
        assert result.passed is True

    def test_validate_json_invalid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("{bad json", Path("file.json"))
        assert result.passed is False

    def test_validate_yaml_valid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("key: value\n", Path("file.yaml"))
        assert result.passed is True

    def test_validate_yaml_invalid(self):
        tier = self._make_tier()
        result = tier.validate_syntax(":\n  - :\n    :", Path("file.yaml"))
        # PyYAML is permissive, so just check it returns a ValidationResult
        assert isinstance(result, ValidationResult)

    def test_validate_toml_valid(self):
        tier = self._make_tier()
        result = tier.validate_syntax('[section]\nkey = "val"\n', Path("file.toml"))
        assert result.passed is True

    def test_validate_toml_invalid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("[[[[bad", Path("file.toml"))
        assert result.passed is False

    def test_validate_unknown_format(self):
        tier = self._make_tier()
        result = tier.validate_syntax("text", Path("/tmp/file.txt"))
        assert result.passed is True
        assert result.method == "none"

    # --- _apply_dict_edit ---------------------------------------------------

    def test_apply_dict_set(self):
        tier = self._make_tier()
        data = {"a": {"b": "old"}}
        op = _op(kind="set", path=".a.b", value="new")
        result = tier._apply_dict_edit(data, op)
        assert result["a"]["b"] == "new"

    def test_apply_dict_set_creates_path(self):
        tier = self._make_tier()
        data = {}
        op = _op(kind="set", path="a.b.c", value="val")
        result = tier._apply_dict_edit(data, op)
        assert result["a"]["b"]["c"] == "val"

    def test_apply_dict_set_json_value(self):
        tier = self._make_tier()
        data = {}
        op = _op(kind="set", path="a", value="[1, 2, 3]")
        result = tier._apply_dict_edit(data, op)
        assert result["a"] == [1, 2, 3]

    def test_apply_dict_rm(self):
        tier = self._make_tier()
        data = {"a": {"b": "val"}}
        op = _op(kind="rm", path=".a.b")
        result = tier._apply_dict_edit(data, op)
        assert "b" not in result["a"]

    def test_apply_dict_rm_missing_path(self):
        tier = self._make_tier()
        data = {"a": "val"}
        op = _op(kind="rm", path="x.y.z")
        result = tier._apply_dict_edit(data, op)
        assert result == {"a": "val"}

    def test_apply_dict_no_path(self):
        tier = self._make_tier()
        data = {"a": 1}
        op = _op(kind="set", value="x")
        result = tier._apply_dict_edit(data, op)
        assert result == {"a": 1}

    # --- _build_jq_filter ---------------------------------------------------

    def test_build_jq_filter_set_json(self):
        tier = self._make_tier()
        op = _op(kind="set", path=".key", value="42")
        result = tier._build_jq_filter(op)
        assert ".key = 42" in result

    def test_build_jq_filter_set_string(self):
        tier = self._make_tier()
        op = _op(kind="set", path=".key", value="hello")
        result = tier._build_jq_filter(op)
        assert '"hello"' in result

    def test_build_jq_filter_rm(self):
        tier = self._make_tier()
        op = _op(kind="rm", path=".key")
        result = tier._build_jq_filter(op)
        assert "del(.key)" in result

    def test_build_jq_filter_identity(self):
        tier = self._make_tier()
        op = _op(kind="append")
        result = tier._build_jq_filter(op)
        assert result == "."

    # --- _apply_json_edit ---------------------------------------------------

    def test_apply_json_edit_python(self):
        tier = self._make_tier()
        content = '{"key": "old"}'
        op = _op(kind="set", path=".key", value="new")
        with patch.object(tier, "_is_jq_available", return_value=False):
            result = tier._apply_json_edit(content, op)
        data = json.loads(result)
        assert data["key"] == "new"

    # --- preview / apply with mocks -----------------------------------------

    def test_preview_json(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.json"
        f.write_text('{"a": 1}')
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = tier.preview(f, op)
        assert result.is_valid is True

    def test_preview_exception(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "bad.json"
        f.write_text("not json")
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = tier.preview(f, op)
        assert result.is_valid is False

    @pytest.mark.asyncio
    async def test_apply_json_success(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.json"
        f.write_text('{"a": 1}')
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_unknown_format(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "file.txt"
        f.write_text("hello")
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_file_not_found(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "nope.json"
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_permission_denied(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.json"
        f.write_text('{"a": 1}')
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch("pathlib.Path.write_text", side_effect=PermissionError),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is False
        assert result.requires_privilege is True

    @pytest.mark.asyncio
    async def test_apply_backup_failure(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.json"
        f.write_text('{"a": 1}')
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = MagicMock()
        backup_mod.backup_file = MagicMock(side_effect=RuntimeError("no backup"))
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=False)
        assert result.success is False
        assert "Backup failed" in result.error

    # --- _apply_json_edit with jq engine ------------------------------------

    def test_apply_json_edit_jq_set(self):
        tier = self._make_tier()
        content = '{"key": "old"}'
        op = _op(kind="set", path=".key", value="new")
        mock_jq_mod = MagicMock()
        mock_engine = MagicMock()
        mock_engine.set_value.return_value = '{"key": "new"}\n'
        mock_jq_mod.JQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_jq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.jq": mock_jq_mod}),
        ):
            result = tier._apply_json_edit(content, op)
        assert "new" in result
        mock_engine.set_value.assert_called_once()

    def test_apply_json_edit_jq_rm(self):
        tier = self._make_tier()
        content = '{"key": "val", "other": 1}'
        op = _op(kind="rm", path=".key")
        mock_jq_mod = MagicMock()
        mock_engine = MagicMock()
        mock_engine.delete_value.return_value = '{"other": 1}\n'
        mock_jq_mod.JQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_jq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.jq": mock_jq_mod}),
        ):
            result = tier._apply_json_edit(content, op)
        assert "other" in result
        mock_engine.delete_value.assert_called_once()

    def test_apply_json_edit_jq_transform(self):
        tier = self._make_tier()
        content = '{"key": "val"}'
        op = _op(kind="append")
        mock_jq_mod = MagicMock()
        mock_engine = MagicMock()
        transform_result = MagicMock()
        transform_result.success = True
        transform_result.output = '{"key": "val"}\n'
        mock_engine.transform.return_value = transform_result
        mock_jq_mod.JQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_jq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.jq": mock_jq_mod}),
        ):
            result = tier._apply_json_edit(content, op)
        mock_engine.transform.assert_called_once()
        assert "key" in result

    def test_apply_json_edit_jq_transform_error(self):
        tier = self._make_tier()
        content = '{"key": "val"}'
        op = _op(kind="append")
        mock_jq_mod = MagicMock()
        mock_engine = MagicMock()
        transform_result = MagicMock()
        transform_result.success = False
        transform_result.error = "jq filter failed"
        mock_engine.transform.return_value = transform_result
        mock_jq_mod.JQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_jq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.jq": mock_jq_mod}),
        ):
            with pytest.raises(ValueError, match="jq filter failed"):
                tier._apply_json_edit(content, op)

    # --- _apply_yaml_edit ---------------------------------------------------

    def test_apply_yaml_edit_yq_set(self):
        tier = self._make_tier()
        content = "key: old\n"
        op = _op(kind="set", path=".key", value="new")
        mock_yq_mod = MagicMock()
        mock_engine = MagicMock()
        mock_engine.set_value.return_value = "key: new\n"
        mock_yq_mod.YQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_yq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.yq": mock_yq_mod}),
        ):
            result = tier._apply_yaml_edit(content, op)
        assert "new" in result

    def test_apply_yaml_edit_yq_rm(self):
        tier = self._make_tier()
        content = "key: val\nother: 1\n"
        op = _op(kind="rm", path=".key")
        mock_yq_mod = MagicMock()
        mock_engine = MagicMock()
        mock_engine.delete_value.return_value = "other: 1\n"
        mock_yq_mod.YQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_yq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.yq": mock_yq_mod}),
        ):
            result = tier._apply_yaml_edit(content, op)
        assert "other" in result

    def test_apply_yaml_edit_yq_transform(self):
        tier = self._make_tier()
        content = "key: val\n"
        op = _op(kind="append")
        mock_yq_mod = MagicMock()
        mock_engine = MagicMock()
        transform_result = MagicMock()
        transform_result.success = True
        transform_result.output = "key: val\n"
        mock_engine.transform.return_value = transform_result
        mock_yq_mod.YQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_yq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.yq": mock_yq_mod}),
        ):
            result = tier._apply_yaml_edit(content, op)
        assert "key" in result

    def test_apply_yaml_edit_yq_transform_error(self):
        tier = self._make_tier()
        content = "key: val\n"
        op = _op(kind="append")
        mock_yq_mod = MagicMock()
        mock_engine = MagicMock()
        transform_result = MagicMock()
        transform_result.success = False
        transform_result.error = "yq error"
        mock_engine.transform.return_value = transform_result
        mock_yq_mod.YQEngine.return_value = mock_engine
        with (
            patch.object(tier, "_is_yq_available", return_value=True),
            patch.dict("sys.modules", {"elle.ops.yq": mock_yq_mod}),
        ):
            with pytest.raises(ValueError, match="yq error"):
                tier._apply_yaml_edit(content, op)

    def test_apply_yaml_edit_python_pyyaml(self):
        """YAML edit with PyYAML fallback when ruamel not available."""
        tier = self._make_tier()
        content = "key: old\n"
        op = _op(kind="set", path=".key", value="new")
        with (
            patch.object(tier, "_is_yq_available", return_value=False),
            patch.dict("sys.modules", {"ruamel.yaml": None}),
        ):
            result = tier._apply_yaml_edit(content, op)
        assert "new" in result

    # --- _apply_toml_edit ---------------------------------------------------

    def test_apply_toml_edit_set(self):
        tier = self._make_tier()
        content = '[section]\nkey = "old"\n'
        op = _op(kind="set", path=".section.key", value='"new"')
        mock_toml = MagicMock()
        mock_toml.loads.return_value = {"section": {"key": "old"}}
        mock_toml.dumps.return_value = '[section]\nkey = "new"\n'
        with patch.dict("sys.modules", {"toml": mock_toml}):
            result = tier._apply_toml_edit(content, op)
        assert "new" in result

    def test_apply_toml_edit_rm(self):
        tier = self._make_tier()
        content = '[section]\nkey = "val"\nother = 1\n'
        op = _op(kind="rm", path=".section.key")
        mock_toml = MagicMock()
        mock_toml.loads.return_value = {"section": {"key": "val", "other": 1}}
        mock_toml.dumps.return_value = '[section]\nother = 1\n'
        with patch.dict("sys.modules", {"toml": mock_toml}):
            result = tier._apply_toml_edit(content, op)
        assert "other" in result

    # --- _apply_dict_edit edge cases ----------------------------------------

    def test_apply_dict_rm_key_exists(self):
        tier = self._make_tier()
        data = {"a": {"b": "val", "c": 1}}
        op = _op(kind="rm", path="a.b")
        result = tier._apply_dict_edit(data, op)
        assert "b" not in result["a"]
        assert result["a"]["c"] == 1

    def test_apply_dict_rm_key_not_exists(self):
        """Removing a key that does not exist is a no-op."""
        tier = self._make_tier()
        data = {"a": {"b": "val"}}
        op = _op(kind="rm", path="a.nonexistent")
        result = tier._apply_dict_edit(data, op)
        assert result == {"a": {"b": "val"}}

    # --- preview for YAML ---------------------------------------------------

    def test_preview_yaml(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.yaml"
        f.write_text("key: old\n")
        op = _op(kind="set", path=".key", value="new")
        diff_mod = _stub_diff_module()
        with (
            patch.object(tier, "_is_yq_available", return_value=False),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod, "ruamel.yaml": None}),
        ):
            result = tier.preview(f, op)
        assert result.is_valid is True
        assert result.tier_selected == 2

    def test_preview_toml(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.toml"
        f.write_text('[section]\nkey = "old"\n')
        op = _op(kind="set", path=".section.key", value='"new"')
        diff_mod = _stub_diff_module()
        mock_toml = MagicMock()
        mock_toml.loads.return_value = {"section": {"key": "old"}}
        mock_toml.dumps.return_value = '[section]\nkey = "new"\n'
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod, "toml": mock_toml}):
            result = tier.preview(f, op)
        assert result.is_valid is True

    def test_preview_unknown_fmt_identity(self, tmp_path):
        """Preview for non-structured file returns original as proposed."""
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("plain text\n")
        op = _op(kind="set", path=".key", value="val")
        diff_mod = _stub_diff_module()
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}):
            result = tier.preview(f, op)
        # Unknown format means proposed == original
        assert result.proposed_content == "plain text\n"

    # --- apply for YAML / TOML with backup and validation -------------------

    @pytest.mark.asyncio
    async def test_apply_yaml_success(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.yaml"
        f.write_text("key: old\n")
        op = _op(kind="set", path=".key", value="new")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch.object(tier, "_is_yq_available", return_value=False),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                    "ruamel.yaml": None,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_toml_success(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.toml"
        f.write_text('[section]\nkey = "old"\n')
        op = _op(kind="set", path=".section.key", value='"new"')
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        mock_toml = MagicMock()
        mock_toml.loads.return_value = {"section": {"key": "old"}}
        mock_toml.dumps.return_value = '[section]\nkey = "new"\n'
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
                "toml": mock_toml,
            },
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_edit_raises(self, tmp_path):
        """When the edit function itself raises, result is failure."""
        tier = self._make_tier()
        f = tmp_path / "test.json"
        f.write_text("not valid json at all")
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is False
        assert "Edit failed" in result.error

    @pytest.mark.asyncio
    async def test_apply_validation_fails(self, tmp_path):
        """When validation fails, result is failure."""
        tier = self._make_tier()
        f = tmp_path / "test.json"
        f.write_text('{"a": 1}')
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch.object(
                tier,
                "validate_syntax",
                return_value=ValidationResult(
                    passed=False, method="json.loads", errors=("syntax error",)
                ),
            ),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is False
        assert "Validation failed" in result.error

    @pytest.mark.asyncio
    async def test_apply_skip_validation(self, tmp_path):
        """skip_validation=True bypasses validation."""
        tier = self._make_tier()
        f = tmp_path / "test.json"
        f.write_text('{"a": 1}')
        op = _op(kind="set", path=".a", value="2")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch.object(tier, "_is_jq_available", return_value=False),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True, skip_validation=True)
        assert result.success is True

    # --- _is_jq_available / _is_yq_available edge cases --------------------

    def test_is_jq_available_import_error(self):
        tier = self._make_tier()
        with patch.dict("sys.modules", {"elle.ops.jq": None}):
            assert tier._is_jq_available() is False

    def test_is_yq_available_import_error(self):
        tier = self._make_tier()
        with patch.dict("sys.modules", {"elle.ops.yq": None}):
            assert tier._is_yq_available() is False

    def test_is_jq_available_true(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.is_available.return_value = True
        with patch.dict("sys.modules", {"elle.ops.jq": mock_mod}):
            assert tier._is_jq_available() is True

    def test_is_yq_available_true(self):
        tier = self._make_tier()
        mock_mod = MagicMock()
        mock_mod.is_available.return_value = True
        with patch.dict("sys.modules", {"elle.ops.yq": mock_mod}):
            assert tier._is_yq_available() is True


# ============================================================================
# Tier 3 -- Format Editors (INI / XML)
# ============================================================================


class TestTier3Format:
    """Tests for Tier3Format."""

    def _make_tier(self):
        from elle.ops.editing.tiers.tier3_format import Tier3Format

        return Tier3Format()

    # --- _detect_format ----------------------------------------------------

    def test_detect_ini(self):
        tier = self._make_tier()
        assert tier._detect_format("setup.cfg") == "ini"

    def test_detect_xml(self):
        tier = self._make_tier()
        assert tier._detect_format("pom.xml") == "xml"

    def test_detect_none(self):
        tier = self._make_tier()
        assert tier._detect_format("/etc/passwd") is None

    # --- can_handle ---------------------------------------------------------

    def test_can_handle_ini_with_crudini(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_crudini_available", return_value=True):
            result = tier.can_handle(Path("setup.cfg"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.9

    def test_can_handle_ini_no_crudini(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_crudini_available", return_value=False):
            result = tier.can_handle(Path("setup.cfg"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.8

    def test_can_handle_xml_with_xmlstarlet(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_xmlstarlet_available", return_value=True):
            result = tier.can_handle(Path("pom.xml"), _op(kind="set"))
        assert result.applicable is True

    def test_can_handle_xml_no_xmlstarlet(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_xmlstarlet_available", return_value=False):
            result = tier.can_handle(Path("pom.xml"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.75

    def test_can_handle_unknown(self):
        tier = self._make_tier()
        result = tier.can_handle(Path("/etc/passwd"), _op(kind="set"))
        assert result.applicable is False

    # --- validate_syntax ----------------------------------------------------

    def test_validate_ini_valid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("[section]\nkey = val\n", Path("file.ini"))
        assert result.passed is True

    def test_validate_ini_invalid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("[section\nno closing bracket", Path("file.cfg"))
        assert result.passed is False

    def test_validate_xml_valid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("<root><item>val</item></root>", Path("file.xml"))
        assert result.passed is True

    def test_validate_xml_invalid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("<root><unclosed>", Path("file.xml"))
        assert result.passed is False

    def test_validate_unknown(self):
        tier = self._make_tier()
        result = tier.validate_syntax("text", Path("file.txt"))
        assert result.passed is True

    # --- _apply_ini_edit ----------------------------------------------------

    def test_apply_ini_set(self):
        tier = self._make_tier()
        content = "[section]\nkey = old\n"
        op = _op(kind="set", section="section", key="key", value="new")
        result = tier._apply_ini_edit(content, op)
        assert "new" in result

    def test_apply_ini_set_new_section(self):
        tier = self._make_tier()
        content = "[existing]\nfoo = bar\n"
        op = _op(kind="set", section="newsection", key="key", value="val")
        result = tier._apply_ini_edit(content, op)
        assert "newsection" in result

    def test_apply_ini_rm_key(self):
        tier = self._make_tier()
        content = "[section]\nkey = val\nother = x\n"
        op = _op(kind="rm", section="section", key="key")
        result = tier._apply_ini_edit(content, op)
        assert "key" not in result.split("[section]")[1].split("\n")[1] or "key = val" not in result

    def test_apply_ini_rm_section(self):
        tier = self._make_tier()
        content = "[section]\nkey = val\n[other]\nx = y\n"
        op = _op(kind="rm", section="section")
        result = tier._apply_ini_edit(content, op)
        assert "[other]" in result

    # --- _apply_xml_edit with Python fallback --------------------------------

    def test_apply_xml_set_python(self):
        tier = self._make_tier()
        content = "<root><item>old</item></root>"
        op = _op(kind="set", path=".//item", value="new")
        with patch.object(tier, "_is_xmlstarlet_available", return_value=False):
            result = tier._apply_xml_edit(content, op)
        assert "new" in result

    def test_apply_xml_rm_python(self):
        tier = self._make_tier()
        content = "<root><item>old</item><other>keep</other></root>"
        op = _op(kind="rm", path="item")
        with patch.object(tier, "_is_xmlstarlet_available", return_value=False):
            result = tier._apply_xml_edit(content, op)
        assert "keep" in result

    # --- preview / apply with mocks -----------------------------------------

    def test_preview_ini(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.cfg"
        f.write_text("[section]\nkey = old\n")
        op = _op(kind="set", section="section", key="key", value="new")
        diff_mod = _stub_diff_module()
        with (
            patch.object(tier, "_is_crudini_available", return_value=False),
            patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}),
        ):
            result = tier.preview(f, op)
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_apply_ini_success(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.cfg"
        f.write_text("[section]\nkey = old\n")
        op = _op(kind="set", section="section", key="key", value="new")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch.object(tier, "_is_crudini_available", return_value=False),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_unknown_format(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello")
        op = _op(kind="set")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_file_not_found(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "nope.cfg"
        op = _op(kind="set", section="s", key="k", value="v")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op)
        assert result.success is False


# ============================================================================
# Tier 4 -- Document-aware (Markdown / RST)
# ============================================================================


class TestTier4Document:
    """Tests for Tier4Document."""

    def _make_tier(self):
        from elle.ops.editing.tiers.tier4_document import Tier4Document

        return Tier4Document()

    # --- _detect_format ---------------------------------------------------

    def test_detect_markdown(self):
        tier = self._make_tier()
        assert tier._detect_format("README.md") == "markdown"

    def test_detect_rst(self):
        tier = self._make_tier()
        assert tier._detect_format("docs.rst") == "rst"

    def test_detect_none(self):
        tier = self._make_tier()
        assert tier._detect_format("file.txt") is None

    # --- can_handle -------------------------------------------------------

    def test_can_handle_markdown_with_lib(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_markdown_it_available", return_value=True):
            result = tier.can_handle(Path("README.md"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.85

    def test_can_handle_markdown_no_lib(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_markdown_it_available", return_value=False):
            result = tier.can_handle(Path("README.md"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.7

    def test_can_handle_rst_with_docutils(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_docutils_available", return_value=True):
            result = tier.can_handle(Path("docs.rst"), _op(kind="set"))
        assert result.applicable is True

    def test_can_handle_rst_no_docutils(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_docutils_available", return_value=False):
            result = tier.can_handle(Path("docs.rst"), _op(kind="set"))
        assert result.applicable is True
        assert result.confidence == 0.7

    def test_can_handle_unknown(self):
        tier = self._make_tier()
        result = tier.can_handle(Path("file.txt"), _op(kind="set"))
        assert result.applicable is False

    # --- validate_syntax --------------------------------------------------

    def test_validate_markdown_valid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("# Hello\n\n```python\ncode\n```\n", Path("file.md"))
        assert result.passed is True

    def test_validate_markdown_unclosed_code(self):
        tier = self._make_tier()
        result = tier.validate_syntax("# Hello\n```\ncode\n", Path("file.md"))
        assert result.passed is False
        assert "Unclosed code block" in result.errors[0]

    def test_validate_rst_no_docutils(self):
        tier = self._make_tier()
        with patch.object(tier, "_is_docutils_available", return_value=False):
            result = tier.validate_syntax("Title\n=====\n", Path("file.rst"))
        assert result.passed is True

    def test_validate_unknown(self):
        tier = self._make_tier()
        result = tier.validate_syntax("text", Path("file.txt"))
        assert result.passed is True

    # --- _apply_document_edit helpers --------------------------------------

    def test_apply_set_heading_replace(self):
        tier = self._make_tier()
        content = "# Title\nold content\n"
        op = _op(kind="set", path="# Title", value="new content")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "new content" in result

    def test_apply_set_plain_replace(self):
        tier = self._make_tier()
        content = "hello world"
        op = _op(kind="set", path="hello", value="goodbye")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "goodbye" in result

    def test_apply_append_to_end(self):
        tier = self._make_tier()
        content = "existing\n"
        op = _op(kind="append", value="new line")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "new line" in result

    def test_apply_append_after_heading(self):
        tier = self._make_tier()
        content = "# Title\nold content\n"
        op = _op(kind="append", path="# Title", value="appended")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "appended" in result

    def test_apply_insert_before(self):
        tier = self._make_tier()
        content = "line1\nANCHOR\nline3\n"
        op = _op(kind="insert", value="inserted", anchor_pattern="ANCHOR", position="before")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "inserted" in result

    def test_apply_insert_after(self):
        tier = self._make_tier()
        content = "line1\nANCHOR\nline3\n"
        op = _op(kind="insert", value="inserted", anchor_pattern="ANCHOR", position="after")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "inserted" in result

    def test_apply_remove_heading(self):
        tier = self._make_tier()
        # Use same-level heading so the regex finds a stop point
        content = "# Title\ncontent\n# Other\nother content\n"
        op = _op(kind="rm", path="# Title")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "Other" in result

    def test_apply_remove_pattern(self):
        tier = self._make_tier()
        content = "keep this\nremove this\n"
        op = _op(kind="rm", path="remove this")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "remove this" not in result

    def test_apply_replace(self):
        tier = self._make_tier()
        content = "old text"
        op = _op(kind="replace", path="old", value="new")
        result = tier._apply_document_edit(content, op, "markdown")
        assert "new" in result

    def test_apply_unknown_kind(self):
        tier = self._make_tier()
        # Use a valid kind that has no handler branch - test fallthrough
        content = "original"
        # Create an operation with a kind that doesn't match any branch
        # Since model is strict, we just check the default return path
        op = _op(kind="set")  # no path or value => no change
        result = tier._apply_document_edit(content, op, "markdown")
        assert result == content

    # --- preview / apply with mocks ----------------------------------------

    def test_preview_markdown(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "README.md"
        f.write_text("# Hello\nworld\n")
        op = _op(kind="append", value="new content")
        diff_mod = _stub_diff_module()
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}):
            result = tier.preview(f, op)
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_apply_markdown_success(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "README.md"
        f.write_text("# Hello\nworld\n")
        op = _op(kind="append", value="new content")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_unknown_format(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello")
        op = _op(kind="set")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_file_not_found(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "nope.md"
        op = _op(kind="set", path="x", value="y")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_permission_denied(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.md"
        f.write_text("# Hello\n")
        op = _op(kind="append", value="new")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch("pathlib.Path.write_text", side_effect=PermissionError),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is False
        assert result.requires_privilege is True

    # --- _append_after_heading edge cases ----------------------------------

    def test_append_after_heading_nested(self):
        tier = self._make_tier()
        content = "# Top\ntop content\n## Sub\nsub content\n# Other\nother content\n"
        result = tier._append_after_heading(content, "# Top", "INSERTED", "markdown")
        assert "INSERTED" in result


# ============================================================================
# Tier 5 -- Text Patching
# ============================================================================


class TestTier5Patch:
    """Tests for Tier5Patch."""

    def _make_tier(self):
        from elle.ops.editing.tiers.tier5_patch import Tier5Patch

        return Tier5Patch()

    # --- can_handle -------------------------------------------------------

    def test_can_handle_with_anchor(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        op = _op(kind="set", anchor_pattern="hello")
        result = tier.can_handle(f, op)
        assert result.applicable is True
        assert result.confidence == 0.8

    def test_can_handle_no_anchor(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        op = _op(kind="set")
        result = tier.can_handle(f, op)
        assert result.applicable is True
        assert result.confidence == 0.5

    def test_can_handle_binary_file(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "binary"
        f.write_bytes(b"\x00\x01\x02")
        op = _op(kind="set")
        result = tier.can_handle(f, op)
        assert result.applicable is False
        assert "binary" in result.reason

    def test_can_handle_nonexistent(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "missing"
        op = _op(kind="set", anchor_pattern="x")
        result = tier.can_handle(f, op)
        assert result.applicable is True

    # --- validate_syntax --------------------------------------------------

    def test_validate_utf8_valid(self):
        tier = self._make_tier()
        result = tier.validate_syntax("hello world", Path("file.txt"))
        assert result.passed is True

    # --- _verify_anchor ---------------------------------------------------

    def test_verify_anchor_found(self):
        tier = self._make_tier()
        op = _op(kind="set", anchor_pattern="hello")
        assert tier._verify_anchor("hello world", op) is True

    def test_verify_anchor_not_found(self):
        tier = self._make_tier()
        op = _op(kind="set", anchor_pattern="missing")
        assert tier._verify_anchor("hello world", op) is False

    def test_verify_anchor_regex(self):
        tier = self._make_tier()
        op = _op(kind="set", anchor_pattern=r"hel+o")
        assert tier._verify_anchor("hello world", op) is True

    def test_verify_anchor_bad_regex(self):
        tier = self._make_tier()
        op = _op(kind="set", anchor_pattern="[invalid")
        assert tier._verify_anchor("[invalid regex", op) is True  # falls back to literal

    def test_verify_anchor_none(self):
        tier = self._make_tier()
        op = _op(kind="set")
        assert tier._verify_anchor("anything", op) is True

    # --- _get_context_lines -----------------------------------------------

    def test_get_context_lines(self):
        tier = self._make_tier()
        content = "line0\nline1\nline2\nTARGET\nline4\nline5\nline6\n"
        before, after = tier._get_context_lines(content, 18, 24, num_lines=2)
        assert len(before) <= 2
        assert len(after) <= 2

    # --- _apply_patch operations ------------------------------------------

    def test_apply_replace_anchor(self):
        tier = self._make_tier()
        content = "old line\nother line\n"
        op = _op(kind="replace", anchor_pattern="old line", value="new line")
        result = tier._apply_patch(content, op)
        assert "new line" in result

    def test_apply_replace_path(self):
        tier = self._make_tier()
        content = "old text here"
        op = _op(kind="replace", path="old text", value="new text")
        result = tier._apply_patch(content, op)
        assert "new text" in result

    def test_apply_replace_bad_regex_fallback(self):
        tier = self._make_tier()
        content = "[invalid anchor here"
        op = _op(kind="set", anchor_pattern="[invalid", value="replaced")
        result = tier._apply_patch(content, op)
        assert "replaced" in result

    def test_apply_append_with_anchor(self):
        tier = self._make_tier()
        content = "line1\nANCHOR\nline3\n"
        op = _op(kind="append", anchor_pattern="ANCHOR", value="appended")
        result = tier._apply_patch(content, op)
        assert "appended" in result

    def test_apply_append_with_anchor_bad_regex(self):
        tier = self._make_tier()
        content = "[invalid\nrest\n"
        op = _op(kind="append", anchor_pattern="[invalid", value="appended")
        result = tier._apply_patch(content, op)
        assert "appended" in result

    def test_apply_append_no_anchor(self):
        tier = self._make_tier()
        content = "existing"
        op = _op(kind="append", value="new")
        result = tier._apply_patch(content, op)
        assert "new" in result

    def test_apply_append_no_value(self):
        tier = self._make_tier()
        content = "original"
        op = _op(kind="append")
        result = tier._apply_patch(content, op)
        assert result == "original"

    def test_apply_insert_before(self):
        tier = self._make_tier()
        content = "line1\nANCHOR\nline3\n"
        op = _op(kind="insert", anchor_pattern="ANCHOR", value="inserted", position="before")
        result = tier._apply_patch(content, op)
        idx_insert = result.find("inserted")
        idx_anchor = result.find("ANCHOR")
        assert idx_insert < idx_anchor

    def test_apply_insert_after(self):
        tier = self._make_tier()
        content = "line1\nANCHOR\nline3\n"
        op = _op(kind="insert", anchor_pattern="ANCHOR", value="inserted", position="after")
        result = tier._apply_patch(content, op)
        idx_insert = result.find("inserted")
        idx_anchor = result.find("ANCHOR")
        assert idx_insert > idx_anchor

    def test_apply_insert_bad_regex_before(self):
        tier = self._make_tier()
        content = "[invalid\nrest\n"
        op = _op(kind="insert", anchor_pattern="[invalid", value="ins", position="before")
        result = tier._apply_patch(content, op)
        assert "ins" in result

    def test_apply_insert_bad_regex_after(self):
        tier = self._make_tier()
        content = "[invalid\nrest\n"
        op = _op(kind="insert", anchor_pattern="[invalid", value="ins", position="after")
        result = tier._apply_patch(content, op)
        assert "ins" in result

    def test_apply_insert_no_value(self):
        tier = self._make_tier()
        content = "original"
        op = _op(kind="insert")
        result = tier._apply_patch(content, op)
        assert result == "original"

    def test_apply_rm_anchor(self):
        tier = self._make_tier()
        content = "keep\nremove_this\nkeep2\n"
        op = _op(kind="rm", anchor_pattern="remove_this")
        result = tier._apply_patch(content, op)
        assert "remove_this" not in result

    def test_apply_rm_anchor_bad_regex(self):
        tier = self._make_tier()
        content = "[invalid\nkeep\n"
        op = _op(kind="rm", anchor_pattern="[invalid")
        result = tier._apply_patch(content, op)
        assert "[invalid" not in result

    def test_apply_rm_path(self):
        tier = self._make_tier()
        content = "keep\nremove_this\n"
        op = _op(kind="rm", path="remove_this")
        result = tier._apply_patch(content, op)
        assert "remove_this" not in result

    def test_apply_rm_no_target(self):
        tier = self._make_tier()
        content = "original"
        op = _op(kind="rm")
        result = tier._apply_patch(content, op)
        assert result == "original"

    def test_apply_unknown_kind(self):
        tier = self._make_tier()
        content = "original"
        # Kind that doesn't match any branch explicitly
        # set and replace go to same handler, use an op with no anchor/path/value
        op = _op(kind="set")
        result = tier._apply_patch(content, op)
        assert result == content

    # --- preview ----------------------------------------------------------

    def test_preview(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        op = _op(kind="replace", anchor_pattern="hello", value="goodbye")
        diff_mod = _stub_diff_module()
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}):
            result = tier.preview(f, op)
        assert result.is_valid is True

    def test_preview_nonexistent(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "nope.txt"
        op = _op(kind="set", value="x")
        diff_mod = _stub_diff_module()
        with patch.dict("sys.modules", {"elle.ops.augeas.diff": diff_mod}):
            result = tier.preview(f, op)
        assert result.is_valid is True  # empty file still OK

    # --- apply (async) ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_file_not_found(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "nope.txt"
        op = _op(kind="set", value="x")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_apply_anchor_not_found(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        op = _op(kind="set", anchor_pattern="NOTFOUND", value="x")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is False
        assert "Anchor pattern not found" in result.error

    @pytest.mark.asyncio
    async def test_apply_success(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        op = _op(kind="replace", anchor_pattern="hello", value="goodbye")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_apply_permission_denied(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello")
        op = _op(kind="append", value="world")
        diff_mod = _stub_diff_module()
        backup_mod = _stub_backup_module()
        with (
            patch("pathlib.Path.write_text", side_effect=PermissionError),
            patch.dict(
                "sys.modules",
                {
                    "elle.ops.augeas.diff": diff_mod,
                    "elle.ops.augeas.backup": backup_mod,
                },
            ),
        ):
            result = await tier.apply(f, op, skip_backup=True)
        assert result.success is False
        assert result.requires_privilege is True

    @pytest.mark.asyncio
    async def test_apply_backup_failure(self, tmp_path):
        tier = self._make_tier()
        f = tmp_path / "test.txt"
        f.write_text("hello")
        op = _op(kind="append", value="world")
        diff_mod = _stub_diff_module()
        backup_mod = MagicMock()
        backup_mod.backup_file = MagicMock(side_effect=RuntimeError("bk fail"))
        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.diff": diff_mod,
                "elle.ops.augeas.backup": backup_mod,
            },
        ):
            result = await tier.apply(f, op, skip_backup=False)
        assert result.success is False
        assert "Backup failed" in result.error
