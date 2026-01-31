"""Tests for the ELLE package learning commands."""

from __future__ import annotations

import asyncio
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


# =============================================================================
# _learn_package deep tests
# =============================================================================


class TestLearnPackageInternal:
    """Tests for the _learn_package internal logic."""

    def test_no_intelligence_sources(self):
        """When aggregator returns empty extraction_sources, return early with error."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ()

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
        ):
            result = run_async(_learn_package("nonexistent-pkg"))
            assert result.capabilities_generated == 0
            assert result.capabilities_validated == 0
            assert "No intelligence sources available" in result.errors

    def test_llm_generation_failure(self):
        """When LLM fails, return error result with extraction_sources."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg", "man")
        mock_intel.primary_binary = "nginx"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "system prompt"

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
        ):
            mock_llm_cls.return_value.generate_json.side_effect = RuntimeError("Ollama down")
            result = run_async(_learn_package("nginx"))
            assert result.capabilities_generated == 0
            assert any("LLM generation failed" in e for e in result.errors)
            assert result.extraction_sources == ("dpkg", "man")

    def test_parse_validate_save_full_pipeline(self):
        """Full pipeline: generate, validate, save capabilities."""
        from elle.cli.package_learn_commands import _learn_package

        mock_binary = MagicMock()
        mock_binary.path = "/usr/bin/nginx"

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"
        mock_intel.metadata.name = "nginx"
        mock_intel.metadata.version = "1.22"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "system prompt"

        caps_json = {
            "capabilities": [
                {
                    "name": "nginx.restart",
                    "description": "Restart nginx",
                    "risk_level": "medium",
                    "command_template": "systemctl restart nginx",
                    "source_command": "nginx",
                    "side_effects": ["service_restart"],
                    "input_fields": [{"name": "force", "constraints": {}}],
                    "output_fields": ["status"],
                }
            ]
        }

        mock_spec = MagicMock()
        mock_spec.name = "nginx.restart"

        mock_validation = MagicMock()
        mock_validation.overall_passed = True
        mock_validation.trust_level = "core"

        mock_store = MagicMock()

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=mock_binary),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.capabilities.autogen.factory.generate_input_model_code", return_value="code"),
            patch("elle.capabilities.autogen.factory.generate_output_model_code", return_value="code"),
            patch("elle.capabilities.autogen.factory.generate_capability_class_code", return_value="code"),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec
            result = run_async(_learn_package("nginx"))
            assert result.capabilities_generated == 1
            assert result.package_name == "nginx"

    def test_dry_run_skips_save(self):
        """In dry_run mode, capabilities are generated but NOT saved."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        mock_spec = MagicMock()
        mock_spec.name = "nginx.restart"

        mock_validation = MagicMock()
        mock_validation.overall_passed = True

        caps_json = {"capabilities": [{"name": "nginx.restart", "description": "x", "risk_level": "low", "command_template": "cmd", "source_command": "nginx", "side_effects": []}]}

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store") as mock_get_store,
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec
            result = run_async(_learn_package("nginx", dry_run=True))
            assert result.capabilities_saved == 0
            mock_get_store.assert_not_called()

    def test_validation_failure_adds_warning(self):
        """When validation fails, add warning and exclude from validated."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        mock_spec = MagicMock()
        mock_spec.name = "nginx.restart"

        mock_stage = MagicMock()
        mock_stage.errors = ["missing template"]
        mock_validation = MagicMock()
        mock_validation.overall_passed = False
        mock_validation.stages = [mock_stage]

        caps_json = {"capabilities": [{"name": "nginx.restart", "description": "x", "risk_level": "low", "command_template": "cmd", "source_command": "nginx", "side_effects": []}]}

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec
            result = run_async(_learn_package("nginx", dry_run=True))
            assert result.capabilities_validated == 0
            assert any("Validation failed" in w for w in result.warnings)

    def test_validation_exception_adds_warning(self):
        """When validate_capability raises, add warning."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        mock_spec = MagicMock()
        mock_spec.name = "nginx.restart"

        caps_json = {"capabilities": [{"name": "nginx.restart", "description": "x", "risk_level": "low", "command_template": "cmd", "source_command": "nginx", "side_effects": []}]}

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", side_effect=RuntimeError("boom")),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec
            result = run_async(_learn_package("nginx", dry_run=True))
            assert result.capabilities_validated == 0
            assert any("Validation error" in w for w in result.warnings)

    def test_invalid_capability_spec_adds_warning(self):
        """When GeneratedCapabilitySpec(...) raises, add warning."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        caps_json = {"capabilities": [{"name": "nginx.restart", "description": "x", "risk_level": "low", "command_template": "cmd", "source_command": "nginx", "side_effects": []}]}

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec", side_effect=TypeError("bad field")),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            result = run_async(_learn_package("nginx", dry_run=True))
            assert result.capabilities_generated == 0
            assert any("Invalid capability spec" in w for w in result.warnings)

    def test_save_failure_adds_warning(self):
        """When store.save() raises, add warning and continue."""
        from elle.cli.package_learn_commands import _learn_package

        mock_binary = MagicMock()
        mock_binary.path = "/usr/bin/nginx"

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"
        mock_intel.metadata.name = "nginx"
        mock_intel.metadata.version = "1.22"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        mock_spec = MagicMock()
        mock_spec.name = "nginx.restart"

        mock_validation = MagicMock()
        mock_validation.overall_passed = True
        mock_validation.trust_level = "core"

        mock_store = MagicMock()
        mock_store.save.side_effect = RuntimeError("DB full")

        caps_json = {"capabilities": [{"name": "nginx.restart", "description": "x", "risk_level": "low", "command_template": "cmd", "source_command": "nginx", "side_effects": []}]}

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=mock_binary),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.capabilities.autogen.factory.generate_input_model_code", return_value="code"),
            patch("elle.capabilities.autogen.factory.generate_output_model_code", return_value="code"),
            patch("elle.capabilities.autogen.factory.generate_capability_class_code", return_value="code"),
            patch("elle.cli.agentic.incident_recorder.record_arm_action"),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec
            result = run_async(_learn_package("nginx"))
            assert result.capabilities_saved == 0
            assert any("Failed to save" in w for w in result.warnings)

    def test_incident_recording_failure_is_silent(self):
        """When record_arm_action fails, it is silently logged."""
        from elle.cli.package_learn_commands import _learn_package

        mock_binary = MagicMock()
        mock_binary.path = "/usr/bin/nginx"

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"
        mock_intel.metadata.name = "nginx"
        mock_intel.metadata.version = "1.22"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        mock_spec = MagicMock()
        mock_spec.name = "nginx.restart"

        mock_validation = MagicMock()
        mock_validation.overall_passed = True
        mock_validation.trust_level = "core"

        mock_store = MagicMock()

        caps_json = {"capabilities": [{"name": "nginx.restart", "description": "x", "risk_level": "low", "command_template": "cmd", "source_command": "nginx", "side_effects": []}]}

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=mock_binary),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch("elle.capabilities.autogen.factory.generate_input_model_code", return_value="code"),
            patch("elle.capabilities.autogen.factory.generate_output_model_code", return_value="code"),
            patch("elle.capabilities.autogen.factory.generate_capability_class_code", return_value="code"),
            patch(
                "elle.cli.agentic.incident_recorder.record_arm_action",
                side_effect=RuntimeError("vault down"),
            ),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec
            # Should not raise
            result = run_async(_learn_package("nginx"))
            assert result.package_name == "nginx"

    def test_no_capabilities_key_in_json(self):
        """When LLM returns JSON without 'capabilities' key, no specs generated."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "nginx"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
        ):
            mock_llm_cls.return_value.generate_json.return_value = {"result": "ok"}
            result = run_async(_learn_package("nginx", dry_run=True))
            assert result.capabilities_generated == 0
            assert result.capabilities_validated == 0

    def test_source_command_defaults_to_primary_binary(self):
        """When cap data lacks source_command, defaults to primary_binary."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "ffmpeg"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        # Cap without source_command
        caps_json = {
            "capabilities": [
                {
                    "name": "ffmpeg.convert",
                    "description": "Convert media",
                    "risk_level": "low",
                    "command_template": "ffmpeg -i {input}",
                    "side_effects": [],
                }
            ]
        }

        mock_spec_cls_instance = MagicMock()
        mock_spec_cls_instance.name = "ffmpeg.convert"

        mock_validation = MagicMock()
        mock_validation.overall_passed = True

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec_cls_instance
            result = run_async(_learn_package("ffmpeg", dry_run=True))
            # The source_command should have been injected
            call_kwargs = mock_spec_cls.call_args[1]
            assert call_kwargs["source_command"] == "ffmpeg"

    def test_input_fields_without_constraints(self):
        """Input fields missing constraints get empty dict added."""
        from elle.cli.package_learn_commands import _learn_package

        mock_intel = MagicMock()
        mock_intel.extraction_sources = ("dpkg",)
        mock_intel.primary_binary = "test"

        mock_aggregator = MagicMock()
        mock_aggregator.gather = AsyncMock(return_value=mock_intel)
        mock_aggregator.to_llm_context.return_value = "context"

        mock_composer = MagicMock()
        mock_composer.compose.return_value = "prompt"

        caps_json = {
            "capabilities": [
                {
                    "name": "test.run",
                    "description": "Run test",
                    "risk_level": "low",
                    "command_template": "test run",
                    "source_command": "test",
                    "side_effects": [],
                    "input_fields": [{"name": "verbose"}],
                    "output_fields": ["result"],
                }
            ]
        }

        mock_spec_instance = MagicMock()
        mock_spec_instance.name = "test.run"

        mock_validation = MagicMock()
        mock_validation.overall_passed = True

        with (
            patch("elle.capabilities.autogen.discover_binary", return_value=None),
            patch("elle.capabilities.autogen.intelligence.get_aggregator", return_value=mock_aggregator),
            patch("elle.rag.prompts.get_composer", return_value=mock_composer),
            patch("elle.rag.llm.LLM") as mock_llm_cls,
            patch("elle.capabilities.autogen.GeneratedCapabilitySpec") as mock_spec_cls,
            patch("elle.capabilities.autogen.validate_capability", return_value=mock_validation),
        ):
            mock_llm_cls.return_value.generate_json.return_value = caps_json
            mock_spec_cls.return_value = mock_spec_instance
            result = run_async(_learn_package("test", dry_run=True))
            # Should have added constraints
            call_kwargs = mock_spec_cls.call_args[1]
            assert call_kwargs["input_fields"][0]["constraints"] == {}


# =============================================================================
# _handle_learn_all tests
# =============================================================================


class TestHandleLearnAll:
    def test_all_dry_run(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        with (
            patch(
                "elle.cli.package_learn_commands._get_all_installed_packages",
                new_callable=AsyncMock,
                return_value=["pkg1", "pkg2", "pkg3"],
            ),
            patch(
                "elle.cli.package_learn_commands._filter_learnable_packages",
                new_callable=AsyncMock,
                return_value=["pkg1", "pkg2"],
            ),
        ):
            result = run_async(_handle_learn_all("--all --dry-run"))
            assert "Would learn 2 packages" in result
            assert "pkg1" in result
            assert "pkg2" in result

    def test_all_no_packages(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        with patch(
            "elle.cli.package_learn_commands._get_all_installed_packages",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = run_async(_handle_learn_all("--all"))
            assert "No installed packages" in result

    def test_all_get_packages_failure(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        with patch(
            "elle.cli.package_learn_commands._get_all_installed_packages",
            new_callable=AsyncMock,
            side_effect=RuntimeError("dpkg broken"),
        ):
            result = run_async(_handle_learn_all("--all"))
            assert "Failed to list installed packages" in result

    def test_all_runs_full_system_learn(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        mock_result = {
            "attempted": 2,
            "succeeded": 1,
            "failed": 1,
            "skipped": 0,
            "capabilities_generated": 3,
            "duration": 10.5,
            "failures": [("pkg2", "LLM timeout")],
        }

        with (
            patch(
                "elle.cli.package_learn_commands._get_all_installed_packages",
                new_callable=AsyncMock,
                return_value=["pkg1", "pkg2"],
            ),
            patch(
                "elle.cli.package_learn_commands._filter_learnable_packages",
                new_callable=AsyncMock,
                return_value=["pkg1", "pkg2"],
            ),
            patch(
                "elle.cli.package_learn_commands._run_full_system_learn",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = run_async(_handle_learn_all("--all"))
            assert "Complete!" in result
            assert "2" in result
            assert "LLM timeout" in result

    def test_all_system_learn_failure(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        with (
            patch(
                "elle.cli.package_learn_commands._get_all_installed_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._filter_learnable_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._run_full_system_learn",
                new_callable=AsyncMock,
                side_effect=RuntimeError("crash"),
            ),
        ):
            result = run_async(_handle_learn_all("--all"))
            assert "failed" in result.lower()

    def test_all_dry_run_many_packages(self):
        """When more than 50 packages, output truncated."""
        from elle.cli.package_learn_commands import _handle_learn_all

        pkgs = [f"pkg{i}" for i in range(70)]
        with (
            patch(
                "elle.cli.package_learn_commands._get_all_installed_packages",
                new_callable=AsyncMock,
                return_value=pkgs,
            ),
            patch(
                "elle.cli.package_learn_commands._filter_learnable_packages",
                new_callable=AsyncMock,
                return_value=pkgs,
            ),
        ):
            result = run_async(_handle_learn_all("--all --dry-run"))
            assert "20 more" in result

    def test_all_with_max_concurrent(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        mock_result = {
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "capabilities_generated": 2,
            "duration": 5.0,
            "failures": [],
        }

        with (
            patch(
                "elle.cli.package_learn_commands._get_all_installed_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._filter_learnable_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._run_full_system_learn",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_run,
        ):
            run_async(_handle_learn_all("--all --max-concurrent=4"))
            call_kwargs = mock_run.call_args
            assert call_kwargs[1]["max_concurrent"] == 4

    def test_all_with_no_skip(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        mock_result = {
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "capabilities_generated": 1,
            "duration": 1.0,
            "failures": [],
        }

        with (
            patch(
                "elle.cli.package_learn_commands._get_all_installed_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._filter_learnable_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._run_full_system_learn",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_run,
        ):
            run_async(_handle_learn_all("--all --no-skip"))
            call_kwargs = mock_run.call_args
            assert call_kwargs[1]["skip_existing"] is False

    def test_all_result_with_skipped(self):
        from elle.cli.package_learn_commands import _handle_learn_all

        mock_result = {
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 5,
            "capabilities_generated": 1,
            "duration": 1.0,
            "failures": [],
        }

        with (
            patch(
                "elle.cli.package_learn_commands._get_all_installed_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._filter_learnable_packages",
                new_callable=AsyncMock,
                return_value=["pkg1"],
            ),
            patch(
                "elle.cli.package_learn_commands._run_full_system_learn",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = run_async(_handle_learn_all("--all"))
            assert "Skipped" in result


# =============================================================================
# _get_all_installed_packages tests
# =============================================================================


class TestGetAllInstalledPackages:
    def test_success(self):
        from elle.cli.package_learn_commands import _get_all_installed_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "pkg1\npkg2\npkg3\n"

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_get_all_installed_packages())
            assert result == ["pkg1", "pkg2", "pkg3"]

    def test_failure(self):
        from elle.cli.package_learn_commands import _get_all_installed_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "not found"

        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="dpkg-query failed"):
                run_async(_get_all_installed_packages())

    def test_empty_lines_filtered(self):
        from elle.cli.package_learn_commands import _get_all_installed_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "pkg1\n\n  \npkg2\n"

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_get_all_installed_packages())
            assert result == ["pkg1", "pkg2"]


# =============================================================================
# _filter_learnable_packages tests
# =============================================================================


class TestFilterLearnablePackages:
    def test_binary_detected(self):
        from elle.cli.package_learn_commands import _filter_learnable_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "/usr/bin/nginx\n/usr/share/doc/nginx/readme"

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_filter_learnable_packages(["nginx"]))
            assert result == ["nginx"]

    def test_manpage_detected(self):
        from elle.cli.package_learn_commands import _filter_learnable_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "/usr/share/man/man1/foo.1.gz\n/usr/share/doc/foo"

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_filter_learnable_packages(["foo"]))
            assert result == ["foo"]

    def test_no_binary_no_manpage(self):
        from elle.cli.package_learn_commands import _filter_learnable_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "/usr/share/doc/libfoo/readme\n/usr/lib/libfoo.so"

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_filter_learnable_packages(["libfoo"]))
            assert result == []

    def test_dpkg_query_failure_skipped(self):
        from elle.cli.package_learn_commands import _filter_learnable_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 1

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_filter_learnable_packages(["broken"]))
            assert result == []

    def test_exception_skipped(self):
        from elle.cli.package_learn_commands import _filter_learnable_packages

        with patch("subprocess.run", side_effect=OSError("permission denied")):
            result = run_async(_filter_learnable_packages(["pkg"]))
            assert result == []

    def test_sbin_binary_detected(self):
        from elle.cli.package_learn_commands import _filter_learnable_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "/usr/sbin/iptables\n/usr/share/doc/iptables"

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_filter_learnable_packages(["iptables"]))
            assert result == ["iptables"]

    def test_man8_detected(self):
        from elle.cli.package_learn_commands import _filter_learnable_packages

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "/usr/share/man/man8/mount.8.gz"

        with patch("subprocess.run", return_value=mock_proc):
            result = run_async(_filter_learnable_packages(["mount"]))
            assert result == ["mount"]


# =============================================================================
# _run_full_system_learn tests
# =============================================================================


class TestRunFullSystemLearn:
    def test_skip_existing_packages(self):
        from elle.cli.package_learn_commands import _run_full_system_learn

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = [("pkg1", "1.0")]

        mock_result = LearnResult(
            package_name="pkg2",
            capabilities_generated=1,
            capabilities_validated=1,
            capabilities_saved=1,
            extraction_sources=("dpkg",),
        )

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.cli.package_learn_commands._learn_package",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = run_async(_run_full_system_learn(["pkg1", "pkg2"], skip_existing=True))
            assert result["skipped"] == 1
            assert result["attempted"] == 1
            assert result["succeeded"] == 1

    def test_no_skip_learns_all(self):
        from elle.cli.package_learn_commands import _run_full_system_learn

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = []

        mock_result = LearnResult(
            package_name="pkg",
            capabilities_generated=1,
            capabilities_validated=1,
            capabilities_saved=1,
            extraction_sources=("dpkg",),
        )

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.cli.package_learn_commands._learn_package",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = run_async(_run_full_system_learn(["pkg1", "pkg2"], skip_existing=False))
            assert result["attempted"] == 2
            assert result["succeeded"] == 2

    def test_learn_failure_tracked(self):
        from elle.cli.package_learn_commands import _run_full_system_learn

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = []

        mock_result_with_errors = LearnResult(
            package_name="bad-pkg",
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=(),
            errors=("No intel",),
        )

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.cli.package_learn_commands._learn_package",
                new_callable=AsyncMock,
                return_value=mock_result_with_errors,
            ),
        ):
            result = run_async(_run_full_system_learn(["bad-pkg"]))
            assert result["failed"] == 1
            assert result["failures"][0] == ("bad-pkg", "No intel")

    def test_exception_in_learn_one(self):
        from elle.cli.package_learn_commands import _run_full_system_learn

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = []

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.cli.package_learn_commands._learn_package",
                new_callable=AsyncMock,
                side_effect=RuntimeError("total crash"),
            ),
        ):
            result = run_async(_run_full_system_learn(["pkg1"]))
            assert result["failed"] == 1
            assert "total crash" in result["failures"][0][1]

    def test_duration_computed(self):
        from elle.cli.package_learn_commands import _run_full_system_learn

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = []

        mock_result = LearnResult(
            package_name="pkg",
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=(),
            errors=("no intel",),
        )

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.cli.package_learn_commands._learn_package",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = run_async(_run_full_system_learn([]))
            assert "duration" in result
            assert result["duration"] >= 0.0


# =============================================================================
# _handle_show extended tests
# =============================================================================


class TestHandleShowExtended:
    def test_show_disabled_capability(self):
        from elle.cli.package_learn_commands import _handle_show

        mock_stored = MagicMock()
        mock_stored.approved = False
        mock_stored.enabled = False
        mock_stored.trust_level.value = "third_party"

        mock_spec = MagicMock()
        mock_spec.name = "custom.run"
        mock_spec.description = "A custom operation for running things with parameters"
        mock_spec.risk_level = "high"
        mock_spec.command_template = "custom-tool run --mode=full --verbose --output"

        mock_store = MagicMock()
        mock_store.list_by_package.return_value = [mock_stored]

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.capabilities.autogen.GeneratedCapabilitySpec.model_validate_json",
                return_value=mock_spec,
            ),
        ):
            result = run_async(_handle_show("custom"))
            assert "pending" in result
            assert "disabled" in result
            assert "custom.run" in result

    def test_show_spec_parse_error(self):
        from elle.cli.package_learn_commands import _handle_show

        mock_stored = MagicMock()
        mock_stored.capability_name = "broken.cap"

        mock_store = MagicMock()
        mock_store.list_by_package.return_value = [mock_stored]

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.capabilities.autogen.GeneratedCapabilitySpec.model_validate_json",
                side_effect=ValueError("bad json"),
            ),
        ):
            result = run_async(_handle_show("broken"))
            assert "Error loading spec" in result
            assert "broken.cap" in result


# =============================================================================
# _handle_approve / _handle_delete incident recording failure tests
# =============================================================================


class TestApproveDeleteRecordingFailure:
    def test_approve_recording_failure_silent(self):
        from elle.cli.package_learn_commands import _handle_approve

        mock_store = MagicMock()
        mock_store.approve.return_value = True

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.cli.agentic.incident_recorder.record_arm_action",
                side_effect=RuntimeError("vault down"),
            ),
        ):
            result = run_async(_handle_approve("test.cap"))
            assert "Approved" in result

    def test_delete_recording_failure_silent(self):
        from elle.cli.package_learn_commands import _handle_delete

        mock_store = MagicMock()
        mock_store.delete.return_value = True

        with (
            patch("elle.capabilities.autogen.get_store", return_value=mock_store),
            patch(
                "elle.cli.agentic.incident_recorder.record_arm_action",
                side_effect=RuntimeError("vault down"),
            ),
        ):
            result = run_async(_handle_delete("test.cap"))
            assert "Deleted" in result


# =============================================================================
# _handle_bootstrap extended tests
# =============================================================================


class TestHandleBootstrapExtended:
    def test_bootstrap_core_flag(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        mock_result = MagicMock()
        mock_result.packages_attempted = 5
        mock_result.packages_succeeded = 5
        mock_result.packages_failed = 0
        mock_result.packages_skipped = 0
        mock_result.capabilities_generated = 10
        mock_result.capabilities_validated = 10
        mock_result.capabilities_saved = 10
        mock_result.duration_seconds = 5.0
        mock_result.failed_packages = []

        with (
            patch(
                "elle.capabilities.autogen.bootstrap.run_bootstrap",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_run,
            patch("elle.capabilities.autogen.bootstrap.set_bootstrap_complete"),
        ):
            run_async(_handle_bootstrap("--core"))
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["include_core"] is True
            assert call_kwargs["include_essential"] is False
            assert call_kwargs["include_optional"] is False
            assert call_kwargs["include_dependencies"] is False

    def test_bootstrap_essential_flag(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        mock_result = MagicMock()
        mock_result.packages_attempted = 5
        mock_result.packages_succeeded = 5
        mock_result.packages_failed = 0
        mock_result.packages_skipped = 0
        mock_result.capabilities_generated = 10
        mock_result.capabilities_validated = 10
        mock_result.capabilities_saved = 10
        mock_result.duration_seconds = 5.0
        mock_result.failed_packages = []

        with (
            patch(
                "elle.capabilities.autogen.bootstrap.run_bootstrap",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_run,
            patch("elle.capabilities.autogen.bootstrap.set_bootstrap_complete"),
        ):
            run_async(_handle_bootstrap("--essential"))
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["include_essential"] is True
            assert call_kwargs["include_core"] is False

    def test_bootstrap_deps_flag(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        mock_result = MagicMock()
        mock_result.packages_attempted = 5
        mock_result.packages_succeeded = 5
        mock_result.packages_failed = 0
        mock_result.packages_skipped = 0
        mock_result.capabilities_generated = 10
        mock_result.capabilities_validated = 10
        mock_result.capabilities_saved = 10
        mock_result.duration_seconds = 5.0
        mock_result.failed_packages = []

        with (
            patch(
                "elle.capabilities.autogen.bootstrap.run_bootstrap",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_run,
            patch("elle.capabilities.autogen.bootstrap.set_bootstrap_complete"),
        ):
            run_async(_handle_bootstrap("--deps"))
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["include_dependencies"] is True
            assert call_kwargs["include_core"] is False

    def test_bootstrap_many_failed_packages(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        mock_result = MagicMock()
        mock_result.packages_attempted = 20
        mock_result.packages_succeeded = 5
        mock_result.packages_failed = 15
        mock_result.packages_skipped = 3
        mock_result.capabilities_generated = 10
        mock_result.capabilities_validated = 8
        mock_result.capabilities_saved = 8
        mock_result.duration_seconds = 60.0
        mock_result.failed_packages = [(f"pkg{i}", f"error{i}") for i in range(15)]

        with (
            patch(
                "elle.capabilities.autogen.bootstrap.run_bootstrap",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("elle.capabilities.autogen.bootstrap.set_bootstrap_complete"),
        ):
            result = run_async(_handle_bootstrap(""))
            assert "Failed packages:" in result
            assert "5 more" in result  # 15 - 10 = 5 truncated

    def test_bootstrap_no_failures_no_skipped(self):
        from elle.cli.package_learn_commands import _handle_bootstrap

        mock_result = MagicMock()
        mock_result.packages_attempted = 5
        mock_result.packages_succeeded = 5
        mock_result.packages_failed = 0
        mock_result.packages_skipped = 0
        mock_result.capabilities_generated = 10
        mock_result.capabilities_validated = 10
        mock_result.capabilities_saved = 10
        mock_result.duration_seconds = 3.0
        mock_result.failed_packages = []

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
            assert "Failed" not in result
            assert "Skipped" not in result


# =============================================================================
# handle_learn_command edge cases
# =============================================================================


class TestHandleLearnCommandEdgeCases:
    def test_all_flag_with_extra_flags(self):
        """--all with additional flags dispatches to _handle_learn_all."""
        with patch(
            "elle.cli.package_learn_commands._handle_learn_all",
            new_callable=AsyncMock,
            return_value="all output",
        ) as mock_all:
            result = run_async(handle_learn_command("--all --dry-run"))
            mock_all.assert_called_once_with("--all --dry-run")
            assert result == "all output"

    def test_package_with_multiple_flags(self):
        """Package with --refresh --dry-run dispatches correctly."""
        with patch(
            "elle.cli.package_learn_commands._handle_learn",
            new_callable=AsyncMock,
            return_value="learn output",
        ) as mock_learn:
            result = run_async(handle_learn_command("nginx --refresh --dry-run"))
            mock_learn.assert_called_once_with("nginx --refresh --dry-run")
            assert result == "learn output"

    def test_session_parameter_passed_through(self):
        """Session parameter does not cause errors."""
        result = run_async(handle_learn_command("", session=None))
        assert "/learn" in result

    def test_show_with_extra_whitespace(self):
        """Whitespace in show args is handled -- args.strip() then split(None, 1)."""
        with patch(
            "elle.cli.package_learn_commands._handle_show",
            new_callable=AsyncMock,
            return_value="show output",
        ) as mock_show:
            result = run_async(handle_learn_command("show   nginx  "))
            # args.strip() => "show   nginx", then split(None, 1) => ["show", "nginx"]
            mock_show.assert_called_once_with("nginx")
            assert result == "show output"


# =============================================================================
# handle_learn_command_sync extended tests
# =============================================================================


class TestHandleLearnCommandSyncExtended:
    def test_sync_no_event_loop(self):
        """When no event loop exists, create one."""
        from elle.cli.package_learn_commands import handle_learn_command_sync

        mock_loop = MagicMock()
        mock_loop.run_until_complete.return_value = "help text"

        # Force no existing event loop, patch new_event_loop and set_event_loop
        with (
            patch("asyncio.get_event_loop", side_effect=RuntimeError("no loop")),
            patch("asyncio.new_event_loop", return_value=mock_loop),
            patch("asyncio.set_event_loop"),
        ):
            result = handle_learn_command_sync("")
            assert result == "help text"
            mock_loop.run_until_complete.assert_called_once()


# =============================================================================
# _handle_list with mixed approval counts
# =============================================================================


class TestHandleListExtended:
    def test_list_mixed_approved_counts(self):
        from elle.cli.package_learn_commands import _handle_list

        cap_approved = MagicMock()
        cap_approved.approved = True

        cap_not_approved = MagicMock()
        cap_not_approved.approved = False

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = [
            ("nginx", "1.22"),
        ]
        mock_store.list_by_package.return_value = [
            cap_approved,
            cap_not_approved,
            cap_not_approved,
        ]

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_list(""))
            assert "nginx" in result
            assert "3 caps" in result
            assert "1 approved" in result

    def test_list_package_unknown_version(self):
        from elle.cli.package_learn_commands import _handle_list

        mock_store = MagicMock()
        mock_store.list_packages_with_capabilities.return_value = [
            ("testpkg", None),
        ]
        mock_store.list_by_package.return_value = []

        with patch("elle.capabilities.autogen.get_store", return_value=mock_store):
            result = run_async(_handle_list(""))
            assert "testpkg" in result
            assert "unknown" in result
