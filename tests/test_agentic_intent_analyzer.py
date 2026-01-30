from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    InformationNeed,
)
from elle.cli.agentic.intent_analyzer import (
    ACTION_PATTERNS,
    CONDITION_PATTERNS,
    INFO_PATTERNS,
    IntentAnalyzer,
    get_intent_analyzer,
    reset_intent_analyzer,
)


# =============================================================================
# Singleton
# =============================================================================


class TestSingleton:
    def setup_method(self) -> None:
        reset_intent_analyzer()

    def teardown_method(self) -> None:
        reset_intent_analyzer()

    def test_get_analyzer(self) -> None:
        a = get_intent_analyzer()
        assert isinstance(a, IntentAnalyzer)

    def test_same_instance(self) -> None:
        a = get_intent_analyzer()
        b = get_intent_analyzer()
        assert a is b

    def test_reset(self) -> None:
        a = get_intent_analyzer()
        reset_intent_analyzer()
        b = get_intent_analyzer()
        assert a is not b


# =============================================================================
# IntentAnalyzer init
# =============================================================================


class TestInit:
    def test_defaults(self) -> None:
        a = IntentAnalyzer()
        assert a.use_llm_fallback is True
        assert a.pattern_confidence == 0.9
        assert a.llm_confidence == 0.8

    def test_custom(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False, pattern_confidence=0.7, llm_confidence=0.6)
        assert a.use_llm_fallback is False
        assert a.pattern_confidence == 0.7
        assert a.llm_confidence == 0.6


# =============================================================================
# analyze - empty input
# =============================================================================


class TestAnalyzeEmpty:
    @pytest.mark.asyncio
    async def test_empty_string(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("")
        assert intent.confidence == 0.0
        assert intent.goal_summary == "Empty input"

    @pytest.mark.asyncio
    async def test_whitespace_only(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("   ")
        assert intent.confidence == 0.0


# =============================================================================
# analyze - service patterns
# =============================================================================


class TestAnalyzeServicePatterns:
    @pytest.mark.asyncio
    async def test_is_running(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("is nginx running")
        assert len(intent.information_needs) >= 1
        service_needs = [n for n in intent.information_needs if n.category == "service"]
        assert len(service_needs) >= 1
        assert service_needs[0].target == "nginx"

    @pytest.mark.asyncio
    async def test_status_of(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("status of sshd")
        assert len(intent.information_needs) >= 1
        assert any(n.target == "sshd" for n in intent.information_needs)

    @pytest.mark.asyncio
    async def test_show_logs(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("show me nginx logs")
        assert len(intent.information_needs) >= 1
        needs = [n for n in intent.information_needs if "logs" in n.aspects]
        assert len(needs) >= 1

    @pytest.mark.asyncio
    async def test_why_failing(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("why is nginx failing")
        assert len(intent.information_needs) >= 1
        service_needs = [n for n in intent.information_needs if n.category == "service"]
        assert len(service_needs) >= 1


# =============================================================================
# analyze - file patterns
# =============================================================================


class TestAnalyzeFilePatterns:
    @pytest.mark.asyncio
    async def test_show_file(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("show /etc/hosts")
        assert len(intent.information_needs) >= 1
        file_needs = [n for n in intent.information_needs if n.category == "file"]
        assert len(file_needs) >= 1

    @pytest.mark.asyncio
    async def test_whats_in(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("what's in /etc/resolv.conf")
        assert len(intent.information_needs) >= 1
        file_needs = [n for n in intent.information_needs if n.category == "file"]
        assert len(file_needs) >= 1


# =============================================================================
# analyze - package patterns
# =============================================================================


class TestAnalyzePackagePatterns:
    @pytest.mark.asyncio
    async def test_is_installed(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("is curl installed")
        assert len(intent.information_needs) >= 1
        pkg_needs = [n for n in intent.information_needs if n.category == "package"]
        assert len(pkg_needs) >= 1
        assert pkg_needs[0].target == "curl"

    @pytest.mark.asyncio
    async def test_package_version(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("nginx version")
        assert len(intent.information_needs) >= 1


# =============================================================================
# analyze - docker patterns
# =============================================================================


class TestAnalyzeDockerPatterns:
    @pytest.mark.asyncio
    async def test_containers_running(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("what containers are running")
        assert len(intent.information_needs) >= 1
        docker_needs = [n for n in intent.information_needs if n.category == "docker"]
        assert len(docker_needs) >= 1

    @pytest.mark.asyncio
    async def test_list_containers(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("list docker containers")
        docker_needs = [n for n in intent.information_needs if n.category == "docker"]
        assert len(docker_needs) >= 1


# =============================================================================
# analyze - network patterns
# =============================================================================


class TestAnalyzeNetworkPatterns:
    @pytest.mark.asyncio
    async def test_listening_ports(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("listening ports")
        assert len(intent.information_needs) >= 1
        net_needs = [n for n in intent.information_needs if n.category == "network"]
        assert len(net_needs) >= 1

    @pytest.mark.asyncio
    async def test_whats_on_port(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("what's listening on port 80")
        net_needs = [n for n in intent.information_needs if n.category == "network"]
        assert len(net_needs) >= 1


# =============================================================================
# analyze - system patterns
# =============================================================================


class TestAnalyzeSystemPatterns:
    @pytest.mark.asyncio
    async def test_system_info(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("system info")
        assert len(intent.information_needs) >= 1
        sys_needs = [n for n in intent.information_needs if n.category == "system"]
        assert len(sys_needs) >= 1

    @pytest.mark.asyncio
    async def test_memory_usage(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("memory usage")
        assert len(intent.information_needs) >= 1


# =============================================================================
# analyze - action patterns
# =============================================================================


class TestAnalyzeActionPatterns:
    @pytest.mark.asyncio
    async def test_restart_service(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("restart nginx")
        assert len(intent.action_requests) >= 1
        restart = [r for r in intent.action_requests if r.action_type == ActionType.RESTART]
        assert len(restart) >= 1
        assert restart[0].target == "nginx"

    @pytest.mark.asyncio
    async def test_start_service(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("start apache")
        assert len(intent.action_requests) >= 1

    @pytest.mark.asyncio
    async def test_stop_service(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("stop mysql")
        assert len(intent.action_requests) >= 1

    @pytest.mark.asyncio
    async def test_install_package(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("install htop")
        assert len(intent.action_requests) >= 1
        install = [r for r in intent.action_requests if r.action_type == ActionType.INSTALL]
        assert len(install) >= 1

    @pytest.mark.asyncio
    async def test_remove_package(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("remove vim")
        assert len(intent.action_requests) >= 1

    @pytest.mark.asyncio
    async def test_fix_service(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("fix nginx")
        assert len(intent.action_requests) >= 1
        fix = [r for r in intent.action_requests if r.action_type == ActionType.FIX]
        assert len(fix) >= 1


# =============================================================================
# analyze - mixed patterns
# =============================================================================


class TestAnalyzeMixedPatterns:
    @pytest.mark.asyncio
    async def test_check_and_restart(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("why is nginx failing restart nginx")
        assert len(intent.information_needs) >= 1
        assert len(intent.action_requests) >= 1

    @pytest.mark.asyncio
    async def test_conditional_action(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("restart nginx if it's not running")
        if intent.action_requests:
            assert intent.action_requests[0].condition is not None


# =============================================================================
# analyze - LLM fallback
# =============================================================================


class TestAnalyzeLlmFallback:
    @pytest.mark.asyncio
    async def test_llm_success(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=True)

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "information_needs": [
                {"category": "service", "target": "complex-svc", "aspects": ["status"]}
            ],
            "action_requests": [],
            "goal_summary": "Check complex service",
        })
        mock_llm.generate.return_value = mock_response

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            intent = await a.analyze("how is complex-svc doing overall with throughput")
            assert len(intent.information_needs) >= 1

    @pytest.mark.asyncio
    async def test_llm_not_available(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=True)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            intent = await a.analyze("some complex query that no pattern matches")
            assert intent.confidence == 0.0

    @pytest.mark.asyncio
    async def test_llm_exception(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=True)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.side_effect = RuntimeError("boom")

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            intent = await a.analyze("some complex query that no pattern matches")
            assert intent.confidence == 0.0

    @pytest.mark.asyncio
    async def test_llm_empty_response(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=True)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_response = MagicMock()
        mock_response.content = ""
        mock_llm.generate.return_value = mock_response

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            intent = await a.analyze("some complex query that no pattern matches")
            assert intent.confidence == 0.0


# =============================================================================
# _parse_llm_response
# =============================================================================


class TestParseLlmResponse:
    def test_valid_json(self) -> None:
        a = IntentAnalyzer()
        data = a._parse_llm_response('{"information_needs": [], "action_requests": []}')
        assert data is not None
        assert "information_needs" in data

    def test_json_in_code_block(self) -> None:
        a = IntentAnalyzer()
        data = a._parse_llm_response('```json\n{"info": "test"}\n```')
        assert data is not None

    def test_json_in_generic_code_block(self) -> None:
        a = IntentAnalyzer()
        data = a._parse_llm_response('```\n{"info": "test"}\n```')
        assert data is not None

    def test_json_with_surrounding_text(self) -> None:
        a = IntentAnalyzer()
        data = a._parse_llm_response('Here is the result: {"key": "value"} done')
        assert data is not None

    def test_invalid_json(self) -> None:
        a = IntentAnalyzer()
        data = a._parse_llm_response("not json at all without braces")
        assert data is None

    def test_broken_json_object(self) -> None:
        a = IntentAnalyzer()
        data = a._parse_llm_response("{invalid json content}")
        assert data is None


# =============================================================================
# _build_intent_from_llm
# =============================================================================


class TestBuildIntentFromLlm:
    def test_full_data(self) -> None:
        a = IntentAnalyzer()
        data = {
            "information_needs": [
                {"category": "service", "target": "nginx", "aspects": ["status", "logs"]},
            ],
            "action_requests": [
                {"action_type": "restart", "target": "nginx", "domain": "service"},
            ],
            "goal_summary": "Check and restart nginx",
        }
        intent = a._build_intent_from_llm(data, "check and restart nginx")
        assert len(intent.information_needs) == 1
        assert len(intent.action_requests) == 1
        assert intent.goal_summary == "Check and restart nginx"

    def test_unknown_category_defaults(self) -> None:
        a = IntentAnalyzer()
        data = {
            "information_needs": [
                {"category": "unknown_cat", "target": "x", "aspects": ["info"]},
            ],
            "action_requests": [],
            "goal_summary": "test",
        }
        intent = a._build_intent_from_llm(data, "test")
        assert intent.information_needs[0].category == "system"

    def test_unknown_action_type(self) -> None:
        a = IntentAnalyzer()
        data = {
            "information_needs": [],
            "action_requests": [
                {"action_type": "fly_to_moon", "target": "rocket", "domain": "space"},
            ],
            "goal_summary": "test",
        }
        intent = a._build_intent_from_llm(data, "test")
        assert intent.action_requests[0].action_type == ActionType.CUSTOM

    def test_missing_goal_summary(self) -> None:
        a = IntentAnalyzer()
        data = {
            "information_needs": [],
            "action_requests": [],
        }
        intent = a._build_intent_from_llm(data, "original input text")
        assert intent.goal_summary == "original input text"

    def test_action_with_condition(self) -> None:
        a = IntentAnalyzer()
        data = {
            "information_needs": [],
            "action_requests": [
                {"action_type": "restart", "target": "nginx", "domain": "service", "condition": "if not running"},
            ],
            "goal_summary": "conditional restart",
        }
        intent = a._build_intent_from_llm(data, "restart if needed")
        assert intent.action_requests[0].condition == "if not running"

    def test_bad_info_need_skipped(self) -> None:
        a = IntentAnalyzer()
        data = {
            "information_needs": [
                None,  # bad entry
                {"category": "service", "target": "nginx", "aspects": ["status"]},
            ],
            "action_requests": [],
            "goal_summary": "test",
        }
        intent = a._build_intent_from_llm(data, "test")
        # The None entry should be skipped
        assert len(intent.information_needs) == 1


# =============================================================================
# _summarize_goal
# =============================================================================


class TestSummarizeGoal:
    def test_info_with_targets(self) -> None:
        a = IntentAnalyzer()
        needs = [InformationNeed(category="service", target="nginx", aspects=("status",))]
        result = a._summarize_goal(needs, [])
        assert "nginx" in result

    def test_info_without_targets(self) -> None:
        a = IntentAnalyzer()
        needs = [InformationNeed(category="system", target="", aspects=("info",))]
        result = a._summarize_goal(needs, [])
        assert "system" in result

    def test_actions_only(self) -> None:
        a = IntentAnalyzer()
        actions = [ActionRequest(action_type=ActionType.RESTART, target="nginx", domain="service")]
        result = a._summarize_goal([], actions)
        assert "restart" in result
        assert "nginx" in result

    def test_both(self) -> None:
        a = IntentAnalyzer()
        needs = [InformationNeed(category="service", target="nginx", aspects=("status",))]
        actions = [ActionRequest(action_type=ActionType.RESTART, target="nginx", domain="service")]
        result = a._summarize_goal(needs, actions)
        assert "nginx" in result
        assert ";" in result

    def test_empty(self) -> None:
        a = IntentAnalyzer()
        result = a._summarize_goal([], [])
        assert result == "Process request"


# =============================================================================
# can_handle
# =============================================================================


class TestCanHandle:
    def test_pattern_match(self) -> None:
        a = IntentAnalyzer()
        assert a.can_handle("is nginx running") is True

    def test_action_pattern_match(self) -> None:
        a = IntentAnalyzer()
        assert a.can_handle("restart nginx") is True

    def test_keyword_match(self) -> None:
        a = IntentAnalyzer()
        assert a.can_handle("what is the service doing") is True

    def test_no_match(self) -> None:
        a = IntentAnalyzer()
        assert a.can_handle("hello world greetings") is False

    def test_keyword_status(self) -> None:
        a = IntentAnalyzer()
        assert a.can_handle("check the status") is True


# =============================================================================
# Condition patterns
# =============================================================================


class TestConditionPatterns:
    @pytest.mark.asyncio
    async def test_if_not_running(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("restart nginx if not running")
        if intent.action_requests:
            assert intent.action_requests[0].condition == "if not running"

    @pytest.mark.asyncio
    async def test_if_down(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("restart apache if it's down")
        if intent.action_requests:
            assert intent.action_requests[0].condition is not None

    @pytest.mark.asyncio
    async def test_if_not_installed(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        intent = await a.analyze("install curl if not installed")
        if intent.action_requests:
            assert intent.action_requests[0].condition is not None


# =============================================================================
# Deduplication
# =============================================================================


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_duplicate_needs_deduplicated(self) -> None:
        a = IntentAnalyzer(use_llm_fallback=False)
        # "is nginx running" might match multiple patterns both yielding service/nginx
        intent = await a.analyze("is nginx running status of nginx")
        # Should not have duplicates for the same category+target
        seen = set()
        for n in intent.information_needs:
            key = (n.category, n.target)
            assert key not in seen
            seen.add(key)


# =============================================================================
# Pattern constants
# =============================================================================


class TestPatternConstants:
    def test_info_patterns_exist(self) -> None:
        assert len(INFO_PATTERNS) > 0

    def test_action_patterns_exist(self) -> None:
        assert len(ACTION_PATTERNS) > 0

    def test_condition_patterns_exist(self) -> None:
        assert len(CONDITION_PATTERNS) > 0
