from __future__ import annotations

"""Tests for the ELLE package learning commands."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.package_learn_commands import (
    LearnResult,
    _format_learn_result,
    _get_help,
    handle_learn_command,
)


# =============================================================================
# Helpers
# =============================================================================


def run_async(coro):
    """Run an async function synchronously for tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# LearnResult model tests
# =============================================================================


class TestLearnResult:
    def test_basic_creation(self):
        result = LearnResult(
            package_name="nginx",
            capabilities_generated=5,
            capabilities_validated=4,
            capabilities_saved=3,
            extraction_sources=("dpkg", "man"),
        )
        assert result.package_name == "nginx"
        assert result.capabilities_generated == 5
        assert result.capabilities_saved == 3

    def test_default_errors_empty(self):
        result = LearnResult(
            package_name="test",
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=(),
        )
        assert result.errors == ()
        assert result.warnings == ()

    def test_with_errors_and_warnings(self):
        result = LearnResult(
            package_name="test",
            capabilities_generated=2,
            capabilities_validated=1,
            capabilities_saved=0,
            extraction_sources=("dpkg",),
            errors=("LLM failed",),
            warnings=("Skipped invalid spec",),
        )
        assert len(result.errors) == 1
        assert len(result.warnings) == 1

    def test_frozen_model(self):
        result = LearnResult(
            package_name="test",
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=(),
        )
        with pytest.raises(Exception):
            result.package_name = "other"

    def test_multiple_extraction_sources(self):
        result = LearnResult(
            package_name="ffmpeg",
            capabilities_generated=10,
            capabilities_validated=8,
            capabilities_saved=8,
            extraction_sources=("dpkg", "man", "bash_completion", "help_output"),
        )
        assert len(result.extraction_sources) == 4


# =============================================================================
# _get_help tests
# =============================================================================


class TestGetHelp:
    def test_returns_help_text(self):
        help_text = _get_help()
        assert "/learn" in help_text
        assert "Usage:" in help_text

    def test_help_includes_commands(self):
        help_text = _get_help()
        assert "list" in help_text
        assert "show" in help_text
        assert "approve" in help_text
        assert "delete" in help_text
        assert "refresh" in help_text

    def test_help_includes_examples(self):
        help_text = _get_help()
        assert "ffmpeg" in help_text
        assert "nginx" in help_text

    def test_help_includes_batch_learning(self):
        help_text = _get_help()
        assert "--all" in help_text
        assert "bootstrap" in help_text


# =============================================================================
# _format_learn_result tests
# =============================================================================


class TestFormatLearnResult:
    def test_successful_result(self):
        result = LearnResult(
            package_name="nginx",
            capabilities_generated=5,
            capabilities_validated=4,
            capabilities_saved=3,
            extraction_sources=("dpkg", "man"),
        )
        output = _format_learn_result(result)
        assert "nginx" in output
        assert "5 capabilities" in output
        assert "4 capabilities" in output
        assert "3 capabilities" in output
        assert "dpkg" in output

    def test_dry_run_format(self):
        result = LearnResult(
            package_name="test",
            capabilities_generated=2,
            capabilities_validated=2,
            capabilities_saved=0,
            extraction_sources=("dpkg",),
        )
        output = _format_learn_result(result, dry_run=True)
        assert "dry-run" in output

    def test_format_with_errors(self):
        result = LearnResult(
            package_name="broken",
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=("dpkg",),
            errors=("LLM generation failed: timeout",),
        )
        output = _format_learn_result(result)
        assert "Errors:" in output
        assert "LLM generation failed" in output

    def test_format_with_warnings(self):
        result = LearnResult(
            package_name="test",
            capabilities_generated=3,
            capabilities_validated=2,
            capabilities_saved=2,
            extraction_sources=("dpkg",),
            warnings=("Invalid spec: missing field",),
        )
        output = _format_learn_result(result)
        assert "Warnings:" in output
        assert "Invalid spec" in output

    def test_format_truncates_many_warnings(self):
        warnings = tuple(f"Warning {i}" for i in range(10))
        result = LearnResult(
            package_name="test",
            capabilities_generated=10,
            capabilities_validated=5,
            capabilities_saved=5,
            extraction_sources=("dpkg",),
            warnings=warnings,
        )
        output = _format_learn_result(result)
        assert "5 more warnings" in output

    def test_format_shows_next_steps(self):
        result = LearnResult(
            package_name="nginx",
            capabilities_generated=3,
            capabilities_validated=3,
            capabilities_saved=3,
            extraction_sources=("dpkg",),
        )
        output = _format_learn_result(result)
        assert "Next steps:" in output
        assert "/learn show nginx" in output

    def test_format_no_next_steps_when_zero_saved(self):
        result = LearnResult(
            package_name="test",
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=("dpkg",),
        )
        output = _format_learn_result(result)
        assert "Next steps:" not in output

    def test_format_sources_listed(self):
        result = LearnResult(
            package_name="test",
            capabilities_generated=1,
            capabilities_validated=1,
            capabilities_saved=1,
            extraction_sources=("dpkg", "man", "help"),
        )
        output = _format_learn_result(result)
        assert "Sources:" in output
        assert "dpkg" in output
        assert "man" in output


# =============================================================================
# handle_learn_command routing tests
# =============================================================================


class TestHandleLearnCommandRouting:
    def test_empty_args_returns_help(self):
        result = run_async(handle_learn_command(""))
        assert "/learn" in result
        assert "Usage:" in result

    def test_help_subcommand(self):
        result = run_async(handle_learn_command("help"))
        assert "/learn" in result
        assert "Usage:" in result

    def test_list_subcommand_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_list",
            new_callable=AsyncMock,
            return_value="list output",
        ) as mock_list:
            result = run_async(handle_learn_command("list"))
            mock_list.assert_called_once_with("")
            assert result == "list output"

    def test_show_subcommand_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_show",
            new_callable=AsyncMock,
            return_value="show output",
        ) as mock_show:
            result = run_async(handle_learn_command("show nginx"))
            mock_show.assert_called_once_with("nginx")
            assert result == "show output"

    def test_approve_subcommand_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_approve",
            new_callable=AsyncMock,
            return_value="approved",
        ) as mock_approve:
            result = run_async(handle_learn_command("approve nginx.restart"))
            mock_approve.assert_called_once_with("nginx.restart")
            assert result == "approved"

    def test_delete_subcommand_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_delete",
            new_callable=AsyncMock,
            return_value="deleted",
        ) as mock_delete:
            result = run_async(handle_learn_command("delete nginx.restart"))
            mock_delete.assert_called_once_with("nginx.restart")
            assert result == "deleted"

    def test_refresh_subcommand_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_refresh",
            new_callable=AsyncMock,
            return_value="refreshed",
        ) as mock_refresh:
            result = run_async(handle_learn_command("refresh nginx"))
            mock_refresh.assert_called_once_with("nginx")
            assert result == "refreshed"

    def test_bootstrap_subcommand_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_bootstrap",
            new_callable=AsyncMock,
            return_value="bootstrap output",
        ) as mock_boot:
            result = run_async(handle_learn_command("bootstrap"))
            mock_boot.assert_called_once_with("")
            assert result == "bootstrap output"

    def test_status_subcommand_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_bootstrap_status",
            new_callable=AsyncMock,
            return_value="status output",
        ) as mock_status:
            result = run_async(handle_learn_command("status"))
            mock_status.assert_called_once_with("")
            assert result == "status output"

    def test_all_flag_dispatched(self):
        with patch(
            "elle.cli.package_learn_commands._handle_learn_all",
            new_callable=AsyncMock,
            return_value="all output",
        ) as mock_all:
            result = run_async(handle_learn_command("--all"))
            mock_all.assert_called_once_with("--all")
            assert result == "all output"

    def test_unknown_package_dispatches_to_learn(self):
        with patch(
            "elle.cli.package_learn_commands._handle_learn",
            new_callable=AsyncMock,
            return_value="learn output",
        ) as mock_learn:
            result = run_async(handle_learn_command("nginx"))
            mock_learn.assert_called_once_with("nginx")
            assert result == "learn output"

    def test_case_insensitive_subcommands(self):
        with patch(
            "elle.cli.package_learn_commands._handle_list",
            new_callable=AsyncMock,
            return_value="list output",
        ):
            result = run_async(handle_learn_command("LIST"))
            assert result == "list output"

    def test_whitespace_stripped(self):
        result = run_async(handle_learn_command("   "))
        assert "/learn" in result  # Should return help for empty/whitespace


# =============================================================================
# _handle_learn tests
# =============================================================================


class TestHandleLearn:
    def test_empty_package_returns_usage(self):
        from elle.cli.package_learn_commands import _handle_learn

        result = run_async(_handle_learn(""))
        assert "Usage:" in result

    def test_learn_success(self):
        from elle.cli.package_learn_commands import _handle_learn

        mock_result = LearnResult(
            package_name="nginx",
            capabilities_generated=3,
            capabilities_validated=3,
            capabilities_saved=3,
            extraction_sources=("dpkg", "man"),
        )

        with patch(
            "elle.cli.package_learn_commands._learn_package",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = run_async(_handle_learn("nginx"))
            assert "nginx" in result
            assert "3 capabilities" in result

    def test_learn_with_refresh_flag(self):
        from elle.cli.package_learn_commands import _handle_learn

        mock_result = LearnResult(
            package_name="nginx",
            capabilities_generated=2,
            capabilities_validated=2,
            capabilities_saved=2,
            extraction_sources=("dpkg",),
        )

        with patch(
            "elle.cli.package_learn_commands._learn_package",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_learn:
            run_async(_handle_learn("nginx --refresh"))
            mock_learn.assert_called_once_with("nginx", force_refresh=True, dry_run=False)

    def test_learn_with_force_flag(self):
        from elle.cli.package_learn_commands import _handle_learn

        mock_result = LearnResult(
            package_name="nginx",
            capabilities_generated=2,
            capabilities_validated=2,
            capabilities_saved=2,
            extraction_sources=("dpkg",),
        )

        with patch(
            "elle.cli.package_learn_commands._learn_package",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_learn:
            run_async(_handle_learn("nginx --force"))
            mock_learn.assert_called_once_with("nginx", force_refresh=True, dry_run=False)

    def test_learn_with_dry_run_flag(self):
        from elle.cli.package_learn_commands import _handle_learn

        mock_result = LearnResult(
            package_name="nginx",
            capabilities_generated=2,
            capabilities_validated=2,
            capabilities_saved=0,
            extraction_sources=("dpkg",),
        )

        with patch(
            "elle.cli.package_learn_commands._learn_package",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_learn:
            result = run_async(_handle_learn("nginx --dry-run"))
            mock_learn.assert_called_once_with("nginx", force_refresh=False, dry_run=True)
            assert "dry-run" in result

    def test_learn_exception(self):
        from elle.cli.package_learn_commands import _handle_learn

        with patch(
            "elle.cli.package_learn_commands._learn_package",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = run_async(_handle_learn("nginx"))
            assert "Error" in result
            assert "nginx" in result


# =============================================================================
# _handle_list tests
# =============================================================================


class TestHandleList:
    def test_empty_list(self):
        from elle.cli.package_learn_commands import _handle_list

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = []

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_list(""))
            assert "No packages" in result

    def test_list_with_packages(self):
        from elle.cli.package_learn_commands import _handle_list

        mock_cap = MagicMock()
        mock_cap.approved = True

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = [
            ("nginx", "1.22"),
            ("docker", "24.0"),
        ]
        mock_store.list_by_package.return_value = [mock_cap]

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_list(""))
            assert "nginx" in result
            assert "docker" in result

    def test_list_exception(self):
        from elle.cli.package_learn_commands import _handle_list

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.side_effect = RuntimeError("DB error")

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_list(""))
            assert "Error" in result


# =============================================================================
# _handle_show tests
# =============================================================================


class TestHandleShow:
    def test_no_package_name(self):
        from elle.cli.package_learn_commands import _handle_show

        result = run_async(_handle_show(""))
        assert "Usage:" in result

    def test_package_not_found(self):
        from elle.cli.package_learn_commands import _handle_show

        mock_store = MagicMock()
        mock_store.list_by_package.return_value = []
        mock_store.list_by_command.return_value = []

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_show("nonexistent"))
            assert "No capabilities found" in result

    def test_show_falls_back_to_command(self):
        from elle.cli.package_learn_commands import _handle_show

        mock_stored = MagicMock()
        mock_stored.approved = True
        mock_stored.enabled = True
        mock_stored.trust_level.value = "core"

        mock_spec = MagicMock()
        mock_spec.name = "nginx.restart"
        mock_spec.description = "Restart nginx server for reload"
        mock_spec.risk_level = "medium"
        mock_spec.command_template = "systemctl restart nginx service safely"

        mock_store = MagicMock()
        mock_store.list_by_package.return_value = []
        mock_store.list_by_command.return_value = [mock_stored]

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.capabilities.autogen.GeneratedCapabilitySpec.model_validate_json",
                return_value=mock_spec,
            ),
        ):
            result = run_async(_handle_show("nginx"))
            assert "nginx" in result

    def test_show_exception(self):
        from elle.cli.package_learn_commands import _handle_show

        mock_store = MagicMock()
        mock_store.list_by_package.side_effect = RuntimeError("DB error")

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_show("nginx"))
            assert "Error" in result


# =============================================================================
# _handle_approve tests
# =============================================================================


class TestHandleApprove:
    def test_no_cap_name(self):
        from elle.cli.package_learn_commands import _handle_approve

        result = run_async(_handle_approve(""))
        assert "Usage:" in result

    def test_approve_success(self):
        from elle.cli.package_learn_commands import _handle_approve

        mock_store = MagicMock()
        mock_store.approve.return_value = True

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            result = run_async(_handle_approve("nginx.restart"))
            assert "Approved" in result
            mock_store.approve.assert_called_once_with("nginx.restart")

    def test_approve_not_found(self):
        from elle.cli.package_learn_commands import _handle_approve

        mock_store = MagicMock()
        mock_store.approve.return_value = False

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_approve("nonexistent"))
            assert "not found" in result

    def test_approve_exception(self):
        from elle.cli.package_learn_commands import _handle_approve

        mock_store = MagicMock()
        mock_store.approve.side_effect = RuntimeError("DB error")

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_approve("test.cap"))
            assert "Error" in result

    def test_approve_records_incident(self):
        from elle.cli.package_learn_commands import _handle_approve

        mock_store = MagicMock()
        mock_store.approve.return_value = True

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.cli.agentic.incident_recorder.record_arm_action") as mock_record,
        ):
            run_async(_handle_approve("nginx.restart"))
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args
            assert call_kwargs[1]["arm_name"] == "package_learning"
            assert call_kwargs[1]["action"] == "approve_capability"


# =============================================================================
# _handle_delete tests
# =============================================================================


class TestHandleDelete:
    def test_no_cap_name(self):
        from elle.cli.package_learn_commands import _handle_delete

        result = run_async(_handle_delete(""))
        assert "Usage:" in result

    def test_delete_success(self):
        from elle.cli.package_learn_commands import _handle_delete

        mock_store = MagicMock()
        mock_store.delete.return_value = True

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            result = run_async(_handle_delete("nginx.restart"))
            assert "Deleted" in result
            mock_store.delete.assert_called_once_with("nginx.restart")

    def test_delete_not_found(self):
        from elle.cli.package_learn_commands import _handle_delete

        mock_store = MagicMock()
        mock_store.delete.return_value = False

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_delete("nonexistent"))
            assert "not found" in result

    def test_delete_exception(self):
        from elle.cli.package_learn_commands import _handle_delete

        mock_store = MagicMock()
        mock_store.delete.side_effect = RuntimeError("DB error")

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_delete("test.cap"))
            assert "Error" in result

    def test_delete_records_incident(self):
        from elle.cli.package_learn_commands import _handle_delete

        mock_store = MagicMock()
        mock_store.delete.return_value = True

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.cli.agentic.incident_recorder.record_arm_action") as mock_record,
        ):
            run_async(_handle_delete("nginx.restart"))
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args
            assert call_kwargs[1]["action"] == "delete_capability"


# =============================================================================
# _handle_refresh tests
# =============================================================================


class TestHandleRefresh:
    def test_no_package_name(self):
        from elle.cli.package_learn_commands import _handle_refresh

        result = run_async(_handle_refresh(""))
        assert "Usage:" in result

    def test_refresh_delegates_to_learn(self):
        from elle.cli.package_learn_commands import _handle_refresh

        mock_result = LearnResult(
            package_name="nginx",
            capabilities_generated=2,
            capabilities_validated=2,
            capabilities_saved=2,
            extraction_sources=("dpkg",),
        )

        with patch(
            "elle.cli.package_learn_commands._learn_package",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_learn:
            result = run_async(_handle_refresh("nginx"))
            mock_learn.assert_called_once_with("nginx", force_refresh=True, dry_run=False)
            assert "nginx" in result


# =============================================================================
# handle_learn_command_sync tests
# =============================================================================


class TestHandleLearnCommandSync:
    def test_sync_wrapper_returns_help(self):
        from elle.cli.package_learn_commands import handle_learn_command_sync

        result = handle_learn_command_sync("")
        assert "/learn" in result
        assert "Usage:" in result

    def test_sync_wrapper_with_help_arg(self):
        from elle.cli.package_learn_commands import handle_learn_command_sync

        result = handle_learn_command_sync("help")
        assert "/learn" in result


# =============================================================================
# _handle_bootstrap tests
# =============================================================================


class TestHandleBootstrap:
    def test_dry_run(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        mock_packages = [
            ("systemctl", "core"),
            ("docker", "optional"),
        ]

        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_packages",
            return_value=mock_packages,
        ):
            result = run_async(_handle_bootstrap("--dry-run"))
            assert "Bootstrap would learn" in result
            assert "systemctl" in result
            assert "docker" in result

    def test_bootstrap_run_success(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        mock_result = MagicMock()
        mock_result.packages_attempted = 10
        mock_result.packages_succeeded = 8
        mock_result.packages_failed = 2
        mock_result.packages_skipped = 0
        mock_result.capabilities_generated = 20
        mock_result.capabilities_validated = 18
        mock_result.capabilities_saved = 18
        mock_result.duration_seconds = 30.5
        mock_result.failed_packages = [("broken-pkg", "LLM timeout")]

        with (
            patch(
                "elle.capabilities.autogen.bootstrap.run_bootstrap",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("elle.capabilities.autogen.bootstrap.set_bootstrap_complete"),
        ):
            result = run_async(_handle_bootstrap(""))
            assert "Bootstrap complete" in result
            assert "10" in result

    def test_bootstrap_exception(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        with patch(
            "elle.capabilities.autogen.bootstrap.run_bootstrap",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection failed"),
        ):
            result = run_async(_handle_bootstrap(""))
            assert "failed" in result.lower()


# =============================================================================
# _handle_bootstrap_status tests
# =============================================================================


class TestHandleBootstrapStatus:
    def test_completed_status(self):
        from elle.cli.package_learn_commands import _handle_bootstrap_status

        with (
            patch(
                "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
                return_value={
                    "completed": True,
                    "last_run": "2024-01-01",
                    "version": "0.1.0",
                    "packages_succeeded": 15,
                    "capabilities_saved": 45,
                },
            ),
            patch(
                "elle.capabilities.autogen.bootstrap.get_bootstrap_packages",
                return_value=[("pkg1", "core")],
            ),
            patch(
                "elle.capabilities.autogen.bootstrap.should_run_bootstrap",
                return_value=False,
            ),
        ):
            result = run_async(_handle_bootstrap_status(""))
            assert "Completed" in result
            assert "15" in result

    def test_not_completed_status(self):
        from elle.cli.package_learn_commands import _handle_bootstrap_status

        with (
            patch(
                "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
                return_value={"completed": False},
            ),
            patch(
                "elle.capabilities.autogen.bootstrap.get_bootstrap_packages",
                return_value=[("pkg1", "core"), ("pkg2", "essential")],
            ),
            patch(
                "elle.capabilities.autogen.bootstrap.should_run_bootstrap",
                return_value=True,
            ),
        ):
            result = run_async(_handle_bootstrap_status(""))
            assert "Not completed" in result
            assert "bootstrap" in result.lower()
