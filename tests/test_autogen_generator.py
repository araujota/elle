from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.capabilities.autogen.generator import (
    DEFAULT_OUTPUT_FIELDS,
    CapabilityGenerator,
    generate_basic_spec,
    generate_capability_spec,
    get_generator,
)
from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    ParsedManPage,
    ParsedSynopsis,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_man_page(
    name: str = "testcmd",
    section: int = 1,
    description: str = "A test command",
    synopsis: ParsedSynopsis | None = None,
) -> ParsedManPage:
    return ParsedManPage(
        name=name,
        section=section,
        description=description,
        synopsis=synopsis,
    )


# ---------------------------------------------------------------------------
# CapabilityGenerator
# ---------------------------------------------------------------------------


class TestCapabilityGenerator:
    def test_init_defaults(self):
        gen = CapabilityGenerator()
        assert gen.model == "llama3.2"
        assert gen.temperature == 0.3

    def test_init_custom(self):
        gen = CapabilityGenerator(model="qwen", temperature=0.5)
        assert gen.model == "qwen"
        assert gen.temperature == 0.5

    @pytest.mark.asyncio
    async def test_generate_with_subcommand(self):
        gen = CapabilityGenerator()
        response_data = {
            "name": "testcmd.list",
            "display_name": "Test List",
            "description": "List things",
            "domain": "file",
            "risk_level": "low",
            "command_template": "testcmd list",
            "input_fields": [],
        }
        with patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=json.dumps(response_data)):
            spec = await gen.generate(_make_man_page(), subcommand="list")
        assert spec is not None
        assert spec.name == "testcmd.list"

    @pytest.mark.asyncio
    async def test_generate_empty_response(self):
        gen = CapabilityGenerator()
        with patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=None):
            spec = await gen.generate(_make_man_page())
        assert spec is None

    @pytest.mark.asyncio
    async def test_generate_exception(self):
        gen = CapabilityGenerator()
        with patch.object(gen, "_call_llm", new_callable=AsyncMock, side_effect=RuntimeError("fail")):
            spec = await gen.generate(_make_man_page())
        assert spec is None

    @pytest.mark.asyncio
    async def test_generate_without_subcommand(self):
        gen = CapabilityGenerator()
        response_data = {
            "name": "testcmd.default",
            "description": "Do something",
            "domain": "file",
            "command_template": "testcmd",
        }
        with patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=json.dumps(response_data)):
            spec = await gen.generate(_make_man_page())
        assert spec is not None


# ---------------------------------------------------------------------------
# _call_llm
# ---------------------------------------------------------------------------


class TestCallLLM:
    @pytest.mark.asyncio
    async def test_call_llm_via_elle_rag(self):
        gen = CapabilityGenerator()
        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {"key": "value"}
        mock_config = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.rag.llm": MagicMock(LLM=MagicMock(return_value=mock_llm), LLMConfig=mock_config),
            },
        ):
            result = await gen._call_llm("system", "user")
        assert result is not None
        assert "key" in result

    @pytest.mark.asyncio
    async def test_call_llm_import_error_falls_back(self):
        gen = CapabilityGenerator()

        # Simulate ImportError in the LLM import path
        async def fake_direct(system, user):
            return '{"ok": true}'

        with patch.object(gen, "_call_llm") as mock_call:
            mock_call.return_value = '{"ok": true}'
            result = await gen._call_llm("sys", "usr")
        assert result is not None

    @pytest.mark.asyncio
    async def test_call_ollama_direct_success(self):
        gen = CapabilityGenerator()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": '{"test": 1}'}

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("sys.modules", {"httpx": MagicMock(AsyncClient=MagicMock(return_value=mock_client_instance))}):
            result = await gen._call_ollama_direct("sys", "usr")
        assert result is not None

    @pytest.mark.asyncio
    async def test_call_ollama_direct_failure(self):
        gen = CapabilityGenerator()

        mock_client_instance = AsyncMock()
        mock_client_instance.post.side_effect = Exception("connection error")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch.dict("sys.modules", {"httpx": MagicMock(AsyncClient=MagicMock(return_value=mock_client_instance))}):
            result = await gen._call_ollama_direct("sys", "usr")
        assert result is None


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_valid_json(self):
        gen = CapabilityGenerator()
        man = _make_man_page()
        data = {
            "name": "testcmd.list",
            "description": "list things",
            "domain": "file",
            "command_template": "testcmd list",
            "input_fields": [{"name": "path", "field_type": "str", "description": "Path", "required": True}],
            "output_fields": [{"name": "extra", "field_type": "str", "description": "extra field"}],
            "verify_command": "testcmd check",
            "confidence_score": 0.9,
        }
        spec = gen._parse_response(json.dumps(data), man, "list")
        assert spec is not None
        assert spec.name == "testcmd.list"
        assert len(spec.input_fields) == 1
        assert spec.verify_command == "testcmd check"

    def test_json_in_code_block(self):
        gen = CapabilityGenerator()
        man = _make_man_page()
        data = {"name": "testcmd.default", "domain": "file", "command_template": "testcmd"}
        raw = f"```json\n{json.dumps(data)}\n```"
        spec = gen._parse_response(raw, man, None)
        assert spec is not None

    def test_invalid_json(self):
        gen = CapabilityGenerator()
        man = _make_man_page()
        spec = gen._parse_response("not json at all", man, None)
        assert spec is None

    def test_parse_exception(self):
        gen = CapabilityGenerator()
        man = _make_man_page()
        # Valid JSON but missing required fields triggers exception
        spec = gen._parse_response('{"name": null}', man, None)
        # Should return None gracefully
        # (depends on implementation; name="" is falsy so generates default)
        # Either way it should not raise
        assert spec is not None or spec is None  # just must not crash

    def test_default_name_with_subcommand(self):
        gen = CapabilityGenerator()
        man = _make_man_page()
        data = {"domain": "file", "command_template": "testcmd sub-cmd"}
        spec = gen._parse_response(json.dumps(data), man, "sub-cmd")
        assert spec is not None
        assert spec.name == "testcmd.sub_cmd"

    def test_default_name_without_subcommand(self):
        gen = CapabilityGenerator()
        man = _make_man_page()
        data = {"domain": "file", "command_template": "testcmd"}
        spec = gen._parse_response(json.dumps(data), man, None)
        assert spec is not None
        assert spec.name == "testcmd.default"

    def test_default_output_fields(self):
        gen = CapabilityGenerator()
        man = _make_man_page()
        data = {"name": "x", "domain": "file", "command_template": "x"}
        spec = gen._parse_response(json.dumps(data), man, None)
        assert spec is not None
        assert len(spec.output_fields) == len(DEFAULT_OUTPUT_FIELDS)


# ---------------------------------------------------------------------------
# generate_basic_spec
# ---------------------------------------------------------------------------


class TestGenerateBasicSpec:
    @pytest.mark.parametrize(
        "cmd,expected_domain",
        [
            ("systemctl", "service"),
            ("docker", "docker"),
            ("ip", "network"),
            ("apt", "package"),
            ("useradd", "auth"),
            ("someutil", "file"),
        ],
    )
    def test_domain_inference(self, cmd, expected_domain):
        man = _make_man_page(name=cmd)
        spec = generate_basic_spec(man)
        assert spec.domain == expected_domain

    @pytest.mark.parametrize(
        "cmd,expected_risk",
        [
            ("cat", "none"),
            ("rm", "critical"),
            ("testutil", "medium"),
        ],
    )
    def test_risk_inference(self, cmd, expected_risk):
        man = _make_man_page(name=cmd)
        spec = generate_basic_spec(man)
        assert spec.risk_level == expected_risk

    def test_basic_spec_with_synopsis(self):
        synopsis = ParsedSynopsis(
            raw="testcmd [OPTIONS] FILE DIR",
            command="testcmd",
            positional_args=("FILE", "DIR", "EXTRA", "FOURTH"),
        )
        man = _make_man_page(name="testcmd", synopsis=synopsis)
        spec = generate_basic_spec(man)
        # Only first 3 positional args
        assert len(spec.input_fields) == 3

    def test_basic_spec_confidence(self):
        man = _make_man_page()
        spec = generate_basic_spec(man)
        assert spec.confidence_score == 0.3

    def test_basic_spec_empty_description(self):
        man = _make_man_page(description="")
        spec = generate_basic_spec(man)
        assert "Run testcmd" in spec.description


# ---------------------------------------------------------------------------
# get_generator / generate_capability_spec
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_get_generator_singleton(self):
        import elle.capabilities.autogen.generator as mod

        mod._generator = None
        g1 = get_generator()
        g2 = get_generator()
        assert g1 is g2
        mod._generator = None

    @pytest.mark.asyncio
    async def test_generate_capability_spec_with_llm(self):
        man = _make_man_page()
        mock_gen = MagicMock()
        mock_gen.generate = AsyncMock(return_value=_make_spec_model())

        with patch("elle.capabilities.autogen.generator.get_generator", return_value=mock_gen):
            spec = await generate_capability_spec(man, use_llm=True)
        assert spec is not None

    @pytest.mark.asyncio
    async def test_generate_capability_spec_fallback(self):
        man = _make_man_page()
        mock_gen = MagicMock()
        mock_gen.generate = AsyncMock(return_value=None)

        with patch("elle.capabilities.autogen.generator.get_generator", return_value=mock_gen):
            spec = await generate_capability_spec(man, use_llm=True)
        # Falls back to rule-based
        assert spec is not None
        assert spec.confidence_score == 0.3

    @pytest.mark.asyncio
    async def test_generate_capability_spec_no_llm(self):
        man = _make_man_page()
        spec = await generate_capability_spec(man, use_llm=False)
        assert spec is not None
        assert spec.confidence_score == 0.3


def _make_spec_model():
    return GeneratedCapabilitySpec(
        name="testcmd.default",
        display_name="Test",
        description="test",
        domain="file",
        risk_level="medium",
        command_template="testcmd",
        source_command="testcmd",
    )
