from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.reboot.grub import (
    GRUBResult,
    _find_submenu_entries,
    _get_grub_oneshot,
    clear_grub_oneshot,
    confirm_boot_success,
    detect_grub_mode,
    get_boot_id,
    get_grub_default,
    get_grub_entries,
    get_grub_state,
    get_saved_default,
    has_rebooted_since,
    is_grub_available,
    prepare_rollback,
    set_grub_default,
    set_grub_oneshot,
    trigger_rollback_reboot,
)

# ---------------------------------------------------------------------------
# detect_grub_mode
# ---------------------------------------------------------------------------


class TestDetectGrubMode:
    def test_efi_detected(self):
        """EFI directory exists -> 'efi'."""
        efi_mock = MagicMock()
        efi_mock.exists.return_value = True
        with patch("elle.daemon.reboot.grub.Path", return_value=efi_mock):
            result = detect_grub_mode()
        assert result == "efi"

    def test_legacy_detected(self):
        """No EFI but GRUB_CONFIG exists -> 'legacy'."""
        efi_mock = MagicMock()
        efi_mock.exists.return_value = False
        grub_cfg_mock = MagicMock()
        grub_cfg_mock.exists.return_value = True
        with (
            patch("elle.daemon.reboot.grub.Path", return_value=efi_mock),
            patch("elle.daemon.reboot.grub.GRUB_CONFIG", grub_cfg_mock),
        ):
            result = detect_grub_mode()
        assert result == "legacy"

    def test_unknown(self):
        """Neither EFI nor GRUB_CONFIG -> 'unknown'."""
        efi_mock = MagicMock()
        efi_mock.exists.return_value = False
        grub_cfg_mock = MagicMock()
        grub_cfg_mock.exists.return_value = False
        with (
            patch("elle.daemon.reboot.grub.Path", return_value=efi_mock),
            patch("elle.daemon.reboot.grub.GRUB_CONFIG", grub_cfg_mock),
        ):
            result = detect_grub_mode()
        assert result == "unknown"


# ---------------------------------------------------------------------------
# is_grub_available
# ---------------------------------------------------------------------------


class TestIsGrubAvailable:
    def test_available(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("elle.daemon.reboot.grub.subprocess.run", return_value=mock_result):
            assert is_grub_available() is True

    def test_not_available(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("elle.daemon.reboot.grub.subprocess.run", return_value=mock_result):
            assert is_grub_available() is False

    def test_exception(self):
        with patch("elle.daemon.reboot.grub.subprocess.run", side_effect=FileNotFoundError):
            assert is_grub_available() is False


# ---------------------------------------------------------------------------
# get_grub_default
# ---------------------------------------------------------------------------


class TestGetGrubDefault:
    def test_reads_default(self, tmp_path):
        grub_file = tmp_path / "grub"
        grub_file.write_text('GRUB_DEFAULT="saved"\nGRUB_TIMEOUT=5\n')
        with patch("elle.daemon.reboot.grub.GRUB_DEFAULT", grub_file):
            result = get_grub_default()
        assert result == "saved"

    def test_no_file(self, tmp_path):
        with patch("elle.daemon.reboot.grub.GRUB_DEFAULT", tmp_path / "nonexistent"):
            result = get_grub_default()
        assert result is None

    def test_no_default_key(self, tmp_path):
        grub_file = tmp_path / "grub"
        grub_file.write_text("GRUB_TIMEOUT=5\n")
        with patch("elle.daemon.reboot.grub.GRUB_DEFAULT", grub_file):
            result = get_grub_default()
        assert result is None

    def test_read_error(self, tmp_path):
        grub_file = tmp_path / "grub"
        grub_file.write_text("GRUB_DEFAULT=0\n")
        with (
            patch("elle.daemon.reboot.grub.GRUB_DEFAULT", grub_file),
            patch.object(Path, "read_text", side_effect=PermissionError),
        ):
            result = get_grub_default()
        assert result is None


# ---------------------------------------------------------------------------
# get_saved_default
# ---------------------------------------------------------------------------


class TestGetSavedDefault:
    def test_reads_saved_entry(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "saved_entry=Ubuntu\nother=thing\n"
        with (
            patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=True))),
            patch("elle.daemon.reboot.grub.subprocess.run", return_value=mock_result),
        ):
            result = get_saved_default()
        assert result == "Ubuntu"

    def test_no_saved_entry(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "other=value\n"
        with (
            patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=True))),
            patch("elle.daemon.reboot.grub.subprocess.run", return_value=mock_result),
        ):
            result = get_saved_default()
        assert result is None

    def test_no_grubenv(self):
        with patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=False))):
            result = get_saved_default()
        assert result is None

    def test_command_fails(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        with (
            patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=True))),
            patch("elle.daemon.reboot.grub.subprocess.run", return_value=mock_result),
        ):
            result = get_saved_default()
        assert result is None

    def test_exception(self):
        with (
            patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=True))),
            patch("elle.daemon.reboot.grub.subprocess.run", side_effect=Exception("fail")),
        ):
            result = get_saved_default()
        assert result is None


# ---------------------------------------------------------------------------
# get_grub_entries
# ---------------------------------------------------------------------------


class TestGetGrubEntries:
    def test_parses_entries(self, tmp_path):
        cfg = tmp_path / "grub.cfg"
        cfg.write_text("menuentry 'Ubuntu' {\n}\nmenuentry 'Recovery' {\n}\n")
        with patch("elle.daemon.reboot.grub.GRUB_CONFIG", cfg):
            entries = get_grub_entries()
        assert "Ubuntu" in entries
        assert "Recovery" in entries

    def test_no_config(self, tmp_path):
        with patch("elle.daemon.reboot.grub.GRUB_CONFIG", tmp_path / "nope"):
            entries = get_grub_entries()
        assert entries == []

    def test_with_submenu(self, tmp_path):
        cfg = tmp_path / "grub.cfg"
        cfg.write_text("menuentry 'Ubuntu' {\n}\nsubmenu 'Advanced' {\n  menuentry 'Ubuntu (old)' {\n  }\n}\n")
        with patch("elle.daemon.reboot.grub.GRUB_CONFIG", cfg):
            entries = get_grub_entries()
        assert "Ubuntu" in entries
        assert "Advanced>Ubuntu (old)" in entries

    def test_parse_exception(self, tmp_path):
        cfg = tmp_path / "grub.cfg"
        cfg.write_text("menuentry 'Ubuntu' {\n}\n")
        with patch("elle.daemon.reboot.grub.GRUB_CONFIG", cfg), patch.object(Path, "read_text", side_effect=IOError):
            entries = get_grub_entries()
        assert entries == []


# ---------------------------------------------------------------------------
# _find_submenu_entries
# ---------------------------------------------------------------------------


class TestFindSubmenuEntries:
    def test_finds_entries(self):
        content = "submenu 'Advanced' {\n  menuentry 'Ubuntu (old)' {}\n  menuentry 'Ubuntu (recovery)' {}\n}\n"
        entries = _find_submenu_entries(content, "Advanced")
        assert "Ubuntu (old)" in entries
        assert "Ubuntu (recovery)" in entries

    def test_no_match(self):
        entries = _find_submenu_entries("nothing here", "Advanced")
        assert entries == []


# ---------------------------------------------------------------------------
# get_grub_state
# ---------------------------------------------------------------------------


class TestGetGrubState:
    def test_returns_state(self):
        with (
            patch("elle.daemon.reboot.grub.get_grub_default", return_value="0"),
            patch("elle.daemon.reboot.grub.get_grub_entries", return_value=["Ubuntu"]),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value="Ubuntu"),
            patch("elle.daemon.reboot.grub._get_grub_oneshot", return_value=None),
        ):
            state = get_grub_state()
        assert state.default_entry == "0"
        assert "Ubuntu" in state.entries


# ---------------------------------------------------------------------------
# _get_grub_oneshot
# ---------------------------------------------------------------------------


class TestGetGrubOneshot:
    def test_reads_next_entry(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "next_entry=2\n"
        with (
            patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=True))),
            patch("elle.daemon.reboot.grub.subprocess.run", return_value=mock_result),
        ):
            result = _get_grub_oneshot()
        assert result == "2"

    def test_no_next_entry(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "saved_entry=0\n"
        with (
            patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=True))),
            patch("elle.daemon.reboot.grub.subprocess.run", return_value=mock_result),
        ):
            result = _get_grub_oneshot()
        assert result is None

    def test_no_grubenv(self):
        with patch("elle.daemon.reboot.grub.GRUB_ENV", MagicMock(exists=MagicMock(return_value=False))):
            result = _get_grub_oneshot()
        assert result is None


# ---------------------------------------------------------------------------
# Async boot management
# ---------------------------------------------------------------------------


class TestSetGrubOneshot:
    @pytest.mark.asyncio
    async def test_grub_not_available(self):
        with patch("elle.daemon.reboot.grub.is_grub_available", return_value=False):
            result = await set_grub_oneshot("0")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_success(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=True))
        with (
            patch("elle.daemon.reboot.grub.is_grub_available", return_value=True),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await set_grub_oneshot("0")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_failure(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=False, error="denied", exit_code=126))
        with (
            patch("elle.daemon.reboot.grub.is_grub_available", return_value=True),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await set_grub_oneshot("0")
        assert result.success is False


class TestConfirmBootSuccess:
    @pytest.mark.asyncio
    async def test_grub_not_available(self):
        with patch("elle.daemon.reboot.grub.is_grub_available", return_value=False):
            result = await confirm_boot_success("0")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_success_with_entry(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=True))
        with (
            patch("elle.daemon.reboot.grub.is_grub_available", return_value=True),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await confirm_boot_success("0")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_success_without_entry(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=True))
        with (
            patch("elle.daemon.reboot.grub.is_grub_available", return_value=True),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value="saved_val"),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await confirm_boot_success(None)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_failure(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=False, error="fail", exit_code=1))
        with (
            patch("elle.daemon.reboot.grub.is_grub_available", return_value=True),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await confirm_boot_success("0")
        assert result.success is False


class TestClearGrubOneshot:
    @pytest.mark.asyncio
    async def test_grub_not_available(self):
        with patch("elle.daemon.reboot.grub.is_grub_available", return_value=False):
            result = await clear_grub_oneshot()
        assert result.success is False

    @pytest.mark.asyncio
    async def test_success(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=True))
        with (
            patch("elle.daemon.reboot.grub.is_grub_available", return_value=True),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await clear_grub_oneshot()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_failure_still_returns_true(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=False))
        with (
            patch("elle.daemon.reboot.grub.is_grub_available", return_value=True),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await clear_grub_oneshot()
        # Even failure returns success=True (see source)
        assert result.success is True


class TestSetGrubDefault:
    @pytest.mark.asyncio
    async def test_delegates(self):
        with patch("elle.daemon.reboot.grub.confirm_boot_success", new_callable=AsyncMock) as mock:
            mock.return_value = GRUBResult(success=True, message="ok")
            result = await set_grub_default("0")
        assert result.success is True


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestPrepareRollback:
    @pytest.mark.asyncio
    async def test_with_fallback(self):
        with (
            patch("elle.daemon.reboot.grub.get_grub_default", return_value="0"),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value="0"),
        ):
            result = await prepare_rollback("1")
        assert result.success is True
        assert "1" in result.message

    @pytest.mark.asyncio
    async def test_default_fallback(self):
        with (
            patch("elle.daemon.reboot.grub.get_grub_default", return_value="0"),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value="0"),
        ):
            result = await prepare_rollback()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_saved_mode_without_saved_entry(self):
        mock_helper = MagicMock()
        mock_helper._run_pkexec = AsyncMock(return_value=MagicMock(success=True))
        with (
            patch("elle.daemon.reboot.grub.get_grub_default", return_value="saved"),
            patch("elle.daemon.reboot.grub.get_saved_default", return_value=None),
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("elle.security.polkit_helper.PolkitAction"),
        ):
            result = await prepare_rollback()
        assert result.success is True


class TestTriggerRollbackReboot:
    @pytest.mark.asyncio
    async def test_success(self):
        with (
            patch("elle.daemon.reboot.grub.set_grub_default", new_callable=AsyncMock) as mock_default,
            patch("elle.daemon.reboot.grub.clear_grub_oneshot", new_callable=AsyncMock),
        ):
            mock_default.return_value = GRUBResult(success=True)
            result = await trigger_rollback_reboot("0")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_default_fails(self):
        with patch("elle.daemon.reboot.grub.set_grub_default", new_callable=AsyncMock) as mock_default:
            mock_default.return_value = GRUBResult(success=False, error="fail")
            result = await trigger_rollback_reboot("0")
        assert result.success is False


# ---------------------------------------------------------------------------
# Boot ID
# ---------------------------------------------------------------------------


class TestBootId:
    def test_get_boot_id(self, tmp_path):
        boot_id_file = tmp_path / "boot_id"
        boot_id_file.write_text("abc-123\n")
        with patch("elle.daemon.reboot.grub.Path") as MockPath:
            mock = MagicMock()
            mock.exists.return_value = True
            mock.read_text.return_value = "abc-123\n"
            MockPath.return_value = mock
            result = get_boot_id()
        assert result == "abc-123"

    def test_get_boot_id_not_exists(self):
        with patch("elle.daemon.reboot.grub.Path") as MockPath:
            mock = MagicMock()
            mock.exists.return_value = False
            MockPath.return_value = mock
            result = get_boot_id()
        assert result is None

    def test_get_boot_id_error(self):
        with patch("elle.daemon.reboot.grub.Path") as MockPath:
            mock = MagicMock()
            mock.exists.side_effect = PermissionError
            MockPath.return_value = mock
            result = get_boot_id()
        assert result is None

    def test_has_rebooted_since_different(self):
        with patch("elle.daemon.reboot.grub.get_boot_id", return_value="new"):
            assert has_rebooted_since("old") is True

    def test_has_rebooted_since_same(self):
        with patch("elle.daemon.reboot.grub.get_boot_id", return_value="same"):
            assert has_rebooted_since("same") is False

    def test_has_rebooted_since_none_saved(self):
        assert has_rebooted_since(None) is False

    def test_has_rebooted_since_none_current(self):
        with patch("elle.daemon.reboot.grub.get_boot_id", return_value=None):
            assert has_rebooted_since("old") is False
