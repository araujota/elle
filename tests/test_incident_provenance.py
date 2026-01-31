"""Tests for incident provenance (trust surfaces) functionality.

Tests the new provenance tracking, decision record storage,
and config file state tracking.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from elle.daemon.incidents.models import (
    ConfidenceBreakdown,
    ConfigFileState,
    DecisionRecord,
    IncidentCitation,
    ManPageCitation,
    Provenance,
    TelemetryCitation,
)
from elle.daemon.incidents.schema import ensure_schema
from elle.daemon.incidents.store import (
    create_incident_draft,
    get_config_state_by_path,
    get_config_states,
    get_decision_record,
    store_config_state,
    store_decision_record,
)


@pytest.fixture
def test_db(incidents_conn):
    """Provide an incidents-schema connection for testing."""
    return incidents_conn


class TestProvenanceModels:
    """Tests for provenance Pydantic models."""

    def test_man_page_citation_creation(self):
        """Test creating a ManPageCitation."""
        citation = ManPageCitation(
            name="systemctl",
            section="1",
            snippet="systemctl - Control the systemd system and service manager",
            match_section="DESCRIPTION",
            relevance_score=0.85,
        )
        assert citation.name == "systemctl"
        assert citation.section == "1"
        assert citation.relevance_score == 0.85

    def test_man_page_citation_frozen(self):
        """Test that ManPageCitation is immutable."""
        citation = ManPageCitation(
            name="systemctl",
            section="1",
            snippet="test",
        )
        with pytest.raises(Exception):
            citation.name = "other"  # type: ignore

    def test_incident_citation_creation(self):
        """Test creating an IncidentCitation."""
        citation = IncidentCitation(
            incident_id="abc-123",
            title="SSH connection refused",
            outcome="improved",
            similarity_score=0.75,
            match_type="semantic",
            successful_actions=("systemctl restart sshd", "ufw allow 22"),
        )
        assert citation.incident_id == "abc-123"
        assert citation.outcome == "improved"
        assert len(citation.successful_actions) == 2

    def test_telemetry_citation_creation(self):
        """Test creating a TelemetryCitation."""
        citation = TelemetryCitation(
            event_ids=("evt-1", "evt-2"),
            event_summaries=("OOM killed nginx", "Memory pressure high"),
            trigger_time=datetime.utcnow(),
        )
        assert len(citation.event_ids) == 2
        assert len(citation.event_summaries) == 2

    def test_provenance_creation(self):
        """Test creating a Provenance with all fields."""
        man_citation = ManPageCitation(
            name="nginx",
            section="8",
            snippet="nginx - HTTP and reverse proxy server",
        )
        incident_citation = IncidentCitation(
            incident_id="inc-1",
            title="Nginx restart",
            outcome="improved",
            similarity_score=0.8,
            match_type="hybrid",
        )
        provenance = Provenance(
            man_pages=(man_citation,),
            prior_incidents=(incident_citation,),
            primary_source="incident_vault",
        )
        assert len(provenance.man_pages) == 1
        assert len(provenance.prior_incidents) == 1
        assert provenance.primary_source == "incident_vault"

    def test_provenance_summary(self):
        """Test Provenance.summary() generation."""
        # Empty provenance
        empty = Provenance()
        assert "LLM" in empty.summary()

        # With man page
        with_man = Provenance(
            man_pages=(
                ManPageCitation(
                    name="systemctl",
                    section="1",
                    snippet="test",
                ),
            ),
        )
        assert "man systemctl(1)" in with_man.summary()

        # With prior incident
        with_prior = Provenance(
            prior_incidents=(
                IncidentCitation(
                    incident_id="1",
                    title="Test",
                    outcome="improved",
                    similarity_score=0.8,
                    match_type="semantic",
                ),
            ),
        )
        assert "similar incident" in with_prior.summary()


class TestConfigFileState:
    """Tests for ConfigFileState model."""

    def test_config_file_state_creation(self):
        """Test creating a ConfigFileState."""
        state = ConfigFileState(
            path="/etc/ssh/sshd_config",
            sha256_before="abc123",
            sha256_after="def456",
            size_bytes=1024,
            mtime=datetime.utcnow(),
            backup_path="/var/lib/elle/backups/ssh/20240101",
        )
        assert state.path == "/etc/ssh/sshd_config"
        assert state.sha256_before == "abc123"
        assert state.sha256_after == "def456"

    def test_config_file_state_defaults(self):
        """Test ConfigFileState defaults."""
        state = ConfigFileState(path="/etc/hosts")
        assert state.sha256_before is None
        assert state.sha256_after is None
        assert state.size_bytes == 0
        assert state.mtime is None
        assert state.backup_path is None


class TestConfidenceBreakdown:
    """Tests for ConfidenceBreakdown model."""

    def test_confidence_breakdown_creation(self):
        """Test creating a ConfidenceBreakdown."""
        conf = ConfidenceBreakdown(
            overall=0.85,
            from_man_vault=0.3,
            from_incident_vault=0.4,
            from_llm=0.35,
            dominant_tier="semantic",
        )
        assert conf.overall == 0.85
        assert conf.dominant_tier == "semantic"

    def test_confidence_breakdown_defaults(self):
        """Test ConfidenceBreakdown defaults."""
        conf = ConfidenceBreakdown(overall=0.5)
        assert conf.from_man_vault == 0.0
        assert conf.from_incident_vault == 0.0
        assert conf.from_telemetry == 0.0
        assert conf.from_llm == 0.0
        assert conf.dominant_tier == "llm"


class TestDecisionRecord:
    """Tests for DecisionRecord model."""

    def test_decision_record_creation(self):
        """Test creating a DecisionRecord."""
        confidence = ConfidenceBreakdown(overall=0.75)
        provenance = Provenance()
        record = DecisionRecord(
            chosen_approach="Restart the SSH service",
            rationale="The service is not responding",
            confidence=confidence,
            provenance=provenance,
            planned_commands=("systemctl restart sshd",),
        )
        assert record.chosen_approach == "Restart the SSH service"
        assert record.rationale == "The service is not responding"
        assert len(record.planned_commands) == 1

    def test_decision_record_defaults(self):
        """Test DecisionRecord defaults."""
        record = DecisionRecord(
            chosen_approach="Test",
            confidence=ConfidenceBreakdown(overall=0.5),
            provenance=Provenance(),
        )
        assert record.rationale == ""
        assert record.planned_commands == ()
        assert record.decided_at is not None


class TestDecisionRecordStorage:
    """Tests for decision record database operations."""

    def test_store_and_retrieve_decision_record(self, test_db):
        """Test storing and retrieving a decision record."""
        # Create an incident first
        incident = create_incident_draft(
            title="Test incident",
            conn=test_db,
        )

        # Create decision record
        confidence = ConfidenceBreakdown(
            overall=0.8,
            from_man_vault=0.2,
            from_incident_vault=0.3,
            from_llm=0.4,
            dominant_tier="semantic",
        )
        provenance = Provenance(
            man_pages=(
                ManPageCitation(
                    name="systemctl",
                    section="1",
                    snippet="Control services",
                    relevance_score=0.7,
                ),
            ),
            primary_source="man_vault",
        )
        record = DecisionRecord(
            chosen_approach="Restart the service",
            rationale="Service is not responding",
            confidence=confidence,
            provenance=provenance,
            planned_commands=("systemctl restart test",),
        )

        # Store it
        store_decision_record(
            incident_id=incident.incident_id,
            decision_record=record,
            conn=test_db,
        )

        # Retrieve it
        retrieved = get_decision_record(
            incident_id=incident.incident_id,
            conn=test_db,
        )

        assert retrieved is not None
        assert retrieved.chosen_approach == "Restart the service"
        assert retrieved.rationale == "Service is not responding"
        assert retrieved.confidence.overall == 0.8
        assert retrieved.confidence.dominant_tier == "semantic"
        assert retrieved.provenance.primary_source == "man_vault"
        assert len(retrieved.provenance.man_pages) == 1
        assert retrieved.provenance.man_pages[0].name == "systemctl"
        assert len(retrieved.planned_commands) == 1

    def test_decision_record_not_found(self, test_db):
        """Test retrieving non-existent decision record."""
        record = get_decision_record("nonexistent-id", conn=test_db)
        assert record is None

    def test_decision_record_replace(self, test_db):
        """Test that storing a new record replaces the old one."""
        incident = create_incident_draft(
            title="Test incident",
            conn=test_db,
        )

        # Store first record
        record1 = DecisionRecord(
            chosen_approach="First approach",
            confidence=ConfidenceBreakdown(overall=0.5),
            provenance=Provenance(),
        )
        store_decision_record(incident.incident_id, record1, conn=test_db)

        # Store second record
        record2 = DecisionRecord(
            chosen_approach="Second approach",
            confidence=ConfidenceBreakdown(overall=0.9),
            provenance=Provenance(),
        )
        store_decision_record(incident.incident_id, record2, conn=test_db)

        # Should get second record
        retrieved = get_decision_record(incident.incident_id, conn=test_db)
        assert retrieved is not None
        assert retrieved.chosen_approach == "Second approach"
        assert retrieved.confidence.overall == 0.9


class TestConfigStateStorage:
    """Tests for config state database operations."""

    def test_store_and_retrieve_config_state(self, test_db):
        """Test storing and retrieving a config state."""
        incident = create_incident_draft(
            title="Config change",
            conn=test_db,
        )

        state = ConfigFileState(
            path="/etc/ssh/sshd_config",
            sha256_before="abc123",
            sha256_after="def456",
            size_bytes=2048,
        )

        store_config_state(
            incident_id=incident.incident_id,
            which="post",
            config_state=state,
            conn=test_db,
        )

        states = get_config_states(incident.incident_id, conn=test_db)
        assert len(states) == 1
        assert states[0].path == "/etc/ssh/sshd_config"
        assert states[0].sha256_before == "abc123"
        assert states[0].sha256_after == "def456"

    def test_config_state_filter_by_which(self, test_db):
        """Test filtering config states by pre/post."""
        incident = create_incident_draft(
            title="Config change",
            conn=test_db,
        )

        pre_state = ConfigFileState(
            path="/etc/hosts",
            sha256_before=None,
            sha256_after="aaa",
        )
        post_state = ConfigFileState(
            path="/etc/hosts",
            sha256_before="aaa",
            sha256_after="bbb",
        )

        store_config_state(incident.incident_id, "pre", pre_state, conn=test_db)
        store_config_state(incident.incident_id, "post", post_state, conn=test_db)

        # Get all
        all_states = get_config_states(incident.incident_id, conn=test_db)
        assert len(all_states) == 2

        # Get pre only
        pre_states = get_config_states(incident.incident_id, which="pre", conn=test_db)
        assert len(pre_states) == 1
        assert pre_states[0].sha256_after == "aaa"

        # Get post only
        post_states = get_config_states(incident.incident_id, which="post", conn=test_db)
        assert len(post_states) == 1
        assert post_states[0].sha256_after == "bbb"

    def test_config_state_by_path(self, test_db):
        """Test retrieving config states by path across incidents."""
        path = "/etc/nginx/nginx.conf"

        # Create two incidents
        inc1 = create_incident_draft("Nginx config 1", conn=test_db)
        inc2 = create_incident_draft("Nginx config 2", conn=test_db)

        state1 = ConfigFileState(path=path, sha256_after="hash1")
        state2 = ConfigFileState(path=path, sha256_after="hash2")

        store_config_state(inc1.incident_id, "post", state1, conn=test_db)
        store_config_state(inc2.incident_id, "post", state2, conn=test_db)

        # Get history for path
        history = get_config_state_by_path(path, conn=test_db)
        assert len(history) == 2

        # Most recent first
        assert history[0][2].sha256_after == "hash2"
        assert history[1][2].sha256_after == "hash1"


class TestSchemaVersion:
    """Tests for schema versioning and migration."""

    def test_new_tables_exist(self, test_db):
        """Test that v2 tables exist."""
        # Check decision_records table
        row = test_db.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'decision_records'"
        ).fetchone()
        assert row is not None

        # Check config_states table
        row = test_db.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'config_states'").fetchone()
        assert row is not None

    def test_fresh_schema_has_all_tables(self, test_db):
        """Test that fresh schema has all v2 tables."""
        ensure_schema(test_db)

        rows = test_db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'incidents'"
        ).fetchall()
        tables = {row["table_name"] for row in rows}

        expected = {
            "incidents",
            "incident_actions",
            "incident_snapshots",
            "incident_events",
            "incident_embeddings",
            "decision_records",
            "config_states",
            "_meta",
        }
        assert expected.issubset(tables)
