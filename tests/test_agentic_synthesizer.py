from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.agentic.models import (
    AgenticIntent,
    AgenticResponse,
    CapabilityCall,
    ExecutionEvidence,
    ExecutionPlan,
    ExecutionResult,
    InformationNeed,
    ParallelGroup,
)
from elle.cli.agentic.synthesizer import (
    FOLLOW_UP_SUGGESTIONS,
    ResponseSynthesizer,
    get_synthesizer,
    reset_synthesizer,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_need(**kwargs: Any) -> InformationNeed:
    defaults: dict[str, Any] = {
        "category": "service",
        "target": "nginx",
        "aspects": ("status",),
    }
    defaults.update(kwargs)
    return InformationNeed(**defaults)


def _make_evidence(**kwargs: Any) -> ExecutionEvidence:
    defaults: dict[str, Any] = {
        "capability": "service.status",
        "args": {"service": "nginx"},
        "success": True,
        "output_text": "ActiveState=active\nSubState=running",
        "duration_ms": 100,
    }
    defaults.update(kwargs)
    return ExecutionEvidence(**defaults)


def _make_execution_result(
    evidence: list[ExecutionEvidence],
) -> ExecutionResult:
    intent = AgenticIntent(
        information_needs=(_make_need(),),
        goal_summary="test",
    )
    call = CapabilityCall(capability="service.status", args={"service": "nginx"}, purpose="check")
    plan = ExecutionPlan(
        parallel_groups=(ParallelGroup(calls=(call,)),),
        intent=intent,
        estimated_duration_ms=100,
    )
    return ExecutionResult(
        plan=plan,
        evidence=tuple(evidence),
        total_duration_ms=100,
    )


# =============================================================================
# Singleton
# =============================================================================


class TestSingleton:
    def setup_method(self) -> None:
        reset_synthesizer()

    def teardown_method(self) -> None:
        reset_synthesizer()

    def test_get_synthesizer(self) -> None:
        s = get_synthesizer()
        assert isinstance(s, ResponseSynthesizer)

    def test_same_instance(self) -> None:
        a = get_synthesizer()
        b = get_synthesizer()
        assert a is b

    def test_reset(self) -> None:
        a = get_synthesizer()
        reset_synthesizer()
        b = get_synthesizer()
        assert a is not b


# =============================================================================
# ResponseSynthesizer init
# =============================================================================


class TestInit:
    def test_defaults(self) -> None:
        s = ResponseSynthesizer()
        assert s.use_llm is True

    def test_no_llm(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        assert s.use_llm is False


# =============================================================================
# synthesize - no evidence
# =============================================================================


class TestSynthesizeNoEvidence:
    @pytest.mark.asyncio
    async def test_no_evidence(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        result = _make_execution_result([])
        response = await s.synthesize("what is nginx status?", result)
        assert isinstance(response, AgenticResponse)
        assert "couldn't gather" in response.answer
        assert response.confidence == 0.0

    @pytest.mark.asyncio
    async def test_all_failed(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [_make_evidence(success=False, output_text=None, error="connection refused")]
        result = _make_execution_result(evidence)
        response = await s.synthesize("check nginx", result)
        assert "couldn't retrieve" in response.answer
        assert "connection refused" in response.answer


# =============================================================================
# synthesize - with LLM
# =============================================================================


class TestSynthesizeWithLlm:
    @pytest.mark.asyncio
    async def test_llm_success(self) -> None:
        s = ResponseSynthesizer(use_llm=True)
        evidence = [_make_evidence()]
        result = _make_execution_result(evidence)

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_response = MagicMock()
        mock_response.content = "Nginx is active and running."
        mock_llm.generate.return_value = mock_response

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            response = await s.synthesize("is nginx running?", result)
            assert "Nginx is active" in response.answer
            assert response.confidence > 0

    @pytest.mark.asyncio
    async def test_llm_not_available(self) -> None:
        s = ResponseSynthesizer(use_llm=True)
        evidence = [_make_evidence()]
        result = _make_execution_result(evidence)

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            response = await s.synthesize("check", result)
            # Falls back to rules
            assert response.answer != ""

    @pytest.mark.asyncio
    async def test_llm_exception(self) -> None:
        s = ResponseSynthesizer(use_llm=True)
        evidence = [_make_evidence()]
        result = _make_execution_result(evidence)

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.side_effect = RuntimeError("llm crash")

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            response = await s.synthesize("check", result)
            # Falls back to rules
            assert response.answer != ""

    @pytest.mark.asyncio
    async def test_llm_empty_content(self) -> None:
        s = ResponseSynthesizer(use_llm=True)
        evidence = [_make_evidence()]
        result = _make_execution_result(evidence)

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_response = MagicMock()
        mock_response.content = ""
        mock_llm.generate.return_value = mock_response

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            response = await s.synthesize("check", result)
            # Falls back to rules
            assert response.answer != ""


# =============================================================================
# synthesize - rules-based fallback
# =============================================================================


class TestSynthesizeRules:
    @pytest.mark.asyncio
    async def test_service_status_format(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="service.status",
                args={"service": "nginx"},
                output_text="ActiveState=active\nSubState=running\nMainPID=1234\nDescription=Nginx web server",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("is nginx running?", result)
        assert "nginx" in response.answer
        assert "active" in response.answer

    @pytest.mark.asyncio
    async def test_package_installed(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="package.info",
                args={"package": "curl"},
                output_text="install ok installed 7.81.0",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("is curl installed?", result)
        assert "curl" in response.answer
        assert "installed" in response.answer

    @pytest.mark.asyncio
    async def test_package_not_installed(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="package.info",
                args={"package": "foo"},
                output_text="deinstall ok not-installed",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("is foo installed?", result)
        assert "not installed" in response.answer

    @pytest.mark.asyncio
    async def test_package_apt_cache(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="package.info",
                args={"package": "vim"},
                output_text="Package: vim\nVersion: 9.0\nDescription: Vi IMproved",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("what version of vim?", result)
        assert "vim" in response.answer
        assert "9.0" in response.answer

    @pytest.mark.asyncio
    async def test_docker_list(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="docker.list",
                args={},
                output_text="NAME  STATUS  IMAGE\napp   Up      myimage",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("list containers", result)
        assert "Docker containers" in response.answer

    @pytest.mark.asyncio
    async def test_network_listeners(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="network.listeners",
                args={},
                output_text="tcp  LISTEN  0.0.0.0:80",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("listening ports", result)
        assert "Listening ports" in response.answer

    @pytest.mark.asyncio
    async def test_system_resources(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="system.resources",
                args={},
                output_text="Mem: 16G total",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("memory usage", result)
        assert "System resources" in response.answer

    @pytest.mark.asyncio
    async def test_generic_output(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [
            _make_evidence(
                capability="file.read",
                args={"path": "/etc/hosts"},
                output_text="127.0.0.1 localhost",
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("show /etc/hosts", result)
        assert "127.0.0.1" in response.answer

    @pytest.mark.asyncio
    async def test_truncation_long_output(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        long_output = "x" * 2000
        evidence = [
            _make_evidence(
                capability="file.read",
                args={},
                output_text=long_output,
            )
        ]
        result = _make_execution_result(evidence)
        response = await s.synthesize("read big file", result)
        assert response.answer.endswith("...")

    @pytest.mark.asyncio
    async def test_empty_output_filtered(self) -> None:
        s = ResponseSynthesizer(use_llm=False)
        evidence = [_make_evidence(output_text="")]
        result = _make_execution_result(evidence)
        response = await s.synthesize("test", result)
        assert "No information" in response.answer


# =============================================================================
# _format_evidence
# =============================================================================


class TestFormatEvidence:
    def test_with_args(self) -> None:
        s = ResponseSynthesizer()
        ev = _make_evidence(args={"service": "nginx"}, output_text="active")
        result = s._format_evidence((ev,))
        assert "[service.status]" in result
        assert "service=nginx" in result
        assert "active" in result

    def test_no_output(self) -> None:
        s = ResponseSynthesizer()
        ev = _make_evidence(output_text=None)
        result = s._format_evidence((ev,))
        assert "(no output)" in result


# =============================================================================
# _calculate_confidence
# =============================================================================


class TestCalculateConfidence:
    def test_no_evidence(self) -> None:
        s = ResponseSynthesizer()
        result = _make_execution_result([])
        assert s._calculate_confidence(result) == 0.0

    def test_all_success_sufficient(self) -> None:
        s = ResponseSynthesizer()
        evidence = [_make_evidence(success=True)]
        result = _make_execution_result(evidence)
        conf = s._calculate_confidence(result)
        assert conf > 0.5

    def test_mixed_results(self) -> None:
        s = ResponseSynthesizer()
        evidence = [
            _make_evidence(success=True),
            _make_evidence(success=False, error="fail"),
        ]
        result = _make_execution_result(evidence)
        conf = s._calculate_confidence(result)
        # success_rate = 0.5, not all_succeeded: 0.5 - 0.2 = 0.3
        assert conf == pytest.approx(0.3, abs=0.01)


# =============================================================================
# _generate_suggestions
# =============================================================================


class TestGenerateSuggestions:
    def test_service_status_suggestions(self) -> None:
        s = ResponseSynthesizer()
        evidence = [_make_evidence(capability="service.status", args={"service": "nginx"})]
        result = _make_execution_result(evidence)
        suggestions = s._generate_suggestions("check nginx", result)
        assert len(suggestions) >= 1
        assert "nginx" in suggestions[0]

    def test_no_suggestions_for_failed(self) -> None:
        s = ResponseSynthesizer()
        evidence = [_make_evidence(success=False, error="fail")]
        result = _make_execution_result(evidence)
        suggestions = s._generate_suggestions("check", result)
        assert len(suggestions) == 0

    def test_max_three_suggestions(self) -> None:
        s = ResponseSynthesizer()
        evidence = [
            _make_evidence(capability="service.status", args={"service": "a"}),
            _make_evidence(capability="service.logs", args={"service": "b"}),
            _make_evidence(capability="file.read", args={"path": "/etc/hosts"}),
            _make_evidence(capability="docker.list", args={}),
        ]
        result = _make_execution_result(evidence)
        suggestions = s._generate_suggestions("check all", result)
        assert len(suggestions) <= 3

    def test_suggestion_without_target(self) -> None:
        s = ResponseSynthesizer()
        evidence = [_make_evidence(capability="docker.list", args={})]
        result = _make_execution_result(evidence)
        suggestions = s._generate_suggestions("list containers", result)
        assert len(suggestions) >= 1


# =============================================================================
# _format_service_status
# =============================================================================


class TestFormatServiceStatus:
    def test_full_status(self) -> None:
        s = ResponseSynthesizer()
        result = s._format_service_status(
            {"service": "nginx"},
            "ActiveState=active\nSubState=running\nMainPID=1234\nDescription=Nginx",
        )
        assert "nginx" in result
        assert "active" in result
        assert "running" in result
        assert "1234" in result

    def test_unknown_state(self) -> None:
        s = ResponseSynthesizer()
        result = s._format_service_status({"service": "test"}, "no valid output")
        assert "test" in result


# =============================================================================
# _format_package_info
# =============================================================================


class TestFormatPackageInfo:
    def test_installed(self) -> None:
        s = ResponseSynthesizer()
        result = s._format_package_info({"package": "curl"}, "install ok installed 7.81.0")
        assert "curl" in result
        assert "installed" in result

    def test_not_installed(self) -> None:
        s = ResponseSynthesizer()
        result = s._format_package_info({"package": "foo"}, "deinstall ok not-installed")
        assert "not installed" in result

    def test_apt_cache_output(self) -> None:
        s = ResponseSynthesizer()
        result = s._format_package_info(
            {"package": "vim"},
            "Package: vim\nVersion: 9.0\nDescription: Vi IMproved",
        )
        assert "9.0" in result
        assert "vim" in result

    def test_apt_cache_no_version(self) -> None:
        s = ResponseSynthesizer()
        result = s._format_package_info({"package": "x"}, "some random output")
        assert len(result) > 0


# =============================================================================
# FOLLOW_UP_SUGGESTIONS constant
# =============================================================================


class TestFollowUpSuggestions:
    def test_has_common_capabilities(self) -> None:
        assert "service.status" in FOLLOW_UP_SUGGESTIONS
        assert "service.logs" in FOLLOW_UP_SUGGESTIONS
        assert "file.read" in FOLLOW_UP_SUGGESTIONS
        assert "docker.list" in FOLLOW_UP_SUGGESTIONS
