"""Tests for narrative_builder.py to boost coverage.

Tests NarrativeBuilder methods with mocked LLM and DB,
rule-based generation, keyword extraction, and formatting.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.narrative import (
    CausalChain,
    CausalLink,
    Narrative,
)
from elle.daemon.incidents.narrative_builder import (
    NarrativeBuilder,
    _ensure_narrative_schema,
    get_narrative,
    get_narratives_for_incident,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_link(
    cause_summary="High CPU load",
    effect_summary="nginx.service became unresponsive",
    relationship="triggered",
    confidence=0.9,
    temporal_delta_sec=30,
    cause_time=None,
    effect_time=None,
) -> CausalLink:
    now = datetime.utcnow()
    return CausalLink(
        cause_type="event",
        cause_id="ev-001",
        cause_summary=cause_summary,
        cause_timestamp=cause_time or now,
        effect_type="event",
        effect_id="ev-002",
        effect_summary=effect_summary,
        effect_timestamp=effect_time or (now + timedelta(seconds=temporal_delta_sec)),
        relationship=relationship,
        confidence=confidence,
        evidence=("journal log shows correlation",),
        temporal_delta_sec=temporal_delta_sec,
    )


def _make_chain(links=None, chain_id="chain-001", overall_confidence=0.85) -> CausalChain:
    if links is None:
        links = (_make_link(),)
    earliest = min(l.cause_timestamp for l in links) if links else None
    latest = max(l.effect_timestamp for l in links) if links else None
    return CausalChain(
        chain_id=chain_id,
        links=tuple(links),
        final_symptom="Service crash",
        overall_confidence=overall_confidence,
        search_iterations=3,
        earliest_timestamp=earliest,
        latest_timestamp=latest,
    )


# ---------------------------------------------------------------------------
# NarrativeBuilder tests
# ---------------------------------------------------------------------------


class TestBuildTimeline:
    @pytest.mark.timeout(10)
    def test_triggered_relationship(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain([_make_link(relationship="triggered")])
        timeline = builder._build_timeline(chain)
        assert len(timeline) == 1
        assert "triggered" in timeline[0]

    @pytest.mark.timeout(10)
    def test_contributed_to_relationship(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain([_make_link(relationship="contributed_to")])
        timeline = builder._build_timeline(chain)
        assert "contributed to" in timeline[0]

    @pytest.mark.timeout(10)
    def test_preceded_relationship(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain([_make_link(relationship="preceded")])
        timeline = builder._build_timeline(chain)
        assert "preceded" in timeline[0]

    @pytest.mark.timeout(10)
    def test_correlated_relationship(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain([_make_link(relationship="correlated")])
        timeline = builder._build_timeline(chain)
        # Correlated uses the else branch with a double arrow
        assert len(timeline) == 1


class TestRuleBasedGeneration:
    @pytest.mark.timeout(10)
    def test_summary_empty_chain(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain(links=())
        summary = builder._generate_summary_rule_based(chain, [])
        assert "Unable to determine" in summary

    @pytest.mark.timeout(10)
    def test_summary_single_link(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain([_make_link()])
        timeline = builder._build_timeline(chain)
        summary = builder._generate_summary_rule_based(chain, timeline)
        assert len(summary) > 0
        assert "caused by" in summary

    @pytest.mark.timeout(10)
    def test_summary_multiple_links(self):
        builder = NarrativeBuilder(use_llm=False)
        now = datetime.utcnow()
        links = [
            _make_link(
                cause_summary="Disk full",
                effect_summary="Swap activated",
                cause_time=now,
                effect_time=now + timedelta(seconds=10),
            ),
            _make_link(
                cause_summary="Swap activated",
                effect_summary="OOM kill",
                cause_time=now + timedelta(seconds=10),
                effect_time=now + timedelta(seconds=20),
            ),
        ]
        chain = _make_chain(links=links)
        timeline = builder._build_timeline(chain)
        summary = builder._generate_summary_rule_based(chain, timeline)
        assert "chain of 2" in summary

    @pytest.mark.timeout(10)
    def test_explanation_empty_chain(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain(links=())
        explanation = builder._generate_explanation_rule_based(chain, [])
        assert "Insufficient data" in explanation

    @pytest.mark.timeout(10)
    def test_explanation_with_links(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain([
            _make_link(confidence=0.9, temporal_delta_sec=30),
            _make_link(confidence=0.3, temporal_delta_sec=60),
        ])
        timeline = builder._build_timeline(chain)
        explanation = builder._generate_explanation_rule_based(chain, timeline)
        assert "high confidence" in explanation
        assert "low confidence" in explanation
        assert "Overall chain confidence" in explanation


class TestBuildNarrative:
    @pytest.mark.timeout(10)
    def test_rule_based(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain()
        narrative = builder.build_narrative(chain)

        assert isinstance(narrative, Narrative)
        assert narrative.model_used == "rule-based"
        assert len(narrative.summary) > 0
        assert len(narrative.timeline) > 0

    @pytest.mark.timeout(10)
    def test_with_store(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain()

        with patch.object(builder, "_store_narrative") as mock_store:
            narrative = builder.build_narrative(chain, incident_id="inc-001", store=True)
            mock_store.assert_called_once()
            assert narrative.model_used == "rule-based"

    @pytest.mark.timeout(10)
    def test_llm_fallback_on_error(self):
        builder = NarrativeBuilder(use_llm=True)
        chain = _make_chain()

        with patch("elle.rag.llm.get_llm", side_effect=ImportError, create=True):
            narrative = builder.build_narrative(chain)
            # Should fall back to rule-based
            assert narrative.model_used in ("rule-based", "fallback")

    @pytest.mark.timeout(10)
    def test_llm_unavailable(self):
        builder = NarrativeBuilder(use_llm=True)
        chain = _make_chain()

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        with patch("elle.rag.llm.get_llm", return_value=mock_llm, create=True):
            narrative = builder.build_narrative(chain)
            assert narrative.model_used == "unavailable"

    @pytest.mark.timeout(10)
    def test_llm_success(self):
        builder = NarrativeBuilder(use_llm=True, model="test-model")
        chain = _make_chain()

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_response = MagicMock()
        mock_response.content = "SUMMARY: Test summary\nEXPLANATION: Test explanation"
        mock_llm.generate.return_value = mock_response
        mock_llm.model = "default-model"

        with patch("elle.rag.llm.get_llm", return_value=mock_llm, create=True):
            narrative = builder.build_narrative(chain)
            assert narrative.summary == "Test summary"
            assert narrative.explanation == "Test explanation"
            assert narrative.model_used == "test-model"


class TestParseLlmResponse:
    @pytest.mark.timeout(10)
    def test_structured_response(self):
        builder = NarrativeBuilder(use_llm=False)
        response = "SUMMARY: High CPU caused crash\nEXPLANATION: The CPU hit 100% causing..."
        summary, explanation = builder._parse_llm_response(response)
        assert summary == "High CPU caused crash"
        assert "CPU hit 100%" in explanation

    @pytest.mark.timeout(10)
    def test_unstructured_response(self):
        builder = NarrativeBuilder(use_llm=False)
        response = "The CPU was high.\n\nThis caused problems."
        summary, explanation = builder._parse_llm_response(response)
        assert summary == "The CPU was high."
        assert "caused problems" in explanation

    @pytest.mark.timeout(10)
    def test_summary_only_response(self):
        builder = NarrativeBuilder(use_llm=False)
        response = "SUMMARY: Just a summary without explanation section"
        summary, explanation = builder._parse_llm_response(response)
        assert "Just a summary" in summary
        assert explanation == ""

    @pytest.mark.timeout(10)
    def test_single_paragraph(self):
        builder = NarrativeBuilder(use_llm=False)
        response = "Just one paragraph."
        summary, explanation = builder._parse_llm_response(response)
        assert summary == "Just one paragraph."
        assert explanation == ""


class TestExtractKeywords:
    @pytest.mark.timeout(10)
    def test_extracts_keywords(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain([
            _make_link(cause_summary="nginx failed with timeout", effect_summary="disk full error"),
        ])
        keywords = builder._extract_keywords(chain, "service failed", "nginx disk full")
        assert "failed" in keywords
        assert "nginx" in keywords
        assert "disk" in keywords

    @pytest.mark.timeout(10)
    def test_extract_keywords_from_text(self):
        builder = NarrativeBuilder(use_llm=False)
        kws = builder._extract_keywords_from_text("nginx failed with OOM and DNS error")
        assert "nginx" in kws
        assert "failed" in kws
        assert "oom" in kws
        assert "dns" in kws
        assert "error" in kws


class TestExtractEntities:
    @pytest.mark.timeout(10)
    def test_service_entity(self):
        builder = NarrativeBuilder(use_llm=False)
        entities = builder._extract_entities_from_text("nginx.service restarted")
        assert "nginx" in entities

    @pytest.mark.timeout(10)
    def test_interface_entity(self):
        builder = NarrativeBuilder(use_llm=False)
        entities = builder._extract_entities_from_text("eth0 went down")
        assert "eth0" in entities

    @pytest.mark.timeout(10)
    def test_device_entity(self):
        builder = NarrativeBuilder(use_llm=False)
        entities = builder._extract_entities_from_text("error on /dev/sda")
        assert "/dev/sda" in entities


class TestFormatDuration:
    @pytest.mark.timeout(10)
    def test_with_timestamps(self):
        builder = NarrativeBuilder(use_llm=False)
        now = datetime.utcnow()
        chain = _make_chain()
        chain = CausalChain(
            chain_id="c",
            links=(),
            earliest_timestamp=now,
            latest_timestamp=now + timedelta(seconds=90),
        )
        result = builder._format_duration(chain)
        assert "1m" in result

    @pytest.mark.timeout(10)
    def test_without_timestamps(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = CausalChain(chain_id="c", links=())
        result = builder._format_duration(chain)
        assert result == "unknown duration"


class TestFormatSeconds:
    @pytest.mark.timeout(10)
    def test_seconds(self):
        builder = NarrativeBuilder(use_llm=False)
        assert builder._format_seconds(45) == "45s"

    @pytest.mark.timeout(10)
    def test_minutes(self):
        builder = NarrativeBuilder(use_llm=False)
        assert builder._format_seconds(120) == "2m"

    @pytest.mark.timeout(10)
    def test_hours(self):
        builder = NarrativeBuilder(use_llm=False)
        assert builder._format_seconds(3600) == "1h"

    @pytest.mark.timeout(10)
    def test_hours_and_minutes(self):
        builder = NarrativeBuilder(use_llm=False)
        assert builder._format_seconds(3900) == "1h 5m"


# ---------------------------------------------------------------------------
# Store / Retrieve tests
# ---------------------------------------------------------------------------

PATCH_GET_CONN = "elle.daemon.incidents.narrative_builder.get_conn"


class TestStoreNarrative:
    @pytest.mark.timeout(10)
    def test_store(self):
        builder = NarrativeBuilder(use_llm=False)
        chain = _make_chain()
        narrative = builder.build_narrative(chain)

        with patch(PATCH_GET_CONN) as mock_gc:
            conn = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            conn.cursor.return_value = cursor

            builder._store_narrative(narrative, incident_id="inc-001")

            # Should call _ensure_narrative_schema and then INSERT
            assert cursor.execute.call_count >= 1


class TestGetNarrative:
    @pytest.mark.timeout(10)
    def test_not_found(self):
        with patch(PATCH_GET_CONN) as mock_gc:
            conn = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            conn.cursor.return_value = cursor
            cursor.fetchone.return_value = None

            result = get_narrative("missing-chain")
            assert result is None


class TestGetNarrativesForIncident:
    @pytest.mark.timeout(10)
    def test_no_narratives(self):
        with patch(PATCH_GET_CONN) as mock_gc:
            conn = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            conn.cursor.return_value = cursor
            cursor.fetchall.return_value = []

            result = get_narratives_for_incident("inc-001")
            assert result == []

    @pytest.mark.timeout(10)
    def test_with_narratives(self):
        with patch(PATCH_GET_CONN) as mock_gc:
            conn = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            conn.cursor.return_value = cursor
            cursor.fetchall.return_value = [{"chain_id": "chain-001"}]

            with patch("elle.daemon.incidents.narrative_builder.get_narrative", return_value=None):
                result = get_narratives_for_incident("inc-001")
                # get_narrative returns None, so filtered out
                assert result == []


class TestEnsureNarrativeSchema:
    @pytest.mark.timeout(10)
    def test_creates_table_and_index(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor

        _ensure_narrative_schema(conn)

        assert cursor.execute.call_count == 2  # Table + index
        conn.commit.assert_called_once()
