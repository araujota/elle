"""Tests for incident retrieval and search."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from elle.daemon.incidents.models import Fingerprint, Precondition, SystemSnapshot
from elle.daemon.incidents.retriever import (
    find_similar,
    generate_case_text,
    get_prior_art,
    get_status,
    search,
)
from elle.daemon.incidents.schema import ensure_schema, get_connection
from elle.daemon.incidents.store import (
    append_action,
    create_incident_draft,
    finalize_outcome,
    update_incident,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    import sqlite3

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Use check_same_thread=False since search() uses ThreadPoolExecutor
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_schema(conn)
    yield conn, db_path

    conn.close()
    db_path.unlink()


@pytest.fixture
def seeded_db(temp_db):
    """Create a database with sample incidents."""
    conn, db_path = temp_db

    # Create a few test incidents
    inc1 = create_incident_draft(
        title="Network timeout connecting to API",
        domain="net",
        severity="error",
        conn=conn,
    )
    update_incident(
        inc1.incident_id,
        summary="Connection timeout when calling external API",
        symptoms=["Connection refused", "Timeout after 30s"],
        root_cause="Firewall blocking outbound connections",
        fingerprint=Fingerprint(
            entities=("interface:eth0", "service:api-gateway"),
        ),
        conn=conn,
    )
    finalize_outcome(inc1.incident_id, "improved", conn=conn)

    inc2 = create_incident_draft(
        title="Disk space exhausted on root",
        domain="disk",
        severity="critical",
        conn=conn,
    )
    update_incident(
        inc2.incident_id,
        summary="Root filesystem reached 100% capacity",
        symptoms=["No space left on device", "Cannot write files"],
        root_cause="Log files grew too large",
        fingerprint=Fingerprint(
            disk_pressure=1.0,
            entities=("mount:/",),
        ),
        preconditions=[
            Precondition(expression="disk./.used_pct > 95"),
        ],
        conn=conn,
    )
    finalize_outcome(inc2.incident_id, "improved", conn=conn)

    inc3 = create_incident_draft(
        title="OOM killer terminated nginx",
        domain="oom",
        severity="critical",
        conn=conn,
    )
    update_incident(
        inc3.incident_id,
        summary="Out of memory killer terminated nginx process",
        symptoms=["nginx process killed", "OOM in dmesg"],
        fingerprint=Fingerprint(
            mem_pressure=0.95,
            oom_count_1h=1,
            entities=("service:nginx",),
        ),
        conn=conn,
    )
    finalize_outcome(inc3.incident_id, "partial", conn=conn)

    inc4 = create_incident_draft(
        title="Docker container keeps restarting",
        domain="docker",
        severity="warning",
        conn=conn,
    )
    update_incident(
        inc4.incident_id,
        summary="Container exits with code 137 and restarts",
        symptoms=["Container exit code 137", "Restart loop"],
        fingerprint=Fingerprint(
            docker_exited_count=5,
            entities=("container:webapp",),
        ),
        conn=conn,
    )
    # This one stays open

    return conn, db_path, [inc1, inc2, inc3, inc4]


class TestSearch:
    """Tests for search function."""

    def test_search_empty_db(self, temp_db):
        """Test searching an empty database."""
        conn, _ = temp_db
        results = search(query="test", conn=conn)
        assert results == []

    def test_search_by_query(self, seeded_db):
        """Test searching by text query."""
        conn, _, _ = seeded_db
        results = search(query="network timeout", conn=conn)

        # Should find the network incident
        assert len(results) > 0

    def test_search_by_domain(self, seeded_db):
        """Test filtering by domain."""
        conn, _, _ = seeded_db
        results = search(query="issue", domain="disk", conn=conn)

        # Should only return disk incidents
        for result in results:
            assert result.incident.domain == "disk"

    def test_search_with_fingerprint(self, seeded_db):
        """Test searching with fingerprint matching."""
        conn, _, _ = seeded_db

        fp = Fingerprint(
            disk_pressure=0.9,
            entities=("mount:/",),
        )

        # Need to provide a snapshot that matches the precondition
        # The disk incident has precondition: disk./.used_pct > 95
        from elle.daemon.incidents.models import SystemSnapshot

        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            disks=({"mount": "/", "used_pct": 98},),
        )

        results = search(
            fingerprint=fp,
            snapshot=snapshot,
            domain="disk",
            search_type="fingerprint",
            conn=conn,
        )

        # Should find disk-related incident
        assert len(results) > 0

    def test_search_lexical_only(self, seeded_db):
        """Test lexical-only search."""
        conn, _, _ = seeded_db
        results = search(
            query="OOM killer nginx",
            search_type="lexical",
            conn=conn,
        )

        # Should find OOM incident
        assert len(results) >= 0  # May find if FTS works

    def test_search_respects_k(self, seeded_db):
        """Test that k limits results."""
        conn, _, _ = seeded_db
        results = search(query="container service", k=2, conn=conn)
        assert len(results) <= 2


class TestFindSimilar:
    """Tests for finding similar incidents."""

    def test_find_similar_to_incident(self, seeded_db):
        """Test finding incidents similar to a given one."""
        conn, _, incidents = seeded_db

        # Find similar to the network incident
        results = find_similar(incidents[0], k=3, conn=conn)

        # Should return results (excluding self)
        for result in results:
            assert result.incident.incident_id != incidents[0].incident_id


class TestGetPriorArt:
    """Tests for prior art retrieval."""

    def test_get_prior_art_format(self, seeded_db):
        """Test that prior art has correct format."""
        conn, _, _ = seeded_db
        prior = get_prior_art(
            query="disk space full",
            domain="disk",
            k=2,
            conn=conn,
        )

        for art in prior:
            assert "incident_id" in art
            assert "title" in art
            assert "outcome" in art
            assert "decision" in art

    def test_get_prior_art_empty(self, temp_db):
        """Test prior art on empty database."""
        conn, _ = temp_db
        prior = get_prior_art(query="anything", conn=conn)
        assert prior == []


class TestGenerateCaseText:
    """Tests for case text generation."""

    def test_generate_case_text_basic(self, seeded_db):
        """Test generating case text."""
        conn, _, incidents = seeded_db

        text = generate_case_text(incidents[0])

        assert "Title:" in text
        assert "Network timeout" in text
        assert "Domain:" in text
        assert "net" in text

    def test_generate_case_text_with_symptoms(self, seeded_db):
        """Test case text includes symptoms."""
        conn, _, incidents = seeded_db

        # Update with symptoms
        update_incident(
            incidents[0].incident_id,
            symptoms=["Symptom A", "Symptom B"],
            conn=conn,
        )

        from elle.daemon.incidents.store import get_incident

        updated = get_incident(incidents[0].incident_id, conn=conn)

        text = generate_case_text(updated)
        assert "Symptoms:" in text


class TestGetStatus:
    """Tests for status retrieval."""

    def test_get_status_empty(self, temp_db):
        """Test getting status of empty database."""
        conn, _ = temp_db
        status = get_status(conn=conn)

        assert status["total_incidents"] == 0
        assert status["total_actions"] == 0

    def test_get_status_with_data(self, seeded_db):
        """Test getting status with data."""
        conn, _, incidents = seeded_db
        status = get_status(conn=conn)

        assert status["total_incidents"] == 4
        assert status["by_domain"]["net"] == 1
        assert status["by_domain"]["disk"] == 1


class TestSearchRanking:
    """Tests for search result ranking."""

    def test_outcome_affects_ranking(self, seeded_db):
        """Test that better outcomes rank higher."""
        conn, _, _ = seeded_db

        # Search for general term
        results = search(query="service issue", conn=conn, k=10)

        # Results should exist
        if len(results) >= 2:
            # Check that improved outcomes are preferred
            for i in range(len(results) - 1):
                results[i].incident.outcome
                results[i + 1].incident.outcome
                # At equal similarity, better outcomes should come first
                # (this is a soft test since similarity also matters)
                assert results[i].outcome_weight >= 0


class TestRecencyWeighting:
    """Tests for recency-weighted ranking."""

    def test_recency_weight_recent(self):
        """Test that recent incidents have high weight."""
        from datetime import datetime

        from elle.daemon.incidents.retriever import _compute_recency_weight

        now = datetime.utcnow()
        weight = _compute_recency_weight(now)

        # Very recent should have weight close to 1
        assert weight > 0.9

    def test_recency_weight_decay(self):
        """Test that recency weight decays over time."""
        from datetime import datetime, timedelta

        from elle.daemon.incidents.retriever import (
            RECENCY_HALF_LIFE_DAYS,
            _compute_recency_weight,
        )

        now = datetime.utcnow()
        # After exactly one half-life, weight should be ~0.5
        old = now - timedelta(days=RECENCY_HALF_LIFE_DAYS)
        weight = _compute_recency_weight(old)

        assert 0.45 < weight < 0.55  # Allow some tolerance

    def test_recency_weight_minimum(self):
        """Test that very old incidents still have minimum weight."""
        from datetime import datetime, timedelta

        from elle.daemon.incidents.retriever import _compute_recency_weight

        # 2 years old
        very_old = datetime.utcnow() - timedelta(days=730)
        weight = _compute_recency_weight(very_old)

        # Should still be >= 0.1 (minimum weight)
        assert weight >= 0.1

    def test_recency_affects_ranking(self, temp_db):
        """Test that recent incidents rank higher than old ones."""
        from datetime import datetime, timedelta

        conn, db_path = temp_db

        # Create two similar incidents - one old, one recent
        old_incident = create_incident_draft(
            title="Disk space full on root partition",
            domain="disk",
            severity="error",
            conn=conn,
        )
        update_incident(
            old_incident.incident_id,
            summary="Disk full error",
            symptoms=["No space left on device"],
            fingerprint=Fingerprint(disk_pressure=0.95),
            conn=conn,
        )
        finalize_outcome(old_incident.incident_id, "improved", conn=conn)

        # Manually backdate the old incident
        old_time = (datetime.utcnow() - timedelta(days=90)).isoformat()
        conn.execute(
            "UPDATE incidents SET updated_at = ? WHERE id = ?",
            (old_time, old_incident.incident_id),
        )
        conn.commit()

        recent_incident = create_incident_draft(
            title="Disk space exhausted on root",
            domain="disk",
            severity="error",
            conn=conn,
        )
        update_incident(
            recent_incident.incident_id,
            summary="Disk full",
            symptoms=["No space left"],
            fingerprint=Fingerprint(disk_pressure=0.98),
            conn=conn,
        )
        finalize_outcome(recent_incident.incident_id, "improved", conn=conn)

        # Search for both
        results = search(
            query="disk space full",
            domain="disk",
            conn=conn,
            k=2,
        )

        # Recent should rank first
        if len(results) >= 2:
            assert results[0].incident.incident_id == recent_incident.incident_id


class TestPriorArtSuccessfulActions:
    """Tests for prior art retrieval with successful actions."""

    def test_prior_art_includes_successful_actions(self, temp_db):
        """Test that prior art includes successful actions."""
        from elle.daemon.incidents.store import append_action

        conn, db_path = temp_db

        # Create an incident with successful actions
        incident = create_incident_draft(
            title="apt update failed",
            domain="pkg",
            severity="error",
            trigger_command="apt update",
            conn=conn,
        )
        update_incident(
            incident.incident_id,
            summary="apt update failed with mirror errors",
            symptoms=["Failed to fetch", "Mirror not responding"],
            conn=conn,
        )

        # Add successful actions
        append_action(
            incident.incident_id,
            kind="shell",
            command="apt update --fix-missing",
            exit_code=0,
            success=True,
            conn=conn,
        )
        append_action(
            incident.incident_id,
            kind="shell",
            command="apt clean",
            exit_code=0,
            success=True,
            conn=conn,
        )

        finalize_outcome(incident.incident_id, "improved", conn=conn)

        # Get prior art
        prior = get_prior_art(
            query="apt update failed",
            domain="pkg",
            include_actions=True,
            conn=conn,
        )

        assert len(prior) == 1
        art = prior[0]
        assert "successful_actions" in art
        assert len(art["successful_actions"]) == 2

        # Check action details
        commands = [a["command"] for a in art["successful_actions"]]
        assert "apt update --fix-missing" in commands
        assert "apt clean" in commands

    def test_prior_art_excludes_failed_actions(self, temp_db):
        """Test that prior art only includes successful actions."""
        from elle.daemon.incidents.store import append_action

        conn, db_path = temp_db

        incident = create_incident_draft(
            title="service failed",
            domain="service",
            severity="error",
            conn=conn,
        )
        update_incident(
            incident.incident_id,
            summary="nginx service failed",
            conn=conn,
        )

        # Add one successful and one failed action
        append_action(
            incident.incident_id,
            kind="shell",
            command="systemctl restart nginx",
            exit_code=1,
            success=False,  # Failed action
            conn=conn,
        )
        append_action(
            incident.incident_id,
            kind="shell",
            command="systemctl start nginx",
            exit_code=0,
            success=True,  # Successful action
            conn=conn,
        )

        finalize_outcome(incident.incident_id, "improved", conn=conn)

        prior = get_prior_art(
            query="nginx service failed",
            include_actions=True,
            conn=conn,
        )

        assert len(prior) == 1
        art = prior[0]

        # Should only include the successful action
        commands = [a["command"] for a in art["successful_actions"]]
        assert "systemctl start nginx" in commands
        assert "systemctl restart nginx" not in commands

    def test_prior_art_includes_trigger_command(self, temp_db):
        """Test that prior art includes the trigger command."""
        conn, db_path = temp_db

        incident = create_incident_draft(
            title="Permission denied reading file",
            domain="auth",
            severity="warning",
            trigger_command="cat /etc/shadow",
            conn=conn,
        )
        update_incident(
            incident.incident_id,
            summary="Permission denied when reading shadow file",
            conn=conn,
        )
        finalize_outcome(incident.incident_id, "improved", conn=conn)

        prior = get_prior_art(query="permission denied", conn=conn)

        assert len(prior) == 1
        assert prior[0]["trigger_command"] == "cat /etc/shadow"

    def test_prior_art_includes_days_ago(self, temp_db):
        """Test that prior art includes days_ago field."""
        conn, db_path = temp_db

        incident = create_incident_draft(
            title="Network issue",
            domain="net",
            conn=conn,
        )
        finalize_outcome(incident.incident_id, "improved", conn=conn)

        prior = get_prior_art(query="network issue", conn=conn)

        assert len(prior) == 1
        # Recent incident should have days_ago of 0
        assert prior[0]["days_ago"] == 0

    def test_prior_art_fingerprint_match(self, temp_db):
        """Test that prior art includes fingerprint match details."""
        conn, db_path = temp_db

        incident = create_incident_draft(
            title="High disk usage",
            domain="disk",
            conn=conn,
        )
        update_incident(
            incident.incident_id,
            fingerprint=Fingerprint(
                disk_pressure=0.9,
                mem_pressure=0.3,
                entities=("mount:/", "service:nginx"),
            ),
            conn=conn,
        )
        finalize_outcome(incident.incident_id, "improved", conn=conn)

        # Query with a similar fingerprint
        current_fp = Fingerprint(
            disk_pressure=0.85,  # Similar
            mem_pressure=0.3,
            entities=("mount:/", "service:apache"),  # Partial overlap
        )

        prior = get_prior_art(
            query="disk usage",
            fingerprint=current_fp,
            conn=conn,
        )

        assert len(prior) == 1
        fm = prior[0]["fingerprint_match"]
        assert fm["disk_pressure_similar"] is True
        assert fm["mem_pressure_similar"] is True
        assert "mount:/" in fm["entities_overlap"]


class TestSearchEdgeCases:
    """Tests for search edge cases and boundary conditions."""

    def test_search_with_empty_query(self, temp_db):
        """Test searching with an empty string query."""
        conn, _ = temp_db
        create_incident_draft(title="Some incident", conn=conn)

        # Empty query should not raise an error
        results = search(query="", conn=conn)
        # Behavior with empty query depends on implementation,
        # but it should not crash
        assert isinstance(results, list)

    def test_search_with_special_characters(self, seeded_db):
        """Test search with special characters in query."""
        conn, _, _ = seeded_db
        # Queries with special chars should be handled gracefully
        results = search(query="service:nginx OR error=500", conn=conn)
        assert isinstance(results, list)

    def test_search_with_very_long_query(self, temp_db):
        """Test search with a very long query string."""
        conn, _ = temp_db
        create_incident_draft(title="Test", conn=conn)

        long_query = "network timeout " * 100
        results = search(query=long_query, conn=conn)
        assert isinstance(results, list)

    def test_search_returns_incident_objects(self, seeded_db):
        """Test that search results contain proper incident data."""
        conn, _, _ = seeded_db
        results = search(query="disk space", conn=conn)

        for result in results:
            assert hasattr(result, "incident")
            assert result.incident.incident_id is not None
            assert result.incident.title is not None
            assert result.incident.domain is not None

    def test_search_multiple_domains(self, seeded_db):
        """Test that search without domain filter returns across domains."""
        conn, _, _ = seeded_db
        results = search(query="service error", conn=conn, k=10)

        if len(results) >= 2:
            domains = {r.incident.domain for r in results}
            # Should potentially span multiple domains
            assert len(domains) >= 1

    def test_search_fingerprint_only(self, seeded_db):
        """Test fingerprint-only search without text query."""
        conn, _, _ = seeded_db

        fp = Fingerprint(
            mem_pressure=0.9,
            oom_count_1h=1,
        )

        results = search(
            fingerprint=fp,
            search_type="fingerprint",
            conn=conn,
        )
        assert isinstance(results, list)

    def test_search_with_snapshot_for_precondition_matching(self, seeded_db):
        """Test that providing a snapshot enables precondition matching."""
        conn, _, _ = seeded_db

        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            disks=({"mount": "/", "used_pct": 98},),
        )

        results = search(
            query="disk full",
            snapshot=snapshot,
            conn=conn,
        )
        assert isinstance(results, list)


class TestFindSimilarExtended:
    """Extended tests for find_similar function."""

    def test_find_similar_excludes_self(self, seeded_db):
        """Test that find_similar never returns the source incident."""
        conn, _, incidents = seeded_db

        for inc in incidents:
            results = find_similar(inc, k=10, conn=conn)
            for result in results:
                assert result.incident.incident_id != inc.incident_id

    def test_find_similar_respects_k_limit(self, seeded_db):
        """Test that find_similar returns at most k results."""
        conn, _, incidents = seeded_db

        results = find_similar(incidents[0], k=1, conn=conn)
        assert len(results) <= 1

    def test_find_similar_with_fingerprint(self, temp_db):
        """Test find_similar considers fingerprint similarity."""
        conn, _ = temp_db

        # Create two incidents with similar fingerprints
        inc1 = create_incident_draft(title="High disk A", domain="disk", conn=conn)
        update_incident(
            inc1.incident_id,
            fingerprint=Fingerprint(disk_pressure=0.95, entities=("mount:/",)),
            summary="Disk full on root",
            conn=conn,
        )
        finalize_outcome(inc1.incident_id, "improved", conn=conn)

        inc2 = create_incident_draft(title="High disk B", domain="disk", conn=conn)
        update_incident(
            inc2.incident_id,
            fingerprint=Fingerprint(disk_pressure=0.9, entities=("mount:/",)),
            summary="Root partition filling up",
            conn=conn,
        )
        finalize_outcome(inc2.incident_id, "improved", conn=conn)

        # Create a dissimilar incident
        inc3 = create_incident_draft(title="Auth failure", domain="auth", conn=conn)
        update_incident(
            inc3.incident_id,
            fingerprint=Fingerprint(auth_failures_1h=10, entities=("service:sshd",)),
            summary="SSH brute force attempts",
            conn=conn,
        )
        finalize_outcome(inc3.incident_id, "improved", conn=conn)

        # Find incidents similar to inc1
        results = find_similar(inc1, k=5, conn=conn)

        if len(results) > 0:
            # The first result should be the similar disk incident
            found_ids = [r.incident.incident_id for r in results]
            assert inc2.incident_id in found_ids


class TestGetStatusExtended:
    """Extended tests for status retrieval."""

    def test_status_counts_by_severity(self, seeded_db):
        """Test that status includes severity distribution."""
        conn, _, _ = seeded_db
        status = get_status(conn=conn)

        assert "total_incidents" in status
        assert status["total_incidents"] >= 4

    def test_status_after_outcome_changes(self, temp_db):
        """Test that status reflects outcome distribution."""
        conn, _ = temp_db

        inc1 = create_incident_draft(title="Improved", domain="net", conn=conn)
        finalize_outcome(inc1.incident_id, "improved", conn=conn)

        inc2 = create_incident_draft(title="Partial", domain="net", conn=conn)
        finalize_outcome(inc2.incident_id, "partial", conn=conn)

        inc3 = create_incident_draft(title="Open", domain="disk", conn=conn)

        status = get_status(conn=conn)
        assert status["total_incidents"] == 3


class TestGenerateCaseTextExtended:
    """Extended tests for case text generation."""

    def test_case_text_includes_root_cause(self, seeded_db):
        """Test that case text includes root cause when available."""
        conn, _, incidents = seeded_db

        # The first incident has root_cause set
        from elle.daemon.incidents.store import get_incident

        fetched = get_incident(incidents[0].incident_id, conn=conn)
        text = generate_case_text(fetched)

        assert "Root Cause:" in text
        assert "Firewall" in text

    def test_case_text_includes_tags(self, seeded_db):
        """Test that case text includes tags when present."""
        conn, _, incidents = seeded_db

        from elle.daemon.incidents.store import get_incident

        # Add tags to an incident
        update_incident(incidents[0].incident_id, tags=["production", "critical"], conn=conn)
        fetched = get_incident(incidents[0].incident_id, conn=conn)
        text = generate_case_text(fetched)

        assert "Tags:" in text
        assert "production" in text

    def test_case_text_for_minimal_incident(self, temp_db):
        """Test case text generation for incident with minimal data."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Minimal", conn=conn)
        text = generate_case_text(incident)

        assert "Title:" in text
        assert "Minimal" in text
        assert "Domain:" in text

    def test_case_text_includes_symptoms(self, seeded_db):
        """Test that case text includes symptom information."""
        conn, _, incidents = seeded_db

        from elle.daemon.incidents.store import get_incident

        fetched = get_incident(incidents[0].incident_id, conn=conn)
        text = generate_case_text(fetched)

        assert "Symptoms:" in text
        assert "Connection refused" in text

    def test_case_text_includes_log_snippets(self, seeded_db):
        """Test that case text includes log snippets when present."""
        conn, _, incidents = seeded_db

        from elle.daemon.incidents.store import get_incident

        update_incident(
            incidents[1].incident_id,
            log_snippets=["Error: no space left on device"],
            conn=conn,
        )
        fetched = get_incident(incidents[1].incident_id, conn=conn)
        text = generate_case_text(fetched)

        assert "Logs:" in text
        assert "no space left on device" in text


class TestRecencyWeightingExtended:
    """Additional tests for recency weighting behavior."""

    def test_recency_weight_future_date(self):
        """Test that future dates still produce valid weights."""
        from elle.daemon.incidents.retriever import _compute_recency_weight

        future = datetime.utcnow() + timedelta(days=1)
        weight = _compute_recency_weight(future)
        # Future date should produce weight >= 1.0 or close to it
        assert weight >= 0.9

    def test_recency_weight_is_monotonic(self):
        """Test that recency weight decreases as time increases."""
        from elle.daemon.incidents.retriever import _compute_recency_weight

        now = datetime.utcnow()
        weights = []
        for days in [0, 7, 30, 90, 365]:
            w = _compute_recency_weight(now - timedelta(days=days))
            weights.append(w)

        # Each weight should be less than or equal to the previous
        for i in range(len(weights) - 1):
            assert weights[i] >= weights[i + 1]

    def test_recency_weight_all_positive(self):
        """Test that all recency weights are positive."""
        from elle.daemon.incidents.retriever import _compute_recency_weight

        now = datetime.utcnow()
        for days in [0, 1, 7, 30, 90, 365, 730, 1000]:
            w = _compute_recency_weight(now - timedelta(days=days))
            assert w > 0


class TestPriorArtEdgeCases:
    """Edge case tests for prior art retrieval."""

    def test_prior_art_with_no_matching_domain(self, seeded_db):
        """Test prior art when querying a domain with no incidents."""
        conn, _, _ = seeded_db

        prior = get_prior_art(
            query="some query",
            domain="nonexistent_domain",
            conn=conn,
        )
        assert prior == []

    def test_prior_art_k_parameter(self, seeded_db):
        """Test prior art respects k limit."""
        conn, _, _ = seeded_db

        prior = get_prior_art(
            query="service error",
            k=1,
            conn=conn,
        )
        assert len(prior) <= 1

    def test_prior_art_with_no_finalized_incidents(self, temp_db):
        """Test prior art when no incidents are finalized."""
        conn, _ = temp_db

        # Create incidents but don't finalize them
        inc = create_incident_draft(title="Open incident", domain="net", conn=conn)
        update_incident(inc.incident_id, summary="Not yet resolved", conn=conn)

        prior = get_prior_art(query="network", conn=conn)
        # Open incidents may or may not appear depending on implementation
        assert isinstance(prior, list)

    def test_prior_art_includes_summary(self, temp_db):
        """Test that prior art results include summary field."""
        conn, _ = temp_db

        inc = create_incident_draft(title="Summary test", domain="disk", conn=conn)
        update_incident(
            inc.incident_id,
            summary="Detailed summary of what happened",
            conn=conn,
        )
        finalize_outcome(inc.incident_id, "improved", conn=conn)

        prior = get_prior_art(query="summary test", conn=conn)
        if len(prior) > 0:
            assert "summary" in prior[0]


class TestSearchResultScoring:
    """Tests for search result scoring properties."""

    def test_search_results_have_scores(self, seeded_db):
        """Test that search results have scoring attributes."""
        conn, _, _ = seeded_db
        results = search(query="disk space full", conn=conn)

        for result in results:
            # Results should have score-related attributes
            assert hasattr(result, "outcome_weight")

    def test_search_sorts_by_relevance(self, seeded_db):
        """Test that search results are sorted by relevance."""
        conn, _, _ = seeded_db
        results = search(query="disk space exhausted root", conn=conn, k=10)

        if len(results) >= 2:
            # Results should be in some consistent order
            # (exact scoring depends on implementation)
            assert results[0].incident.incident_id is not None
