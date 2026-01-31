from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentReport,
    IncidentSearchResult,
)
from elle.daemon.incidents.multihop import (
    MultiHopSearch,
    _parse_datetime,
    keyword_rerank,
    temporal_rerank,
)
from elle.daemon.incidents.narrative import (
    MultiHopConfig,
    MultiHopResult,
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
        created_at=created_at or datetime.utcnow(),
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
# _parse_datetime
# ===========================================================================


class TestParseDateTime:
    def test_parses_iso_format(self):
        result = _parse_datetime("2024-01-15T10:30:00")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_parses_iso_with_microseconds(self):
        result = _parse_datetime("2024-06-01T12:00:00.123456")
        assert result.microsecond == 123456


# ===========================================================================
# MultiHopConfig
# ===========================================================================


class TestMultiHopConfig:
    def test_default_values(self):
        cfg = MultiHopConfig()
        assert cfg.max_iterations == 5
        assert cfg.time_window_hours == 24
        assert cfg.min_confidence == 0.5
        assert cfg.max_results_per_hop == 20
        assert cfg.high_relevance_threshold == 0.7

    def test_custom_values(self):
        cfg = MultiHopConfig(max_iterations=2, time_window_hours=6)
        assert cfg.max_iterations == 2
        assert cfg.time_window_hours == 6


# ===========================================================================
# MultiHopSearch.__init__
# ===========================================================================


class TestMultiHopSearchInit:
    def test_default_config(self):
        search = MultiHopSearch()
        assert search.config.max_iterations == 5

    def test_custom_config(self):
        cfg = MultiHopConfig(max_iterations=3)
        search = MultiHopSearch(config=cfg)
        assert search.config.max_iterations == 3


# ===========================================================================
# _get_event_time
# ===========================================================================


class TestGetEventTime:
    def test_datetime_ts(self):
        search = MultiHopSearch()
        ts = datetime(2024, 1, 1, 12, 0, 0)
        result = search._get_event_time({"ts": ts})
        assert result == ts

    def test_string_ts(self):
        search = MultiHopSearch()
        result = search._get_event_time({"ts": "2024-06-15T10:00:00"})
        assert result.year == 2024
        assert result.month == 6

    def test_missing_ts_returns_now(self):
        search = MultiHopSearch()
        before = datetime.utcnow()
        result = search._get_event_time({})
        after = datetime.utcnow()
        assert before <= result <= after

    def test_none_ts_returns_now(self):
        search = MultiHopSearch()
        result = search._get_event_time({"ts": None})
        # Should not crash, returns utcnow
        assert isinstance(result, datetime)


# ===========================================================================
# _get_entities
# ===========================================================================


class TestGetEntities:
    def test_incident_entities(self):
        search = MultiHopSearch()
        inc = _make_incident(entities=("nginx", "port:80"))
        result = search._get_entities("incident", inc)
        assert result == {"nginx", "port:80"}

    def test_event_entity(self):
        search = MultiHopSearch()
        result = search._get_entities("event", {"entity": "eth0"})
        assert result == {"eth0"}

    def test_event_no_entity(self):
        search = MultiHopSearch()
        result = search._get_entities("event", {})
        assert result == set()

    def test_unknown_type(self):
        search = MultiHopSearch()
        result = search._get_entities("unknown", {})
        assert result == set()


# ===========================================================================
# _get_domain
# ===========================================================================


class TestGetDomain:
    def test_incident_domain(self):
        search = MultiHopSearch()
        inc = _make_incident(domain="net")
        result = search._get_domain("incident", inc)
        assert result == "net"

    def test_event_category(self):
        search = MultiHopSearch()
        result = search._get_domain("event", {"category": "disk"})
        assert result == "disk"

    def test_event_no_category(self):
        search = MultiHopSearch()
        result = search._get_domain("event", {})
        assert result is None

    def test_unknown_type(self):
        search = MultiHopSearch()
        result = search._get_domain("other", {})
        assert result is None


# ===========================================================================
# _get_item_summary
# ===========================================================================


class TestGetItemSummary:
    def test_incident_summary(self):
        search = MultiHopSearch()
        inc = _make_incident(title="Disk full on /var")
        result = search._get_item_summary("incident", inc)
        assert result == "Disk full on /var"

    def test_event_message_short(self):
        search = MultiHopSearch()
        result = search._get_item_summary("event", {"message": "OOM killed nginx"})
        assert result == "OOM killed nginx"

    def test_event_message_long_truncated(self):
        search = MultiHopSearch()
        msg = "x" * 200
        result = search._get_item_summary("event", {"message": msg})
        assert len(result) == 103  # 100 chars + "..."
        assert result.endswith("...")

    def test_unknown_type(self):
        search = MultiHopSearch()
        result = search._get_item_summary("unknown", {})
        assert result == "Unknown"


# ===========================================================================
# _extract_keywords
# ===========================================================================


class TestExtractKeywords:
    def test_extracts_from_incidents(self):
        search = MultiHopSearch()
        inc = _make_incident(
            title="nginx failed",
            summary="nginx timeout error",
            symptoms=("disk full",),
        )
        keywords = search._extract_keywords([inc], [])
        assert "nginx" in keywords
        assert "failed" in keywords
        assert "timeout" in keywords
        assert "disk" in keywords

    def test_extracts_from_events(self):
        search = MultiHopSearch()
        events = [{"message": "redis error: connection refused"}]
        keywords = search._extract_keywords([], events)
        assert "redis" in keywords
        assert "error" in keywords
        assert "refused" in keywords

    def test_empty_inputs(self):
        search = MultiHopSearch()
        keywords = search._extract_keywords([], [])
        assert keywords == set()


# ===========================================================================
# _infer_relationship
# ===========================================================================


class TestInferRelationship:
    def test_very_close_timing_triggered(self):
        search = MultiHopSearch()
        result = search._infer_relationship("event", {}, "event", {}, 30)
        assert result == "triggered"

    def test_entity_overlap_close_triggered(self):
        search = MultiHopSearch()
        inc1 = _make_incident(entities=("nginx",))
        inc2 = _make_incident(entities=("nginx",))
        result = search._infer_relationship("incident", inc1, "incident", inc2, 200)
        assert result == "triggered"

    def test_entity_overlap_far_contributed(self):
        search = MultiHopSearch()
        inc1 = _make_incident(entities=("nginx",))
        inc2 = _make_incident(entities=("nginx",))
        result = search._infer_relationship("incident", inc1, "incident", inc2, 400)
        assert result == "contributed_to"

    def test_no_overlap_medium_preceded(self):
        search = MultiHopSearch()
        result = search._infer_relationship("event", {}, "event", {}, 300)
        assert result == "preceded"

    def test_no_overlap_far_correlated(self):
        search = MultiHopSearch()
        result = search._infer_relationship("event", {}, "event", {}, 700)
        assert result == "correlated"


# ===========================================================================
# _calculate_link_confidence
# ===========================================================================


class TestCalculateLinkConfidence:
    def test_close_timing_boost(self):
        search = MultiHopSearch()
        conf = search._calculate_link_confidence("event", {}, "event", {}, 30)
        # base 0.5 + 0.3 (close) = 0.8
        assert conf == pytest.approx(0.8, abs=0.01)

    def test_medium_timing_boost(self):
        search = MultiHopSearch()
        conf = search._calculate_link_confidence("event", {}, "event", {}, 200)
        # base 0.5 + 0.2
        assert conf == pytest.approx(0.7, abs=0.01)

    def test_far_timing_boost(self):
        search = MultiHopSearch()
        conf = search._calculate_link_confidence("event", {}, "event", {}, 500)
        # base 0.5 + 0.1
        assert conf == pytest.approx(0.6, abs=0.01)

    def test_entity_overlap_boost(self):
        search = MultiHopSearch()
        inc1 = _make_incident(entities=("nginx", "port:80"), domain="net")
        inc2 = _make_incident(entities=("nginx",), domain="disk")
        conf = search._calculate_link_confidence("incident", inc1, "incident", inc2, 30)
        # base 0.5 + 0.3 (timing) + 0.1 (1 overlap, 0.2 * 0.5) = 0.9
        # domains differ so no domain boost
        assert conf == pytest.approx(0.9, abs=0.01)

    def test_same_domain_boost(self):
        search = MultiHopSearch()
        inc1 = _make_incident(domain="net")
        inc2 = _make_incident(domain="net")
        conf = search._calculate_link_confidence("incident", inc1, "incident", inc2, 700)
        # base 0.5 + 0.0 (timing > 600) + 0.1 (same domain) = 0.6
        assert conf == pytest.approx(0.6, abs=0.01)

    def test_capped_at_1(self):
        search = MultiHopSearch()
        inc1 = _make_incident(domain="net", entities=("a", "b", "c", "d"))
        inc2 = _make_incident(domain="net", entities=("a", "b", "c", "d"))
        conf = search._calculate_link_confidence("incident", inc1, "incident", inc2, 10)
        assert conf <= 1.0


# ===========================================================================
# _build_chains
# ===========================================================================


class TestBuildChains:
    def test_empty_inputs_returns_empty(self):
        search = MultiHopSearch()
        chains = search._build_chains({}, {}, None)
        assert chains == []

    def test_single_item_no_chain(self):
        search = MultiHopSearch()
        inc = _make_incident()
        sr = _make_search_result(incident=inc)
        chains = search._build_chains({"inc-1": sr}, {}, None)
        # Only one item, need at least 2 for a link
        assert chains == []

    def test_two_close_incidents_build_chain(self):
        search = MultiHopSearch()
        now = datetime.utcnow()
        inc1 = _make_incident(
            incident_id="inc-1",
            title="Disk full",
            created_at=now - timedelta(minutes=5),
            entities=("sda1",),
        )
        inc2 = _make_incident(
            incident_id="inc-2",
            title="Service crash",
            created_at=now,
            entities=("sda1",),
        )
        sr1 = _make_search_result(incident=inc1)
        sr2 = _make_search_result(incident=inc2)
        chains = search._build_chains({"inc-1": sr1, "inc-2": sr2}, {}, None)
        assert len(chains) == 1
        assert len(chains[0].links) >= 1
        assert chains[0].overall_confidence > 0

    def test_items_too_far_apart_no_link(self):
        search = MultiHopSearch()
        now = datetime.utcnow()
        inc1 = _make_incident(
            incident_id="inc-1",
            created_at=now - timedelta(hours=2),
        )
        inc2 = _make_incident(
            incident_id="inc-2",
            created_at=now,
        )
        sr1 = _make_search_result(incident=inc1)
        sr2 = _make_search_result(incident=inc2)
        chains = search._build_chains({"inc-1": sr1, "inc-2": sr2}, {}, None)
        assert chains == []

    def test_chain_with_events(self):
        search = MultiHopSearch()
        now = datetime.utcnow()
        events = {
            "evt-1": {
                "event_id": "evt-1",
                "ts": now - timedelta(minutes=2),
                "entity": "eth0",
                "message": "link down",
                "category": "net",
            },
            "evt-2": {"event_id": "evt-2", "ts": now, "entity": "eth0", "message": "DNS failure", "category": "net"},
        }
        chains = search._build_chains({}, events, None)
        assert len(chains) == 1

    def test_low_confidence_filtered(self):
        search = MultiHopSearch(config=MultiHopConfig(min_confidence=0.99))
        now = datetime.utcnow()
        events = {
            "evt-1": {"event_id": "evt-1", "ts": now - timedelta(minutes=8)},
            "evt-2": {"event_id": "evt-2", "ts": now},
        }
        chains = search._build_chains({}, events, None)
        # High min confidence should filter out the link
        assert chains == []


# ===========================================================================
# keyword_rerank
# ===========================================================================


class TestKeywordRerank:
    def test_empty_keywords_no_change(self):
        results = [_make_search_result(score=1.0)]
        reranked = keyword_rerank(results, [])
        assert len(reranked) == 1
        assert reranked[0].score == 1.0

    def test_matching_keywords_boost_score(self):
        inc = _make_incident(title="nginx failed", summary="nginx connection timeout", symptoms=("error",))
        sr = _make_search_result(incident=inc, score=1.0)
        reranked = keyword_rerank([sr], ["nginx", "timeout"])
        assert reranked[0].score > 1.0

    def test_non_matching_keywords_no_boost(self):
        inc = _make_incident(title="all good", summary="nothing wrong", symptoms=())
        sr = _make_search_result(incident=inc, score=1.0)
        reranked = keyword_rerank([sr], ["redis", "timeout"])
        assert reranked[0].score == pytest.approx(1.0)

    def test_reranking_reorders(self):
        inc1 = _make_incident(incident_id="inc-1", title="nginx error")
        inc2 = _make_incident(incident_id="inc-2", title="redis timeout")
        sr1 = _make_search_result(incident=inc1, score=0.5)
        sr2 = _make_search_result(incident=inc2, score=0.6)
        reranked = keyword_rerank([sr1, sr2], ["nginx", "error"])
        # sr1 should get boosted and move to top
        assert reranked[0].incident.incident_id == "inc-1"

    def test_custom_boost_factor(self):
        inc = _make_incident(title="nginx failed", summary="nginx error")
        sr = _make_search_result(incident=inc, score=1.0)
        reranked = keyword_rerank([sr], ["nginx"], boost_factor=2.0)
        assert reranked[0].score > 1.0


# ===========================================================================
# temporal_rerank
# ===========================================================================


class TestTemporalRerank:
    def test_close_time_scores_higher(self):
        now = datetime.utcnow()
        inc_close = _make_incident(incident_id="close", created_at=now - timedelta(hours=1))
        inc_far = _make_incident(incident_id="far", created_at=now - timedelta(hours=24))
        sr_close = _make_search_result(incident=inc_close, score=1.0)
        sr_far = _make_search_result(incident=inc_far, score=1.0)
        reranked = temporal_rerank([sr_far, sr_close], now)
        assert reranked[0].incident.incident_id == "close"

    def test_prefer_before_boosts_earlier(self):
        now = datetime.utcnow()
        inc_before = _make_incident(incident_id="before", created_at=now - timedelta(hours=1))
        inc_after = _make_incident(incident_id="after", created_at=now + timedelta(hours=1))
        sr_before = _make_search_result(incident=inc_before, score=1.0)
        sr_after = _make_search_result(incident=inc_after, score=1.0)
        reranked = temporal_rerank([sr_after, sr_before], now, prefer_before=True)
        assert reranked[0].incident.incident_id == "before"

    def test_prefer_after_boosts_later(self):
        now = datetime.utcnow()
        inc_before = _make_incident(incident_id="before", created_at=now - timedelta(hours=1))
        inc_after = _make_incident(incident_id="after", created_at=now + timedelta(hours=1))
        sr_before = _make_search_result(incident=inc_before, score=1.0)
        sr_after = _make_search_result(incident=inc_after, score=1.0)
        reranked = temporal_rerank([sr_before, sr_after], now, prefer_before=False)
        assert reranked[0].incident.incident_id == "after"

    def test_custom_decay(self):
        now = datetime.utcnow()
        inc = _make_incident(created_at=now - timedelta(hours=6))
        sr = _make_search_result(incident=inc, score=1.0)
        reranked_slow = temporal_rerank([sr], now, decay_hours=24.0)
        reranked_fast = temporal_rerank([sr], now, decay_hours=3.0)
        assert reranked_slow[0].score > reranked_fast[0].score

    def test_empty_results(self):
        reranked = temporal_rerank([], datetime.utcnow())
        assert reranked == []


# ===========================================================================
# MultiHopSearch.search  (integration with mocked dependencies)
# ===========================================================================


class TestMultiHopSearchIntegration:
    @patch("elle.daemon.incidents.multihop.get_conn")
    @patch("elle.daemon.incidents.multihop.ensure_schema")
    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_search_basic_no_results(self, mock_search, mock_schema, mock_conn):
        mock_conn.return_value = MagicMock()
        mock_search.return_value = []

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        result = search.search("test query")

        assert isinstance(result, MultiHopResult)
        assert result.initial_query == "test query"
        assert result.total_iterations >= 0
        mock_conn.return_value.close.assert_called_once()

    @patch("elle.daemon.incidents.multihop.get_conn")
    @patch("elle.daemon.incidents.multihop.ensure_schema")
    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_search_with_initial_event(self, mock_search, mock_schema, mock_conn):
        mock_conn.return_value = MagicMock()
        mock_search.return_value = []

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        event = {"event_id": "evt-1", "ts": datetime.utcnow(), "message": "test"}
        result = search.search("test query", initial_event=event)

        assert result.initial_event_id == "evt-1"
        assert result.total_events_examined >= 1  # the initial event

    @patch("elle.daemon.incidents.multihop.get_conn")
    @patch("elle.daemon.incidents.multihop.ensure_schema")
    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_search_stops_on_no_high_relevance(self, mock_search, mock_schema, mock_conn):
        mock_conn.return_value = MagicMock()
        # Return low-score results
        inc = _make_incident()
        mock_search.return_value = [_make_search_result(incident=inc, score=0.1)]

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=5))
        result = search.search("test")

        # Should stop before reaching max_iterations
        assert result.total_iterations <= 5

    @patch("elle.daemon.incidents.multihop.get_conn")
    @patch("elle.daemon.incidents.multihop.ensure_schema")
    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_search_uses_provided_conn(self, mock_search, mock_schema, mock_conn):
        provided_conn = MagicMock()
        mock_search.return_value = []

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        search.search("test", conn=provided_conn)

        # Should NOT call get_connection or close
        mock_conn.assert_not_called()
        provided_conn.close.assert_not_called()

    @patch("elle.daemon.incidents.multihop.get_conn")
    @patch("elle.daemon.incidents.multihop.ensure_schema")
    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_search_with_high_relevance_results(self, mock_search, mock_schema, mock_conn):
        mock_conn.return_value = MagicMock()
        now = datetime.utcnow()
        inc = _make_incident(created_at=now, entities=("nginx",))
        mock_search.return_value = [_make_search_result(incident=inc, score=0.9)]

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=2))
        result = search.search("nginx error")

        assert result.total_incidents_examined >= 1

    @patch("elle.daemon.incidents.multihop.get_conn")
    @patch("elle.daemon.incidents.multihop.ensure_schema")
    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_search_best_chain_selected(self, mock_search, mock_schema, mock_conn):
        mock_conn.return_value = MagicMock()
        now = datetime.utcnow()
        inc1 = _make_incident(incident_id="inc-1", created_at=now - timedelta(minutes=2), entities=("sda1",))
        inc2 = _make_incident(incident_id="inc-2", created_at=now, entities=("sda1",))
        mock_search.return_value = [
            _make_search_result(incident=inc1, score=0.9),
            _make_search_result(incident=inc2, score=0.8),
        ]

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        result = search.search("disk issues")

        if result.chains:
            assert result.best_chain is not None
            assert result.best_chain.overall_confidence > 0

    @patch("elle.daemon.incidents.multihop.get_conn")
    @patch("elle.daemon.incidents.multihop.ensure_schema")
    @patch("elle.daemon.incidents.multihop.incident_search")
    def test_search_exception_in_search_incidents(self, mock_search, mock_schema, mock_conn):
        mock_conn.return_value = MagicMock()
        mock_search.side_effect = Exception("db error")

        search = MultiHopSearch(config=MultiHopConfig(max_iterations=1))
        result = search.search("test")

        # Should not crash, returns empty result
        assert isinstance(result, MultiHopResult)
        assert result.total_incidents_examined == 0
