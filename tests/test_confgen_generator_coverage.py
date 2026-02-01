"""Tests for confgen generator coverage."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.rag.confgen.generator import (
    LLMConfigGenerator,
    get_generator,
    reset_generator,
)
from elle.rag.confgen.models import (
    ConfigGenContext,
    ConfigGenLLMError,
    ConfigGenRequest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    request: str = "Enable logging in myapp",
    mode: str = "edit",
    target_path: str | None = "/etc/myapp/config.yaml",
    domain: str | None = None,
    file_type: str | None = None,
    current_content: str | None = None,
) -> ConfigGenRequest:
    """Build a ConfigGenRequest with defaults."""
    return ConfigGenRequest(
        request=request,
        mode=mode,
        target_path=Path(target_path) if target_path else None,
        domain=domain,
        file_type=file_type,
        current_content=current_content,
    )


def _make_context(
    request: ConfigGenRequest | None = None,
    current_content: str | None = "key: value\n",
    man_docs: tuple[str, ...] = (),
    prior_art: tuple[dict[str, Any], ...] = (),
    schema_hint: dict[str, Any] | None = None,
    detected_domain: str = "general",
    detected_file_type: str = "yaml",
) -> ConfigGenContext:
    """Build a ConfigGenContext with defaults."""
    if request is None:
        request = _make_request()
    return ConfigGenContext(
        request=request,
        current_content=current_content,
        man_docs=man_docs,
        prior_art=prior_art,
        schema_hint=schema_hint,
        detected_domain=detected_domain,
        detected_file_type=detected_file_type,
    )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for get_generator / reset_generator."""

    def teardown_method(self) -> None:
        reset_generator()

    def test_get_generator_returns_instance(self) -> None:
        """get_generator() returns an LLMConfigGenerator."""
        gen = get_generator()
        assert isinstance(gen, LLMConfigGenerator)

    def test_get_generator_is_singleton(self) -> None:
        """get_generator() returns the same instance."""
        g1 = get_generator()
        g2 = get_generator()
        assert g1 is g2

    def test_reset_generator_creates_new(self) -> None:
        """reset_generator() causes next call to create new instance."""
        g1 = get_generator()
        reset_generator()
        g2 = get_generator()
        assert g1 is not g2


# ---------------------------------------------------------------------------
# LLM property
# ---------------------------------------------------------------------------


class TestLLMProperty:
    """Tests for llm property auto-init."""

    def test_llm_property_auto_inits(self) -> None:
        """llm property calls get_llm() when _llm is None."""
        mock_llm = MagicMock()
        gen = LLMConfigGenerator(llm=None)
        with patch("elle.rag.confgen.generator.get_llm", return_value=mock_llm):
            result = gen.llm
        assert result is mock_llm

    def test_llm_property_returns_provided(self) -> None:
        """llm property returns the provided LLM instance."""
        mock_llm = MagicMock()
        gen = LLMConfigGenerator(llm=mock_llm)
        assert gen.llm is mock_llm


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------


class TestBuildContext:
    """Tests for build_context."""

    def test_build_context_auto_detects_domain(self) -> None:
        """build_context auto-detects domain from target_path."""
        gen = LLMConfigGenerator(llm=MagicMock())
        request = _make_request(target_path="/etc/netplan/01-config.yaml")

        with (
            patch("elle.rag.confgen.generator.detect_domain", return_value="network"),
            patch("elle.rag.confgen.generator.detect_file_type", return_value="yaml"),
            patch("elle.rag.confgen.generator.get_handler") as mock_handler,
            patch.object(gen, "_search_man_vault", return_value=()),
            patch.object(gen, "_search_incident_vault", return_value=()),
        ):
            mock_handler.return_value.get_schema_hint.return_value = None
            ctx = gen.build_context(request)

        assert ctx.detected_domain == "network"
        assert ctx.detected_file_type == "yaml"

    def test_build_context_uses_explicit_domain(self) -> None:
        """build_context uses domain from request when provided."""
        gen = LLMConfigGenerator(llm=MagicMock())
        request = _make_request(domain="ssh", file_type="augeas")

        with (
            patch("elle.rag.confgen.generator.get_handler") as mock_handler,
            patch.object(gen, "_search_man_vault", return_value=()),
            patch.object(gen, "_search_incident_vault", return_value=()),
        ):
            mock_handler.return_value.get_schema_hint.return_value = None
            ctx = gen.build_context(request)

        assert ctx.detected_domain == "ssh"
        assert ctx.detected_file_type == "augeas"

    def test_build_context_reads_file_for_edit_mode(self, tmp_path) -> None:
        """build_context reads current file content for edit mode."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("existing: content\n")

        gen = LLMConfigGenerator(llm=MagicMock())
        request = _make_request(
            mode="edit",
            target_path=str(config_file),
            current_content=None,
        )

        with (
            patch("elle.rag.confgen.generator.detect_domain", return_value="general"),
            patch("elle.rag.confgen.generator.detect_file_type", return_value="yaml"),
            patch("elle.rag.confgen.generator.get_handler") as mock_handler,
            patch.object(gen, "_search_man_vault", return_value=()),
            patch.object(gen, "_search_incident_vault", return_value=()),
        ):
            mock_handler.return_value.get_schema_hint.return_value = None
            ctx = gen.build_context(request)

        assert ctx.current_content == "existing: content\n"

    def test_build_context_file_read_failure(self, tmp_path) -> None:
        """build_context handles file read failure gracefully."""
        gen = LLMConfigGenerator(llm=MagicMock())
        request = _make_request(
            mode="edit",
            target_path="/nonexistent/file.yaml",
            current_content=None,
        )

        with (
            patch("elle.rag.confgen.generator.detect_domain", return_value="general"),
            patch("elle.rag.confgen.generator.detect_file_type", return_value="yaml"),
            patch("elle.rag.confgen.generator.get_handler") as mock_handler,
            patch.object(gen, "_search_man_vault", return_value=()),
            patch.object(gen, "_search_incident_vault", return_value=()),
        ):
            mock_handler.return_value.get_schema_hint.return_value = None
            ctx = gen.build_context(request)

        assert ctx.current_content is None

    def test_build_context_no_target_path(self) -> None:
        """build_context works without a target_path."""
        gen = LLMConfigGenerator(llm=MagicMock())
        request = _make_request(target_path=None)

        with (
            patch.object(gen, "_search_man_vault", return_value=()),
            patch.object(gen, "_search_incident_vault", return_value=()),
        ):
            ctx = gen.build_context(request)

        assert ctx.detected_domain == "general"
        assert ctx.detected_file_type == "text"


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGenerate:
    """Tests for generate."""

    def test_generate_llm_unavailable(self) -> None:
        """generate() raises ConfigGenLLMError when LLM is unavailable."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False
        gen = LLMConfigGenerator(llm=mock_llm)
        ctx = _make_context()

        with pytest.raises(ConfigGenLLMError, match="LLM not available"):
            gen.generate(ctx)

    def test_generate_edit_mode(self) -> None:
        """generate() calls LLM for edit mode and returns result."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate_json.return_value = {
            "title": "Enable logging",
            "explanation": "Adds logging config",
            "operations": [
                {"kind": "set", "path": ".logging.enabled", "value": "true", "explanation": "Enable logging"},
            ],
        }
        gen = LLMConfigGenerator(llm=mock_llm)
        ctx = _make_context()

        with patch("elle.rag.confgen.generator.get_handler") as mock_handler:
            mock_handler.return_value.get_validation_command.return_value = None
            result = gen.generate(ctx)

        assert result is not None
        assert result.title == "Enable logging"
        assert len(result.operations) == 1

    def test_generate_create_mode(self) -> None:
        """generate() calls LLM for create mode."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate_json.return_value = {
            "title": "Create config",
            "explanation": "Creates new config file",
            "generated_content": "key: value\n",
        }
        gen = LLMConfigGenerator(llm=mock_llm)
        request = _make_request(mode="create")
        ctx = _make_context(request=request)

        with patch("elle.rag.confgen.generator.get_handler") as mock_handler:
            mock_handler.return_value.get_validation_command.return_value = None
            result = gen.generate(ctx)

        assert result is not None
        assert result.generated_content == "key: value\n"

    def test_generate_patch_mode(self) -> None:
        """generate() calls LLM for patch mode."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate_json.return_value = {
            "title": "Patch config",
            "explanation": "Patches existing config",
            "operations": [],
        }
        gen = LLMConfigGenerator(llm=mock_llm)
        request = _make_request(mode="patch")
        ctx = _make_context(request=request)

        with patch("elle.rag.confgen.generator.get_handler") as mock_handler:
            mock_handler.return_value.get_validation_command.return_value = None
            result = gen.generate(ctx)

        assert result is not None
        assert result.title == "Patch config"

    def test_generate_llm_json_error(self) -> None:
        """generate() raises ConfigGenLLMError on LLMJSONError."""
        from elle.rag.llm import LLMJSONError

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate_json.side_effect = LLMJSONError("bad json", raw_content="raw")
        gen = LLMConfigGenerator(llm=mock_llm)
        ctx = _make_context()

        with pytest.raises(ConfigGenLLMError, match="Failed to parse"):
            gen.generate(ctx)

    def test_generate_llm_error(self) -> None:
        """generate() raises ConfigGenLLMError on LLMError."""
        from elle.rag.llm import LLMError

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate_json.side_effect = LLMError("connection timeout")
        gen = LLMConfigGenerator(llm=mock_llm)
        ctx = _make_context()

        with pytest.raises(ConfigGenLLMError, match="connection timeout"):
            gen.generate(ctx)


# ---------------------------------------------------------------------------
# _search_man_vault
# ---------------------------------------------------------------------------


class TestSearchManVault:
    """Tests for _search_man_vault."""

    def test_man_vault_success(self) -> None:
        """_search_man_vault returns documentation snippets on success."""
        gen = LLMConfigGenerator(llm=MagicMock())

        mock_result = MagicMock()
        mock_result.text = "This is documentation text about the topic."

        mock_search = MagicMock(return_value=[mock_result])
        mock_retriever = MagicMock()
        mock_retriever.search = mock_search

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_retriever}):
            results = gen._search_man_vault("test query", "network")

        assert len(results) == 1
        assert "documentation text" in results[0]

    def test_man_vault_import_error(self) -> None:
        """_search_man_vault returns empty tuple on ImportError."""
        gen = LLMConfigGenerator(llm=MagicMock())

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": None}):
            results = gen._search_man_vault("test query", "general")

        assert results == ()

    def test_man_vault_exception(self) -> None:
        """_search_man_vault returns empty tuple on exception."""
        gen = LLMConfigGenerator(llm=MagicMock())

        mock_retriever = MagicMock()
        mock_retriever.search.side_effect = RuntimeError("DB error")

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_retriever}):
            results = gen._search_man_vault("test query", "general")

        assert results == ()


# ---------------------------------------------------------------------------
# _search_incident_vault
# ---------------------------------------------------------------------------


class TestSearchIncidentVault:
    """Tests for _search_incident_vault."""

    def test_incident_vault_success(self) -> None:
        """_search_incident_vault returns incident summaries on success."""
        gen = LLMConfigGenerator(llm=MagicMock())

        mock_result = MagicMock()
        mock_result.title = "Fix network issue"
        mock_result.outcome = "resolved"
        mock_result.root_cause = "misconfigured DNS"

        mock_search = MagicMock(return_value=[mock_result])
        mock_retriever = MagicMock()
        mock_retriever.search = mock_search

        with patch.dict("sys.modules", {"elle.daemon.incidents.retriever": mock_retriever}):
            results = gen._search_incident_vault("test query", "network")

        assert len(results) == 1
        assert results[0]["title"] == "Fix network issue"
        assert results[0]["outcome"] == "resolved"

    def test_incident_vault_import_error(self) -> None:
        """_search_incident_vault returns empty tuple on ImportError."""
        gen = LLMConfigGenerator(llm=MagicMock())

        with patch.dict("sys.modules", {"elle.daemon.incidents.retriever": None}):
            results = gen._search_incident_vault("test query", "general")

        assert results == ()

    def test_incident_vault_exception(self) -> None:
        """_search_incident_vault returns empty tuple on exception."""
        gen = LLMConfigGenerator(llm=MagicMock())

        mock_retriever = MagicMock()
        mock_retriever.search.side_effect = RuntimeError("DB error")

        with patch.dict("sys.modules", {"elle.daemon.incidents.retriever": mock_retriever}):
            results = gen._search_incident_vault("test query", "general")

        assert results == ()
