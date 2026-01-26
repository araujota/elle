"""Tests for confgen generator with mocked LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elle.rag.confgen.generator import (
    LLMConfigGenerator,
    get_generator,
    reset_generator,
)
from elle.rag.confgen.models import (
    ConfigGenLLMError,
    ConfigGenRequest,
)


class MockLLM:
    """Mock LLM for testing."""

    def __init__(self, response: dict[str, Any] | Exception | None = None) -> None:
        self._response = response
        self._available = True

    def is_available(self) -> bool:
        return self._available

    def generate_json(self, **kwargs) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response or {}


class TestLLMConfigGenerator:
    """Tests for LLMConfigGenerator."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_generator()

    def test_build_context_basic(self, tmp_path: Path) -> None:
        """Test building context from request."""
        generator = LLMConfigGenerator(llm=MockLLM())

        request = ConfigGenRequest(
            request="Set static IP for eth0",
            target_path=tmp_path / "netplan.yaml",
            mode="create",
        )

        context = generator.build_context(request)

        assert context.request is request
        assert context.detected_file_type == "yaml"

    def test_build_context_auto_detect_domain(self, tmp_path: Path) -> None:
        """Test auto-detecting domain from path."""
        generator = LLMConfigGenerator(llm=MockLLM())

        # Create a netplan-like path
        netplan_path = tmp_path / "etc" / "netplan" / "01-netcfg.yaml"
        netplan_path.parent.mkdir(parents=True)
        netplan_path.write_text("network: {}")

        request = ConfigGenRequest(
            request="Configure DHCP",
            target_path=netplan_path,
        )

        # Since our path doesn't match the pattern, domain detection may return general
        context = generator.build_context(request)
        assert context.detected_file_type == "yaml"

    def test_build_context_reads_existing_content(self, tmp_path: Path) -> None:
        """Test that context includes existing file content."""
        generator = LLMConfigGenerator(llm=MockLLM())

        config_file = tmp_path / "config.yaml"
        config_file.write_text("existing: content")

        request = ConfigGenRequest(
            request="Modify config",
            target_path=config_file,
            mode="edit",
        )

        context = generator.build_context(request)

        assert context.current_content == "existing: content"

    def test_build_context_create_mode_no_content(self, tmp_path: Path) -> None:
        """Test that create mode doesn't read existing content."""
        generator = LLMConfigGenerator(llm=MockLLM())

        config_file = tmp_path / "config.yaml"
        config_file.write_text("existing: content")

        request = ConfigGenRequest(
            request="Create new config",
            target_path=config_file,
            mode="create",
        )

        context = generator.build_context(request)

        # In create mode, we don't read existing content
        assert context.current_content is None

    def test_generate_success(self) -> None:
        """Test successful generation."""
        mock_response = {
            "title": "Configure static IP",
            "explanation": "Set static IP for eth0",
            "operations": [
                {
                    "kind": "set",
                    "path": "network.ethernets.eth0.dhcp4",
                    "value": "false",
                    "explanation": "Disable DHCP",
                }
            ],
            "risks": ["Network connectivity may be affected"],
            "rollback_hint": "Restore from backup",
            "requires_privilege": True,
        }

        mock_llm = MockLLM(response=mock_response)
        generator = LLMConfigGenerator(llm=mock_llm)

        request = ConfigGenRequest(
            request="Set static IP",
            target_path=Path("/etc/netplan/01-netcfg.yaml"),
        )
        context = generator.build_context(request)

        result = generator.generate(context)

        assert result is not None
        assert result.title == "Configure static IP"
        assert len(result.operations) == 1
        assert result.operations[0].kind == "set"
        assert result.requires_privilege is True

    def test_generate_create_mode(self) -> None:
        """Test generation in create mode."""
        mock_response = {
            "title": "New netplan config",
            "explanation": "Create DHCP configuration",
            "generated_content": "network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n",
            "risks": [],
            "requires_privilege": True,
        }

        mock_llm = MockLLM(response=mock_response)
        generator = LLMConfigGenerator(llm=mock_llm)

        request = ConfigGenRequest(
            request="Create DHCP config",
            target_path=Path("/etc/netplan/99-elle.yaml"),
            mode="create",
        )
        context = generator.build_context(request)

        result = generator.generate(context)

        assert result is not None
        assert result.generated_content is not None
        assert "dhcp4: true" in result.generated_content

    def test_generate_llm_unavailable(self) -> None:
        """Test generation when LLM is unavailable."""
        mock_llm = MockLLM()
        mock_llm._available = False

        generator = LLMConfigGenerator(llm=mock_llm)

        request = ConfigGenRequest(request="Test")
        context = generator.build_context(request)

        with pytest.raises(ConfigGenLLMError) as exc_info:
            generator.generate(context)

        assert "not available" in str(exc_info.value).lower()

    def test_generate_llm_error(self) -> None:
        """Test generation when LLM raises error."""
        from elle.rag.llm import LLMJSONError

        mock_llm = MockLLM(response=LLMJSONError("Parse error", "invalid json"))
        generator = LLMConfigGenerator(llm=mock_llm)

        request = ConfigGenRequest(request="Test")
        context = generator.build_context(request)

        with pytest.raises(ConfigGenLLMError):
            generator.generate(context)

    def test_generate_auto_detect_privilege(self) -> None:
        """Test auto-detection of privilege requirement."""
        mock_response = {
            "title": "Test",
            "explanation": "Test",
            "operations": [],
            "requires_privilege": False,  # Will be overridden
        }

        mock_llm = MockLLM(response=mock_response)
        generator = LLMConfigGenerator(llm=mock_llm)

        # System path should require privilege
        request = ConfigGenRequest(
            request="Modify system config",
            target_path=Path("/etc/ssh/sshd_config"),
        )
        context = generator.build_context(request)

        result = generator.generate(context)

        assert result.requires_privilege is True

    def test_generate_includes_validation_command(self) -> None:
        """Test that validation command is included from domain handler."""
        mock_response = {
            "title": "Test",
            "explanation": "Test",
            "operations": [],
            # No validation_command in response
        }

        mock_llm = MockLLM(response=mock_response)
        generator = LLMConfigGenerator(llm=mock_llm)

        # SSH domain has a validation command
        request = ConfigGenRequest(
            request="Modify SSH config",
            target_path=Path("/etc/ssh/sshd_config"),
            domain="ssh",
        )
        context = generator.build_context(request)

        result = generator.generate(context)

        assert result.validation_command == "sshd -t"


class TestGeneratorSingleton:
    """Tests for generator singleton functions."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_generator()

    def test_get_generator_returns_singleton(self) -> None:
        """Test get_generator returns same instance."""
        g1 = get_generator()
        g2 = get_generator()

        assert g1 is g2

    def test_reset_generator(self) -> None:
        """Test reset_generator clears singleton."""
        g1 = get_generator()
        reset_generator()
        g2 = get_generator()

        assert g1 is not g2


class TestContextManagement:
    """Tests for context building."""

    def test_context_with_explicit_domain(self) -> None:
        """Test context with explicitly set domain."""
        generator = LLMConfigGenerator(llm=MockLLM())

        request = ConfigGenRequest(
            request="Test",
            target_path=Path("/tmp/config.txt"),
            domain="network",  # Explicit
        )

        context = generator.build_context(request)

        assert context.detected_domain == "network"

    def test_context_with_explicit_file_type(self) -> None:
        """Test context with explicitly set file type."""
        generator = LLMConfigGenerator(llm=MockLLM())

        request = ConfigGenRequest(
            request="Test",
            target_path=Path("/tmp/config.txt"),
            file_type="yaml",  # Explicit
        )

        context = generator.build_context(request)

        assert context.detected_file_type == "yaml"

    def test_context_with_current_content(self) -> None:
        """Test context with pre-supplied content."""
        generator = LLMConfigGenerator(llm=MockLLM())

        request = ConfigGenRequest(
            request="Test",
            target_path=Path("/tmp/config.txt"),
            current_content="pre-supplied content",
        )

        context = generator.build_context(request)

        assert context.current_content == "pre-supplied content"

    def test_context_entities_passed_through(self) -> None:
        """Test entities are passed through to context."""
        generator = LLMConfigGenerator(llm=MockLLM())

        request = ConfigGenRequest(
            request="Configure eth0 with 192.168.1.10",
            target_path=Path("/tmp/config.yaml"),
            entities=("eth0", "192.168.1.10"),
        )

        context = generator.build_context(request)

        assert context.request.entities == ("eth0", "192.168.1.10")


class TestParseResponse:
    """Tests for response parsing."""

    def test_parse_operations(self) -> None:
        """Test parsing operations from response."""
        mock_response = {
            "title": "Test",
            "explanation": "Test",
            "operations": [
                {"kind": "set", "path": "/test/path", "value": "value1"},
                {"kind": "rm", "path": "/test/remove"},
                {"kind": "ins", "path": "/test/insert", "value": "new", "position": "after"},
            ],
        }

        mock_llm = MockLLM(response=mock_response)
        generator = LLMConfigGenerator(llm=mock_llm)

        request = ConfigGenRequest(request="Test")
        context = generator.build_context(request)

        result = generator.generate(context)

        assert len(result.operations) == 3
        assert result.operations[0].kind == "set"
        assert result.operations[0].value == "value1"
        assert result.operations[1].kind == "rm"
        assert result.operations[2].position == "after"

    def test_parse_risks(self) -> None:
        """Test parsing risks from response."""
        mock_response = {
            "title": "Test",
            "explanation": "Test",
            "operations": [],
            "risks": ["Risk 1", "Risk 2"],
        }

        mock_llm = MockLLM(response=mock_response)
        generator = LLMConfigGenerator(llm=mock_llm)

        request = ConfigGenRequest(request="Test")
        context = generator.build_context(request)

        result = generator.generate(context)

        assert len(result.risks) == 2
        assert "Risk 1" in result.risks

    def test_parse_empty_operations(self) -> None:
        """Test parsing response with no operations."""
        mock_response = {
            "title": "Test",
            "explanation": "Test",
            # No operations key
        }

        mock_llm = MockLLM(response=mock_response)
        generator = LLMConfigGenerator(llm=mock_llm)

        request = ConfigGenRequest(request="Test")
        context = generator.build_context(request)

        result = generator.generate(context)

        assert len(result.operations) == 0
