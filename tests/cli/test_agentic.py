"""Tests for the agentic question answering system."""

from __future__ import annotations

import pytest

from elle.cli.agentic.analyzer import InformationNeedAnalyzer
from elle.cli.agentic.models import (
    CapabilityCall,
    InformationNeed,
)

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
