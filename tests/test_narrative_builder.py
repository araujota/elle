"""Tests for narrative generation from causal chains.

Tests the NarrativeBuilder, narrative storage/retrieval, and
incident-level narrative generation from
elle.daemon.incidents.narrative_builder.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any
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
    _json_dumps,
    _json_loads,
    get_narrative,
    get_narratives_for_incident,
)


# =============================================================================
# Fixtures
# =============================================================================


def _make_link(
    cause_summary: str = "Disk full",
    effect_summary: str = "PostgreSQL WAL write failed",
    relationship: str = "triggered",
    confidence: float = 0.9,
    delta_sec: int = 30,
    cause_type: str = "event",
    effect_type: str = "incident",
) -> CausalLink:
    """Helper to create a CausalLink for testing."""
    now = datetime.utcnow()
    return CausalLink(
        cause_type=cause_type,
        cause_id="cause-001",
        cause_summary=cause_summary,
        cause_timestamp=now - timedelta(seconds=delta_sec),
        effect_type=effect_type,
        effect_id="effect-001",
        effect_summary=effect_summary,
        effect_timestamp=now,
        relationship=relationship,
        confidence=confidence,
        evidence=("disk usage at 95%", "WAL write error in logs"),
        temporal_delta_sec=delta_sec,
    )


def _make_chain(
    links: tuple[CausalLink, ...] | None = None,
    confidence: float = 0.85,
    chain_id: str = "chain-test-001",
) -> CausalChain:
    """Helper to create a CausalChain for testing."""
    if links is None:
        links = (_make_link(),)

    now = datetime.utcnow()
    return CausalChain(
        chain_id=chain_id,
        root_cause=links[0] if links else None,
        links=links,
        final_symptom="Service degradation",
        overall_confidence=confidence,
        search_iterations=3,
        earliest_timestamp=now - timedelta(minutes=10),
        latest_timestamp=now,
    )


@pytest.fixture
def builder() -> NarrativeBuilder:
    """Create a NarrativeBuilder with LLM disabled."""
    return NarrativeBuilder(use_llm=False)


@pytest.fixture
def builder_with_llm() -> NarrativeBuilder:
    """Create a NarrativeBuilder with LLM enabled."""
    return NarrativeBuilder(use_llm=True, model="test-model")


@pytest.fixture
def single_link() -> CausalLink:
    """Create a single causal link."""
    return _make_link()


@pytest.fixture
def multi_link_chain() -> CausalChain:
    """Create a chain with multiple links."""
    now = datetime.utcnow()
    link1 = CausalLink(
        cause_type="condition",
        cause_id="cond-001",
        cause_summary="High memory pressure",
        cause_timestamp=now - timedelta(minutes=5),
        effect_type="event",
        effect_id="evt-001",
        effect_summary="OOM killer activated",
        effect_timestamp=now - timedelta(minutes=4),
        relationship="triggered",
        confidence=0.95,
        evidence=("mem usage 98%",),
        temporal_delta_sec=60,
    )
    link2 = CausalLink(
        cause_type="event",
        cause_id="evt-001",
        cause_summary="OOM killer activated",
        cause_timestamp=now - timedelta(minutes=4),
        effect_type="event",
        effect_id="evt-002",
        effect_summary="nginx process killed",
        effect_timestamp=now - timedelta(minutes=3),
        relationship="triggered",
        confidence=0.9,
        evidence=("OOM log entry", "nginx PID gone"),
        temporal_delta_sec=60,
    )
    link3 = CausalLink(
        cause_type="event",
        cause_id="evt-002",
        cause_summary="nginx process killed",
        cause_timestamp=now - timedelta(minutes=3),
        effect_type="symptom",
        effect_id="sym-001",
        effect_summary="HTTP 502 errors",
        effect_timestamp=now - timedelta(minutes=2),
        relationship="contributed_to",
        confidence=0.85,
        evidence=("502 in access.log",),
        temporal_delta_sec=60,
    )
    return CausalChain(
        chain_id="chain-multi-001",
        root_cause=link1,
        links=(link1, link2, link3),
        final_symptom="HTTP 502 errors",
        overall_confidence=0.73,
        search_iterations=4,
        earliest_timestamp=now - timedelta(minutes=5),
        latest_timestamp=now - timedelta(minutes=2),
    )


@pytest.fixture
def empty_chain() -> CausalChain:
    """Create a chain with no links."""
    return CausalChain(
        chain_id="chain-empty-001",
        links=(),
        final_symptom="",
        overall_confidence=0.0,
    )


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with narrative schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_narrative_schema(conn)
    return conn


# =============================================================================
# JSON Helper Tests
# =============================================================================


class TestJsonHelpers:
    """Tests for _json_dumps and _json_loads helpers."""

    def test_json_dumps_basic(self) -> None:
        """Test basic JSON serialization."""
        result = _json_dumps({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_json_dumps_datetime(self) -> None:
        """Test that datetimes are serialized via default=str."""
        dt = datetime(2024, 1, 15, 12, 30, 0)
        result = _json_dumps({"when": dt})
        parsed = json.loads(result)
        assert "2024-01-15" in parsed["when"]

    def test_json_dumps_list(self) -> None:
        """Test serializing a list."""
        result = _json_dumps(["a", "b", "c"])
        assert json.loads(result) == ["a", "b", "c"]

    def test_json_loads_valid(self) -> None:
        """Test parsing valid JSON."""
        result = _json_loads('["a", "b"]')
        assert result == ["a", "b"]

    def test_json_loads_none(self) -> None:
        """Test that None input returns empty list."""
        result = _json_loads(None)
        assert result == []

    def test_json_loads_empty_string(self) -> None:
        """Test that empty string returns empty list."""
        result = _json_loads("")
        assert result == []

    def test_json_loads_object(self) -> None:
        """Test parsing a JSON object."""
        result = _json_loads('{"k": 1}')
        assert result == {"k": 1}


# =============================================================================
# NarrativeBuilder Core Tests
# =============================================================================


class TestBuildTimeline:
    """Tests for _build_timeline method."""

    def test_timeline_triggered(self, builder: NarrativeBuilder) -> None:
        """Test timeline entry for 'triggered' relationship."""
        chain = _make_chain(links=(_make_link(relationship="triggered"),))
        timeline = builder._build_timeline(chain)

        assert len(timeline) == 1
        assert "triggered" in timeline[0].lower()

    def test_timeline_contributed_to(self, builder: NarrativeBuilder) -> None:
        """Test timeline entry for 'contributed_to' relationship."""
        chain = _make_chain(links=(_make_link(relationship="contributed_to"),))
        timeline = builder._build_timeline(chain)

        assert len(timeline) == 1
        assert "contributed to" in timeline[0]

    def test_timeline_preceded(self, builder: NarrativeBuilder) -> None:
        """Test timeline entry for 'preceded' relationship."""
        chain = _make_chain(links=(_make_link(relationship="preceded"),))
        timeline = builder._build_timeline(chain)

        assert len(timeline) == 1
        assert "preceded" in timeline[0].lower()

    def test_timeline_correlated(self, builder: NarrativeBuilder) -> None:
        """Test timeline entry for 'correlated' (default) relationship."""
        chain = _make_chain(links=(_make_link(relationship="correlated"),))
        timeline = builder._build_timeline(chain)

        assert len(timeline) == 1
        # Correlated uses the bidirectional arrow
        assert "\u2194" in timeline[0]  # ↔ character

    def test_timeline_multiple_links(
        self, builder: NarrativeBuilder, multi_link_chain: CausalChain
    ) -> None:
        """Test timeline with multiple links."""
        timeline = builder._build_timeline(multi_link_chain)
        assert len(timeline) == 3

    def test_timeline_empty_chain(
        self, builder: NarrativeBuilder, empty_chain: CausalChain
    ) -> None:
        """Test timeline from an empty chain."""
        timeline = builder._build_timeline(empty_chain)
        assert timeline == []

    def test_timeline_contains_timestamps(self, builder: NarrativeBuilder) -> None:
        """Test that timeline entries contain timestamp strings."""
        chain = _make_chain()
        timeline = builder._build_timeline(chain)

        assert len(timeline) == 1
        # Should contain HH:MM:SS format
        assert "[" in timeline[0]
        assert "]" in timeline[0]


# =============================================================================
# Rule-Based Generation Tests
# =============================================================================


class TestRuleBasedSummary:
    """Tests for _generate_summary_rule_based method."""

    def test_summary_single_link(self, builder: NarrativeBuilder) -> None:
        """Test summary generation for a single-link chain."""
        chain = _make_chain()
        timeline = builder._build_timeline(chain)
        summary = builder._generate_summary_rule_based(chain, timeline)

        assert len(summary) > 0
        assert "PostgreSQL WAL write failed" in summary

    def test_summary_multi_link(
        self, builder: NarrativeBuilder, multi_link_chain: CausalChain
    ) -> None:
        """Test summary for a multi-link chain mentions the chain length."""
        timeline = builder._build_timeline(multi_link_chain)
        summary = builder._generate_summary_rule_based(multi_link_chain, timeline)

        assert "3 related events" in summary
        assert "HTTP 502 errors" in summary

    def test_summary_empty_chain(
        self, builder: NarrativeBuilder, empty_chain: CausalChain
    ) -> None:
        """Test summary for an empty chain."""
        timeline = builder._build_timeline(empty_chain)
        summary = builder._generate_summary_rule_based(empty_chain, timeline)

        assert summary == "Unable to determine causal chain."

    def test_summary_same_cause_and_effect(self, builder: NarrativeBuilder) -> None:
        """Test summary when first cause == last effect."""
        link = _make_link(
            cause_summary="Service crash",
            effect_summary="Service crash",
        )
        chain = _make_chain(links=(link,))
        timeline = builder._build_timeline(chain)
        summary = builder._generate_summary_rule_based(chain, timeline)

        assert "Service crash" in summary
        # Should not redundantly say "was caused by Service crash"
        assert summary.count("Service crash") >= 1


class TestRuleBasedExplanation:
    """Tests for _generate_explanation_rule_based method."""

    def test_explanation_single_link(self, builder: NarrativeBuilder) -> None:
        """Test explanation for a single-link chain."""
        chain = _make_chain()
        timeline = builder._build_timeline(chain)
        explanation = builder._generate_explanation_rule_based(chain, timeline)

        assert "sequence of events" in explanation.lower()
        assert "directly triggered" in explanation
        assert "85%" in explanation  # Overall confidence

    def test_explanation_multi_link(
        self, builder: NarrativeBuilder, multi_link_chain: CausalChain
    ) -> None:
        """Test explanation for a multi-link chain."""
        timeline = builder._build_timeline(multi_link_chain)
        explanation = builder._generate_explanation_rule_based(multi_link_chain, timeline)

        assert "1." in explanation
        assert "2." in explanation
        assert "3." in explanation
        assert "73%" in explanation  # Overall confidence

    def test_explanation_empty_chain(
        self, builder: NarrativeBuilder, empty_chain: CausalChain
    ) -> None:
        """Test explanation for an empty chain."""
        timeline = builder._build_timeline(empty_chain)
        explanation = builder._generate_explanation_rule_based(empty_chain, timeline)

        assert "Insufficient data" in explanation

    def test_explanation_high_confidence_label(self, builder: NarrativeBuilder) -> None:
        """Test that high confidence links get labeled."""
        link = _make_link(confidence=0.95)
        chain = _make_chain(links=(link,))
        timeline = builder._build_timeline(chain)
        explanation = builder._generate_explanation_rule_based(chain, timeline)

        assert "[high confidence]" in explanation

    def test_explanation_low_confidence_label(self, builder: NarrativeBuilder) -> None:
        """Test that low confidence links get labeled."""
        link = _make_link(confidence=0.3)
        chain = _make_chain(links=(link,))
        timeline = builder._build_timeline(chain)
        explanation = builder._generate_explanation_rule_based(chain, timeline)

        assert "[low confidence]" in explanation

    def test_explanation_temporal_delta(self, builder: NarrativeBuilder) -> None:
        """Test that temporal delta is shown in explanation."""
        link = _make_link(delta_sec=120)
        chain = _make_chain(links=(link,))
        timeline = builder._build_timeline(chain)
        explanation = builder._generate_explanation_rule_based(chain, timeline)

        assert "2m later" in explanation

    def test_explanation_relationship_types(self, builder: NarrativeBuilder) -> None:
        """Test all relationship type texts."""
        for rel, expected in [
            ("triggered", "directly triggered"),
            ("contributed_to", "contributed to"),
            ("preceded", "occurred before"),
            ("correlated", "was correlated with"),
        ]:
            link = _make_link(relationship=rel)
            chain = _make_chain(links=(link,))
            timeline = builder._build_timeline(chain)
            explanation = builder._generate_explanation_rule_based(chain, timeline)
            assert expected in explanation, f"Expected '{expected}' for relationship '{rel}'"


# =============================================================================
# Keyword and Entity Extraction Tests
# =============================================================================


class TestExtractKeywords:
    """Tests for keyword extraction."""

    def test_extract_keywords_from_chain(self, builder: NarrativeBuilder) -> None:
        """Test that keywords are extracted from chain content."""
        link = _make_link(
            cause_summary="nginx failed with timeout error",
            effect_summary="HTTP service disk full",
        )
        chain = _make_chain(links=(link,))
        keywords = builder._extract_keywords(chain, "nginx failed", "disk was full")

        assert "nginx" in keywords
        assert "failed" in keywords
        assert "timeout" in keywords
        assert "disk" in keywords

    def test_extract_keywords_from_text(self, builder: NarrativeBuilder) -> None:
        """Test extracting keywords from raw text."""
        keywords = builder._extract_keywords_from_text(
            "The mysql service failed due to disk exhausted and memory OOM"
        )
        assert "mysql" in keywords
        assert "failed" in keywords
        assert "disk" in keywords
        assert "memory" in keywords
        assert "oom" in keywords

    def test_extract_keywords_empty_text(self, builder: NarrativeBuilder) -> None:
        """Test extracting from empty text."""
        keywords = builder._extract_keywords_from_text("")
        assert keywords == set()

    def test_extract_keywords_case_insensitive(self, builder: NarrativeBuilder) -> None:
        """Test that keyword extraction is case-insensitive."""
        keywords = builder._extract_keywords_from_text("NGINX Failed due to DNS Error")
        assert "nginx" in keywords
        assert "failed" in keywords
        assert "dns" in keywords
        assert "error" in keywords

    def test_keywords_sorted(self, builder: NarrativeBuilder) -> None:
        """Test that extracted keywords are returned sorted."""
        link = _make_link(
            cause_summary="redis service timeout",
            effect_summary="network port error",
        )
        chain = _make_chain(links=(link,))
        keywords = builder._extract_keywords(chain, "", "")

        assert keywords == sorted(keywords)


class TestExtractEntities:
    """Tests for entity extraction."""

    def test_extract_service_entity(self, builder: NarrativeBuilder) -> None:
        """Test extracting .service entity names."""
        entities = builder._extract_entities_from_text("nginx.service stopped")
        assert "nginx" in entities

    def test_extract_network_interface(self, builder: NarrativeBuilder) -> None:
        """Test extracting network interface names."""
        entities = builder._extract_entities_from_text("eth0 link down, also ens33 failed")
        assert "eth0" in entities
        assert "ens33" in entities

    def test_extract_device_path(self, builder: NarrativeBuilder) -> None:
        """Test extracting device paths."""
        entities = builder._extract_entities_from_text("Error on /dev/sda")
        assert "/dev/sda" in entities

    def test_extract_file_path(self, builder: NarrativeBuilder) -> None:
        """Test extracting file paths."""
        entities = builder._extract_entities_from_text("Config at /etc/nginx/conf changed")
        # Should find file path patterns
        assert any("/etc" in e for e in entities)

    def test_extract_entities_empty(self, builder: NarrativeBuilder) -> None:
        """Test extracting from empty text."""
        entities = builder._extract_entities_from_text("")
        assert entities == set()


# =============================================================================
# Duration Formatting Tests
# =============================================================================


class TestFormatDuration:
    """Tests for _format_duration and _format_seconds methods."""

    def test_format_seconds_under_minute(self, builder: NarrativeBuilder) -> None:
        """Test formatting seconds under 60."""
        assert builder._format_seconds(30) == "30s"
        assert builder._format_seconds(0) == "0s"
        assert builder._format_seconds(59) == "59s"

    def test_format_seconds_minutes(self, builder: NarrativeBuilder) -> None:
        """Test formatting minutes."""
        assert builder._format_seconds(60) == "1m"
        assert builder._format_seconds(120) == "2m"
        assert builder._format_seconds(3540) == "59m"

    def test_format_seconds_hours(self, builder: NarrativeBuilder) -> None:
        """Test formatting hours."""
        assert builder._format_seconds(3600) == "1h"
        assert builder._format_seconds(7200) == "2h"

    def test_format_seconds_hours_and_minutes(self, builder: NarrativeBuilder) -> None:
        """Test formatting hours with minutes."""
        assert builder._format_seconds(5400) == "1h 30m"
        assert builder._format_seconds(3660) == "1h 1m"

    def test_format_duration_known_timestamps(self, builder: NarrativeBuilder) -> None:
        """Test formatting chain duration with known timestamps."""
        chain = _make_chain()  # 10 minute window
        duration = builder._format_duration(chain)
        assert "10m" in duration

    def test_format_duration_no_timestamps(self, builder: NarrativeBuilder) -> None:
        """Test formatting when timestamps are missing."""
        chain = CausalChain(
            chain_id="no-ts",
            links=(),
            overall_confidence=0.0,
            earliest_timestamp=None,
            latest_timestamp=None,
        )
        duration = builder._format_duration(chain)
        assert duration == "unknown duration"


# =============================================================================
# LLM Integration Tests (mocked)
# =============================================================================


class TestLLMGeneration:
    """Tests for LLM-based narrative generation."""

    def test_build_llm_prompt(self, builder_with_llm: NarrativeBuilder) -> None:
        """Test LLM prompt construction."""
        chain = _make_chain()
        timeline = ["[12:00:00] Event A -> triggered -> Event B"]
        prompt = builder_with_llm._build_llm_prompt(chain, timeline)

        assert "Timeline:" in prompt
        assert "SUMMARY:" in prompt
        assert "EXPLANATION:" in prompt
        assert "85%" in prompt  # Overall confidence
        assert "root cause" in prompt.lower()

    def test_parse_llm_response_structured(self, builder: NarrativeBuilder) -> None:
        """Test parsing a well-structured LLM response."""
        response = (
            "SUMMARY: A disk issue caused cascading failures.\n\n"
            "EXPLANATION: The root cause was a full disk partition that "
            "prevented PostgreSQL from writing WAL files."
        )
        summary, explanation = builder._parse_llm_response(response)

        assert "disk issue" in summary
        assert "root cause" in explanation.lower()

    def test_parse_llm_response_no_structure(self, builder: NarrativeBuilder) -> None:
        """Test parsing an unstructured LLM response."""
        response = (
            "The system experienced disk issues.\n\n"
            "This led to PostgreSQL failures and eventually HTTP 502 errors."
        )
        summary, explanation = builder._parse_llm_response(response)

        assert "disk issues" in summary
        assert "PostgreSQL" in explanation

    def test_parse_llm_response_summary_only(self, builder: NarrativeBuilder) -> None:
        """Test parsing when only SUMMARY is present."""
        response = "SUMMARY: Something happened with the disk."
        summary, explanation = builder._parse_llm_response(response)

        assert "disk" in summary
        assert explanation == ""

    def test_parse_llm_response_empty(self, builder: NarrativeBuilder) -> None:
        """Test parsing an empty response."""
        summary, explanation = builder._parse_llm_response("")
        assert summary == ""
        assert explanation == ""

    @patch("elle.daemon.incidents.narrative_builder.NarrativeBuilder._generate_with_llm")
    def test_generate_with_llm_called(
        self, mock_llm: MagicMock, builder_with_llm: NarrativeBuilder
    ) -> None:
        """Test that LLM generation is called when use_llm=True."""
        mock_llm.return_value = ("LLM summary", "LLM explanation", "test-model")

        chain = _make_chain()
        narrative = builder_with_llm.build_narrative(chain)

        mock_llm.assert_called_once()
        assert narrative.summary == "LLM summary"
        assert narrative.explanation == "LLM explanation"
        assert narrative.model_used == "test-model"

    def test_llm_fallback_on_error(self, builder_with_llm: NarrativeBuilder) -> None:
        """Test that LLM failure falls back to rule-based generation."""
        # The _generate_with_llm method imports get_llm from elle.rag.llm
        # inside a try block. If the import fails, it catches the exception
        # and falls back to rule-based generation.
        mock_rag_llm = MagicMock()
        mock_rag_llm.get_llm = MagicMock(side_effect=ImportError("no LLM"))

        with patch.dict("sys.modules", {"elle.rag.llm": mock_rag_llm}):
            summary, explanation, model = builder_with_llm._generate_with_llm(
                _make_chain(), ["timeline entry"]
            )
        assert model == "fallback"
        assert len(summary) > 0

    def test_llm_unavailable_falls_back(self, builder_with_llm: NarrativeBuilder) -> None:
        """Test fallback when LLM reports itself as unavailable."""
        mock_llm_obj = MagicMock()
        mock_llm_obj.is_available.return_value = False

        mock_rag_llm = MagicMock()
        mock_rag_llm.get_llm = MagicMock(return_value=mock_llm_obj)

        with patch.dict("sys.modules", {"elle.rag.llm": mock_rag_llm}):
            summary, explanation, model = builder_with_llm._generate_with_llm(
                _make_chain(), ["timeline entry"]
            )

        assert model == "unavailable"
        assert len(summary) > 0


# =============================================================================
# Full Narrative Build Tests
# =============================================================================


class TestBuildNarrative:
    """Tests for the full build_narrative method."""

    def test_build_narrative_rule_based(self, builder: NarrativeBuilder) -> None:
        """Test building a complete narrative without LLM."""
        chain = _make_chain()
        narrative = builder.build_narrative(chain)

        assert isinstance(narrative, Narrative)
        assert len(narrative.summary) > 0
        assert len(narrative.explanation) > 0
        assert narrative.model_used == "rule-based"
        assert isinstance(narrative.generated_at, datetime)
        assert narrative.chain.chain_id == chain.chain_id

    def test_build_narrative_multi_link(
        self, builder: NarrativeBuilder, multi_link_chain: CausalChain
    ) -> None:
        """Test building narrative from a multi-link chain."""
        narrative = builder.build_narrative(multi_link_chain)

        assert len(narrative.timeline) == 3
        assert "HTTP 502 errors" in narrative.summary
        assert len(narrative.keywords) > 0

    def test_build_narrative_empty_chain(
        self, builder: NarrativeBuilder, empty_chain: CausalChain
    ) -> None:
        """Test building narrative from an empty chain."""
        narrative = builder.build_narrative(empty_chain)

        assert narrative.summary == "Unable to determine causal chain."
        assert "Insufficient data" in narrative.explanation
        assert narrative.timeline == ()

    def test_build_narrative_stores_to_db(
        self, builder: NarrativeBuilder, in_memory_db: sqlite3.Connection
    ) -> None:
        """Test that narrative is stored when connection is provided."""
        chain = _make_chain(chain_id="chain-store-test")
        narrative = builder.build_narrative(
            chain,
            incident_id="INC-STORE-001",
            conn=in_memory_db,
        )

        # Verify it was stored
        cursor = in_memory_db.cursor()
        cursor.execute("SELECT * FROM narratives WHERE chain_id = ?", ("chain-store-test",))
        row = cursor.fetchone()
        assert row is not None
        assert row["incident_id"] == "INC-STORE-001"
        assert row["summary"] == narrative.summary

    def test_build_narrative_no_store_without_conn(self, builder: NarrativeBuilder) -> None:
        """Test that narrative is not stored when no connection is given."""
        chain = _make_chain()
        narrative = builder.build_narrative(chain)

        assert isinstance(narrative, Narrative)
        # No assertion on DB since no conn was passed

    def test_build_narrative_keywords_populated(self, builder: NarrativeBuilder) -> None:
        """Test that keywords are populated from chain content."""
        link = _make_link(
            cause_summary="nginx service timeout on port 80",
            effect_summary="HTTP error returned to client",
        )
        chain = _make_chain(links=(link,))
        narrative = builder.build_narrative(chain)

        assert len(narrative.keywords) > 0
        # Keywords should include recognized technical terms
        keyword_set = set(narrative.keywords)
        assert "nginx" in keyword_set or "service" in keyword_set or "timeout" in keyword_set


# =============================================================================
# Narrative Storage and Retrieval Tests
# =============================================================================


class TestNarrativeStorage:
    """Tests for narrative database storage and retrieval."""

    def test_ensure_narrative_schema(self, in_memory_db: sqlite3.Connection) -> None:
        """Test that the narrative schema is created properly."""
        cursor = in_memory_db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='narratives'")
        row = cursor.fetchone()
        assert row is not None

    def test_ensure_narrative_schema_idempotent(self, in_memory_db: sqlite3.Connection) -> None:
        """Test that calling schema creation twice is safe."""
        _ensure_narrative_schema(in_memory_db)
        _ensure_narrative_schema(in_memory_db)
        # Should not raise

    def test_store_and_retrieve_narrative(
        self, builder: NarrativeBuilder, in_memory_db: sqlite3.Connection
    ) -> None:
        """Test round-trip storage and retrieval of a narrative."""
        chain = _make_chain(chain_id="chain-roundtrip-001")
        original = builder.build_narrative(
            chain,
            incident_id="INC-RT-001",
            conn=in_memory_db,
        )

        # Retrieve
        with patch(
            "elle.daemon.incidents.narrative_builder.get_connection",
            return_value=in_memory_db,
        ), patch(
            "elle.daemon.incidents.narrative_builder.ensure_schema",
        ):
            retrieved = get_narrative("chain-roundtrip-001", conn=in_memory_db)

        assert retrieved is not None
        assert retrieved.summary == original.summary
        assert retrieved.explanation == original.explanation
        assert retrieved.model_used == original.model_used

    def test_get_narrative_not_found(self, in_memory_db: sqlite3.Connection) -> None:
        """Test retrieving a narrative that does not exist."""
        with patch(
            "elle.daemon.incidents.narrative_builder.get_connection",
            return_value=in_memory_db,
        ), patch(
            "elle.daemon.incidents.narrative_builder.ensure_schema",
        ):
            result = get_narrative("nonexistent-chain", conn=in_memory_db)
        assert result is None

    def test_store_narrative_replaces_on_duplicate(
        self, builder: NarrativeBuilder, in_memory_db: sqlite3.Connection
    ) -> None:
        """Test that storing with same chain_id replaces the previous entry."""
        chain = _make_chain(chain_id="chain-dup-001")
        builder.build_narrative(chain, conn=in_memory_db)

        # Build again with different content (same chain_id)
        link2 = _make_link(
            cause_summary="Different cause",
            effect_summary="Different effect",
        )
        chain2 = _make_chain(chain_id="chain-dup-001", links=(link2,))
        builder.build_narrative(chain2, conn=in_memory_db)

        # Should have only one row
        cursor = in_memory_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM narratives WHERE chain_id = ?", ("chain-dup-001",))
        count = cursor.fetchone()[0]
        assert count == 1

    def test_get_narratives_for_incident(
        self, builder: NarrativeBuilder, in_memory_db: sqlite3.Connection
    ) -> None:
        """Test retrieving all narratives for an incident."""
        chain1 = _make_chain(chain_id="chain-inc-001")
        chain2 = _make_chain(chain_id="chain-inc-002")

        builder.build_narrative(chain1, incident_id="INC-MULTI", conn=in_memory_db)
        builder.build_narrative(chain2, incident_id="INC-MULTI", conn=in_memory_db)

        with patch(
            "elle.daemon.incidents.narrative_builder.get_connection",
            return_value=in_memory_db,
        ), patch(
            "elle.daemon.incidents.narrative_builder.ensure_schema",
        ):
            narratives = get_narratives_for_incident("INC-MULTI", conn=in_memory_db)

        assert len(narratives) == 2

    def test_get_narratives_for_incident_none(self, in_memory_db: sqlite3.Connection) -> None:
        """Test retrieving narratives for an incident with none."""
        with patch(
            "elle.daemon.incidents.narrative_builder.get_connection",
            return_value=in_memory_db,
        ), patch(
            "elle.daemon.incidents.narrative_builder.ensure_schema",
        ):
            narratives = get_narratives_for_incident("INC-EMPTY", conn=in_memory_db)
        assert narratives == []


# =============================================================================
# Different Incident Type / Domain / Severity Tests
# =============================================================================


class TestDifferentDomains:
    """Tests for narratives across different domain types."""

    def test_network_domain_narrative(self, builder: NarrativeBuilder) -> None:
        """Test narrative for network-related causal chain."""
        link = _make_link(
            cause_summary="DNS resolver timeout",
            effect_summary="HTTP connection refused",
        )
        chain = _make_chain(links=(link,))
        narrative = builder.build_narrative(chain)

        assert "DNS resolver timeout" in narrative.summary or "HTTP connection refused" in narrative.summary
        assert len(narrative.keywords) > 0

    def test_disk_domain_narrative(self, builder: NarrativeBuilder) -> None:
        """Test narrative for disk-related causal chain."""
        link = _make_link(
            cause_summary="disk /dev/sda SMART error",
            effect_summary="I/O operations failed",
        )
        chain = _make_chain(links=(link,))
        narrative = builder.build_narrative(chain)

        assert len(narrative.summary) > 0
        keyword_set = set(narrative.keywords)
        # Should capture disk/I/O related keywords
        assert "disk" in keyword_set or "i/o" in keyword_set

    def test_service_domain_narrative(self, builder: NarrativeBuilder) -> None:
        """Test narrative for service-related causal chain."""
        link = _make_link(
            cause_summary="systemd restart of redis.service",
            effect_summary="redis process denied connection",
        )
        chain = _make_chain(links=(link,))
        narrative = builder.build_narrative(chain)

        assert len(narrative.summary) > 0
        keyword_set = set(narrative.keywords)
        assert "redis" in keyword_set or "service" in keyword_set

    def test_low_confidence_narrative(self, builder: NarrativeBuilder) -> None:
        """Test narrative with very low confidence chain."""
        link = _make_link(confidence=0.2)
        chain = _make_chain(links=(link,), confidence=0.2)
        narrative = builder.build_narrative(chain)

        assert len(narrative.summary) > 0
        assert "[low confidence]" in narrative.explanation


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Edge case tests for narrative builder."""

    def test_narrative_with_very_long_chain(self, builder: NarrativeBuilder) -> None:
        """Test narrative generation with a long chain of events."""
        now = datetime.utcnow()
        links = []
        for i in range(10):
            links.append(
                CausalLink(
                    cause_type="event",
                    cause_id=f"evt-{i:03d}",
                    cause_summary=f"Event {i} occurred",
                    cause_timestamp=now - timedelta(minutes=10 - i),
                    effect_type="event",
                    effect_id=f"evt-{i + 1:03d}",
                    effect_summary=f"Event {i + 1} occurred",
                    effect_timestamp=now - timedelta(minutes=9 - i),
                    relationship="triggered",
                    confidence=0.8,
                    temporal_delta_sec=60,
                )
            )

        chain = CausalChain(
            chain_id="chain-long-001",
            root_cause=links[0],
            links=tuple(links),
            final_symptom="Final event",
            overall_confidence=0.8**10,
            search_iterations=10,
            earliest_timestamp=now - timedelta(minutes=10),
            latest_timestamp=now,
        )

        narrative = builder.build_narrative(chain)
        assert len(narrative.timeline) == 10
        assert "10 related events" in narrative.summary

    def test_narrative_special_characters_in_summary(self, builder: NarrativeBuilder) -> None:
        """Test narrative with special characters in event summaries."""
        link = _make_link(
            cause_summary="Error in /etc/nginx/conf.d/*.conf",
            effect_summary='Service returned "500 Internal Server Error"',
        )
        chain = _make_chain(links=(link,))
        narrative = builder.build_narrative(chain)

        assert len(narrative.summary) > 0

    def test_narrative_unicode_in_summaries(self, builder: NarrativeBuilder) -> None:
        """Test narrative handles unicode content."""
        link = _make_link(
            cause_summary="Fehler: Verbindung verweigert",
            effect_summary="Service unavailable",
        )
        chain = _make_chain(links=(link,))
        narrative = builder.build_narrative(chain)

        assert len(narrative.summary) > 0

    def test_narrative_zero_temporal_delta(self, builder: NarrativeBuilder) -> None:
        """Test narrative with zero temporal delta between events."""
        link = _make_link(delta_sec=0)
        chain = _make_chain(links=(link,))
        narrative = builder.build_narrative(chain)

        # Should not mention "0s later" in the explanation
        # because temporal_delta_sec > 0 check prevents it
        assert "0s later" not in narrative.explanation

    def test_build_narrative_preserves_chain_reference(self, builder: NarrativeBuilder) -> None:
        """Test that the narrative retains a reference to its source chain."""
        chain = _make_chain(chain_id="chain-ref-001")
        narrative = builder.build_narrative(chain)

        assert narrative.chain is chain
        assert narrative.chain.chain_id == "chain-ref-001"
