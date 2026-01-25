"""Tests for narrative and multi-hop search models."""

from datetime import datetime, timedelta

import pytest

from elle.daemon.incidents.narrative import (
    CausalChain,
    CausalLink,
    MultiHopConfig,
    MultiHopResult,
    Narrative,
    SearchHop,
)


class TestCausalLink:
    """Tests for CausalLink model."""

    def test_create_causal_link(self):
        """Test creating a causal link."""
        now = datetime.utcnow()
        link = CausalLink(
            cause_type="event",
            cause_id="evt-001",
            cause_summary="Disk reached 95% capacity",
            cause_timestamp=now - timedelta(minutes=5),
            effect_type="incident",
            effect_id="inc-001",
            effect_summary="PostgreSQL failed to write WAL",
            effect_timestamp=now,
            relationship="triggered",
            confidence=0.9,
            evidence=("disk:/ at 95%", "WAL write error in logs"),
            temporal_delta_sec=300,
        )

        assert link.cause_type == "event"
        assert link.effect_type == "incident"
        assert link.relationship == "triggered"
        assert link.confidence == 0.9
        assert len(link.evidence) == 2
        assert link.temporal_delta_sec == 300

    def test_causal_link_defaults(self):
        """Test CausalLink default values."""
        now = datetime.utcnow()
        link = CausalLink(
            cause_type="condition",
            cause_id="cond-001",
            cause_summary="High memory pressure",
            cause_timestamp=now,
            effect_type="symptom",
            effect_id="sym-001",
            effect_summary="Application slowdown",
            effect_timestamp=now,
        )

        assert link.relationship == "correlated"
        assert link.confidence == 0.5
        assert link.evidence == ()
        assert link.temporal_delta_sec == 0

    def test_causal_link_frozen(self):
        """Test that CausalLink is immutable."""
        now = datetime.utcnow()
        link = CausalLink(
            cause_type="event",
            cause_id="evt-001",
            cause_summary="Test",
            cause_timestamp=now,
            effect_type="symptom",
            effect_id="sym-001",
            effect_summary="Test",
            effect_timestamp=now,
        )

        with pytest.raises(Exception):
            link.confidence = 0.8  # Should raise


class TestCausalChain:
    """Tests for CausalChain model."""

    def test_create_causal_chain(self):
        """Test creating a causal chain."""
        now = datetime.utcnow()
        link1 = CausalLink(
            cause_type="event",
            cause_id="evt-001",
            cause_summary="Disk full",
            cause_timestamp=now - timedelta(minutes=10),
            effect_type="event",
            effect_id="evt-002",
            effect_summary="Journal write failed",
            effect_timestamp=now - timedelta(minutes=5),
            relationship="triggered",
            confidence=0.9,
        )
        link2 = CausalLink(
            cause_type="event",
            cause_id="evt-002",
            cause_summary="Journal write failed",
            cause_timestamp=now - timedelta(minutes=5),
            effect_type="incident",
            effect_id="inc-001",
            effect_summary="PostgreSQL crashed",
            effect_timestamp=now,
            relationship="triggered",
            confidence=0.85,
        )

        chain = CausalChain(
            chain_id="chain-001",
            root_cause=link1,
            links=(link1, link2),
            final_symptom="PostgreSQL crashed",
            overall_confidence=0.765,  # 0.9 * 0.85
            search_iterations=3,
            earliest_timestamp=now - timedelta(minutes=10),
            latest_timestamp=now,
        )

        assert chain.chain_id == "chain-001"
        assert len(chain.links) == 2
        assert chain.final_symptom == "PostgreSQL crashed"
        assert chain.search_iterations == 3

    def test_compute_overall_confidence(self):
        """Test computing overall confidence."""
        now = datetime.utcnow()
        links = tuple([
            CausalLink(
                cause_type="event",
                cause_id=f"evt-{i}",
                cause_summary=f"Event {i}",
                cause_timestamp=now,
                effect_type="event",
                effect_id=f"evt-{i+1}",
                effect_summary=f"Event {i+1}",
                effect_timestamp=now,
                confidence=0.8,
            )
            for i in range(3)
        ])

        chain = CausalChain(
            chain_id="test",
            links=links,
        )

        # 0.8 * 0.8 * 0.8 = 0.512
        assert abs(chain.compute_overall_confidence() - 0.512) < 0.001

    def test_empty_chain_confidence(self):
        """Test that empty chain has zero confidence."""
        chain = CausalChain(chain_id="empty")
        assert chain.compute_overall_confidence() == 0.0


class TestNarrative:
    """Tests for Narrative model."""

    def test_create_narrative(self):
        """Test creating a narrative."""
        chain = CausalChain(
            chain_id="chain-001",
            final_symptom="Service unavailable",
        )

        narrative = Narrative(
            chain=chain,
            summary="PostgreSQL failed because disk reached 95% capacity, preventing WAL writes.",
            timeline=(
                "10:00 - Disk usage reached 95%",
                "10:05 - WAL write errors started",
                "10:10 - PostgreSQL crashed",
            ),
            explanation="The root cause was insufficient disk space on the primary partition...",
            keywords=("disk", "postgresql", "wal", "storage"),
            model_used="qwen2.5:7b",
        )

        assert "PostgreSQL" in narrative.summary
        assert len(narrative.timeline) == 3
        assert len(narrative.keywords) == 4
        assert narrative.model_used == "qwen2.5:7b"


class TestSearchHop:
    """Tests for SearchHop model."""

    def test_create_search_hop(self):
        """Test creating a search hop."""
        now = datetime.utcnow()
        hop = SearchHop(
            iteration=0,
            query="postgresql crash",
            search_type="incident",
            time_window_start=now - timedelta(hours=24),
            time_window_end=now,
            results_count=15,
            high_relevance_count=3,
            entities_discovered=("service:postgresql", "disk:/"),
            keywords_extracted=("wal", "checkpoint", "disk full"),
        )

        assert hop.iteration == 0
        assert hop.search_type == "incident"
        assert hop.results_count == 15
        assert len(hop.entities_discovered) == 2


class TestMultiHopResult:
    """Tests for MultiHopResult model."""

    def test_create_multihop_result(self):
        """Test creating a multi-hop result."""
        chain = CausalChain(
            chain_id="best",
            overall_confidence=0.85,
        )

        result = MultiHopResult(
            initial_query="why did postgres crash",
            chains=(chain,),
            best_chain=chain,
            hops=(),
            total_iterations=3,
            total_events_examined=50,
            total_incidents_examined=10,
        )

        assert result.initial_query == "why did postgres crash"
        assert result.best_chain is not None
        assert result.total_iterations == 3


class TestMultiHopConfig:
    """Tests for MultiHopConfig model."""

    def test_default_config(self):
        """Test default configuration values."""
        config = MultiHopConfig()
        assert config.max_iterations == 5
        assert config.time_window_hours == 24
        assert config.min_confidence == 0.5
        assert config.max_results_per_hop == 20
        assert config.expand_entities is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = MultiHopConfig(
            max_iterations=3,
            time_window_hours=6,
            min_confidence=0.7,
            expand_entities=False,
        )

        assert config.max_iterations == 3
        assert config.time_window_hours == 6
        assert config.expand_entities is False

    def test_config_validation(self):
        """Test configuration validation."""
        with pytest.raises(Exception):
            MultiHopConfig(max_iterations=0)  # Must be >= 1

        with pytest.raises(Exception):
            MultiHopConfig(min_confidence=1.5)  # Must be <= 1.0
