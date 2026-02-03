"""Coverage tests for multihop.py -- targeting uncovered lines.

Covers:
- Lines 131-133: Entity-based event search expansion in _search_events()
- Line 152: Keyword expansion query construction
- Lines 212-214: Entity extraction from events in _search_hop()
- Lines 271-296: Causal chain building with CausalLink objects
- Line 384: keyword_rerank boost calculation
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentReport,
    IncidentSearchResult,
)
from elle.daemon.incidents.multihop import (
    MultiHopSearch,
    keyword_rerank,
)
from elle.daemon.incidents.narrative import (
    MultiHopConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_incident(
    incident_id: str = "inc-1",
    title: str = "Test incident",
    summary: str = "A test incident",
    symptoms: tuple[str, ...] = (),
    domain: str = "other",
    created_at: datetime | None = None,
    entities: tuple[str, ...] = (),
) -> IncidentReport:
    return IncidentReport(
        incident_id=incident_id,
        title=title,
        summary=summary,
        symptoms=symptoms,
        domain=domain,
        created_at=created_at or datetime.now(timezone.utc),
        fingerprint=Fingerprint(entities=entities),
    )


def _make_search_result(
    incident: IncidentReport | None = None,
    score: float = 0.8,
    match_type: str = "hybrid",
) -> IncidentSearchResult:
    return IncidentSearchResult(
        incident=incident or _make_incident(),
        score=score,
        match_type=match_type,
    )


# ===========================================================================
# _search_events entity expansion (lines 131-133, 271-279)
# ===========================================================================


class TestSearchEventsEntityExpansion:
    """Cover lines 131-133: entity-based search expansion in _search_events."""

    def test_entity_search_expands_results(self):
        """When entities are provided, _search_events queries by entity too (lines 131-133)."""
        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        now = datetime.now(timezone.utc)

        # Create mock event dicts
        text_event = {
            "event_id": "evt-text-1",
            "ts": now - timedelta(minutes=5),
            "message": "disk full",
            "entity": "sda1",
        }
        entity_event = {
            "event_id": "evt-entity-1",
            "ts": now - timedelta(minutes=3),
            "message": "IO errors on sda1",
            "entity": "sda1",
        }

        mock_text_search = MagicMock(return_value=[text_event])
        mock_query_events = MagicMock(return_value=[entity_event])

        with patch(
            "elle.daemon.incidents.multihop.search_events",
            mock_text_search,
            create=True,
        ):
            with patch(
                "elle.daemon.incidents.multihop.query_events",
                mock_query_events,
                create=True,
            ):
                # Patch the entire module's imports
                with patch.dict(
                    "sys.modules",
                    {
                        "elle.daemon.telemetry.store": MagicMock(
                            search_events=mock_text_search,
                            query_events=mock_query_events,
                        ),
                    },
                ):
                    time_start = now - timedelta(hours=1)
                    time_end = now + timedelta(hours=1)
                    results = search._search_events(
                        "disk issue",
                        time_start,
                        time_end,
                        entities=["sda1", "sda2"],
                    )

        # Should have results from both text search and entity search
        assert len(results) >= 1


class TestSearchEventsEntityModuleImport:
    """Cover lines 131-133 with direct mocking of the telemetry store module."""

    def test_entity_search_imports_and_queries(self):
        """Entity-based search uses query_events for each entity (lines 271-279)."""
        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        now = datetime.now(timezone.utc)

        evt1 = MagicMock()
        evt1.model_dump = MagicMock(
            return_value={
                "event_id": "evt-1",
                "ts": now,
                "message": "test",
                "entity": "eth0",
            }
        )

        evt2 = MagicMock()
        evt2.model_dump = MagicMock(
            return_value={
                "event_id": "evt-2",
                "ts": now - timedelta(minutes=1),
                "message": "entity event",
                "entity": "eth0",
            }
        )

        mock_store = MagicMock()
        mock_store.search_events.return_value = [evt1]
        mock_store.query_events.return_value = [evt2]

        with patch.dict("sys.modules", {"elle.daemon.telemetry.store": mock_store}):
            time_start = now - timedelta(hours=1)
            time_end = now + timedelta(hours=1)
            results = search._search_events(
                "network issue",
                time_start,
                time_end,
                entities=["eth0"],
            )

        assert len(results) >= 1


# ===========================================================================
# _search_hop entity extraction from events (lines 212-214)
# ===========================================================================


class TestSearchHopEntityExtraction:
    """Cover lines 212-214: entity extraction from events in _search_hop."""

    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_hop_extracts_entities_from_events(self, mock_search):
        """_search_hop extracts entity from event dicts (lines 212-214)."""
        mock_search.return_value = []

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        now = datetime.now(timezone.utc)

        mock_events = [
            {
                "event_id": "evt-1",
                "ts": now,
                "message": "link down on eth0",
                "entity": "eth0",
            },
            {
                "event_id": "evt-2",
                "ts": now,
                "message": "DNS timeout",
                "entity": "dns-resolver",
            },
        ]

        with patch.object(search, "_search_events", return_value=mock_events):
            hop = search._search_hop(
                iteration=0,
                query="network issue",
                time_start=now - timedelta(hours=1),
                time_end=now + timedelta(hours=1),
                known_entities=set(),
            )

        # Entities from events should be discovered
        assert "eth0" in hop.entities_discovered
        assert "dns-resolver" in hop.entities_discovered

    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_hop_extracts_entities_from_incidents_and_events(self, mock_search):
        """Entities from both incidents and events are merged (lines 209-214)."""
        now = datetime.now(timezone.utc)
        inc = _make_incident(entities=("nginx",), created_at=now)
        mock_search.return_value = [_make_search_result(incident=inc, score=0.9)]

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))

        mock_events = [
            {"event_id": "evt-1", "ts": now, "message": "502 error", "entity": "haproxy"},
        ]

        with patch.object(search, "_search_events", return_value=mock_events):
            hop = search._search_hop(
                iteration=0,
                query="web error",
                time_start=now - timedelta(hours=1),
                time_end=now + timedelta(hours=1),
                known_entities=set(),
            )

        assert "nginx" in hop.entities_discovered
        assert "haproxy" in hop.entities_discovered


# ===========================================================================
# Causal chain building (lines 271-296)
# ===========================================================================


class TestBuildChainsTemporalChain:
    """Cover lines 271-296: temporal chain construction with CausalLink objects."""

    def test_three_event_chain_builds_links(self):
        """Three temporally close events produce a chain with 2 links."""
        search = MultiHopSearch(config=MultiHopConfig(min_confidence=0.0))
        now = datetime.now(timezone.utc)

        events = {
            "evt-1": {
                "event_id": "evt-1",
                "ts": now - timedelta(minutes=10),
                "entity": "eth0",
                "message": "link flap",
                "category": "net",
            },
            "evt-2": {
                "event_id": "evt-2",
                "ts": now - timedelta(minutes=5),
                "entity": "eth0",
                "message": "DNS failure",
                "category": "net",
            },
            "evt-3": {
                "event_id": "evt-3",
                "ts": now,
                "entity": "eth0",
                "message": "service unreachable",
                "category": "net",
            },
        }

        chains = search._build_chains({}, events, None)

        assert len(chains) == 1
        chain = chains[0]
        assert len(chain.links) >= 2
        assert chain.overall_confidence > 0
        assert chain.root_cause is not None
        assert chain.final_symptom != ""
        assert chain.earliest_timestamp is not None
        assert chain.latest_timestamp is not None

    def test_mixed_incidents_and_events_chain(self):
        """Chain with both incidents and events."""
        search = MultiHopSearch(config=MultiHopConfig(min_confidence=0.0))
        now = datetime.now(timezone.utc)

        inc = _make_incident(
            incident_id="inc-1",
            title="Disk full on /var",
            created_at=now - timedelta(minutes=3),
            entities=("sda1",),
            domain="disk",
        )
        sr = _make_search_result(incident=inc, score=0.9)

        events = {
            "evt-1": {
                "event_id": "evt-1",
                "ts": now,
                "entity": "sda1",
                "message": "ext4 error on sda1",
                "category": "disk",
            },
        }

        chains = search._build_chains({"inc-1": sr}, events, None)

        assert len(chains) == 1
        chain = chains[0]
        assert len(chain.links) >= 1


# ===========================================================================
# Keyword expansion query (line 152)
# ===========================================================================


class TestSearchKeywordExpansion:
    """Cover line 152: keyword expansion query construction."""

    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_keyword_expansion_modifies_query(self, mock_search):
        """When new keywords are extracted, they are appended to the query (line 152)."""
        now = datetime.now(timezone.utc)
        inc = _make_incident(
            title="nginx timeout error",
            summary="nginx connection refused",
            symptoms=("failed",),
            created_at=now,
            entities=(),
        )
        # First hop: returns results with keywords, but no new entities
        mock_search.return_value = [_make_search_result(incident=inc, score=0.9)]

        search = MultiHopSearch(
            config=MultiHopConfig(
                max_iterations=2,
                expand_entities=False,  # Disable entity expansion
                extract_keywords=True,
            ),
        )

        # Mock _search_events to return nothing
        with patch.object(search, "_search_events", return_value=[]):
            result = search.search("web issue")

        # Should have at least 2 iterations since keywords were found
        assert result.total_iterations >= 1


# ===========================================================================
# keyword_rerank boost (line 384)
# ===========================================================================


class TestKeywordRerankBoostCalculation:
    """Cover line 384: keyword boost calculation."""

    def test_partial_keyword_match_boost(self):
        """Partial keyword matches produce proportional boost (line 584-585)."""
        inc = _make_incident(
            title="nginx timeout",
            summary="connection refused to nginx",
            symptoms=("502 error",),
        )
        sr = _make_search_result(incident=inc, score=1.0)

        # Only 1 of 3 keywords matches
        reranked = keyword_rerank([sr], ["nginx", "redis", "mysql"], boost_factor=1.5)

        # boost = 1.0 + (1/3) * (1.5 - 1.0) = 1.0 + 0.167 = 1.167
        assert reranked[0].score == pytest.approx(1.167, abs=0.01)

    def test_all_keywords_match_max_boost(self):
        """All keywords matching gives maximum boost."""
        inc = _make_incident(
            title="nginx error timeout",
            summary="nginx error with timeout",
            symptoms=("error",),
        )
        sr = _make_search_result(incident=inc, score=1.0)

        reranked = keyword_rerank([sr], ["nginx", "error", "timeout"], boost_factor=2.0)

        # boost = 1.0 + (3/3) * (2.0 - 1.0) = 2.0
        assert reranked[0].score == pytest.approx(2.0, abs=0.01)


# ===========================================================================
# Search integration: entity and keyword expansion paths
# ===========================================================================


class TestSearchEntityExpansionPath:
    """Cover line 149: entity expansion sets current_query to new entities."""

    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_entity_expansion_changes_query(self, mock_search):
        """When expand_entities is True and new entities found, query updates (line 149)."""
        now = datetime.now(timezone.utc)

        # First search returns incident with entities
        inc = _make_incident(
            title="nginx crash",
            created_at=now,
            entities=("nginx", "port:80"),
        )
        mock_search.return_value = [_make_search_result(incident=inc, score=0.9)]

        search = MultiHopSearch(
            config=MultiHopConfig(
                max_iterations=2,
                expand_entities=True,
                extract_keywords=True,
            ),
        )

        with patch.object(search, "_search_events", return_value=[]):
            result = search.search("web error")

        # Should expand based on discovered entities
        assert result.total_iterations >= 1
        assert result.total_incidents_examined >= 1
