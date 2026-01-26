"""Tests for Incident Vault store operations."""

import tempfile
from pathlib import Path

import pytest

from elle.daemon.incidents.models import Fingerprint, Precondition, SystemSnapshot
from elle.daemon.incidents.schema import ensure_schema, get_connection
from elle.daemon.incidents.store import (
    append_action,
    attach_snapshot,
    create_incident_draft,
    delete_incident,
    finalize_outcome,
    get_actions,
    get_embedding,
    get_incident,
    get_linked_events,
    get_snapshot,
    get_snapshots,
    link_events,
    list_incidents,
    update_incident,
    upsert_embedding,
)


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


class TestIncidentCRUD:
    """Tests for incident CRUD operations."""

    def test_create_incident_draft(self, temp_db):
        """Test creating an incident draft."""
        conn, _ = temp_db
        incident = create_incident_draft(
            title="Test incident",
            domain="net",
            severity="error",
            conn=conn,
        )

        assert incident is not None
        assert incident.incident_id is not None
        assert incident.title == "Test incident"
        assert incident.domain == "net"
        assert incident.severity == "error"
        assert incident.status == "open"

    def test_get_incident(self, temp_db):
        """Test getting an incident by ID."""
        conn, _ = temp_db
        created = create_incident_draft(title="Get test", conn=conn)

        fetched = get_incident(created.incident_id, conn=conn)
        assert fetched is not None
        assert fetched.incident_id == created.incident_id
        assert fetched.title == "Get test"

    def test_get_nonexistent_incident(self, temp_db):
        """Test getting a nonexistent incident."""
        conn, _ = temp_db
        fetched = get_incident("nonexistent-id", conn=conn)
        assert fetched is None

    def test_update_incident(self, temp_db):
        """Test updating an incident."""
        conn, _ = temp_db
        created = create_incident_draft(title="Update test", conn=conn)

        updated = update_incident(
            created.incident_id,
            summary="Updated summary",
            status="mitigated",
            symptoms=["Symptom 1", "Symptom 2"],
            conn=conn,
        )

        assert updated is not None
        assert updated.summary == "Updated summary"
        assert updated.status == "mitigated"
        assert updated.symptoms == ("Symptom 1", "Symptom 2")
        assert updated.updated_at > created.updated_at

    def test_update_with_fingerprint(self, temp_db):
        """Test updating incident with fingerprint."""
        conn, _ = temp_db
        created = create_incident_draft(title="Fingerprint test", conn=conn)

        fp = Fingerprint(
            disk_pressure=0.85,
            mem_pressure=0.5,
            entities=("service:nginx", "interface:eth0"),
        )
        updated = update_incident(
            created.incident_id,
            fingerprint=fp,
            conn=conn,
        )

        assert updated is not None
        assert updated.fingerprint.disk_pressure == 0.85
        assert "service:nginx" in updated.fingerprint.entities

    def test_update_with_preconditions(self, temp_db):
        """Test updating incident with preconditions."""
        conn, _ = temp_db
        created = create_incident_draft(title="Precond test", conn=conn)

        preconds = [
            Precondition(expression="disk./.used_pct > 90", description="High disk"),
            Precondition(expression="mem_pressure > 0.8", description="High memory"),
        ]
        updated = update_incident(
            created.incident_id,
            preconditions=preconds,
            conn=conn,
        )

        assert updated is not None
        assert len(updated.preconditions) == 2
        assert updated.preconditions[0].expression == "disk./.used_pct > 90"

    def test_delete_incident(self, temp_db):
        """Test deleting an incident."""
        conn, _ = temp_db
        created = create_incident_draft(title="Delete test", conn=conn)

        result = delete_incident(created.incident_id, conn=conn)
        assert result is True

        fetched = get_incident(created.incident_id, conn=conn)
        assert fetched is None

    def test_delete_nonexistent(self, temp_db):
        """Test deleting a nonexistent incident."""
        conn, _ = temp_db
        result = delete_incident("nonexistent-id", conn=conn)
        assert result is False

    def test_list_incidents(self, temp_db):
        """Test listing incidents."""
        conn, _ = temp_db
        create_incident_draft(title="Incident 1", domain="net", conn=conn)
        create_incident_draft(title="Incident 2", domain="disk", conn=conn)
        create_incident_draft(title="Incident 3", domain="net", conn=conn)

        all_incidents = list_incidents(conn=conn)
        assert len(all_incidents) == 3

        net_incidents = list_incidents(domain="net", conn=conn)
        assert len(net_incidents) == 2

    def test_list_with_pagination(self, temp_db):
        """Test listing with pagination."""
        conn, _ = temp_db
        for i in range(5):
            create_incident_draft(title=f"Incident {i}", conn=conn)

        page1 = list_incidents(limit=2, offset=0, conn=conn)
        assert len(page1) == 2

        page2 = list_incidents(limit=2, offset=2, conn=conn)
        assert len(page2) == 2


class TestActions:
    """Tests for action operations."""

    def test_append_action(self, temp_db):
        """Test appending an action."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Action test", conn=conn)

        action = append_action(
            incident.incident_id,
            kind="shell",
            command="apt update",
            exit_code=0,
            success=True,
            conn=conn,
        )

        assert action is not None
        assert action.incident_id == incident.incident_id
        assert action.step_index == 0
        assert action.kind == "shell"
        assert action.command == "apt update"
        assert action.success is True

    def test_append_multiple_actions(self, temp_db):
        """Test appending multiple actions."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Multi action", conn=conn)

        action1 = append_action(incident.incident_id, kind="shell", command="cmd1", conn=conn)
        action2 = append_action(incident.incident_id, kind="shell", command="cmd2", conn=conn)
        action3 = append_action(incident.incident_id, kind="verify", command="check", conn=conn)

        assert action1.step_index == 0
        assert action2.step_index == 1
        assert action3.step_index == 2

    def test_get_actions(self, temp_db):
        """Test getting actions for an incident."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Get actions", conn=conn)

        append_action(incident.incident_id, kind="shell", command="cmd1", conn=conn)
        append_action(incident.incident_id, kind="shell", command="cmd2", conn=conn)

        actions = get_actions(incident.incident_id, conn=conn)
        assert len(actions) == 2
        assert actions[0].step_index == 0
        assert actions[1].step_index == 1

    def test_action_with_payload(self, temp_db):
        """Test action with payload dict."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Payload test", conn=conn)

        append_action(
            incident.incident_id,
            kind="edit",
            payload={"file": "/etc/hosts", "content": "new line"},
            conn=conn,
        )

        fetched = get_actions(incident.incident_id, conn=conn)[0]
        assert fetched.payload["file"] == "/etc/hosts"


class TestSnapshots:
    """Tests for snapshot operations."""

    def test_attach_snapshot(self, temp_db):
        """Test attaching a snapshot."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Snapshot test", conn=conn)

        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0-41-generic",
            uptime_sec=3600,
            cpu_load=(1.0, 0.8, 0.5),
            mem_total_mb=16384,
            mem_free_mb=8192,
            mem_available_mb=10240,
        )

        result = attach_snapshot(
            incident.incident_id,
            "pre",
            snapshot,
            conn=conn,
        )

        assert result is not None
        assert result.which == "pre"
        assert result.snapshot.os == "Ubuntu 24.04"

    def test_get_snapshot(self, temp_db):
        """Test getting a specific snapshot."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Get snapshot", conn=conn)

        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=1800,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
        )

        attach_snapshot(incident.incident_id, "pre", snapshot, conn=conn)

        fetched = get_snapshot(incident.incident_id, "pre", conn=conn)
        assert fetched is not None
        assert fetched.snapshot.uptime_sec == 1800

    def test_get_all_snapshots(self, temp_db):
        """Test getting all snapshots for an incident."""
        conn, _ = temp_db
        incident = create_incident_draft(title="All snapshots", conn=conn)

        pre = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(1.0, 1.0, 1.0),
            mem_total_mb=1000,
            mem_free_mb=100,
            mem_available_mb=200,
        )
        post = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=200,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=1000,
            mem_free_mb=500,
            mem_available_mb=600,
        )

        attach_snapshot(incident.incident_id, "pre", pre, conn=conn)
        attach_snapshot(incident.incident_id, "post", post, conn=conn)

        snapshots = get_snapshots(incident.incident_id, conn=conn)
        assert "pre" in snapshots
        assert "post" in snapshots
        assert snapshots["pre"].snapshot.uptime_sec == 100
        assert snapshots["post"].snapshot.uptime_sec == 200


class TestEventLinks:
    """Tests for event linking."""

    def test_link_events(self, temp_db):
        """Test linking events to an incident."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Link test", conn=conn)

        count = link_events(
            incident.incident_id,
            ["event-1", "event-2", "event-3"],
            conn=conn,
        )

        assert count == 3

    def test_link_events_idempotent(self, temp_db):
        """Test that linking is idempotent."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Idempotent", conn=conn)

        link_events(incident.incident_id, ["event-1"], conn=conn)
        count = link_events(incident.incident_id, ["event-1", "event-2"], conn=conn)

        # Only event-2 should be new
        assert count == 1

    def test_get_linked_events(self, temp_db):
        """Test getting linked events."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Get links", conn=conn)

        link_events(incident.incident_id, ["event-1", "event-2"], conn=conn)

        events = get_linked_events(incident.incident_id, conn=conn)
        assert len(events) == 2
        assert "event-1" in events
        assert "event-2" in events


class TestEmbeddings:
    """Tests for embedding operations."""

    def test_upsert_embedding(self, temp_db):
        """Test storing an embedding."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Embed test", conn=conn)

        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        upsert_embedding(incident.incident_id, embedding, "test-model", conn=conn)

        fetched = get_embedding(incident.incident_id, conn=conn)
        assert fetched is not None
        assert len(fetched) == 5
        assert abs(fetched[0] - 0.1) < 0.0001

    def test_update_embedding(self, temp_db):
        """Test updating an embedding."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Update embed", conn=conn)

        upsert_embedding(incident.incident_id, [1.0, 2.0], "model1", conn=conn)
        upsert_embedding(incident.incident_id, [3.0, 4.0], "model2", conn=conn)

        fetched = get_embedding(incident.incident_id, conn=conn)
        assert abs(fetched[0] - 3.0) < 0.0001


class TestOutcome:
    """Tests for outcome finalization."""

    def test_finalize_improved(self, temp_db):
        """Test finalizing with improved outcome."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Outcome test", conn=conn)

        result = finalize_outcome(
            incident.incident_id,
            outcome="improved",
            verification_steps=["check 1", "check 2"],
            root_cause="Configuration error",
            conn=conn,
        )

        assert result is not None
        assert result.outcome == "improved"
        assert result.status == "resolved"
        assert result.root_cause == "Configuration error"
        assert result.time_to_resolve_sec is not None

    def test_finalize_partial(self, temp_db):
        """Test finalizing with partial outcome."""
        conn, _ = temp_db
        incident = create_incident_draft(title="Partial test", conn=conn)

        result = finalize_outcome(
            incident.incident_id,
            outcome="partial",
            conn=conn,
        )

        assert result.outcome == "partial"
        assert result.status == "mitigated"
        assert result.time_to_mitigate_sec is not None
