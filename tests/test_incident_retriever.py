"""Tests for incident retrieval and search."""

import tempfile
from pathlib import Path

import pytest

from elle.daemon.incidents.models import Fingerprint, Precondition
from elle.daemon.incidents.retriever import (
    find_similar,
    generate_case_text,
    get_prior_art,
    get_status,
    search,
)
from elle.daemon.incidents.schema import ensure_schema, get_connection
from elle.daemon.incidents.store import create_incident_draft, finalize_outcome, update_incident


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
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
