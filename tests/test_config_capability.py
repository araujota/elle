from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.core.config import (
    CONFIG_CAPABILITIES,
    ConfigEditCapability,
    ConfigEditInput,
    ConfigEditOutput,
    ConfigOperation,
    ConfigPreviewCapability,
    ConfigPreviewInput,
    ConfigRollbackCapability,
    ConfigRollbackInput,
)
from elle.capabilities.models import CapabilityResult

# =========================================================================
# ConfigOperation model tests
# =========================================================================


class TestConfigOperationModel:
    def test_set_operation(self) -> None:
        op = ConfigOperation(kind="set", path="/files/etc/test/key", value="val")
        assert op.kind == "set"
        assert op.value == "val"

    def test_rm_operation(self) -> None:
        op = ConfigOperation(kind="rm", path="/files/etc/test/key")
        assert op.kind == "rm"
        assert op.value is None


# =========================================================================
# ConfigEditCapability tests
# =========================================================================


class TestConfigEditCapability:
    @pytest.fixture()
    def cap(self) -> ConfigEditCapability:
        return ConfigEditCapability()

    @pytest.fixture()
    def edit_input(self) -> ConfigEditInput:
        return ConfigEditInput(
            file_path="/etc/test.conf",
            operations=(ConfigOperation(kind="set", path="section/key", value="new_value"),),
            description="test edit",
        )

    def test_spec_name(self, cap: ConfigEditCapability) -> None:
        assert cap.spec.name == "config.edit"

    def test_spec_domain(self, cap: ConfigEditCapability) -> None:
        assert cap.spec.domain == "config"

    def test_spec_risk(self, cap: ConfigEditCapability) -> None:
        assert cap.spec.risk == "medium"

    def test_spec_trust_level(self, cap: ConfigEditCapability) -> None:
        assert cap.spec.trust_level == "core"

    def test_dry_run_augeas_not_available(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        with patch.dict("sys.modules", {"elle.ops.augeas.controller": None, "elle.ops.augeas.models": None}):
            result = cap.dry_run(edit_input)
            assert result.is_valid is False

    def test_dry_run_success(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        mock_preview = MagicMock()
        mock_preview.requires_privilege = False
        mock_preview.diff = "--- a\n+++ b\n-old\n+new"

        mock_controller = MagicMock()
        mock_controller.preview.return_value = mock_preview

        mock_augeas_module = MagicMock()
        mock_augeas_module.get_controller.return_value = mock_controller
        mock_models_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.controller": mock_augeas_module,
                "elle.ops.augeas.models": mock_models_module,
            },
        ):
            result = cap.dry_run(edit_input)
            assert result.is_valid is True
            assert result.diff == "--- a\n+++ b\n-old\n+new"

    def test_dry_run_exception(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        mock_augeas_module = MagicMock()
        mock_augeas_module.get_controller.side_effect = RuntimeError("boom")
        mock_models_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.controller": mock_augeas_module,
                "elle.ops.augeas.models": mock_models_module,
            },
        ):
            result = cap.dry_run(edit_input)
            assert result.is_valid is False

    def test_run_import_error(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        with patch.dict("sys.modules", {"elle.ops.augeas.controller": None, "elle.ops.augeas.models": None}):
            result = cap.run(edit_input)
            assert result.success is False
            assert "not available" in (result.error or "")

    def test_run_generic_exception(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        mock_augeas_module = MagicMock()
        mock_augeas_module.get_controller.side_effect = RuntimeError("controller error")
        mock_models_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.controller": mock_augeas_module,
                "elle.ops.augeas.models": mock_models_module,
            },
        ):
            result = cap.run(edit_input)
            assert result.success is False
            assert "controller error" in (result.error or "")

    def test_verify_file_not_found(self, cap: ConfigEditCapability) -> None:
        inp = ConfigEditInput(
            file_path="/tmp/nonexistent_config_xyz.conf",
            operations=(),
            description="",
        )
        result = cap.verify(inp)
        assert result.passed is False

    def test_verify_file_exists_import_error(self, cap: ConfigEditCapability, tmp_path: Path) -> None:
        f = tmp_path / "test.conf"
        f.write_text("key=val")
        inp = ConfigEditInput(file_path=str(f), operations=(), description="")
        with patch.dict("sys.modules", {"elle.ops.augeas.validators": None}):
            result = cap.verify(inp)
            assert result.passed is True

    def test_rollback_no_output(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        run_result = CapabilityResult(success=True, output=None)
        rollback_result = cap.rollback(edit_input, run_result)
        assert rollback_result.success is False

    def test_rollback_no_backup(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        output = ConfigEditOutput(
            file_path="/etc/test.conf",
            changes=(),
            diff="",
            backup_path=None,
        )
        run_result = CapabilityResult(success=True, output=output)
        rollback_result = cap.rollback(edit_input, run_result)
        assert rollback_result.success is False

    def test_rollback_import_error(self, cap: ConfigEditCapability, edit_input: ConfigEditInput) -> None:
        output = ConfigEditOutput(
            file_path="/etc/test.conf",
            changes=(),
            diff="",
            backup_path="/tmp/backup.conf",
        )
        run_result = CapabilityResult(success=True, output=output)
        with patch.dict("sys.modules", {"elle.ops.augeas.controller": None}):
            rollback_result = cap.rollback(edit_input, run_result)
            assert rollback_result.success is False


# =========================================================================
# ConfigPreviewCapability tests
# =========================================================================


class TestConfigPreviewCapability:
    @pytest.fixture()
    def cap(self) -> ConfigPreviewCapability:
        return ConfigPreviewCapability()

    @pytest.fixture()
    def preview_input(self) -> ConfigPreviewInput:
        return ConfigPreviewInput(
            file_path="/etc/test.conf",
            operations=(ConfigOperation(kind="set", path="section/key", value="val"),),
        )

    def test_spec_name(self, cap: ConfigPreviewCapability) -> None:
        assert cap.spec.name == "config.preview"

    def test_spec_risk_none(self, cap: ConfigPreviewCapability) -> None:
        assert cap.spec.risk == "none"

    def test_spec_idempotent(self, cap: ConfigPreviewCapability) -> None:
        assert cap.spec.idempotent is True

    def test_dry_run_always_valid(self, cap: ConfigPreviewCapability, preview_input: ConfigPreviewInput) -> None:
        result = cap.dry_run(preview_input)
        assert result.is_valid is True
        assert result.requires_confirmation is False

    def test_run_import_error(self, cap: ConfigPreviewCapability, preview_input: ConfigPreviewInput) -> None:
        with patch.dict("sys.modules", {"elle.ops.augeas.controller": None, "elle.ops.augeas.models": None}):
            result = cap.run(preview_input)
            assert result.success is False

    def test_run_exception(self, cap: ConfigPreviewCapability, preview_input: ConfigPreviewInput) -> None:
        mock_augeas_module = MagicMock()
        mock_augeas_module.get_controller.side_effect = RuntimeError("preview error")
        mock_models_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.ops.augeas.controller": mock_augeas_module,
                "elle.ops.augeas.models": mock_models_module,
            },
        ):
            result = cap.run(preview_input)
            assert result.success is False
            assert "preview error" in (result.error or "")


# =========================================================================
# ConfigRollbackCapability tests
# =========================================================================


class TestConfigRollbackCapability:
    @pytest.fixture()
    def cap(self) -> ConfigRollbackCapability:
        return ConfigRollbackCapability()

    @pytest.fixture()
    def rollback_input(self) -> ConfigRollbackInput:
        return ConfigRollbackInput(
            incident_id="inc-123",
            path="/etc/test.conf",
        )

    def test_spec_name(self, cap: ConfigRollbackCapability) -> None:
        assert cap.spec.name == "config.rollback"

    def test_spec_risk_high(self, cap: ConfigRollbackCapability) -> None:
        assert cap.spec.risk == "high"

    def test_spec_requires_privilege(self, cap: ConfigRollbackCapability) -> None:
        assert cap.spec.requires_privilege is True

    def test_dry_run_import_error(self, cap: ConfigRollbackCapability, rollback_input: ConfigRollbackInput) -> None:
        with patch.dict("sys.modules", {"elle.daemon.incidents.config_tracker": None}):
            result = cap.dry_run(rollback_input)
            assert result.is_valid is False

    def test_run_import_error(self, cap: ConfigRollbackCapability, rollback_input: ConfigRollbackInput) -> None:
        with patch.dict("sys.modules", {"elle.daemon.incidents.config_tracker": None}):
            result = cap.run(rollback_input)
            assert result.success is False

    def test_verify_file_missing(self, cap: ConfigRollbackCapability, rollback_input: ConfigRollbackInput) -> None:
        inp = ConfigRollbackInput(
            incident_id="inc-123",
            path="/tmp/nonexistent_rollback_xyz.conf",
        )
        result = cap.verify(inp)
        assert result.passed is False

    def test_verify_file_exists(self, cap: ConfigRollbackCapability, tmp_path: Path) -> None:
        f = tmp_path / "config.conf"
        f.write_text("restored")
        inp = ConfigRollbackInput(incident_id="inc-123", path=str(f))
        result = cap.verify(inp)
        assert result.passed is True


# =========================================================================
# CONFIG_CAPABILITIES export tests
# =========================================================================


class TestConfigCapabilitiesExport:
    def test_exports_three_capabilities(self) -> None:
        assert len(CONFIG_CAPABILITIES) == 3

    def test_contains_edit(self) -> None:
        assert ConfigEditCapability in CONFIG_CAPABILITIES

    def test_contains_preview(self) -> None:
        assert ConfigPreviewCapability in CONFIG_CAPABILITIES

    def test_contains_rollback(self) -> None:
        assert ConfigRollbackCapability in CONFIG_CAPABILITIES
