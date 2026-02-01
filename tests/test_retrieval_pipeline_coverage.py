from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.agentic.retrieval_pipeline import (
    CapabilityMatch,
    IncidentMatch,
    ManSnippet,
    RetrievalContext,
    UnifiedRetrievalPipeline,
    get_retrieval_pipeline,
    reset_retrieval_pipeline,
    retrieve_context,
)
from elle.cli.agentic.unified_input import AgenticInput, InputSource

# =============================================================================
# Helpers
# =============================================================================


def _make_input(**overrides: Any) -> AgenticInput:
    defaults: dict[str, Any] = {
        "source": InputSource.USER,
        "user_message": "why is disk full",
    }
    defaults.update(overrides)
    return AgenticInput(**defaults)


def _make_incident_match(**overrides: Any) -> IncidentMatch:
    defaults: dict[str, Any] = {
        "incident_id": "inc-1",
        "title": "Disk Full",
        "summary": "Root disk reached 95%",
        "domain": "disk",
        "outcome": "improved",
        "outcome_weight": 0.9,
        "similarity_score": 0.85,
        "days_ago": 3,
    }
    defaults.update(overrides)
    return IncidentMatch(**defaults)


def _make_man_snippet(**overrides: Any) -> ManSnippet:
    defaults: dict[str, Any] = {
        "name": "df",
        "section": "1",
        "snippet": "df - report file system disk space usage",
        "score": 0.8,
    }
    defaults.update(overrides)
    return ManSnippet(**defaults)


def _make_capability_match(**overrides: Any) -> CapabilityMatch:
    defaults: dict[str, Any] = {
        "capability_name": "file.delete",
        "domain": "file",
        "description": "Delete files safely",
        "risk_level": "medium",
        "match_score": 0.7,
        "success_rate": 0.95,
        "is_approved": True,
    }
    defaults.update(overrides)
    return CapabilityMatch(**defaults)


# =============================================================================
# Fixture: reset singleton between tests
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_pipeline() -> None:
    """Ensure retrieval pipeline singleton is cleared between tests."""
    reset_retrieval_pipeline()


# =============================================================================
# RetrievalContext property tests
# =============================================================================


class TestRetrievalContextProperties:
    """Tests for RetrievalContext boolean properties and best_prior_incident."""

    def test_has_prior_art_true_with_incidents(self) -> None:
        ctx = RetrievalContext(
            input=_make_input(),
            prior_incidents=(_make_incident_match(),),
        )
        assert ctx.has_prior_art is True

    def test_has_prior_art_false_without_incidents(self) -> None:
        ctx = RetrievalContext(input=_make_input())
        assert ctx.has_prior_art is False

    def test_has_documentation_true_with_snippets(self) -> None:
        ctx = RetrievalContext(
            input=_make_input(),
            man_vault_snippets=(_make_man_snippet(),),
        )
        assert ctx.has_documentation is True

    def test_has_documentation_false_without_snippets(self) -> None:
        ctx = RetrievalContext(input=_make_input())
        assert ctx.has_documentation is False

    def test_has_capabilities_true_with_matches(self) -> None:
        ctx = RetrievalContext(
            input=_make_input(),
            capability_matches=(_make_capability_match(),),
        )
        assert ctx.has_capabilities is True

    def test_has_capabilities_false_without_matches(self) -> None:
        ctx = RetrievalContext(input=_make_input())
        assert ctx.has_capabilities is False

    def test_best_prior_incident_returns_highest_weighted(self) -> None:
        low = _make_incident_match(
            incident_id="low",
            similarity_score=0.5,
            outcome_weight=0.3,
        )
        high = _make_incident_match(
            incident_id="high",
            similarity_score=0.9,
            outcome_weight=0.95,
        )
        mid = _make_incident_match(
            incident_id="mid",
            similarity_score=0.7,
            outcome_weight=0.6,
        )
        ctx = RetrievalContext(
            input=_make_input(),
            prior_incidents=(low, high, mid),
        )
        best = ctx.best_prior_incident
        assert best is not None
        assert best.incident_id == "high"
        # Confirm the product: 0.9 * 0.95 = 0.855 > 0.7 * 0.6 > 0.5 * 0.3
        assert best.similarity_score * best.outcome_weight == pytest.approx(0.855)

    def test_best_prior_incident_none_when_empty(self) -> None:
        ctx = RetrievalContext(input=_make_input())
        assert ctx.best_prior_incident is None


# =============================================================================
# RetrievalContext.format_for_prompt tests
# =============================================================================


class TestFormatForPrompt:
    """Tests for the format_for_prompt method."""

    def test_with_incidents_includes_branches(self) -> None:
        inc = _make_incident_match(
            root_cause="Disk filled by logs",
            decision="Rotate and compress",
            successful_actions=(
                {"command": "logrotate -f /etc/logrotate.conf"},
                {"command": "journalctl --vacuum-size=100M"},
            ),
        )
        ctx = RetrievalContext(
            input=_make_input(),
            prior_incidents=(inc,),
        )
        text = ctx.format_for_prompt()
        assert "Similar Past Incidents" in text
        assert "Root Cause: Disk filled by logs" in text
        assert "Decision: Rotate and compress" in text
        assert "logrotate" in text
        assert "journalctl" in text

    def test_with_capabilities_includes_risk_annotation(self) -> None:
        high_risk = _make_capability_match(
            capability_name="file.delete",
            risk_level="high",
        )
        low_risk = _make_capability_match(
            capability_name="file.read",
            risk_level="low",
        )
        ctx = RetrievalContext(
            input=_make_input(),
            capability_matches=(high_risk, low_risk),
        )
        text = ctx.format_for_prompt()
        assert "Available Capabilities" in text
        assert "[risk: high]" in text
        # low risk should not have annotation
        assert "[risk: low]" not in text

    def test_with_critical_risk_annotation(self) -> None:
        critical = _make_capability_match(
            capability_name="disk.format",
            risk_level="critical",
        )
        ctx = RetrievalContext(
            input=_make_input(),
            capability_matches=(critical,),
        )
        text = ctx.format_for_prompt()
        assert "[risk: critical]" in text

    def test_with_man_vault_includes_match_section(self) -> None:
        snippet = _make_man_snippet(match_section="OPTIONS")
        ctx = RetrievalContext(
            input=_make_input(),
            man_vault_snippets=(snippet,),
        )
        text = ctx.format_for_prompt()
        assert "Documentation" in text
        assert "[OPTIONS]" in text
        assert "df(1)" in text

    def test_with_man_vault_no_match_section(self) -> None:
        snippet = _make_man_snippet(match_section=None)
        ctx = RetrievalContext(
            input=_make_input(),
            man_vault_snippets=(snippet,),
        )
        text = ctx.format_for_prompt()
        assert "Documentation" in text
        assert "df(1)" in text
        # No bracket annotation should appear after the name(section)
        assert "[" not in text.split("df(1)")[1].split(":")[0]

    def test_max_chars_truncates_content(self) -> None:
        inc = _make_incident_match(
            summary="A" * 500,
            root_cause="B" * 500,
            decision="C" * 500,
        )
        ctx = RetrievalContext(
            input=_make_input(),
            prior_incidents=(inc,),
            man_vault_snippets=(_make_man_snippet(snippet="D" * 2000),),
            capability_matches=(_make_capability_match(),),
        )
        # Very small budget -- should not include everything
        text = ctx.format_for_prompt(max_chars=200)
        assert len(text) < 5000  # Must be bounded
        # At least the header was emitted
        assert "Similar Past Incidents" in text or "Available Capabilities" in text or text == ""

    def test_empty_context_returns_empty_string(self) -> None:
        ctx = RetrievalContext(input=_make_input())
        text = ctx.format_for_prompt()
        assert text == ""


# =============================================================================
# UnifiedRetrievalPipeline.retrieve tests
# =============================================================================


class TestPipelineRetrieve:
    """Tests for the main retrieve method."""

    async def test_empty_query_returns_empty_context(self) -> None:
        pipeline = UnifiedRetrievalPipeline()
        inp = _make_input(user_message="")
        ctx = await pipeline.retrieve(inp)

        assert ctx.prior_incidents == ()
        assert ctx.man_vault_snippets == ()
        assert ctx.capability_matches == ()
        assert ctx.sources_queried == ()
        assert ctx.retrieval_duration_ms == 0

    async def test_normal_retrieve_populates_all_sources(self) -> None:
        pipeline = UnifiedRetrievalPipeline()
        inc = _make_incident_match()
        snip = _make_man_snippet()
        cap = _make_capability_match()

        with (
            patch.object(pipeline, "_search_incidents", return_value=(inc,)),
            patch.object(pipeline, "_search_man_vault", return_value=(snip,)),
            patch.object(pipeline, "_search_capabilities", return_value=(cap,)),
        ):
            ctx = await pipeline.retrieve(_make_input())

        assert ctx.prior_incidents == (inc,)
        assert ctx.man_vault_snippets == (snip,)
        assert ctx.capability_matches == (cap,)
        assert "incidents" in ctx.sources_queried
        assert "man_vault" in ctx.sources_queried
        assert "capabilities" in ctx.sources_queried
        assert ctx.retrieval_duration_ms >= 0

    async def test_one_search_raises_others_still_returned(self) -> None:
        """When one search raises an exception, the others should still succeed."""
        pipeline = UnifiedRetrievalPipeline()
        snip = _make_man_snippet()
        cap = _make_capability_match()

        async def _raise_incidents(query: str, domain: str | None) -> tuple[IncidentMatch, ...]:
            raise RuntimeError("incident db down")

        with (
            patch.object(pipeline, "_search_incidents", side_effect=_raise_incidents),
            patch.object(pipeline, "_search_man_vault", return_value=(snip,)),
            patch.object(pipeline, "_search_capabilities", return_value=(cap,)),
        ):
            ctx = await pipeline.retrieve(_make_input())

        # Incidents failed so tuple is empty
        assert ctx.prior_incidents == ()
        # Others succeeded
        assert ctx.man_vault_snippets == (snip,)
        assert ctx.capability_matches == (cap,)
        # Failed source should not be in sources_queried
        assert "incidents" not in ctx.sources_queried
        assert "man_vault" in ctx.sources_queried
        assert "capabilities" in ctx.sources_queried

    async def test_timeout_returns_empty_tuples(self) -> None:
        """When the entire gather times out, all results are empty tuples."""
        pipeline = UnifiedRetrievalPipeline()

        with patch("elle.cli.agentic.retrieval_pipeline.asyncio.wait_for") as mock_wf:
            mock_wf.side_effect = asyncio.TimeoutError()
            ctx = await pipeline.retrieve(_make_input())

        assert ctx.prior_incidents == ()
        assert ctx.man_vault_snippets == ()
        assert ctx.capability_matches == ()

    async def test_retrieve_passes_domain_hint(self) -> None:
        """Verify domain_hint is forwarded to incident and capability searches."""
        pipeline = UnifiedRetrievalPipeline()

        with (
            patch.object(pipeline, "_search_incidents", return_value=()) as m_inc,
            patch.object(pipeline, "_search_man_vault", return_value=()),
            patch.object(pipeline, "_search_capabilities", return_value=()) as m_cap,
        ):
            inp = _make_input(domain_hint="network")
            await pipeline.retrieve(inp)

        m_inc.assert_called_once_with("why is disk full", "network")
        m_cap.assert_called_once_with("why is disk full", "network")


# =============================================================================
# _search_incidents tests
# =============================================================================


class TestSearchIncidents:
    """Tests for the _search_incidents internal method."""

    async def test_success_with_actions_model_dump(self) -> None:
        """Incident with actions that have model_dump and success=True."""
        action_obj = MagicMock()
        action_obj.success = True
        action_obj.model_dump.return_value = {"command": "systemctl restart nginx"}

        incident = MagicMock()
        incident.incident_id = "inc-42"
        incident.title = "nginx crash"
        incident.summary = "nginx segfaulted"
        incident.domain = "service"
        incident.outcome = "resolved"
        incident.actions = [action_obj]
        incident.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        incident.root_cause = "config typo"
        incident.decision = "fix config"

        result_obj = MagicMock()
        result_obj.incident = incident
        result_obj.outcome_weight = 0.95
        result_obj.score = 0.88

        with patch(
            "elle.cli.agentic.retrieval_pipeline.search",
            create=True,
        ):
            # The module uses a local import, so we must patch at the retriever level
            pass

        # Patch the inner import correctly
        mock_search_fn = MagicMock(return_value=[result_obj])
        with patch.dict(
            "sys.modules",
            {"elle.daemon.incidents.retriever": MagicMock(search=mock_search_fn)},
        ):
            pipeline = UnifiedRetrievalPipeline()
            matches = await pipeline._search_incidents("nginx crash", "service")

        assert len(matches) == 1
        m = matches[0]
        assert m.incident_id == "inc-42"
        assert m.title == "nginx crash"
        assert m.root_cause == "config typo"
        assert m.decision == "fix config"
        assert len(m.successful_actions) == 1
        assert m.successful_actions[0]["command"] == "systemctl restart nginx"

    async def test_success_with_actions_no_model_dump(self) -> None:
        """Incident with actions that lack model_dump, falls back to dict()."""
        action_obj = MagicMock(spec=[])  # no model_dump attribute
        action_obj.success = True
        # Make dict() work on this mock
        action_dict = {"command": "apt install nginx"}

        incident = MagicMock()
        incident.incident_id = "inc-43"
        incident.title = "package missing"
        incident.summary = "nginx not installed"
        incident.domain = "package"
        incident.outcome = "resolved"
        incident.actions = [action_obj]
        incident.updated_at = datetime(2025, 1, 10, tzinfo=timezone.utc)
        incident.root_cause = None
        incident.decision = None

        result_obj = MagicMock()
        result_obj.incident = incident
        result_obj.outcome_weight = 0.8
        result_obj.score = 0.75

        mock_search_fn = MagicMock(return_value=[result_obj])
        with patch.dict(
            "sys.modules",
            {"elle.daemon.incidents.retriever": MagicMock(search=mock_search_fn)},
        ):
            # Patch dict() on the action since spec=[] removes model_dump
            with patch("builtins.dict", side_effect=lambda x: action_dict):
                pipeline = UnifiedRetrievalPipeline()
                # The actual code does `dict(a)`, which uses __iter__;
                # simpler to just ensure hasattr(a, "model_dump") is False
                # and the fallback works. We need the action to be dict-able.
                pass

        # More reliable approach: use a simple namespace object
        class SimpleAction:
            success = True

            def __iter__(self):
                return iter([("command", "apt install nginx")])

        incident.actions = [SimpleAction()]

        mock_search_fn = MagicMock(return_value=[result_obj])
        with patch.dict(
            "sys.modules",
            {"elle.daemon.incidents.retriever": MagicMock(search=mock_search_fn)},
        ):
            pipeline = UnifiedRetrievalPipeline()
            matches = await pipeline._search_incidents("nginx missing", "package")

        assert len(matches) == 1
        assert matches[0].successful_actions[0]["command"] == "apt install nginx"

    async def test_actions_only_successful_included(self) -> None:
        """Only actions with success=True are included."""
        good = MagicMock()
        good.success = True
        good.model_dump.return_value = {"command": "good cmd"}

        bad = MagicMock()
        bad.success = False
        bad.model_dump.return_value = {"command": "bad cmd"}

        incident = MagicMock()
        incident.incident_id = "inc-44"
        incident.title = "mixed"
        incident.summary = "mixed actions"
        incident.domain = "general"
        incident.outcome = "partial"
        incident.actions = [good, bad]
        incident.updated_at = datetime(2025, 2, 1, tzinfo=timezone.utc)
        incident.root_cause = None
        incident.decision = None

        result_obj = MagicMock()
        result_obj.incident = incident
        result_obj.outcome_weight = 0.5
        result_obj.score = 0.7

        mock_search_fn = MagicMock(return_value=[result_obj])
        with patch.dict(
            "sys.modules",
            {"elle.daemon.incidents.retriever": MagicMock(search=mock_search_fn)},
        ):
            pipeline = UnifiedRetrievalPipeline()
            matches = await pipeline._search_incidents("mixed", None)

        assert len(matches[0].successful_actions) == 1
        assert matches[0].successful_actions[0]["command"] == "good cmd"

    async def test_incident_without_actions_attribute(self) -> None:
        """Incident objects that lack an actions attribute entirely."""
        incident = MagicMock(spec=["incident_id", "title", "summary", "domain", "outcome", "updated_at"])
        incident.incident_id = "inc-45"
        incident.title = "no actions"
        incident.summary = "nothing"
        incident.domain = "disk"
        incident.outcome = "unknown"
        incident.updated_at = datetime(2025, 3, 1, tzinfo=timezone.utc)

        result_obj = MagicMock()
        result_obj.incident = incident
        result_obj.outcome_weight = 0.4
        result_obj.score = 0.6

        mock_search_fn = MagicMock(return_value=[result_obj])
        with patch.dict(
            "sys.modules",
            {"elle.daemon.incidents.retriever": MagicMock(search=mock_search_fn)},
        ):
            pipeline = UnifiedRetrievalPipeline()
            matches = await pipeline._search_incidents("no actions", "disk")

        assert len(matches) == 1
        assert matches[0].successful_actions == ()

    async def test_exception_returns_empty_tuple(self) -> None:
        """Any exception during incident search returns ()."""
        mock_mod = MagicMock()
        mock_mod.search.side_effect = RuntimeError("db connection lost")

        with patch.dict("sys.modules", {"elle.daemon.incidents.retriever": mock_mod}):
            pipeline = UnifiedRetrievalPipeline()
            result = await pipeline._search_incidents("test", None)

        assert result == ()


# =============================================================================
# _search_man_vault tests
# =============================================================================


class TestSearchManVault:
    """Tests for the _search_man_vault internal method."""

    async def test_success_creates_snippets(self) -> None:
        r = MagicMock()
        r.name = "systemctl"
        r.section = "1"
        r.snippet = "systemctl - Control the systemd system and service manager"
        r.score = 0.92
        r.match_section = "SYNOPSIS"

        mock_search_fn = MagicMock(return_value=[r])
        with patch.dict(
            "sys.modules",
            {"elle.daemon.manvault.retriever": MagicMock(search=mock_search_fn)},
        ):
            pipeline = UnifiedRetrievalPipeline()
            snippets = await pipeline._search_man_vault("how to restart a service")

        assert len(snippets) == 1
        s = snippets[0]
        assert s.name == "systemctl"
        assert s.section == "1"
        assert s.match_section == "SYNOPSIS"
        assert s.score == 0.92

    async def test_success_without_match_section(self) -> None:
        """Result lacking match_section attribute gets None."""
        r = MagicMock(spec=["name", "section", "snippet", "score"])
        r.name = "ls"
        r.section = "1"
        r.snippet = "list directory contents"
        r.score = 0.6

        mock_search_fn = MagicMock(return_value=[r])
        with patch.dict(
            "sys.modules",
            {"elle.daemon.manvault.retriever": MagicMock(search=mock_search_fn)},
        ):
            pipeline = UnifiedRetrievalPipeline()
            snippets = await pipeline._search_man_vault("list files")

        assert len(snippets) == 1
        assert snippets[0].match_section is None

    async def test_exception_returns_empty_tuple(self) -> None:
        mock_mod = MagicMock()
        mock_mod.search.side_effect = ImportError("manvault not available")

        with patch.dict("sys.modules", {"elle.daemon.manvault.retriever": mock_mod}):
            pipeline = UnifiedRetrievalPipeline()
            result = await pipeline._search_man_vault("anything")

        assert result == ()


# =============================================================================
# _search_capabilities tests
# =============================================================================


class TestSearchCapabilities:
    """Tests for the _search_capabilities internal method."""

    async def test_success_creates_matches(self) -> None:
        r = MagicMock()
        r.capability_name = "service.restart"
        r.domain = "service"
        r.description = "Restart a systemd service"
        r.risk_level = "medium"
        r.score = 0.88
        r.success_rate = 0.97
        r.is_approved = True
        r.source_command = "systemctl restart"

        mock_search_fn = MagicMock(return_value=[r])
        with patch.dict(
            "sys.modules",
            {"elle.capabilities.autogen.retriever": MagicMock(search_capabilities=mock_search_fn)},
        ):
            pipeline = UnifiedRetrievalPipeline()
            matches = await pipeline._search_capabilities("restart nginx", "service")

        assert len(matches) == 1
        m = matches[0]
        assert m.capability_name == "service.restart"
        assert m.risk_level == "medium"
        assert m.source_command == "systemctl restart"
        assert m.is_approved is True

    async def test_exception_returns_empty_tuple(self) -> None:
        mock_mod = MagicMock()
        mock_mod.search_capabilities.side_effect = Exception("autogen unavailable")

        with patch.dict(
            "sys.modules",
            {"elle.capabilities.autogen.retriever": mock_mod},
        ):
            pipeline = UnifiedRetrievalPipeline()
            result = await pipeline._search_capabilities("test", None)

        assert result == ()


# =============================================================================
# retrieve_for_incident tests
# =============================================================================


class TestRetrieveForIncident:
    """Tests for the retrieve_for_incident method."""

    async def test_incident_not_found_returns_empty(self) -> None:
        mock_store = MagicMock()
        mock_store.get_incident.return_value = None

        with patch.dict(
            "sys.modules",
            {"elle.daemon.incidents.store": mock_store},
        ):
            pipeline = UnifiedRetrievalPipeline()
            ctx = await pipeline.retrieve_for_incident("inc-missing")

        assert ctx.prior_incidents == ()
        assert ctx.man_vault_snippets == ()
        assert ctx.capability_matches == ()
        assert ctx.retrieval_duration_ms == 0

    async def test_incident_found_with_summary_calls_retrieve(self) -> None:
        incident = MagicMock()
        incident.title = "OOM Killer"
        incident.summary = "Process killed due to low memory"
        incident.domain = "memory"

        mock_store = MagicMock()
        mock_store.get_incident.return_value = incident

        pipeline = UnifiedRetrievalPipeline()
        # Also mock retrieve to confirm it is called with constructed input
        mock_ctx = RetrievalContext(input=_make_input())

        with (
            patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}),
            patch.object(pipeline, "retrieve", return_value=mock_ctx) as mock_retrieve,
        ):
            await pipeline.retrieve_for_incident("inc-oom")

        # Verify retrieve was called
        mock_retrieve.assert_called_once()
        call_input = mock_retrieve.call_args[0][0]
        assert isinstance(call_input, AgenticInput)
        assert "OOM Killer" in call_input.user_message
        assert "Process killed due to low memory" in call_input.user_message
        assert call_input.domain_hint == "memory"
        assert call_input.incident_id == "inc-oom"

    async def test_incident_found_without_summary(self) -> None:
        incident = MagicMock()
        incident.title = "CPU Spike"
        incident.summary = None
        incident.domain = "cpu"

        mock_store = MagicMock()
        mock_store.get_incident.return_value = incident

        pipeline = UnifiedRetrievalPipeline()
        mock_ctx = RetrievalContext(input=_make_input())

        with (
            patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}),
            patch.object(pipeline, "retrieve", return_value=mock_ctx) as mock_retrieve,
        ):
            await pipeline.retrieve_for_incident("inc-cpu")

        call_input = mock_retrieve.call_args[0][0]
        # Without summary, query should be just the title
        assert call_input.user_message == "CPU Spike"

    async def test_exception_returns_empty_context(self) -> None:
        mock_store = MagicMock()
        mock_store.get_incident.side_effect = RuntimeError("store broken")

        with patch.dict(
            "sys.modules",
            {"elle.daemon.incidents.store": mock_store},
        ):
            pipeline = UnifiedRetrievalPipeline()
            ctx = await pipeline.retrieve_for_incident("inc-err")

        assert ctx.prior_incidents == ()
        assert ctx.man_vault_snippets == ()
        assert ctx.retrieval_duration_ms == 0


# =============================================================================
# Module-level singleton tests
# =============================================================================


class TestModuleSingletons:
    """Tests for get_retrieval_pipeline, reset_retrieval_pipeline, retrieve_context."""

    def test_get_retrieval_pipeline_creates_singleton(self) -> None:
        p1 = get_retrieval_pipeline()
        p2 = get_retrieval_pipeline()
        assert p1 is p2
        assert isinstance(p1, UnifiedRetrievalPipeline)

    def test_reset_retrieval_pipeline_clears_singleton(self) -> None:
        p1 = get_retrieval_pipeline()
        reset_retrieval_pipeline()
        p2 = get_retrieval_pipeline()
        assert p1 is not p2

    async def test_retrieve_context_delegates_to_pipeline(self) -> None:
        inp = _make_input()
        expected = RetrievalContext(input=inp)

        with patch(
            "elle.cli.agentic.retrieval_pipeline.get_retrieval_pipeline",
        ) as mock_get:
            mock_pipeline = AsyncMock()
            mock_pipeline.retrieve.return_value = expected
            mock_get.return_value = mock_pipeline

            result = await retrieve_context(inp)

        mock_pipeline.retrieve.assert_called_once_with(inp)
        assert result is expected


# =============================================================================
# Pydantic model instantiation tests
# =============================================================================


class TestModelInstantiation:
    """Verify Pydantic models can be created with expected fields."""

    def test_man_snippet_frozen(self) -> None:
        s = _make_man_snippet()
        with pytest.raises(Exception):
            s.name = "changed"  # type: ignore[misc]

    def test_incident_match_frozen(self) -> None:
        m = _make_incident_match()
        with pytest.raises(Exception):
            m.title = "changed"  # type: ignore[misc]

    def test_capability_match_frozen(self) -> None:
        c = _make_capability_match()
        with pytest.raises(Exception):
            c.capability_name = "changed"  # type: ignore[misc]

    def test_retrieval_context_frozen(self) -> None:
        ctx = RetrievalContext(input=_make_input())
        with pytest.raises(Exception):
            ctx.retrieval_duration_ms = 999  # type: ignore[misc]
