from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path
import subprocess as sp

from elle.security.polkit_helper import (
    PolkitAction,
    PolkitResult,
    PolkitHelper,
    get_helper,
    write_privileged,
    run_validator,
    is_authorized,
    run_privileged_sync,
    write_privileged_sync,
    create_group_sync,
    add_user_to_group_sync,
    start_service_sync,
    enable_service_sync,
    install_polkit_rules_sync,
)


# ---------------------------------------------------------------------------
# PolkitAction constants
# ---------------------------------------------------------------------------

class TestPolkitAction:

    def test_action_ids_exist(self):
        assert PolkitAction.EDIT_SYSTEM_CONFIG.startswith("com.elle")
        assert PolkitAction.MANAGE_SERVICES.startswith("com.elle")
        assert PolkitAction.READ_PRIVILEGED_LOGS.startswith("com.elle")
        assert PolkitAction.REBOOT.startswith("com.elle")


# ---------------------------------------------------------------------------
# PolkitResult model
# ---------------------------------------------------------------------------

class TestPolkitResult:

    def test_defaults(self):
        r = PolkitResult(success=True)
        assert r.authorized is True
        assert r.output == ""
        assert r.error == ""
        assert r.exit_code == 0


# ---------------------------------------------------------------------------
# PolkitHelper._run_pkexec
# ---------------------------------------------------------------------------

class TestRunPkexec:

    @pytest.mark.asyncio
    async def test_success(self):
        helper = PolkitHelper()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "output"
        mock_result.stderr = ""
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = await helper._run_pkexec(["ls", "/"], "action.id")
        assert result.success is True
        assert result.authorized is True

    @pytest.mark.asyncio
    async def test_auth_denied(self):
        helper = PolkitHelper()
        mock_result = MagicMock()
        mock_result.returncode = 126
        mock_result.stdout = ""
        mock_result.stderr = "denied"
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = await helper._run_pkexec(["ls", "/"], "action.id")
        assert result.success is False
        assert result.authorized is False

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        helper = PolkitHelper()
        mock_result = MagicMock()
        mock_result.returncode = 127
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = await helper._run_pkexec(["nonexistent"], "action.id")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_timeout(self):
        helper = PolkitHelper(timeout=1.0)
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=sp.TimeoutExpired("cmd", 1)):
            result = await helper._run_pkexec(["slow"], "action.id")
        assert result.success is False
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=FileNotFoundError):
            result = await helper._run_pkexec(["x"], "action.id")
        assert result.success is False
        assert "pkexec not found" in result.error

    @pytest.mark.asyncio
    async def test_general_exception(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=RuntimeError("fail")):
            result = await helper._run_pkexec(["x"], "action.id")
        assert result.success is False
        assert "fail" in result.error

    @pytest.mark.asyncio
    async def test_nonzero_exit(self):
        helper = PolkitHelper()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error occurred"
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = await helper._run_pkexec(["failing-cmd"], "action.id")
        assert result.success is False
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        helper = PolkitHelper()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result) as mock_run:
            result = await helper._run_pkexec(["ls"], "action.id", timeout=5.0)
        assert result.success is True
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 5.0


# ---------------------------------------------------------------------------
# write_privileged
# ---------------------------------------------------------------------------

class TestWritePrivileged:

    @pytest.mark.asyncio
    async def test_write_success(self, tmp_path):
        helper = PolkitHelper()
        target = tmp_path / "testfile.conf"
        target.write_text("old content")

        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock_pkexec:
            mock_pkexec.return_value = PolkitResult(success=True)
            result = await helper.write_privileged(target, "new content")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_write_temp_file_failure(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.tempfile.NamedTemporaryFile", side_effect=OSError("disk full")):
            result = await helper.write_privileged("/etc/test", "content")
        assert result.success is False
        assert "temp file" in result.error

    @pytest.mark.asyncio
    async def test_write_pkexec_failure(self, tmp_path):
        helper = PolkitHelper()
        target = tmp_path / "testfile"
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock_pkexec:
            mock_pkexec.return_value = PolkitResult(success=False, error="denied")
            result = await helper.write_privileged(target, "content")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_write_preserve_permissions(self, tmp_path):
        helper = PolkitHelper()
        target = tmp_path / "testfile"
        target.write_text("old")

        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock_pkexec:
            mock_pkexec.return_value = PolkitResult(success=True)
            result = await helper.write_privileged(target, "new", preserve_permissions=True)
        assert result.success is True
        # chmod and chown calls
        assert mock_pkexec.call_count >= 2  # cp + chmod (at least)

    @pytest.mark.asyncio
    async def test_write_no_preserve(self, tmp_path):
        helper = PolkitHelper()
        target = tmp_path / "newfile"
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock_pkexec:
            mock_pkexec.return_value = PolkitResult(success=True)
            result = await helper.write_privileged(target, "content", preserve_permissions=False)
        assert result.success is True
        # Only cp call, no chmod
        assert mock_pkexec.call_count == 1


# ---------------------------------------------------------------------------
# run_validator
# ---------------------------------------------------------------------------

class TestRunValidator:

    @pytest.mark.asyncio
    async def test_success(self):
        helper = PolkitHelper()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = await helper.run_validator(["echo", "test"])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_permission_error_escalates(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=PermissionError), \
             patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock_pkexec:
            mock_pkexec.return_value = PolkitResult(success=True)
            result = await helper.run_validator(["restricted-cmd"])
        assert result.success is True

    @pytest.mark.asyncio
    async def test_timeout(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=sp.TimeoutExpired("cmd", 60)):
            result = await helper.run_validator(["slow"])
        assert result.success is False
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=FileNotFoundError):
            result = await helper.run_validator(["nonexistent"])
        assert result.success is False
        assert "not found" in result.error


# ---------------------------------------------------------------------------
# read_privileged / delete_privileged / create_directory / create_backup / restore_backup
# ---------------------------------------------------------------------------

class TestHelperOperations:

    @pytest.mark.asyncio
    async def test_read_privileged(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True, output="file content")
            result = await helper.read_privileged("/etc/test")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete_privileged(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.delete_privileged("/tmp/test")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_directory(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.create_directory("/var/lib/test", mode="700")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_backup(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock, \
             patch.object(helper, "create_directory", new_callable=AsyncMock) as mock_mkdir:
            mock.return_value = PolkitResult(success=True)
            mock_mkdir.return_value = PolkitResult(success=True)
            result = await helper.create_backup("/etc/test", "/backup/test.bak")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_create_backup_mkdir_fails(self):
        helper = PolkitHelper()
        with patch.object(helper, "create_directory", new_callable=AsyncMock) as mock_mkdir:
            mock_mkdir.return_value = PolkitResult(success=False, error="fail")
            result = await helper.create_backup("/etc/test", "/backup/test.bak")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_restore_backup(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.restore_backup("/backup/test.bak", "/etc/test")
        assert result.success is True


# ---------------------------------------------------------------------------
# check_authorization
# ---------------------------------------------------------------------------

class TestCheckAuthorization:

    def test_root(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.os.geteuid", return_value=0):
            assert helper.check_authorization("action.id") is True

    def test_pkexec_available(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.os.geteuid", return_value=1000), \
             patch("elle.security.polkit_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert helper.check_authorization("action.id") is True

    def test_pkexec_not_available(self):
        helper = PolkitHelper()
        with patch("elle.security.polkit_helper.os.geteuid", return_value=1000), \
             patch("elle.security.polkit_helper.subprocess.run", side_effect=FileNotFoundError):
            assert helper.check_authorization("action.id") is False


# ---------------------------------------------------------------------------
# Setup operations
# ---------------------------------------------------------------------------

class TestSetupOperations:

    @pytest.mark.asyncio
    async def test_create_group_exists(self):
        helper = PolkitHelper()
        mock_grp = MagicMock()
        mock_grp.getgrnam.return_value = MagicMock()
        with patch.dict("sys.modules", {"grp": mock_grp}):
            result = await helper.create_group("elle")
        assert result.success is True
        assert "already exists" in result.output

    @pytest.mark.asyncio
    async def test_create_group_new(self):
        helper = PolkitHelper()
        mock_grp = MagicMock()
        mock_grp.getgrnam.side_effect = KeyError("not found")
        with patch.dict("sys.modules", {"grp": mock_grp}), \
             patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock_pkexec:
            mock_pkexec.return_value = PolkitResult(success=True)
            result = await helper.create_group("elle")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_add_user_to_group(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.add_user_to_group("testuser", "elle")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_start_service(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.start_service("elled")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_stop_service(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.stop_service("elled")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_enable_service(self):
        helper = PolkitHelper()
        with patch.object(helper, "_run_pkexec", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.enable_service("elled")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_install_polkit_rules(self):
        helper = PolkitHelper()
        with patch.object(helper, "write_privileged", new_callable=AsyncMock) as mock:
            mock.return_value = PolkitResult(success=True)
            result = await helper.install_polkit_rules("rules content")
        assert result.success is True


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestModuleConvenience:

    def test_get_helper_singleton(self):
        import elle.security.polkit_helper as mod
        mod._helper = None
        h1 = get_helper()
        h2 = get_helper()
        assert h1 is h2
        mod._helper = None

    @pytest.mark.asyncio
    async def test_write_privileged_convenience(self):
        with patch("elle.security.polkit_helper.get_helper") as mock_get:
            mock_helper = MagicMock()
            mock_helper.write_privileged = AsyncMock(return_value=PolkitResult(success=True))
            mock_get.return_value = mock_helper
            result = await write_privileged("/etc/test", "content")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_run_validator_convenience(self):
        with patch("elle.security.polkit_helper.get_helper") as mock_get:
            mock_helper = MagicMock()
            mock_helper.run_validator = AsyncMock(return_value=PolkitResult(success=True))
            mock_get.return_value = mock_helper
            result = await run_validator(["echo", "test"])
        assert result.success is True

    def test_is_authorized_convenience(self):
        with patch("elle.security.polkit_helper.get_helper") as mock_get:
            mock_helper = MagicMock()
            mock_helper.check_authorization.return_value = True
            mock_get.return_value = mock_helper
            assert is_authorized() is True


# ---------------------------------------------------------------------------
# Synchronous functions
# ---------------------------------------------------------------------------

class TestSyncFunctions:

    def test_run_privileged_sync_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = run_privileged_sync(["echo", "test"])
        assert result.success is True

    def test_run_privileged_sync_auth_denied(self):
        mock_result = MagicMock()
        mock_result.returncode = 126
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = run_privileged_sync(["cmd"])
        assert result.success is False
        assert result.authorized is False

    def test_run_privileged_sync_not_found(self):
        mock_result = MagicMock()
        mock_result.returncode = 127
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("elle.security.polkit_helper.subprocess.run", return_value=mock_result):
            result = run_privileged_sync(["cmd"])
        assert result.success is False
        assert "not found" in result.error

    def test_run_privileged_sync_timeout(self):
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=sp.TimeoutExpired("cmd", 60)):
            result = run_privileged_sync(["slow"])
        assert result.success is False

    def test_run_privileged_sync_file_not_found(self):
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=FileNotFoundError):
            result = run_privileged_sync(["x"])
        assert result.success is False
        assert "pkexec not found" in result.error

    def test_run_privileged_sync_exception(self):
        with patch("elle.security.polkit_helper.subprocess.run", side_effect=RuntimeError("err")):
            result = run_privileged_sync(["x"])
        assert result.success is False

    def test_write_privileged_sync_success(self, tmp_path):
        with patch("elle.security.polkit_helper.run_privileged_sync") as mock_run:
            mock_run.return_value = PolkitResult(success=True)
            result = write_privileged_sync(tmp_path / "test.conf", "content")
        assert result.success is True

    def test_write_privileged_sync_with_mode(self, tmp_path):
        with patch("elle.security.polkit_helper.run_privileged_sync") as mock_run:
            mock_run.return_value = PolkitResult(success=True)
            result = write_privileged_sync(tmp_path / "test.conf", "content", mode="644")
        assert result.success is True
        # Should have been called for cp and chmod
        assert mock_run.call_count == 2

    def test_write_privileged_sync_cp_fails(self, tmp_path):
        with patch("elle.security.polkit_helper.run_privileged_sync") as mock_run:
            mock_run.return_value = PolkitResult(success=False, error="denied")
            result = write_privileged_sync(tmp_path / "test.conf", "content")
        assert result.success is False

    def test_write_privileged_sync_temp_file_failure(self):
        with patch("elle.security.polkit_helper.tempfile.NamedTemporaryFile", side_effect=OSError("fail")):
            result = write_privileged_sync("/etc/test", "content")
        assert result.success is False

    def test_create_group_sync_exists(self):
        mock_grp = MagicMock()
        mock_grp.getgrnam.return_value = MagicMock()
        with patch.dict("sys.modules", {"grp": mock_grp}):
            result = create_group_sync("elle")
        assert result.success is True
        assert "already exists" in result.output

    def test_create_group_sync_new(self):
        mock_grp = MagicMock()
        mock_grp.getgrnam.side_effect = KeyError
        with patch.dict("sys.modules", {"grp": mock_grp}), \
             patch("elle.security.polkit_helper.run_privileged_sync") as mock_run:
            mock_run.return_value = PolkitResult(success=True)
            result = create_group_sync("elle")
        assert result.success is True

    def test_add_user_to_group_sync(self):
        with patch("elle.security.polkit_helper.run_privileged_sync") as mock_run:
            mock_run.return_value = PolkitResult(success=True)
            result = add_user_to_group_sync("user", "elle")
        assert result.success is True

    def test_start_service_sync(self):
        with patch("elle.security.polkit_helper.run_privileged_sync") as mock_run:
            mock_run.return_value = PolkitResult(success=True)
            result = start_service_sync("elled")
        assert result.success is True

    def test_enable_service_sync(self):
        with patch("elle.security.polkit_helper.run_privileged_sync") as mock_run:
            mock_run.return_value = PolkitResult(success=True)
            result = enable_service_sync("elled")
        assert result.success is True

    def test_install_polkit_rules_sync(self):
        with patch("elle.security.polkit_helper.write_privileged_sync") as mock_write:
            mock_write.return_value = PolkitResult(success=True)
            result = install_polkit_rules_sync("rules content")
        assert result.success is True
