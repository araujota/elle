"""Tests for GRUB management module (elle.daemon.reboot.grub).

Covers:
- Detection: detect_grub_mode, is_grub_available, get_grub_default,
  get_saved_default, get_grub_entries, _find_submenu_entries, get_grub_state
- Boot ID: get_boot_id, has_rebooted_since
- Async boot management: set_grub_oneshot, confirm_boot_success,
  clear_grub_oneshot, set_grub_default
- Rollback: prepare_rollback, trigger_rollback_reboot

All filesystem and subprocess calls are mocked so the tests run without
GRUB installed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.reboot.grub import (
    GRUBResult,
    _find_submenu_entries,
    _get_grub_oneshot,
    detect_grub_mode,
    get_boot_id,
    get_grub_default,
    get_grub_entries,
    get_grub_state,
    get_saved_default,
    has_rebooted_since,
    is_grub_available,
)

# Patch target prefix for the module under test
_MOD = "elle.daemon.reboot.grub"


# =============================================================================
# GRUBResult model
# =============================================================================


class TestGRUBResult:
    """Verify GRUBResult Pydantic model basics."""

    def test_success_result(self):
        """A success result should carry the correct defaults."""
        r = GRUBResult(success=True, message="ok")
        assert r.success is True
        assert r.message == "ok"
        assert r.error == ""
        assert r.exit_code == 0

    def test_failure_result(self):
        """A failure result should carry the error and exit code."""
        r = GRUBResult(success=False, error="boom", exit_code=1)
        assert r.success is False
        assert r.error == "boom"
        assert r.exit_code == 1

    def test_frozen_model(self):
        """GRUBResult is frozen and should reject attribute mutation."""
        r = GRUBResult(success=True)
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]


# =============================================================================
# detect_grub_mode
# =============================================================================


class TestDetectGrubMode:
    """Tests for detect_grub_mode()."""

    @patch("pathlib.Path.exists")
    def test_efi_mode(self, mock_exists):
        """Returns 'efi' when /sys/firmware/efi exists."""
        # Path.exists is called first for /sys/firmware/efi
        mock_exists.return_value = True
        assert detect_grub_mode() == "efi"

    @patch(f"{_MOD}.GRUB_CONFIG")
    @patch(f"{_MOD}.Path")
    def test_legacy_mode(self, mock_path_cls, mock_grub_config):
        """Returns 'legacy' when EFI dir missing but grub.cfg exists."""
        efi_path = MagicMock()
        efi_path.exists.return_value = False
        mock_path_cls.return_value = efi_path
        mock_grub_config.exists.return_value = True
        assert detect_grub_mode() == "legacy"

    @patch(f"{_MOD}.GRUB_CONFIG")
    @patch(f"{_MOD}.Path")
    def test_unknown_mode(self, mock_path_cls, mock_grub_config):
        """Returns 'unknown' when neither EFI dir nor grub.cfg exist."""
        efi_path = MagicMock()
        efi_path.exists.return_value = False
        mock_path_cls.return_value = efi_path
        mock_grub_config.exists.return_value = False
        assert detect_grub_mode() == "unknown"


# =============================================================================
# is_grub_available
# =============================================================================


class TestIsGrubAvailable:
    """Tests for is_grub_available()."""

    @patch("subprocess.run")
    def test_grub_available(self, mock_run):
        """Returns True when 'which grub-reboot' succeeds."""
        mock_run.return_value = MagicMock(returncode=0)
        assert is_grub_available() is True
        mock_run.assert_called_once_with(
            ["which", "grub-reboot"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("subprocess.run")
    def test_grub_not_available(self, mock_run):
        """Returns False when 'which grub-reboot' has non-zero exit."""
        mock_run.return_value = MagicMock(returncode=1)
        assert is_grub_available() is False

    @patch("subprocess.run", side_effect=FileNotFoundError("no such binary"))
    def test_exception_returns_false(self, mock_run):
        """Returns False when subprocess.run raises an exception."""
        assert is_grub_available() is False


# =============================================================================
# get_grub_default
# =============================================================================


class TestGetGrubDefault:
    """Tests for get_grub_default()."""

    @patch(f"{_MOD}.GRUB_DEFAULT")
    def test_reads_numeric_default(self, mock_path):
        """Reads GRUB_DEFAULT=0 from config."""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "# comment\nGRUB_DEFAULT=0\nGRUB_TIMEOUT=5\n"
        assert get_grub_default() == "0"

    @patch(f"{_MOD}.GRUB_DEFAULT")
    def test_reads_saved_default(self, mock_path):
        """Reads GRUB_DEFAULT=saved from config."""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'GRUB_DEFAULT="saved"\n'
        assert get_grub_default() == "saved"

    @patch(f"{_MOD}.GRUB_DEFAULT")
    def test_reads_quoted_value_single(self, mock_path):
        """Strips single quotes around value."""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "GRUB_DEFAULT='2'\n"
        assert get_grub_default() == "2"

    @patch(f"{_MOD}.GRUB_DEFAULT")
    def test_file_missing(self, mock_path):
        """Returns None when /etc/default/grub does not exist."""
        mock_path.exists.return_value = False
        assert get_grub_default() is None

    @patch(f"{_MOD}.GRUB_DEFAULT")
    def test_read_error(self, mock_path):
        """Returns None when file read raises an exception."""
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = PermissionError("denied")
        assert get_grub_default() is None

    @patch(f"{_MOD}.GRUB_DEFAULT")
    def test_no_grub_default_line(self, mock_path):
        """Returns None when GRUB_DEFAULT line is absent."""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "GRUB_TIMEOUT=5\n"
        assert get_grub_default() is None


# =============================================================================
# get_saved_default
# =============================================================================


class TestGetSavedDefault:
    """Tests for get_saved_default()."""

    @patch("subprocess.run")
    @patch(f"{_MOD}.GRUB_ENV")
    def test_reads_saved_entry(self, mock_env, mock_run):
        """Returns saved_entry value from grub-editenv output."""
        mock_env.exists.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="saved_entry=1\nnext_entry=\n",
        )
        assert get_saved_default() == "1"

    @patch(f"{_MOD}.GRUB_ENV")
    def test_grubenv_missing(self, mock_env):
        """Returns None when grubenv does not exist."""
        mock_env.exists.return_value = False
        assert get_saved_default() is None

    @patch("subprocess.run")
    @patch(f"{_MOD}.GRUB_ENV")
    def test_grub_editenv_nonzero_exit(self, mock_env, mock_run):
        """Returns None when grub-editenv exits with non-zero code."""
        mock_env.exists.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert get_saved_default() is None

    @patch("subprocess.run")
    @patch(f"{_MOD}.GRUB_ENV")
    def test_no_saved_entry_line(self, mock_env, mock_run):
        """Returns None when output has no saved_entry= line."""
        mock_env.exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="next_entry=3\n")
        assert get_saved_default() is None

    @patch("subprocess.run", side_effect=OSError("timeout"))
    @patch(f"{_MOD}.GRUB_ENV")
    def test_exception_returns_none(self, mock_env, mock_run):
        """Returns None when subprocess raises."""
        mock_env.exists.return_value = True
        assert get_saved_default() is None


# =============================================================================
# get_grub_entries / _find_submenu_entries
# =============================================================================

# Realistic minimal grub.cfg fragment for testing entry parsing
_SAMPLE_GRUB_CFG = """\
### BEGIN /etc/grub.d/10_linux ###
menuentry 'Ubuntu' --class ubuntu {
    linux /vmlinuz root=/dev/sda1
    initrd /initrd.img
}
menuentry 'Ubuntu, with Linux 5.15.0-91-generic' --class ubuntu {
    linux /vmlinuz-5.15.0-91-generic root=/dev/sda1
}
submenu 'Advanced options for Ubuntu' $menuentry_id_option {
    menuentry 'Ubuntu, with Linux 5.15.0-91-generic (recovery mode)' {
        linux /vmlinuz-5.15.0-91-generic root=/dev/sda1 recovery
    }
    menuentry 'Ubuntu, with Linux 5.15.0-88-generic' {
        linux /vmlinuz-5.15.0-88-generic root=/dev/sda1
    }
}
### END /etc/grub.d/10_linux ###
"""


class TestGetGrubEntries:
    """Tests for get_grub_entries() and _find_submenu_entries()."""

    @patch(f"{_MOD}.GRUB_CONFIG")
    def test_parses_top_level_entries(self, mock_config):
        """Extracts top-level menuentry names."""
        mock_config.exists.return_value = True
        mock_config.read_text.return_value = _SAMPLE_GRUB_CFG

        entries = get_grub_entries()
        assert "Ubuntu" in entries
        assert "Ubuntu, with Linux 5.15.0-91-generic" in entries

    @patch(f"{_MOD}.GRUB_CONFIG")
    def test_parses_submenu_entries(self, mock_config):
        """Extracts nested submenu>entry entries."""
        mock_config.exists.return_value = True
        mock_config.read_text.return_value = _SAMPLE_GRUB_CFG

        entries = get_grub_entries()
        expected_nested = "Advanced options for Ubuntu>Ubuntu, with Linux 5.15.0-91-generic (recovery mode)"
        assert expected_nested in entries

    @patch(f"{_MOD}.GRUB_CONFIG")
    def test_total_entry_count(self, mock_config):
        """The sample config yields 6 entries: 2 top-level menuentry matches,
        2 nested menuentry matches from the top-level regex pass, and 2
        submenu>entry entries from the submenu parsing pass."""
        mock_config.exists.return_value = True
        mock_config.read_text.return_value = _SAMPLE_GRUB_CFG

        entries = get_grub_entries()
        # The top-level regex picks up all 4 menuentry lines (including those
        # inside the submenu block), then 2 submenu>entry entries are appended.
        assert len(entries) == 6

    @patch(f"{_MOD}.GRUB_CONFIG")
    def test_config_missing(self, mock_config):
        """Returns empty list when grub.cfg does not exist."""
        mock_config.exists.return_value = False
        assert get_grub_entries() == []

    @patch(f"{_MOD}.GRUB_CONFIG")
    def test_config_read_error(self, mock_config):
        """Returns empty list on read failure."""
        mock_config.exists.return_value = True
        mock_config.read_text.side_effect = PermissionError("denied")
        assert get_grub_entries() == []

    def test_find_submenu_entries_no_match(self):
        """_find_submenu_entries returns empty when submenu name is not found."""
        assert _find_submenu_entries("no submenus here", "Nonexistent") == []

    @patch(f"{_MOD}.GRUB_CONFIG")
    def test_empty_config(self, mock_config):
        """Returns empty list for empty config file."""
        mock_config.exists.return_value = True
        mock_config.read_text.return_value = ""
        assert get_grub_entries() == []


# =============================================================================
# _get_grub_oneshot
# =============================================================================


class TestGetGrubOneshot:
    """Tests for _get_grub_oneshot()."""

    @patch("subprocess.run")
    @patch(f"{_MOD}.GRUB_ENV")
    def test_reads_next_entry(self, mock_env, mock_run):
        """Returns next_entry value from grubenv."""
        mock_env.exists.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="saved_entry=0\nnext_entry=2\n",
        )
        assert _get_grub_oneshot() == "2"

    @patch(f"{_MOD}.GRUB_ENV")
    def test_grubenv_missing(self, mock_env):
        """Returns None when grubenv missing."""
        mock_env.exists.return_value = False
        assert _get_grub_oneshot() is None

    @patch("subprocess.run")
    @patch(f"{_MOD}.GRUB_ENV")
    def test_no_next_entry(self, mock_env, mock_run):
        """Returns None when no next_entry line is present."""
        mock_env.exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="saved_entry=0\n")
        assert _get_grub_oneshot() is None


# =============================================================================
# get_grub_state
# =============================================================================


class TestGetGrubState:
    """Tests for get_grub_state()."""

    @patch(f"{_MOD}._get_grub_oneshot", return_value=None)
    @patch(f"{_MOD}.get_saved_default", return_value="0")
    @patch(f"{_MOD}.get_grub_entries", return_value=["Ubuntu", "Recovery"])
    @patch(f"{_MOD}.get_grub_default", return_value="0")
    def test_assembles_state(self, mock_def, mock_ent, mock_saved, mock_oneshot):
        """get_grub_state aggregates all component calls."""
        state = get_grub_state()
        assert state.default_entry == "0"
        assert state.entries == ("Ubuntu", "Recovery")
        assert state.saved_default == "0"
        assert state.oneshot_entry is None

    @patch(f"{_MOD}._get_grub_oneshot", return_value="3")
    @patch(f"{_MOD}.get_saved_default", return_value=None)
    @patch(f"{_MOD}.get_grub_entries", return_value=[])
    @patch(f"{_MOD}.get_grub_default", return_value=None)
    def test_defaults_on_missing(self, mock_def, mock_ent, mock_saved, mock_oneshot):
        """Uses '0' fallback when get_grub_default returns None."""
        state = get_grub_state()
        assert state.default_entry == "0"
        assert state.entries == ()
        assert state.oneshot_entry == "3"


# =============================================================================
# Boot ID functions
# =============================================================================


class TestBootId:
    """Tests for get_boot_id() and has_rebooted_since()."""

    @patch(f"{_MOD}.Path")
    def test_get_boot_id_success(self, mock_path_cls):
        """Returns stripped boot ID from /proc/sys/kernel/random/boot_id."""
        boot_id_file = MagicMock()
        boot_id_file.exists.return_value = True
        boot_id_file.read_text.return_value = "abc-123-def\n"
        mock_path_cls.return_value = boot_id_file
        assert get_boot_id() == "abc-123-def"

    @patch(f"{_MOD}.Path")
    def test_get_boot_id_missing(self, mock_path_cls):
        """Returns None when boot_id file does not exist."""
        boot_id_file = MagicMock()
        boot_id_file.exists.return_value = False
        mock_path_cls.return_value = boot_id_file
        assert get_boot_id() is None

    @patch(f"{_MOD}.Path")
    def test_get_boot_id_read_error(self, mock_path_cls):
        """Returns None when file read raises."""
        boot_id_file = MagicMock()
        boot_id_file.exists.return_value = True
        boot_id_file.read_text.side_effect = OSError("read failed")
        mock_path_cls.return_value = boot_id_file
        assert get_boot_id() is None

    @patch(f"{_MOD}.get_boot_id", return_value="new-boot-id")
    def test_has_rebooted_different_id(self, mock_get):
        """Returns True when current boot ID differs from saved."""
        assert has_rebooted_since("old-boot-id") is True

    @patch(f"{_MOD}.get_boot_id", return_value="same-id")
    def test_has_not_rebooted_same_id(self, mock_get):
        """Returns False when current boot ID matches saved."""
        assert has_rebooted_since("same-id") is False

    def test_has_rebooted_none_saved(self):
        """Returns False when saved_boot_id is None."""
        assert has_rebooted_since(None) is False

    @patch(f"{_MOD}.get_boot_id", return_value=None)
    def test_has_rebooted_current_none(self, mock_get):
        """Returns False when current boot ID cannot be read."""
        assert has_rebooted_since("some-id") is False


# =============================================================================
# Async boot management: set_grub_oneshot
# =============================================================================


class TestSetGrubOneshot:
    """Tests for set_grub_oneshot()."""

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=False)
    async def test_grub_not_available(self, mock_avail):
        """Returns failure when GRUB commands are missing."""
        from elle.daemon.reboot.grub import set_grub_oneshot

        result = await set_grub_oneshot("0")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch("elle.security.polkit_helper.get_helper")
    async def test_oneshot_success(self, mock_get_helper, mock_avail):
        """Returns success when grub-reboot succeeds via polkit."""
        from elle.daemon.reboot.grub import set_grub_oneshot
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=True, output=""))
        mock_get_helper.return_value = helper

        result = await set_grub_oneshot("2")
        assert result.success is True
        assert "entry: 2" in result.message

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch("elle.security.polkit_helper.get_helper")
    async def test_oneshot_failure(self, mock_get_helper, mock_avail):
        """Returns failure when grub-reboot fails."""
        from elle.daemon.reboot.grub import set_grub_oneshot
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=False, error="auth denied", exit_code=126))
        mock_get_helper.return_value = helper

        result = await set_grub_oneshot("2")
        assert result.success is False
        assert result.error == "auth denied"
        assert result.exit_code == 126


# =============================================================================
# Async boot management: confirm_boot_success
# =============================================================================


class TestConfirmBootSuccess:
    """Tests for confirm_boot_success()."""

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=False)
    async def test_grub_not_available(self, mock_avail):
        """Returns failure when GRUB commands are missing."""
        from elle.daemon.reboot.grub import confirm_boot_success

        result = await confirm_boot_success()
        assert result.success is False

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch(f"{_MOD}.get_saved_default", return_value="1")
    @patch("elle.security.polkit_helper.get_helper")
    async def test_confirm_uses_saved_default(self, mock_get_helper, mock_saved, mock_avail):
        """When entry is None, falls back to get_saved_default()."""
        from elle.daemon.reboot.grub import confirm_boot_success
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=True, output=""))
        mock_get_helper.return_value = helper

        result = await confirm_boot_success(entry=None)
        assert result.success is True
        assert "1" in result.message
        # Verify grub-set-default was called with the saved default
        args = helper._run_pkexec.call_args[0][0]
        assert args == ["grub-set-default", "1"]

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch(f"{_MOD}.get_saved_default", return_value=None)
    @patch("elle.security.polkit_helper.get_helper")
    async def test_confirm_falls_back_to_zero(self, mock_get_helper, mock_saved, mock_avail):
        """Falls back to '0' when saved default is also None."""
        from elle.daemon.reboot.grub import confirm_boot_success
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=True, output=""))
        mock_get_helper.return_value = helper

        result = await confirm_boot_success(entry=None)
        assert result.success is True
        args = helper._run_pkexec.call_args[0][0]
        assert args == ["grub-set-default", "0"]

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch("elle.security.polkit_helper.get_helper")
    async def test_confirm_explicit_entry(self, mock_get_helper, mock_avail):
        """Explicit entry parameter is used directly."""
        from elle.daemon.reboot.grub import confirm_boot_success
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=True, output=""))
        mock_get_helper.return_value = helper

        result = await confirm_boot_success(entry="5")
        assert result.success is True
        assert "5" in result.message

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch("elle.security.polkit_helper.get_helper")
    async def test_confirm_failure(self, mock_get_helper, mock_avail):
        """Returns failure when grub-set-default fails."""
        from elle.daemon.reboot.grub import confirm_boot_success
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=False, error="permission denied", exit_code=1))
        mock_get_helper.return_value = helper

        result = await confirm_boot_success(entry="0")
        assert result.success is False
        assert result.error == "permission denied"


# =============================================================================
# Async boot management: clear_grub_oneshot
# =============================================================================


class TestClearGrubOneshot:
    """Tests for clear_grub_oneshot()."""

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=False)
    async def test_grub_not_available(self, mock_avail):
        """Returns failure when GRUB is missing."""
        from elle.daemon.reboot.grub import clear_grub_oneshot

        result = await clear_grub_oneshot()
        assert result.success is False

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch("elle.security.polkit_helper.get_helper")
    async def test_clear_success(self, mock_get_helper, mock_avail):
        """Returns success when unset completes."""
        from elle.daemon.reboot.grub import clear_grub_oneshot
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=True, output=""))
        mock_get_helper.return_value = helper

        result = await clear_grub_oneshot()
        assert result.success is True
        assert "cleared" in result.message.lower()

    @pytest.mark.asyncio
    @patch(f"{_MOD}.is_grub_available", return_value=True)
    @patch("elle.security.polkit_helper.get_helper")
    async def test_clear_failure_is_still_success(self, mock_get_helper, mock_avail):
        """Even when pkexec fails, clear returns success (next_entry may not exist)."""
        from elle.daemon.reboot.grub import clear_grub_oneshot
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=False, error="no such var", exit_code=1))
        mock_get_helper.return_value = helper

        result = await clear_grub_oneshot()
        # Source explicitly returns success=True even on failure
        assert result.success is True


# =============================================================================
# Rollback: prepare_rollback
# =============================================================================


class TestPrepareRollback:
    """Tests for prepare_rollback()."""

    @pytest.mark.asyncio
    @patch(f"{_MOD}.get_grub_default", return_value="0")
    @patch(f"{_MOD}.get_saved_default", return_value="0")
    async def test_prepare_uses_current_default(self, mock_saved, mock_def):
        """Uses get_grub_default() when fallback_entry is None."""
        from elle.daemon.reboot.grub import prepare_rollback

        result = await prepare_rollback(fallback_entry=None)
        assert result.success is True
        assert "0" in result.message

    @pytest.mark.asyncio
    async def test_prepare_explicit_fallback(self):
        """Uses explicit fallback_entry when provided."""
        from elle.daemon.reboot.grub import prepare_rollback

        with patch(f"{_MOD}.get_saved_default", return_value="1"), patch(f"{_MOD}.get_grub_default", return_value="1"):
            result = await prepare_rollback(fallback_entry="3")
        assert result.success is True
        assert "3" in result.message

    @pytest.mark.asyncio
    @patch("elle.security.polkit_helper.get_helper")
    @patch(f"{_MOD}.get_grub_default", return_value="saved")
    @patch(f"{_MOD}.get_saved_default", return_value=None)
    async def test_prepare_sets_saved_entry_when_grub_default_is_saved(self, mock_saved, mock_def, mock_get_helper):
        """When GRUB_DEFAULT=saved and saved_entry is None, sets saved_entry via polkit."""
        from elle.daemon.reboot.grub import prepare_rollback
        from elle.security.polkit_helper import PolkitResult

        helper = MagicMock()
        helper._run_pkexec = AsyncMock(return_value=PolkitResult(success=True, output=""))
        mock_get_helper.return_value = helper

        result = await prepare_rollback(fallback_entry=None)
        assert result.success is True
        # Verify grub-editenv set was called
        helper._run_pkexec.assert_called_once()
        cmd_args = helper._run_pkexec.call_args[0][0]
        assert "saved_entry=saved" in cmd_args[-1]


# =============================================================================
# Rollback: trigger_rollback_reboot
# =============================================================================


class TestTriggerRollbackReboot:
    """Tests for trigger_rollback_reboot()."""

    @pytest.mark.asyncio
    @patch(f"{_MOD}.clear_grub_oneshot")
    @patch(f"{_MOD}.set_grub_default")
    async def test_success_path(self, mock_set_default, mock_clear):
        """Full rollback: sets default, clears oneshot, returns success."""
        from elle.daemon.reboot.grub import trigger_rollback_reboot

        mock_set_default.return_value = GRUBResult(success=True, message="ok")
        mock_clear.return_value = GRUBResult(success=True, message="cleared")

        result = await trigger_rollback_reboot("1")
        assert result.success is True
        assert "1" in result.message
        mock_set_default.assert_called_once_with("1")
        mock_clear.assert_called_once()

    @pytest.mark.asyncio
    @patch(f"{_MOD}.set_grub_default")
    async def test_default_set_failure_short_circuits(self, mock_set_default):
        """If set_grub_default fails, returns early without clearing oneshot."""
        from elle.daemon.reboot.grub import trigger_rollback_reboot

        mock_set_default.return_value = GRUBResult(success=False, error="failed", exit_code=1)

        result = await trigger_rollback_reboot("1")
        assert result.success is False
        assert result.error == "failed"


# =============================================================================
# set_grub_default (thin wrapper)
# =============================================================================


class TestSetGrubDefault:
    """Tests for set_grub_default() which delegates to confirm_boot_success()."""

    @pytest.mark.asyncio
    @patch(f"{_MOD}.confirm_boot_success")
    async def test_delegates_to_confirm(self, mock_confirm):
        """set_grub_default delegates to confirm_boot_success with entry."""
        from elle.daemon.reboot.grub import set_grub_default

        mock_confirm.return_value = GRUBResult(success=True, message="done")
        result = await set_grub_default("4")
        assert result.success is True
        mock_confirm.assert_called_once_with("4")
