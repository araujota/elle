"""Tests for the agentic question answering system."""

from __future__ import annotations

import pytest

from elle.cli.agentic.analyzer import InformationNeedAnalyzer
from elle.cli.agentic.models import (
    CapabilityCall,
    GatheredEvidence,
    GatherPlan,
    GatherResult,
    InformationNeed,
)
from elle.cli.agentic.selector import CapabilitySelector


# =============================================================================
# InformationNeedAnalyzer Tests
# =============================================================================


class TestInformationNeedAnalyzer:
    """Tests for the InformationNeedAnalyzer."""

    @pytest.fixture
    def analyzer(self) -> InformationNeedAnalyzer:
        """Create an analyzer without LLM fallback for testing."""
        return InformationNeedAnalyzer(use_llm_fallback=False)

    def test_service_status_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of service status questions."""
        needs = analyzer.analyze("what is the status of nginx?")

        assert len(needs) == 1
        assert needs[0].category == "service"
        assert needs[0].target == "nginx"
        assert "status" in needs[0].aspects

    def test_is_running_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of 'is X running' questions."""
        needs = analyzer.analyze("is postgresql running?")

        assert len(needs) == 1
        assert needs[0].category == "service"
        assert needs[0].target == "postgresql"
        assert "status" in needs[0].aspects

    def test_service_logs_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of service log questions."""
        needs = analyzer.analyze("show me nginx logs")

        assert len(needs) == 1
        assert needs[0].category == "service"
        assert needs[0].target == "nginx"
        assert "logs" in needs[0].aspects

    def test_file_content_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of file content questions."""
        needs = analyzer.analyze("what's in /etc/hosts?")

        assert len(needs) == 1
        assert needs[0].category == "file"
        assert needs[0].target == "/etc/hosts"
        assert "content" in needs[0].aspects

    def test_package_info_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of package questions."""
        needs = analyzer.analyze("is docker installed?")

        assert len(needs) == 1
        assert needs[0].category == "package"
        assert needs[0].target == "docker"
        assert "info" in needs[0].aspects

    def test_package_version_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of package version questions."""
        needs = analyzer.analyze("what version of python3 do I have?")

        assert len(needs) == 1
        assert needs[0].category == "package"
        assert needs[0].target == "python3"

    def test_docker_containers_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of docker container questions."""
        needs = analyzer.analyze("what containers are running?")

        assert len(needs) == 1
        assert needs[0].category == "docker"
        assert "list" in needs[0].aspects

    def test_network_listeners_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of network listener questions."""
        needs = analyzer.analyze("what's listening on the ports?")

        assert len(needs) == 1
        assert needs[0].category == "network"
        assert "listeners" in needs[0].aspects

    def test_port_specific_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of specific port questions."""
        needs = analyzer.analyze("what's on port 8080?")

        assert len(needs) == 1
        assert needs[0].category == "network"
        assert needs[0].target == "8080"

    def test_system_resources_detection(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test detection of system resource questions."""
        needs = analyzer.analyze("how much memory is being used?")

        assert len(needs) == 1
        assert needs[0].category == "system"
        assert "resources" in needs[0].aspects

    def test_no_match_returns_empty(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test that unrecognized questions return empty."""
        needs = analyzer.analyze("what is the meaning of life?")
        assert len(needs) == 0

    def test_empty_question_returns_empty(self, analyzer: InformationNeedAnalyzer) -> None:
        """Test that empty questions return empty."""
        needs = analyzer.analyze("")
        assert len(needs) == 0


# =============================================================================
# CapabilitySelector Tests
# =============================================================================


class TestCapabilitySelector:
    """Tests for the CapabilitySelector."""

    @pytest.fixture
    def selector(self) -> CapabilitySelector:
        """Create a selector for testing."""
        return CapabilitySelector()

    def test_service_status_selection(self, selector: CapabilitySelector) -> None:
        """Test capability selection for service status."""
        needs = (
            InformationNeed(
                category="service",
                target="nginx",
                aspects=("status",),
            ),
        )

        plan = selector.select(needs)

        assert len(plan.calls) >= 1
        assert any(c.capability == "service.status" for c in plan.calls)

        # Check args are substituted
        status_call = next(c for c in plan.calls if c.capability == "service.status")
        assert status_call.args.get("service") == "nginx"

    def test_file_read_selection(self, selector: CapabilitySelector) -> None:
        """Test capability selection for file read."""
        needs = (
            InformationNeed(
                category="file",
                target="/etc/hosts",
                aspects=("content",),
            ),
        )

        plan = selector.select(needs)

        assert len(plan.calls) >= 1
        assert any(c.capability == "file.read" for c in plan.calls)

        read_call = next(c for c in plan.calls if c.capability == "file.read")
        assert read_call.args.get("path") == "/etc/hosts"

    def test_docker_list_selection(self, selector: CapabilitySelector) -> None:
        """Test capability selection for docker list."""
        needs = (
            InformationNeed(
                category="docker",
                target="",
                aspects=("list",),
            ),
        )

        plan = selector.select(needs)

        assert len(plan.calls) >= 1
        assert any(c.capability == "docker.list" for c in plan.calls)

    def test_empty_needs_returns_empty_plan(self, selector: CapabilitySelector) -> None:
        """Test that empty needs return empty plan."""
        plan = selector.select(())

        assert len(plan.calls) == 0
        assert plan.estimated_duration_ms == 0

    def test_multiple_aspects_creates_multiple_calls(self, selector: CapabilitySelector) -> None:
        """Test that multiple aspects create multiple capability calls."""
        needs = (
            InformationNeed(
                category="service",
                target="nginx",
                aspects=("status", "logs"),
            ),
        )

        plan = selector.select(needs)

        # Should have both service.status and service.logs
        capabilities = {c.capability for c in plan.calls}
        assert "service.status" in capabilities
        assert "service.logs" in capabilities

    def test_deduplication(self, selector: CapabilitySelector) -> None:
        """Test that duplicate calls are deduplicated."""
        needs = (
            InformationNeed(
                category="service",
                target="nginx",
                aspects=("status",),
            ),
            InformationNeed(
                category="service",
                target="nginx",
                aspects=("status",),
            ),
        )

        plan = selector.select(needs)

        # Should only have one service.status call for nginx
        status_calls = [c for c in plan.calls if c.capability == "service.status"]
        assert len(status_calls) == 1


# =============================================================================
# Model Tests
# =============================================================================


class TestModels:
    """Tests for the Pydantic models."""

    def test_information_need_frozen(self) -> None:
        """Test that InformationNeed is frozen."""
        need = InformationNeed(
            category="service",
            target="nginx",
            aspects=("status",),
        )

        with pytest.raises(Exception):  # ValidationError or AttributeError
            need.target = "apache"  # type: ignore

    def test_capability_call_frozen(self) -> None:
        """Test that CapabilityCall is frozen."""
        call = CapabilityCall(
            capability="service.status",
            args={"service": "nginx"},
            purpose="Get service status",
        )

        with pytest.raises(Exception):
            call.capability = "other"  # type: ignore

    def test_gathered_evidence_defaults(self) -> None:
        """Test GatheredEvidence default values."""
        evidence = GatheredEvidence(
            capability="service.status",
            args={"service": "nginx"},
            success=True,
            duration_ms=100,
        )

        assert evidence.output is None
        assert evidence.error is None
        assert evidence.timestamp is not None

    def test_gather_plan_creation(self) -> None:
        """Test GatherPlan creation."""
        need = InformationNeed(
            category="service",
            target="nginx",
            aspects=("status",),
        )
        call = CapabilityCall(
            capability="service.status",
            args={"service": "nginx"},
            purpose="Get service status",
        )

        plan = GatherPlan(
            needs=(need,),
            calls=(call,),
            estimated_duration_ms=100,
        )

        assert len(plan.needs) == 1
        assert len(plan.calls) == 1
        assert plan.estimated_duration_ms == 100

    def test_gather_result_creation(self) -> None:
        """Test GatherResult creation."""
        need = InformationNeed(
            category="service",
            target="nginx",
            aspects=("status",),
        )
        call = CapabilityCall(
            capability="service.status",
            args={"service": "nginx"},
            purpose="Get service status",
        )
        plan = GatherPlan(
            needs=(need,),
            calls=(call,),
            estimated_duration_ms=100,
        )
        evidence = GatheredEvidence(
            capability="service.status",
            args={"service": "nginx"},
            success=True,
            output="active",
            duration_ms=50,
        )

        result = GatherResult(
            plan=plan,
            evidence=(evidence,),
            sufficient=True,
        )

        assert result.sufficient is True
        assert len(result.evidence) == 1
        assert result.missing == ()
