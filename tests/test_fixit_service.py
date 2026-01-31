"""Tests for the FixitService orchestration layer.

Covers:
- Context building from session state (basic, empty, history truncation)
- Man Vault search: success, ImportError, RuntimeError, non-numeric score
- Incident Vault search: ImportError graceful degradation, disabled path
- Error keyword extraction: error types, file paths, services, dedup, limits
- Incident creation: success, ImportError, RuntimeError
- Action recording: success, ImportError silence, truncation
- LLM analysis: success, unavailable, JSON error, ImportError, LLMUnavailableError
- LLM response parsing: valid, minimal, malformed
- Full pipeline: no-LLM, with analysis + verification, incident creation
- Capability-based fix execution and raw-command fallback
- Confidence computation and citation building
- Module-level singleton management (get / reset)
- Finalize incident: ImportError, RuntimeError
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401 - used in pytest.raises

from elle.cli.fixit.models import (
    CommandFailure,
    FixCommand,
    FixitAction,
    FixitAnalysis,
    FixitContext,
    FixitDiagnosis,
    FixitOutcome,
    FixitResult,
    ManSnippetContext,
    PriorArtContext,
    SuccessfulAction,
    VerificationResult,
    VerificationStatus,
    VerifiedFixCommand,
)
from elle.cli.fixit.service import (
    FixitService,
    get_fixit_service,
    reset_fixit_service,
)

# =============================================================================
# Shared helpers
# =============================================================================

_SVC = "elle.cli.fixit.service"


def _make_session(**overrides: Any) -> MagicMock:
    """Build a lightweight mock Session with sensible defaults."""
    session = MagicMock()
    session.last_cmd = overrides.get("last_cmd", "ls /nonexistent")
    session.last_exit = overrides.get("last_exit", 2)
    session.last_stdout = overrides.get("last_stdout", "")
    session.last_stderr = overrides.get("last_stderr", "No such file or directory")
    session.cwd = overrides.get("cwd", Path("/home/user"))
    session.history = overrides.get("history", ("cd /home/user", "ls /nonexistent"))
    return session


def _make_failure(**overrides: Any) -> CommandFailure:
    return CommandFailure(
        command=overrides.get("command", "apt install foo"),
        exit_code=overrides.get("exit_code", 1),
        stdout=overrides.get("stdout", ""),
        stderr=overrides.get("stderr", "E: Unable to locate package foo"),
        cwd=overrides.get("cwd", Path("/home/user")),
    )


def _make_context(**overrides: Any) -> FixitContext:
    failure = overrides.get("failure", _make_failure())
    return FixitContext(
        failure=failure,
        history=overrides.get("history", ()),
        man_snippets=overrides.get("man_snippets", ()),
        prior_art=overrides.get("prior_art", ()),
    )


def _make_diagnosis(**overrides: Any) -> FixitDiagnosis:
    return FixitDiagnosis(
        error_category=overrides.get("error_category", "dependency_missing"),
        summary=overrides.get("summary", "Package not found"),
        root_cause=overrides.get("root_cause", "The package foo is not in the repo"),
        confidence=overrides.get("confidence", 0.85),
    )


def _make_analysis(**overrides: Any) -> FixitAnalysis:
    return FixitAnalysis(
        diagnosis=overrides.get("diagnosis", _make_diagnosis()),
        suggestions=overrides.get(
            "suggestions",
            (
                FixCommand(
                    command="apt update",
                    explanation="Refresh package index",
                    risk_level="safe",
                ),
            ),
        ),
        alternative_approach=overrides.get("alternative_approach"),
        learn_more=overrides.get("learn_more", ()),
    )


def _make_result(**overrides: Any) -> FixitResult:
    return FixitResult(
        context=overrides.get("context", _make_context()),
        analysis=overrides.get("analysis"),
        incident_id=overrides.get("incident_id"),
    )


# =============================================================================
# TestFixitServiceInit
# =============================================================================


class TestFixitServiceInit:
    """Tests for service initialization."""

    def test_default_init(self):
        """Default service enables all features."""
        service = FixitService()
        assert service._use_man_vault is True
        assert service._use_incident_vault is True
        assert service._use_llm is True

    def test_custom_init_all_disabled(self):
        """All features can be explicitly disabled."""
        service = FixitService(
            use_man_vault=False,
            use_incident_vault=False,
            use_llm=False,
        )
        assert service._use_man_vault is False
        assert service._use_incident_vault is False
        assert service._use_llm is False


# =============================================================================
# TestBuildContext
# =============================================================================


class TestBuildContext:
    """Tests for FixitService.build_context."""

    def test_build_context_basic(self):
        """Context is correctly populated from session fields."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False)
        session = _make_session()

        ctx = svc.build_context(session)

        assert ctx.failure.command == "ls /nonexistent"
        assert ctx.failure.exit_code == 2
        assert ctx.failure.stderr == "No such file or directory"
        assert ctx.failure.cwd == Path("/home/user")
        assert ctx.history == ("cd /home/user", "ls /nonexistent")

    def test_build_context_none_values_produce_defaults(self):
        """Handles None values in session gracefully (defaults to empty)."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False)
        session = _make_session(
            last_cmd=None,
            last_exit=None,
            last_stdout=None,
            last_stderr=None,
            history=None,
        )

        ctx = svc.build_context(session)

        assert ctx.failure.command == ""
        assert ctx.failure.exit_code == 1
        assert ctx.failure.stdout == ""
        assert ctx.failure.stderr == ""
        assert ctx.history == ()

    def test_build_context_history_truncated_to_10(self):
        """Only the last 10 history entries are retained."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False)
        session = _make_session(history=tuple(f"cmd_{i}" for i in range(25)))

        ctx = svc.build_context(session)

        assert len(ctx.history) == 10
        assert ctx.history[0] == "cmd_15"

    def test_build_context_vaults_disabled(self):
        """With vaults disabled, man_snippets and prior_art are empty."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False)
        session = _make_session()

        ctx = svc.build_context(session)

        assert ctx.man_snippets == ()
        assert ctx.prior_art == ()


# =============================================================================
# TestSearchManVault
# =============================================================================


class TestSearchManVault:
    """Tests for Man Vault searching with graceful degradation."""

    def test_search_man_vault_success(self):
        """Results are converted to ManSnippetContext tuples with correct scores."""
        svc = FixitService(use_man_vault=True, use_incident_vault=False)
        failure = _make_failure(command="chmod +x script.sh", stderr="Operation not permitted")

        mock_result = MagicMock()
        mock_result.name = "chmod"
        mock_result.section = "1"
        mock_result.snippet = "change file mode bits"
        mock_result.match_section = "DESCRIPTION"
        mock_result.score = 0.92

        mock_mod = MagicMock()
        mock_mod.search = MagicMock(return_value=[mock_result])

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            snippets = svc._search_man_vault(failure)

        assert len(snippets) == 1
        assert snippets[0].name == "chmod"
        assert snippets[0].section == "1"
        assert snippets[0].relevance_score == 0.92

    def test_search_man_vault_import_error(self):
        """ImportError gracefully returns empty tuple."""
        svc = FixitService(use_man_vault=True, use_incident_vault=False)
        failure = _make_failure()

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": None}):
            snippets = svc._search_man_vault(failure)

        assert snippets == ()

    def test_search_man_vault_runtime_error(self):
        """Runtime errors during search return empty tuple."""
        svc = FixitService(use_man_vault=True, use_incident_vault=False)
        failure = _make_failure()

        mock_mod = MagicMock()
        mock_mod.search = MagicMock(side_effect=RuntimeError("db locked"))

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            snippets = svc._search_man_vault(failure)

        assert snippets == ()

    def test_search_man_vault_non_numeric_score_defaults_to_half(self):
        """Non-numeric score attribute defaults to 0.5."""
        svc = FixitService(use_man_vault=True, use_incident_vault=False)
        failure = _make_failure(command="grep pattern file")

        mock_result = MagicMock()
        mock_result.name = "grep"
        mock_result.section = "1"
        mock_result.snippet = "search file patterns"
        mock_result.match_section = None
        mock_result.score = "not-a-number"

        mock_mod = MagicMock()
        mock_mod.search = MagicMock(return_value=[mock_result])

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            snippets = svc._search_man_vault(failure)

        assert len(snippets) == 1
        assert snippets[0].relevance_score == 0.5

    def test_search_man_vault_score_clamped(self):
        """Score above 1.0 is clamped to 1.0 and below 0 clamped to 0.0."""
        svc = FixitService()
        failure = _make_failure(command="ls")

        high = MagicMock()
        high.name = "ls"
        high.section = "1"
        high.snippet = "list"
        high.match_section = None
        high.score = 5.0

        low = MagicMock()
        low.name = "ls"
        low.section = "1"
        low.snippet = "list"
        low.match_section = None
        low.score = -2.0

        mock_mod = MagicMock()
        mock_mod.search = MagicMock(return_value=[high, low])

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            snippets = svc._search_man_vault(failure)

        assert snippets[0].relevance_score == 1.0
        assert snippets[1].relevance_score == 0.0


# =============================================================================
# TestSearchPriorArt
# =============================================================================


class TestSearchPriorArt:
    """Tests for Incident Vault search with daemon API + fallback."""

    def test_search_prior_art_all_import_errors_return_empty(self):
        """ImportError from all search tiers returns empty tuple."""
        svc = FixitService(use_man_vault=False, use_incident_vault=True)
        failure = _make_failure()

        with patch.dict(
            "sys.modules",
            {
                "elle.cli.daemon_client": None,
                "elle.daemon.incidents.correlator": None,
                "elle.daemon.incidents.retriever": None,
                "elle.daemon.incidents.snapshot": None,
            },
        ):
            result = svc._search_prior_art(failure)

        assert result == ()

    def test_search_prior_art_skipped_when_disabled(self):
        """Prior art search is skipped when use_incident_vault=False."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False)
        session = _make_session()
        ctx = svc.build_context(session)
        assert ctx.prior_art == ()


# =============================================================================
# TestExtractErrorKeywords
# =============================================================================


class TestExtractErrorKeywords:
    """Tests for _extract_error_keywords helper."""

    def test_extracts_error_types(self):
        """Extracts common error pattern keywords like 'not found'."""
        svc = FixitService()
        keywords = svc._extract_error_keywords("ENOENT: not found /usr/bin/node")
        lower = [k.lower() for k in keywords]
        assert "not found" in lower or "enoent" in lower

    def test_extracts_file_basenames(self):
        """Extracts basenames from full file paths."""
        svc = FixitService()
        keywords = svc._extract_error_keywords("cannot open /etc/nginx/nginx.conf: denied")
        assert "nginx.conf" in keywords

    def test_extracts_service_names(self):
        """Extracts systemd unit names (*.service pattern)."""
        svc = FixitService()
        keywords = svc._extract_error_keywords("Failed to start nginx.service")
        assert "nginx.service" in keywords

    def test_empty_stderr_returns_empty_list(self):
        """Empty stderr returns an empty keyword list."""
        svc = FixitService()
        assert svc._extract_error_keywords("") == []

    def test_deduplication(self):
        """Duplicate keywords appear only once."""
        svc = FixitService()
        keywords = svc._extract_error_keywords("error: error: error")
        assert keywords.count("error") == 1

    def test_short_keywords_excluded(self):
        """Keywords with 2 or fewer characters are filtered out."""
        svc = FixitService()
        keywords = svc._extract_error_keywords("E: something failed")
        assert all(len(kw) > 2 for kw in keywords)

    def test_maximum_ten_keywords(self):
        """Result list is capped at 10 keywords."""
        svc = FixitService()
        stderr = " ".join(f"/path/to/file_{i}.conf" for i in range(30))
        keywords = svc._extract_error_keywords(stderr)
        assert len(keywords) <= 10


# =============================================================================
# TestCreateIncident
# =============================================================================


class TestCreateIncident:
    """Tests for _create_incident."""

    def test_create_incident_success(self):
        """Returns incident_id when correlator creates it successfully."""
        svc = FixitService()
        failure = _make_failure()

        mock_correlator = MagicMock()
        mock_correlator.create_from_command_failure.return_value = "INC-001"
        mock_mod = MagicMock()
        mock_mod.get_correlator.return_value = mock_correlator

        with patch.dict("sys.modules", {"elle.daemon.incidents.correlator": mock_mod}):
            result = svc._create_incident(failure)

        assert result == "INC-001"
        mock_correlator.create_from_command_failure.assert_called_once_with(
            command="apt install foo",
            stderr="E: Unable to locate package foo",
            exit_code=1,
        )

    def test_create_incident_import_error(self):
        """ImportError returns None without raising."""
        svc = FixitService()
        failure = _make_failure()

        with patch.dict("sys.modules", {"elle.daemon.incidents.correlator": None}):
            result = svc._create_incident(failure)

        assert result is None

    def test_create_incident_runtime_error(self):
        """Runtime errors during creation return None."""
        svc = FixitService()
        failure = _make_failure()

        mock_mod = MagicMock()
        mock_mod.get_correlator.side_effect = RuntimeError("db locked")

        with patch.dict("sys.modules", {"elle.daemon.incidents.correlator": mock_mod}):
            result = svc._create_incident(failure)

        assert result is None


# =============================================================================
# TestRecordAction
# =============================================================================


class TestRecordAction:
    """Tests for record_action."""

    def test_record_action_success(self):
        """Action details are forwarded to the incidents store."""
        svc = FixitService()
        action = FixitAction(
            command="apt update",
            exit_code=0,
            stdout="Hit:1 ...",
            stderr="",
            success=True,
            privileged=False,
            duration_ms=1200,
        )

        mock_store = MagicMock()
        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            svc.record_action("INC-001", action)

        mock_store.append_action.assert_called_once()
        kw = mock_store.append_action.call_args[1]
        assert kw["incident_id"] == "INC-001"
        assert kw["command"] == "apt update"
        assert kw["success"] is True

    def test_record_action_import_error_silent(self):
        """ImportError is silently swallowed (no raise)."""
        svc = FixitService()
        action = FixitAction(command="ls", exit_code=0, success=True)

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": None}):
            svc.record_action("INC-001", action)  # must not raise

    def test_record_action_runtime_error_silent(self):
        """Runtime error inside append_action is caught and logged."""
        svc = FixitService()
        action = FixitAction(command="ls", exit_code=0, success=True)

        mock_store = MagicMock()
        mock_store.append_action.side_effect = RuntimeError("write failed")

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            svc.record_action("INC-001", action)  # must not raise

    def test_record_action_truncates_long_output(self):
        """Stdout and stderr are truncated to 1000 characters."""
        svc = FixitService()
        long_output = "x" * 5000
        action = FixitAction(
            command="verbose_cmd",
            exit_code=0,
            stdout=long_output,
            stderr=long_output,
            success=True,
        )

        mock_store = MagicMock()
        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            svc.record_action("INC-001", action)

        kw = mock_store.append_action.call_args[1]
        assert len(kw["stdout"]) == 1000
        assert len(kw["stderr"]) == 1000


# =============================================================================
# TestAnalyzeWithLLM
# =============================================================================


class TestAnalyzeWithLLM:
    """Tests for _analyze_with_llm."""

    def test_analyze_success(self):
        """Valid LLM JSON response is parsed into FixitAnalysis."""
        svc = FixitService(use_llm=True)
        context = _make_context()

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate_json.return_value = {
            "diagnosis": {
                "error_category": "dependency_missing",
                "summary": "Package not found",
                "root_cause": "apt cache stale",
                "confidence": 0.9,
            },
            "suggestions": [
                {
                    "command": "apt update",
                    "explanation": "Refresh cache",
                    "risk_level": "safe",
                }
            ],
        }

        mock_rag = MagicMock()
        mock_rag.get_llm.return_value = mock_llm
        mock_rag.LLMJSONError = type("LLMJSONError", (Exception,), {})
        mock_rag.LLMUnavailableError = type("LLMUnavailableError", (Exception,), {})

        with patch.dict("sys.modules", {"elle.rag.llm": mock_rag}):
            result = svc._analyze_with_llm(context)

        assert result is not None
        assert result.diagnosis.error_category == "dependency_missing"
        assert len(result.suggestions) == 1
        assert result.suggestions[0].command == "apt update"

    def test_analyze_llm_not_available(self):
        """Returns None when LLM reports itself as unavailable."""
        svc = FixitService(use_llm=True)
        context = _make_context()

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        mock_rag = MagicMock()
        mock_rag.get_llm.return_value = mock_llm
        mock_rag.LLMJSONError = type("LLMJSONError", (Exception,), {})
        mock_rag.LLMUnavailableError = type("LLMUnavailableError", (Exception,), {})

        with patch.dict("sys.modules", {"elle.rag.llm": mock_rag}):
            result = svc._analyze_with_llm(context)

        assert result is None

    def test_analyze_llm_json_error(self):
        """Returns None when LLM returns unparseable JSON."""
        svc = FixitService(use_llm=True)
        context = _make_context()

        LLMJSONError = type("LLMJSONError", (Exception,), {})

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate_json.side_effect = LLMJSONError("bad json")

        mock_rag = MagicMock()
        mock_rag.get_llm.return_value = mock_llm
        mock_rag.LLMJSONError = LLMJSONError
        mock_rag.LLMUnavailableError = type("LLMUnavailableError", (Exception,), {})

        with patch.dict("sys.modules", {"elle.rag.llm": mock_rag}):
            result = svc._analyze_with_llm(context)

        assert result is None

    def test_analyze_llm_import_error(self):
        """Exercises the ImportError path when elle.rag.llm is absent.

        Known issue in the source: when ImportError is raised inside
        the outer try, Python evaluates ``except LLMUnavailableError``
        before ``except ImportError``, but LLMUnavailableError is
        unbound (the import that defines it failed), so Python raises
        UnboundLocalError.  The test documents this behaviour.
        """
        svc = FixitService(use_llm=True)
        context = _make_context()

        # Remove the cached module so import triggers fresh resolution
        saved = sys.modules.pop("elle.rag.llm", None)
        import elle.rag as _rag_pkg

        saved_attr = getattr(_rag_pkg, "llm", None)
        if hasattr(_rag_pkg, "llm"):
            delattr(_rag_pkg, "llm")

        # Block the module so import raises ModuleNotFoundError
        sys.modules["elle.rag.llm"] = None  # type: ignore[assignment]

        try:
            # The source tries ``except LLMUnavailableError`` before
            # ``except ImportError``, but the name is unbound,
            # so UnboundLocalError propagates.
            with pytest.raises((UnboundLocalError, NameError)):
                svc._analyze_with_llm(context)
        finally:
            # Restore original state
            if saved is not None:
                sys.modules["elle.rag.llm"] = saved
            elif "elle.rag.llm" in sys.modules:
                del sys.modules["elle.rag.llm"]
            if saved_attr is not None:
                _rag_pkg.llm = saved_attr

    def test_analyze_llm_unavailable_error(self):
        """Returns None when LLMUnavailableError is raised."""
        svc = FixitService(use_llm=True)
        context = _make_context()

        LLMUnavailableError = type("LLMUnavailableError", (Exception,), {})

        mock_rag = MagicMock()
        mock_rag.LLMJSONError = type("LLMJSONError", (Exception,), {})
        mock_rag.LLMUnavailableError = LLMUnavailableError
        mock_rag.get_llm.side_effect = LLMUnavailableError("service down")

        with patch.dict("sys.modules", {"elle.rag.llm": mock_rag}):
            result = svc._analyze_with_llm(context)

        assert result is None

    def test_analyze_generic_exception(self):
        """Returns None and logs when an unexpected exception fires."""
        svc = FixitService(use_llm=True)
        context = _make_context()

        mock_rag = MagicMock()
        mock_rag.LLMJSONError = type("LLMJSONError", (Exception,), {})
        mock_rag.LLMUnavailableError = type("LLMUnavailableError", (Exception,), {})
        mock_rag.get_llm.side_effect = ValueError("unexpected")

        with patch.dict("sys.modules", {"elle.rag.llm": mock_rag}):
            result = svc._analyze_with_llm(context)

        assert result is None


# =============================================================================
# TestParseLLMResponse
# =============================================================================


class TestParseLLMResponse:
    """Tests for _parse_llm_response."""

    def test_parse_valid_full_response(self):
        """Fully populated response is parsed correctly."""
        svc = FixitService()
        resp = {
            "diagnosis": {
                "error_category": "permission_denied",
                "summary": "Cannot write to file",
                "root_cause": "User lacks write permissions",
                "confidence": 0.95,
            },
            "suggestions": [
                {
                    "command": "chmod 644 /etc/config",
                    "explanation": "Set correct permissions",
                    "risk_level": "moderate",
                    "requires_privilege": True,
                    "verification": "ls -la /etc/config",
                },
                {
                    "command": "",
                    "explanation": "Empty command should be filtered",
                    "risk_level": "safe",
                },
            ],
            "alternative_approach": "Use chown instead",
            "learn_more": ["chmod(1)", "chown(1)"],
        }

        result = svc._parse_llm_response(resp)

        assert result is not None
        assert result.diagnosis.confidence == 0.95
        # Empty command is filtered
        assert len(result.suggestions) == 1
        assert result.suggestions[0].requires_privilege is True
        assert result.alternative_approach == "Use chown instead"
        assert result.learn_more == ("chmod(1)", "chown(1)")

    def test_parse_minimal_response_uses_defaults(self):
        """Response with empty dicts still parses using defaults."""
        svc = FixitService()
        resp = {
            "diagnosis": {},
            "suggestions": [],
        }

        result = svc._parse_llm_response(resp)

        assert result is not None
        assert result.diagnosis.error_category == "other"
        assert result.diagnosis.summary == "Unknown error"
        assert result.diagnosis.root_cause == "Unknown cause"
        assert result.diagnosis.confidence == 0.5
        assert len(result.suggestions) == 0
        assert result.alternative_approach is None
        assert result.learn_more == ()

    def test_parse_malformed_response_returns_none(self):
        """Completely invalid argument returns None."""
        svc = FixitService()
        result = svc._parse_llm_response(None)  # type: ignore[arg-type]
        assert result is None


# =============================================================================
# TestRunFullPipeline
# =============================================================================


class TestRunFullPipeline:
    """Tests for run_full_pipeline orchestration."""

    def test_pipeline_no_llm_no_vaults(self):
        """Pipeline returns empty result when everything is disabled."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False, use_llm=False)
        session = _make_session()

        result = svc.run_full_pipeline(session)

        assert result.analysis is None
        assert result.verified_suggestions == ()
        assert result.incident_id is None
        assert result.outcome == FixitOutcome.SKIPPED

    def test_pipeline_with_llm_analysis_and_verification(self):
        """Pipeline integrates LLM analysis then verifies suggestions."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False, use_llm=True)
        session = _make_session()

        analysis = _make_analysis()

        verified_cmd = VerifiedFixCommand(
            fix=analysis.suggestions[0],
            verification=VerificationResult(
                command="apt update",
                status=VerificationStatus.PASSED,
                checks_passed=("denylist", "parse", "executable"),
            ),
            is_safe=True,
        )

        with patch.object(svc, "_analyze_with_llm", return_value=analysis):
            with patch(f"{_SVC}.verify_analysis", return_value=((verified_cmd,), ())):
                result = svc.run_full_pipeline(session)

        assert result.analysis is not None
        assert result.analysis.diagnosis.error_category == "dependency_missing"
        assert len(result.verified_suggestions) == 1
        assert result.verified_suggestions[0].is_safe is True

    def test_pipeline_creates_incident_when_vault_enabled(self):
        """Pipeline creates incident via _create_incident."""
        svc = FixitService(use_man_vault=False, use_incident_vault=True, use_llm=False)
        session = _make_session()

        with patch.object(svc, "_create_incident", return_value="INC-042"):
            result = svc.run_full_pipeline(session)

        assert result.incident_id == "INC-042"

    def test_pipeline_incident_creation_failure_continues(self):
        """Pipeline proceeds when incident creation returns None."""
        svc = FixitService(use_man_vault=False, use_incident_vault=True, use_llm=False)
        session = _make_session()

        with patch.object(svc, "_create_incident", return_value=None):
            result = svc.run_full_pipeline(session)

        assert result.incident_id is None

    def test_pipeline_llm_returns_none_no_suggestions(self):
        """When LLM returns None, no suggestions are produced."""
        svc = FixitService(use_man_vault=False, use_incident_vault=False, use_llm=True)
        session = _make_session()

        with patch.object(svc, "_analyze_with_llm", return_value=None):
            result = svc.run_full_pipeline(session)

        assert result.analysis is None
        assert result.verified_suggestions == ()

    @patch(f"{_SVC}.FixitService._search_man_vault")
    def test_pipeline_includes_man_vault_results(self, mock_man_vault):
        """Man vault results appear in the context when enabled."""
        mock_man_vault.return_value = (
            ManSnippetContext(
                name="cat",
                section="1",
                snippet="concatenate files",
            ),
        )

        svc = FixitService(use_man_vault=True, use_incident_vault=False, use_llm=False)
        session = _make_session()

        result = svc.run_full_pipeline(session)

        assert len(result.context.man_snippets) == 1
        assert result.context.man_snippets[0].name == "cat"


# =============================================================================
# TestExecuteFix
# =============================================================================


class TestExecuteFix:
    """Tests for execute_fix (capability path and raw-command fallback)."""

    # The imports happen inside execute_fix, so patch at their home modules.
    _MAP = "elle.cli.planner.command_mapper.map_command_to_capability"
    _RUN = "elle.cli.subprocess_runner.run_safe"

    @patch(_MAP)
    @patch(_RUN)
    def test_raw_fallback_success(self, mock_run_safe, mock_map):
        """Falls back to raw command when no capability mapping exists."""
        svc = FixitService()
        session = _make_session()
        result = _make_result(incident_id="INC-010")

        mock_map.return_value = MagicMock(
            success=False,
            capability=None,
            unmapped_reason="no matching pattern",
        )
        mock_run_safe.return_value = MagicMock(
            exit_code=0,
            stdout="done",
            stderr="",
            success=True,
        )

        new_result, action = svc.execute_fix(result, "apt update", session)

        assert action.success is True
        assert action.command == "apt update"
        assert action.exit_code == 0
        assert len(new_result.actions) == 1

    @patch(_MAP)
    @patch(_RUN)
    def test_raw_fallback_records_to_incident(self, mock_run_safe, mock_map):
        """Action is recorded when incident_id is present."""
        svc = FixitService()
        session = _make_session()
        result = _make_result(incident_id="INC-020")

        mock_map.return_value = MagicMock(
            success=False,
            capability=None,
            unmapped_reason="unmapped",
        )
        mock_run_safe.return_value = MagicMock(
            exit_code=0,
            stdout="ok",
            stderr="",
            success=True,
        )

        with patch.object(svc, "record_action") as mock_record:
            new_result, action = svc.execute_fix(result, "echo hello", session)
            mock_record.assert_called_once_with("INC-020", action)

    @patch(_MAP)
    @patch(_RUN)
    def test_no_record_when_no_incident_id(self, mock_run_safe, mock_map):
        """record_action is not called when incident_id is None."""
        svc = FixitService()
        session = _make_session()
        result = _make_result(incident_id=None)

        mock_map.return_value = MagicMock(
            success=False,
            capability=None,
            unmapped_reason="unmapped",
        )
        mock_run_safe.return_value = MagicMock(
            exit_code=0,
            stdout="",
            stderr="",
            success=True,
        )

        with patch.object(svc, "record_action") as mock_record:
            svc.execute_fix(result, "ls", session)
            mock_record.assert_not_called()

    @patch(_MAP)
    @patch(_RUN)
    def test_raw_fallback_failed_command(self, mock_run_safe, mock_map):
        """Failed raw command reports exit_code and stderr correctly."""
        svc = FixitService()
        session = _make_session()
        result = _make_result()

        mock_map.return_value = MagicMock(
            success=False,
            capability=None,
            unmapped_reason="unmapped",
        )
        mock_run_safe.return_value = MagicMock(
            exit_code=127,
            stdout="",
            stderr="command not found",
            success=False,
        )

        new_result, action = svc.execute_fix(result, "nonexistent_cmd", session)

        assert action.success is False
        assert action.exit_code == 127
        assert "command not found" in action.stderr


# =============================================================================
# TestFinalizeIncident
# =============================================================================


class TestFinalizeIncident:
    """Tests for finalize_incident."""

    def test_finalize_import_error_silent(self):
        """ImportError is silently caught without raising."""
        svc = FixitService()
        result = _make_result()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.incidents.models": None,
                "elle.daemon.incidents.store": None,
            },
        ):
            svc.finalize_incident("INC-001", result)  # must not raise

    def test_finalize_runtime_error_silent(self):
        """Runtime errors in store functions are caught and logged."""
        svc = FixitService()
        analysis = _make_analysis()
        context = _make_context()
        result = FixitResult(
            context=context,
            analysis=analysis,
            outcome=FixitOutcome.IMPROVED,
        )

        mock_store = MagicMock()
        mock_store.finalize_outcome.side_effect = RuntimeError("db error")
        mock_store.update_incident = MagicMock()
        mock_store.store_decision_record = MagicMock()

        mock_models = MagicMock()
        mock_models.DecisionRecord = MagicMock()
        mock_models.Provenance = MagicMock()
        mock_models.ManPageCitation = MagicMock()
        mock_models.IncidentCitation = MagicMock()
        mock_models.ConfidenceBreakdown = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.incidents.models": mock_models,
                "elle.daemon.incidents.store": mock_store,
            },
        ):
            svc.finalize_incident("INC-001", result)  # must not raise

    def test_finalize_outcome_mapping(self):
        """All FixitOutcome values map to the expected incident outcomes."""
        svc = FixitService()
        context = _make_context()

        # Verify the outcome mapping is correct by checking each value
        expected_map = {
            FixitOutcome.IMPROVED: "improved",
            FixitOutcome.PARTIAL: "partial",
            FixitOutcome.NO_CHANGE: "no_change",
            FixitOutcome.WORSE: "worse",
            FixitOutcome.SKIPPED: "unknown",
            FixitOutcome.ERROR: "no_change",
        }

        for fixit_outcome, expected_str in expected_map.items():
            result = FixitResult(context=context, outcome=fixit_outcome)

            mock_store = MagicMock()
            mock_models = MagicMock()

            with patch.dict(
                "sys.modules",
                {
                    "elle.daemon.incidents.models": mock_models,
                    "elle.daemon.incidents.store": mock_store,
                },
            ):
                svc.finalize_incident("INC-001", result)

            kw = mock_store.finalize_outcome.call_args[1]
            assert kw["outcome"] == expected_str, (
                f"FixitOutcome.{fixit_outcome.name} should map to '{expected_str}', got '{kw['outcome']}'"
            )


# =============================================================================
# TestComputeConfidence
# =============================================================================


class TestComputeConfidence:
    """Tests for _compute_confidence."""

    def test_confidence_llm_only(self):
        """LLM-only: overall = llm_conf * 0.5, dominant_tier = 'llm'."""
        svc = FixitService()
        result = FixitResult(
            context=_make_context(),
            analysis=_make_analysis(diagnosis=_make_diagnosis(confidence=0.8)),
        )

        mock_provenance = MagicMock()
        mock_provenance.man_pages = []
        mock_provenance.prior_incidents = []

        mock_models = MagicMock()
        mock_models.ConfidenceBreakdown = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.models": mock_models}):
            svc._compute_confidence(result, mock_provenance)

        kw = mock_models.ConfidenceBreakdown.call_args[1]
        assert abs(kw["overall"] - 0.4) < 0.01
        assert kw["dominant_tier"] == "llm"

    def test_confidence_with_man_vault_lexical(self):
        """Man vault with high relevance makes dominant_tier 'lexical'."""
        svc = FixitService()
        result = FixitResult(
            context=_make_context(),
            analysis=_make_analysis(diagnosis=_make_diagnosis(confidence=0.6)),
        )

        mock_man = MagicMock()
        mock_man.relevance_score = 0.9

        mock_provenance = MagicMock()
        mock_provenance.man_pages = [mock_man]
        mock_provenance.prior_incidents = []

        mock_models = MagicMock()
        mock_models.ConfidenceBreakdown = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.models": mock_models}):
            svc._compute_confidence(result, mock_provenance)

        kw = mock_models.ConfidenceBreakdown.call_args[1]
        assert abs(kw["from_man_vault"] - 0.27) < 0.01
        assert kw["dominant_tier"] == "lexical"

    def test_confidence_with_good_prior_incident_semantic(self):
        """Good prior incidents push dominant_tier to 'semantic'."""
        svc = FixitService()
        result = FixitResult(
            context=_make_context(),
            analysis=_make_analysis(diagnosis=_make_diagnosis(confidence=0.5)),
        )

        mock_incident = MagicMock()
        mock_incident.outcome = "improved"
        mock_incident.similarity_score = 0.8

        mock_provenance = MagicMock()
        mock_provenance.man_pages = []
        mock_provenance.prior_incidents = [mock_incident]

        mock_models = MagicMock()
        mock_models.ConfidenceBreakdown = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.models": mock_models}):
            svc._compute_confidence(result, mock_provenance)

        kw = mock_models.ConfidenceBreakdown.call_args[1]
        assert abs(kw["from_incident_vault"] - 0.32) < 0.01
        assert kw["dominant_tier"] == "semantic"

    def test_confidence_capped_at_one(self):
        """Overall confidence is capped at 1.0 even with high sources."""
        svc = FixitService()
        result = FixitResult(
            context=_make_context(),
            analysis=_make_analysis(diagnosis=_make_diagnosis(confidence=1.0)),
        )

        # High man vault + high incident vault
        mock_man = MagicMock()
        mock_man.relevance_score = 1.0

        mock_incident = MagicMock()
        mock_incident.outcome = "improved"
        mock_incident.similarity_score = 1.0

        mock_provenance = MagicMock()
        mock_provenance.man_pages = [mock_man]
        mock_provenance.prior_incidents = [mock_incident]

        mock_models = MagicMock()
        mock_models.ConfidenceBreakdown = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.models": mock_models}):
            svc._compute_confidence(result, mock_provenance)

        kw = mock_models.ConfidenceBreakdown.call_args[1]
        assert kw["overall"] <= 1.0


# =============================================================================
# TestBuildCitations
# =============================================================================


class TestBuildCitations:
    """Tests for _build_man_citations and _build_incident_citations."""

    def test_build_man_citations_truncates_snippet(self):
        """Man snippets longer than 500 characters are truncated."""
        svc = FixitService()
        long_snippet = "x" * 800
        snippet = ManSnippetContext(
            name="ls",
            section="1",
            snippet=long_snippet,
            match_section="DESCRIPTION",
            relevance_score=0.8,
        )
        context = _make_context(man_snippets=(snippet,))

        mock_models = MagicMock()
        mock_models.ManPageCitation = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.models": mock_models}):
            citations = svc._build_man_citations(context)

        assert len(citations) == 1
        kw = mock_models.ManPageCitation.call_args[1]
        assert len(kw["snippet"]) == 500

    def test_build_incident_citations_extracts_successful_commands(self):
        """Successful actions are flattened to command strings."""
        svc = FixitService()
        art = PriorArtContext(
            incident_id="INC-100",
            title="Package failure",
            outcome="improved",
            successful_actions=(
                SuccessfulAction(command="apt update"),
                SuccessfulAction(command="apt install foo"),
            ),
            score=0.75,
            match_type="hybrid",
        )
        context = _make_context(prior_art=(art,))

        mock_models = MagicMock()
        mock_models.IncidentCitation = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.models": mock_models}):
            citations = svc._build_incident_citations(context)

        assert len(citations) == 1
        kw = mock_models.IncidentCitation.call_args[1]
        assert kw["incident_id"] == "INC-100"
        assert kw["successful_actions"] == ("apt update", "apt install foo")

    def test_build_incident_citations_empty_actions(self):
        """Prior art with no successful actions yields empty actions tuple."""
        svc = FixitService()
        art = PriorArtContext(
            incident_id="INC-200",
            title="Empty incident",
            outcome="no_change",
            successful_actions=(),
            score=0.3,
            match_type="lexical",
        )
        context = _make_context(prior_art=(art,))

        mock_models = MagicMock()
        mock_models.IncidentCitation = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.models": mock_models}):
            svc._build_incident_citations(context)

        kw = mock_models.IncidentCitation.call_args[1]
        assert kw["successful_actions"] == ()


# =============================================================================
# TestGetAndResetSingleton
# =============================================================================


class TestGetAndResetSingleton:
    """Tests for module-level get_fixit_service / reset_fixit_service."""

    def teardown_method(self):
        reset_fixit_service()

    def test_get_returns_same_instance(self):
        """get_fixit_service returns the same singleton each call."""
        a = get_fixit_service()
        b = get_fixit_service()
        assert a is b

    def test_reset_clears_singleton(self):
        """reset_fixit_service forces creation of a new instance."""
        a = get_fixit_service()
        reset_fixit_service()
        b = get_fixit_service()
        assert a is not b

    def test_default_service_has_all_features_enabled(self):
        """Default singleton has man_vault, incident_vault, and LLM enabled."""
        svc = get_fixit_service()
        assert svc._use_man_vault is True
        assert svc._use_incident_vault is True
        assert svc._use_llm is True
