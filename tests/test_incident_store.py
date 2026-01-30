"""Tests for Incident Vault store operations."""

from __future__ import annotations

from datetime import datetime

import pytest

from elle.daemon.incidents.models import (
    ConfidenceBreakdown,
    ConfigFileState,
    DecisionRecord,
    Fingerprint,
    Precondition,
    Provenance,
    SystemSnapshot,
)
from elle.daemon.incidents.store import (
    append_action,
    attach_control_surface_snapshot,
    attach_snapshot,
    attach_telemetry_snapshot,
    create_incident_draft,
    delete_incident,
    finalize_outcome,
    get_actions,
    get_all_embeddings,
    get_config_state_by_path,
    get_config_states,
    get_control_surface_snapshot,
    get_control_surface_snapshots,
    get_decision_record,
    get_embedding,
    get_incident,
    get_incident_count,
    get_action_count,
    get_snapshot_count,
    get_incidents_without_embeddings,
    get_linked_events,
    get_prepared_plan,
    get_snapshot,
    get_snapshots,
    get_surface_hashes,
    get_telemetry_snapshot,
    get_telemetry_snapshots,
    link_events,
    list_incidents,
    store_config_state,
    store_decision_record,
    update_incident,
    upsert_embedding,
)


@pytest.fixture
def conn(incidents_conn):
    """Provide an incidents-schema connection for testing."""
    return incidents_conn


class TestIncidentCRUD:
    """Tests for incident CRUD operations."""

    def test_create_incident_draft(self, conn):
        """Test creating an incident draft."""
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

    def test_get_incident(self, conn):
        """Test getting an incident by ID."""
        created = create_incident_draft(title="Get test", conn=conn)

        fetched = get_incident(created.incident_id, conn=conn)
        assert fetched is not None
        assert fetched.incident_id == created.incident_id
        assert fetched.title == "Get test"

    def test_get_nonexistent_incident(self, conn):
        """Test getting a nonexistent incident."""
        fetched = get_incident("nonexistent-id", conn=conn)
        assert fetched is None

    def test_update_incident(self, conn):
        """Test updating an incident."""
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

    def test_update_with_fingerprint(self, conn):
        """Test updating incident with fingerprint."""
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

    def test_update_with_preconditions(self, conn):
        """Test updating incident with preconditions."""
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

    def test_delete_incident(self, conn):
        """Test deleting an incident."""
        created = create_incident_draft(title="Delete test", conn=conn)

        result = delete_incident(created.incident_id, conn=conn)
        assert result is True

        fetched = get_incident(created.incident_id, conn=conn)
        assert fetched is None

    def test_delete_nonexistent(self, conn):
        """Test deleting a nonexistent incident."""
        result = delete_incident("nonexistent-id", conn=conn)
        assert result is False

    def test_list_incidents(self, conn):
        """Test listing incidents."""
        create_incident_draft(title="Incident 1", domain="net", conn=conn)
        create_incident_draft(title="Incident 2", domain="disk", conn=conn)
        create_incident_draft(title="Incident 3", domain="net", conn=conn)

        all_incidents = list_incidents(conn=conn)
        assert len(all_incidents) == 3

        net_incidents = list_incidents(domain="net", conn=conn)
        assert len(net_incidents) == 2

    def test_list_with_pagination(self, conn):
        """Test listing with pagination."""
        for i in range(5):
            create_incident_draft(title=f"Incident {i}", conn=conn)

        page1 = list_incidents(limit=2, offset=0, conn=conn)
        assert len(page1) == 2

        page2 = list_incidents(limit=2, offset=2, conn=conn)
        assert len(page2) == 2


class TestActions:
    """Tests for action operations."""

    def test_append_action(self, conn):
        """Test appending an action."""
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

    def test_append_multiple_actions(self, conn):
        """Test appending multiple actions."""
        incident = create_incident_draft(title="Multi action", conn=conn)

        action1 = append_action(incident.incident_id, kind="shell", command="cmd1", conn=conn)
        action2 = append_action(incident.incident_id, kind="shell", command="cmd2", conn=conn)
        action3 = append_action(incident.incident_id, kind="verify", command="check", conn=conn)

        assert action1.step_index == 0
        assert action2.step_index == 1
        assert action3.step_index == 2

    def test_get_actions(self, conn):
        """Test getting actions for an incident."""
        incident = create_incident_draft(title="Get actions", conn=conn)

        append_action(incident.incident_id, kind="shell", command="cmd1", conn=conn)
        append_action(incident.incident_id, kind="shell", command="cmd2", conn=conn)

        actions = get_actions(incident.incident_id, conn=conn)
        assert len(actions) == 2
        assert actions[0].step_index == 0
        assert actions[1].step_index == 1

    def test_action_with_payload(self, conn):
        """Test action with payload dict."""
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

    def test_attach_snapshot(self, conn):
        """Test attaching a snapshot."""
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

    def test_get_snapshot(self, conn):
        """Test getting a specific snapshot."""
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

    def test_get_all_snapshots(self, conn):
        """Test getting all snapshots for an incident."""
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

    def test_link_events(self, conn):
        """Test linking events to an incident."""
        incident = create_incident_draft(title="Link test", conn=conn)

        count = link_events(
            incident.incident_id,
            ["event-1", "event-2", "event-3"],
            conn=conn,
        )

        assert count == 3

    def test_link_events_idempotent(self, conn):
        """Test that linking is idempotent."""
        incident = create_incident_draft(title="Idempotent", conn=conn)

        link_events(incident.incident_id, ["event-1"], conn=conn)
        count = link_events(incident.incident_id, ["event-1", "event-2"], conn=conn)

        # Only event-2 should be new
        assert count == 1

    def test_get_linked_events(self, conn):
        """Test getting linked events."""
        incident = create_incident_draft(title="Get links", conn=conn)

        link_events(incident.incident_id, ["event-1", "event-2"], conn=conn)

        events = get_linked_events(incident.incident_id, conn=conn)
        assert len(events) == 2
        assert "event-1" in events
        assert "event-2" in events


class TestEmbeddings:
    """Tests for embedding operations."""

    def test_upsert_embedding(self, conn):
        """Test storing an embedding."""
        incident = create_incident_draft(title="Embed test", conn=conn)

        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        upsert_embedding(incident.incident_id, embedding, "test-model", conn=conn)

        fetched = get_embedding(incident.incident_id, conn=conn)
        assert fetched is not None
        assert len(fetched) == 5
        assert abs(fetched[0] - 0.1) < 0.0001

    def test_update_embedding(self, conn):
        """Test updating an embedding."""
        incident = create_incident_draft(title="Update embed", conn=conn)

        upsert_embedding(incident.incident_id, [1.0, 2.0], "model1", conn=conn)
        upsert_embedding(incident.incident_id, [3.0, 4.0], "model2", conn=conn)

        fetched = get_embedding(incident.incident_id, conn=conn)
        assert abs(fetched[0] - 3.0) < 0.0001


class TestOutcome:
    """Tests for outcome finalization."""

    def test_finalize_improved(self, conn):
        """Test finalizing with improved outcome."""
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

    def test_finalize_partial(self, conn):
        """Test finalizing with partial outcome."""
        incident = create_incident_draft(title="Partial test", conn=conn)

        result = finalize_outcome(
            incident.incident_id,
            outcome="partial",
            conn=conn,
        )

        assert result.outcome == "partial"
        assert result.status == "mitigated"
        assert result.time_to_mitigate_sec is not None

    def test_finalize_no_change(self, conn):
        """Test finalizing with no_change outcome keeps open status."""
        incident = create_incident_draft(title="No change test", conn=conn)

        result = finalize_outcome(
            incident.incident_id,
            outcome="no_change",
            conn=conn,
        )

        assert result is not None
        assert result.outcome == "no_change"
        assert result.status == "open"
        assert result.time_to_resolve_sec is None

    def test_finalize_worse(self, conn):
        """Test finalizing with worse outcome keeps open status."""
        incident = create_incident_draft(title="Worse test", conn=conn)

        result = finalize_outcome(
            incident.incident_id,
            outcome="worse",
            conn=conn,
        )

        assert result is not None
        assert result.outcome == "worse"
        assert result.status == "open"

    def test_finalize_nonexistent_incident(self, conn):
        """Test finalizing a nonexistent incident returns None."""
        result = finalize_outcome("nonexistent-id", outcome="improved", conn=conn)
        assert result is None


class TestDecisionRecords:
    """Tests for decision record operations."""

    def test_store_and_retrieve_decision_record(self, conn):
        """Test storing and retrieving a decision record."""
        incident = create_incident_draft(title="Decision test", conn=conn)

        confidence = ConfidenceBreakdown(
            overall=0.85,
            from_man_vault=0.3,
            from_incident_vault=0.4,
            from_llm=0.15,
        )
        provenance = Provenance(primary_source="incident_vault")
        record = DecisionRecord(
            chosen_approach="Restart the service and clear cache",
            rationale="Prior incidents with similar fingerprint were resolved this way",
            confidence=confidence,
            provenance=provenance,
            planned_commands=("systemctl restart nginx", "rm -rf /var/cache/nginx/*"),
        )

        store_decision_record(incident.incident_id, record, conn=conn)
        fetched = get_decision_record(incident.incident_id, conn=conn)

        assert fetched is not None
        assert fetched.chosen_approach == "Restart the service and clear cache"
        assert fetched.rationale == "Prior incidents with similar fingerprint were resolved this way"
        assert fetched.confidence.overall == 0.85
        assert fetched.confidence.from_man_vault == 0.3
        assert fetched.provenance.primary_source == "incident_vault"
        assert len(fetched.planned_commands) == 2
        assert "systemctl restart nginx" in fetched.planned_commands

    def test_decision_record_replaces_on_upsert(self, conn):
        """Test that storing a second decision record replaces the first."""
        incident = create_incident_draft(title="Replace decision", conn=conn)

        confidence = ConfidenceBreakdown(overall=0.5)
        provenance = Provenance()
        record1 = DecisionRecord(
            chosen_approach="First approach",
            confidence=confidence,
            provenance=provenance,
        )
        record2 = DecisionRecord(
            chosen_approach="Better approach",
            confidence=ConfidenceBreakdown(overall=0.9),
            provenance=provenance,
        )

        store_decision_record(incident.incident_id, record1, conn=conn)
        store_decision_record(incident.incident_id, record2, conn=conn)

        fetched = get_decision_record(incident.incident_id, conn=conn)
        assert fetched is not None
        assert fetched.chosen_approach == "Better approach"
        assert fetched.confidence.overall == 0.9

    def test_get_decision_record_not_found(self, conn):
        """Test that getting a nonexistent decision record returns None."""
        result = get_decision_record("nonexistent-id", conn=conn)
        assert result is None

    def test_decision_record_with_provenance(self, conn):
        """Test decision record with full provenance details."""
        incident = create_incident_draft(title="Provenance test", conn=conn)

        from elle.daemon.incidents.models import ManPageCitation, IncidentCitation

        provenance = Provenance(
            man_pages=(
                ManPageCitation(
                    name="systemctl",
                    section="1",
                    snippet="Restart a running service",
                    relevance_score=0.9,
                ),
            ),
            prior_incidents=(
                IncidentCitation(
                    incident_id="prior-001",
                    title="Similar nginx crash",
                    outcome="improved",
                    similarity_score=0.8,
                    match_type="fingerprint",
                    successful_actions=("systemctl restart nginx",),
                ),
            ),
            primary_source="man_vault",
        )
        record = DecisionRecord(
            chosen_approach="Restart nginx",
            confidence=ConfidenceBreakdown(overall=0.75),
            provenance=provenance,
        )

        store_decision_record(incident.incident_id, record, conn=conn)
        fetched = get_decision_record(incident.incident_id, conn=conn)

        assert fetched is not None
        assert len(fetched.provenance.man_pages) == 1
        assert fetched.provenance.man_pages[0].name == "systemctl"
        assert len(fetched.provenance.prior_incidents) == 1
        assert fetched.provenance.prior_incidents[0].title == "Similar nginx crash"
        assert fetched.provenance.primary_source == "man_vault"


class TestConfigState:
    """Tests for config state CRUD operations."""

    def test_store_and_retrieve_config_state(self, conn):
        """Test storing and retrieving config file state."""
        incident = create_incident_draft(title="Config test", conn=conn)

        config_state = ConfigFileState(
            path="/etc/nginx/nginx.conf",
            sha256_before="abc123",
            sha256_after="def456",
            size_bytes=2048,
            backup_path="/var/lib/elle/backups/nginx.conf.bak",
        )

        store_config_state(incident.incident_id, "pre", config_state, conn=conn)

        states = get_config_states(incident.incident_id, conn=conn)
        assert len(states) == 1
        assert states[0].path == "/etc/nginx/nginx.conf"
        assert states[0].sha256_before == "abc123"
        assert states[0].sha256_after == "def456"
        assert states[0].size_bytes == 2048
        assert states[0].backup_path == "/var/lib/elle/backups/nginx.conf.bak"

    def test_config_state_with_mtime(self, conn):
        """Test config state preserves modification time."""
        incident = create_incident_draft(title="Mtime test", conn=conn)

        now = datetime.utcnow()
        config_state = ConfigFileState(
            path="/etc/hosts",
            sha256_after="aabbcc",
            size_bytes=512,
            mtime=now,
        )

        store_config_state(incident.incident_id, "post", config_state, conn=conn)

        states = get_config_states(incident.incident_id, which="post", conn=conn)
        assert len(states) == 1
        assert states[0].mtime is not None

    def test_config_state_filter_by_which(self, conn):
        """Test filtering config states by pre/post."""
        incident = create_incident_draft(title="Filter test", conn=conn)

        pre_state = ConfigFileState(path="/etc/ssh/sshd_config", size_bytes=100)
        post_state = ConfigFileState(path="/etc/ssh/sshd_config", size_bytes=200, sha256_after="newvalue")

        store_config_state(incident.incident_id, "pre", pre_state, conn=conn)
        store_config_state(incident.incident_id, "post", post_state, conn=conn)

        pre_states = get_config_states(incident.incident_id, which="pre", conn=conn)
        assert len(pre_states) == 1
        assert pre_states[0].size_bytes == 100

        post_states = get_config_states(incident.incident_id, which="post", conn=conn)
        assert len(post_states) == 1
        assert post_states[0].size_bytes == 200

        all_states = get_config_states(incident.incident_id, conn=conn)
        assert len(all_states) == 2

    def test_config_state_by_path_across_incidents(self, conn):
        """Test tracking config file changes across incidents."""

        inc1 = create_incident_draft(title="Inc 1", conn=conn)
        inc2 = create_incident_draft(title="Inc 2", conn=conn)

        state1 = ConfigFileState(path="/etc/nginx/nginx.conf", sha256_before="v1", sha256_after="v2")
        state2 = ConfigFileState(path="/etc/nginx/nginx.conf", sha256_before="v2", sha256_after="v3")

        store_config_state(inc1.incident_id, "pre", state1, conn=conn)
        store_config_state(inc2.incident_id, "pre", state2, conn=conn)

        history = get_config_state_by_path("/etc/nginx/nginx.conf", conn=conn)
        assert len(history) == 2
        # Each entry is (incident_id, which, ConfigFileState)
        for entry in history:
            assert len(entry) == 3
            assert entry[2].path == "/etc/nginx/nginx.conf"


class TestTelemetrySnapshots:
    """Tests for telemetry snapshot operations (V4)."""

    def _make_telemetry_snapshot(self):
        """Create a minimal TelemetrySnapshot for testing."""
        from elle.daemon.incidents.telemetry_snapshot import (
            CPUPressure,
            DiskIOPressure,
            GPUMetrics,
            KernelHardwareSignals,
            MemoryPressure,
            NetworkLiveness,
            TelemetrySnapshot,
        )

        return TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.5, load_5m=1.0, load_15m=0.8),
            memory=MemoryPressure(total_mb=16384, available_mb=8192),
            disk=DiskIOPressure(),
            network=NetworkLiveness(),
            kernel=KernelHardwareSignals(),
            gpu=GPUMetrics(),
            hostname="testhost",
            uptime_sec=3600,
        )

    def test_attach_and_get_telemetry_snapshot(self, conn):
        """Test attaching and retrieving a telemetry snapshot."""
        incident = create_incident_draft(title="Telemetry test", conn=conn)

        snapshot = self._make_telemetry_snapshot()
        attach_telemetry_snapshot(incident.incident_id, "pre", snapshot, conn=conn)

        fetched = get_telemetry_snapshot(incident.incident_id, "pre", conn=conn)
        assert fetched is not None
        assert fetched.cpu.load_1m == 1.5
        assert fetched.memory.total_mb == 16384
        assert fetched.hostname == "testhost"

    def test_get_telemetry_snapshot_not_found(self, conn):
        """Test getting a nonexistent telemetry snapshot returns None."""
        incident = create_incident_draft(title="No telem", conn=conn)

        fetched = get_telemetry_snapshot(incident.incident_id, "pre", conn=conn)
        assert fetched is None

    def test_get_all_telemetry_snapshots(self, conn):
        """Test getting all telemetry snapshots for an incident."""
        incident = create_incident_draft(title="All telem", conn=conn)

        pre_snap = self._make_telemetry_snapshot()
        post_snap = self._make_telemetry_snapshot()

        attach_telemetry_snapshot(incident.incident_id, "pre", pre_snap, conn=conn)
        attach_telemetry_snapshot(incident.incident_id, "post", post_snap, conn=conn)

        snapshots = get_telemetry_snapshots(incident.incident_id, conn=conn)
        assert "pre" in snapshots
        assert "post" in snapshots

    def test_telemetry_snapshot_replace_on_same_which(self, conn):
        """Test that re-attaching a telemetry snapshot replaces the previous one."""
        incident = create_incident_draft(title="Replace telem", conn=conn)

        snap1 = self._make_telemetry_snapshot()
        attach_telemetry_snapshot(incident.incident_id, "pre", snap1, conn=conn)

        # Create a second snapshot with different data
        from elle.daemon.incidents.telemetry_snapshot import (
            CPUPressure,
            DiskIOPressure,
            GPUMetrics,
            KernelHardwareSignals,
            MemoryPressure,
            NetworkLiveness,
            TelemetrySnapshot,
        )

        snap2 = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=5.0, load_5m=4.0, load_15m=3.0),
            memory=MemoryPressure(total_mb=32768, available_mb=1024),
            disk=DiskIOPressure(),
            network=NetworkLiveness(),
            kernel=KernelHardwareSignals(),
            gpu=GPUMetrics(),
            hostname="replaced",
        )
        attach_telemetry_snapshot(incident.incident_id, "pre", snap2, conn=conn)

        fetched = get_telemetry_snapshot(incident.incident_id, "pre", conn=conn)
        assert fetched is not None
        assert fetched.cpu.load_1m == 5.0
        assert fetched.hostname == "replaced"


class TestControlSurfaceSnapshots:
    """Tests for control surface snapshot operations (V4)."""

    def _make_control_surface_snapshot(self):
        """Create a minimal ControlSurfaceSnapshot for testing."""
        from elle.daemon.incidents.control_surface import (
            ControlSurfaceSnapshot,
            HardwareIdentitySurface,
            KernelPolicySurface,
            NetworkControlSurface,
            PackageSurface,
            PrivilegeSurface,
            SchedulerSurface,
        )

        return ControlSurfaceSnapshot(
            packages=PackageSurface(),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(
                network_manager="NetworkManager",
                firewall_frontend="ufw",
                resolver_mode="systemd-resolved",
            ),
            kernel_policy=KernelPolicySurface(kernel_version="6.8.0"),
            hardware=HardwareIdentitySurface(cpu_cores=4, total_memory_mb=16384),
        )

    def test_attach_and_get_control_surface(self, conn):
        """Test attaching and retrieving a control surface snapshot."""
        incident = create_incident_draft(title="Control surface test", conn=conn)

        snapshot = self._make_control_surface_snapshot()
        attach_control_surface_snapshot(incident.incident_id, "pre", snapshot, conn=conn)

        fetched = get_control_surface_snapshot(incident.incident_id, "pre", conn=conn)
        assert fetched is not None
        assert fetched.network_control.network_manager == "NetworkManager"
        assert fetched.kernel_policy.kernel_version == "6.8.0"

    def test_get_control_surface_not_found(self, conn):
        """Test getting a nonexistent control surface returns None."""
        incident = create_incident_draft(title="No surface", conn=conn)

        fetched = get_control_surface_snapshot(incident.incident_id, "pre", conn=conn)
        assert fetched is None

    def test_get_all_control_surface_snapshots(self, conn):
        """Test getting all control surface snapshots for an incident."""
        incident = create_incident_draft(title="All surfaces", conn=conn)

        snap = self._make_control_surface_snapshot()
        attach_control_surface_snapshot(incident.incident_id, "pre", snap, conn=conn)
        attach_control_surface_snapshot(incident.incident_id, "post", snap, conn=conn)

        snapshots = get_control_surface_snapshots(incident.incident_id, conn=conn)
        assert "pre" in snapshots
        assert "post" in snapshots

    def test_get_surface_hashes(self, conn):
        """Test retrieving surface hashes without loading full snapshot."""
        incident = create_incident_draft(title="Hash test", conn=conn)

        snapshot = self._make_control_surface_snapshot()
        attach_control_surface_snapshot(incident.incident_id, "pre", snapshot, conn=conn)

        hashes = get_surface_hashes(incident.incident_id, "pre", conn=conn)
        assert hashes is not None
        assert isinstance(hashes, dict)
        # Should have standard surface hash keys
        assert "packages" in hashes
        assert "scheduler" in hashes
        assert "privilege" in hashes
        assert "network_control" in hashes

    def test_get_surface_hashes_not_found(self, conn):
        """Test getting surface hashes for nonexistent snapshot returns None."""
        result = get_surface_hashes("nonexistent-id", "pre", conn=conn)
        assert result is None


class TestListIncidentsFilters:
    """Tests for list_incidents with various filters."""

    def test_list_by_status(self, conn):
        """Test listing incidents filtered by status."""
        inc1 = create_incident_draft(title="Open inc", conn=conn)
        inc2 = create_incident_draft(title="Resolved inc", conn=conn)
        finalize_outcome(inc2.incident_id, "improved", conn=conn)

        open_incidents = list_incidents(status="open", conn=conn)
        assert len(open_incidents) == 1
        assert open_incidents[0].title == "Open inc"

        resolved_incidents = list_incidents(status="resolved", conn=conn)
        assert len(resolved_incidents) == 1
        assert resolved_incidents[0].title == "Resolved inc"

    def test_list_combined_filters(self, conn):
        """Test listing with both status and domain filters."""
        create_incident_draft(title="Open net", domain="net", conn=conn)
        inc2 = create_incident_draft(title="Resolved net", domain="net", conn=conn)
        finalize_outcome(inc2.incident_id, "improved", conn=conn)
        create_incident_draft(title="Open disk", domain="disk", conn=conn)

        results = list_incidents(status="open", domain="net", conn=conn)
        assert len(results) == 1
        assert results[0].title == "Open net"

    def test_list_empty_result(self, conn):
        """Test listing with filters that match nothing."""
        create_incident_draft(title="Test", domain="net", conn=conn)

        results = list_incidents(domain="nonexistent-domain", conn=conn)
        assert len(results) == 0

    def test_list_ordering_by_updated_at(self, conn):
        """Test that list_incidents returns incidents ordered by updated_at descending."""
        inc1 = create_incident_draft(title="First", conn=conn)
        inc2 = create_incident_draft(title="Second", conn=conn)
        # Update the first incident so its updated_at becomes most recent
        update_incident(inc1.incident_id, summary="Updated", conn=conn)

        results = list_incidents(conn=conn)
        assert len(results) == 2
        # First incident was updated last, should be first in the list
        assert results[0].incident_id == inc1.incident_id


class TestFingerprintOperations:
    """Tests for fingerprint storage and retrieval."""

    def test_fingerprint_round_trip(self, conn):
        """Test that fingerprints survive store-retrieve cycle."""
        incident = create_incident_draft(title="FP round trip", conn=conn)

        fp = Fingerprint(
            disk_pressure=0.92,
            mem_pressure=0.75,
            swap_pressure=0.1,
            cpu_pressure=2.5,
            oom_count_1h=3,
            net_flaps_1h=2,
            service_failures_1h=1,
            auth_failures_1h=5,
            entities=("service:nginx", "mount:/var", "interface:eth0"),
            smart_pct_used_max=80,
            temp_max_c=65,
            docker_exited_count=2,
        )

        update_incident(incident.incident_id, fingerprint=fp, conn=conn)
        fetched = get_incident(incident.incident_id, conn=conn)

        assert fetched is not None
        assert fetched.fingerprint.disk_pressure == 0.92
        assert fetched.fingerprint.mem_pressure == 0.75
        assert fetched.fingerprint.swap_pressure == 0.1
        assert fetched.fingerprint.cpu_pressure == 2.5
        assert fetched.fingerprint.oom_count_1h == 3
        assert fetched.fingerprint.net_flaps_1h == 2
        assert fetched.fingerprint.service_failures_1h == 1
        assert fetched.fingerprint.auth_failures_1h == 5
        assert len(fetched.fingerprint.entities) == 3
        assert "service:nginx" in fetched.fingerprint.entities

    def test_default_fingerprint(self, conn):
        """Test that incidents without explicit fingerprint get defaults."""
        incident = create_incident_draft(title="Default FP", conn=conn)
        fetched = get_incident(incident.incident_id, conn=conn)

        assert fetched is not None
        assert fetched.fingerprint.disk_pressure == 0.0
        assert fetched.fingerprint.mem_pressure == 0.0
        assert fetched.fingerprint.entities == ()


class TestUpdateWithV5Fields:
    """Tests for V5 schema fields (forecast tracking)."""

    def test_create_with_trigger_source(self, conn):
        """Test creating incident with trigger source."""
        incident = create_incident_draft(
            title="Telemetry triggered incident",
            trigger_source="telemetry",
            trigger_command="disk_usage_trend",
            conn=conn,
        )

        fetched = get_incident(incident.incident_id, conn=conn)
        assert fetched is not None
        assert fetched.trigger_source == "telemetry"
        assert fetched.trigger_command == "disk_usage_trend"

    def test_get_prepared_plan_empty(self, conn):
        """Test getting a prepared plan when none exists."""
        incident = create_incident_draft(title="No plan", conn=conn)

        plan = get_prepared_plan(incident.incident_id, conn=conn)
        assert plan is None

    def test_update_multiple_fields_at_once(self, conn):
        """Test updating multiple fields simultaneously."""
        incident = create_incident_draft(title="Multi update", conn=conn)

        updated = update_incident(
            incident.incident_id,
            summary="Detailed summary",
            symptoms=["Symptom A", "Symptom B", "Symptom C"],
            suspected_causes=["Cause 1", "Cause 2"],
            tags=["urgent", "network"],
            confidence=0.88,
            log_snippets=["Error: connection refused", "Timeout: 30s"],
            metrics={"latency_ms": 500, "error_rate": 0.15},
            conn=conn,
        )

        assert updated is not None
        assert updated.summary == "Detailed summary"
        assert len(updated.symptoms) == 3
        assert len(updated.suspected_causes) == 2
        assert updated.tags == ("urgent", "network")
        assert updated.confidence == 0.88
        assert len(updated.log_snippets) == 2
        assert updated.metrics["latency_ms"] == 500


class TestStatistics:
    """Tests for statistical counting operations."""

    def test_incident_count(self, conn):
        """Test getting total incident count."""
        assert get_incident_count(conn=conn) == 0

        create_incident_draft(title="Inc 1", conn=conn)
        create_incident_draft(title="Inc 2", conn=conn)

        assert get_incident_count(conn=conn) == 2

    def test_action_count(self, conn):
        """Test getting total action count."""
        assert get_action_count(conn=conn) == 0

        incident = create_incident_draft(title="Action count", conn=conn)
        append_action(incident.incident_id, kind="shell", command="cmd1", conn=conn)
        append_action(incident.incident_id, kind="shell", command="cmd2", conn=conn)

        assert get_action_count(conn=conn) == 2

    def test_snapshot_count(self, conn):
        """Test getting total snapshot count."""
        assert get_snapshot_count(conn=conn) == 0

        incident = create_incident_draft(title="Snap count", conn=conn)
        snap = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=1000,
            mem_free_mb=500,
            mem_available_mb=600,
        )
        attach_snapshot(incident.incident_id, "pre", snap, conn=conn)

        assert get_snapshot_count(conn=conn) == 1


class TestEmbeddingsExtended:
    """Extended tests for embedding operations."""

    def test_get_all_embeddings(self, conn):
        """Test getting all embeddings."""
        inc1 = create_incident_draft(title="Embed 1", conn=conn)
        inc2 = create_incident_draft(title="Embed 2", conn=conn)

        upsert_embedding(inc1.incident_id, [0.1, 0.2, 0.3], "model-a", conn=conn)
        upsert_embedding(inc2.incident_id, [0.4, 0.5, 0.6], "model-a", conn=conn)

        all_embeds = get_all_embeddings(conn=conn)
        assert len(all_embeds) == 2
        assert inc1.incident_id in all_embeds
        assert inc2.incident_id in all_embeds

    def test_get_incidents_without_embeddings(self, conn):
        """Test finding incidents that lack embeddings."""
        inc1 = create_incident_draft(title="With embed", conn=conn)
        inc2 = create_incident_draft(title="Without embed", conn=conn)

        upsert_embedding(inc1.incident_id, [0.1, 0.2], "model-a", conn=conn)

        missing = get_incidents_without_embeddings(conn=conn)
        assert inc2.incident_id in missing
        assert inc1.incident_id not in missing

    def test_get_embedding_not_found(self, conn):
        """Test getting an embedding for an incident that has none."""
        result = get_embedding("nonexistent-id", conn=conn)
        assert result is None


class TestSnapshotReplacement:
    """Tests for snapshot replacement behavior."""

    def test_snapshot_replace_on_same_which(self, conn):
        """Test that re-attaching a snapshot replaces the previous one."""
        incident = create_incident_draft(title="Replace snap", conn=conn)

        snap1 = SystemSnapshot(
            os="Ubuntu", kernel="6.5", uptime_sec=100,
            cpu_load=(1.0, 1.0, 1.0), mem_total_mb=8000,
            mem_free_mb=1000, mem_available_mb=2000,
        )
        snap2 = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=500,
            cpu_load=(0.1, 0.1, 0.1), mem_total_mb=16000,
            mem_free_mb=8000, mem_available_mb=12000,
        )

        attach_snapshot(incident.incident_id, "pre", snap1, conn=conn)
        attach_snapshot(incident.incident_id, "pre", snap2, conn=conn)

        fetched = get_snapshot(incident.incident_id, "pre", conn=conn)
        assert fetched is not None
        assert fetched.snapshot.kernel == "6.8"
        assert fetched.snapshot.uptime_sec == 500

    def test_get_snapshot_not_found(self, conn):
        """Test getting a snapshot that does not exist."""
        incident = create_incident_draft(title="No snap", conn=conn)

        result = get_snapshot(incident.incident_id, "pre", conn=conn)
        assert result is None

    def test_get_snapshots_empty(self, conn):
        """Test getting snapshots when none are attached."""
        incident = create_incident_draft(title="Empty snaps", conn=conn)

        result = get_snapshots(incident.incident_id, conn=conn)
        assert result == {}


class TestDeleteCascade:
    """Tests for cascade deletion behavior."""

    def test_delete_cascade_removes_actions(self, conn):
        """Test that deleting an incident also removes its actions."""
        incident = create_incident_draft(title="Cascade test", conn=conn)
        append_action(incident.incident_id, kind="shell", command="test", conn=conn)

        assert len(get_actions(incident.incident_id, conn=conn)) == 1

        delete_incident(incident.incident_id, conn=conn)

        assert len(get_actions(incident.incident_id, conn=conn)) == 0

    def test_delete_cascade_removes_events(self, conn):
        """Test that deleting an incident also removes linked events."""
        incident = create_incident_draft(title="Cascade events", conn=conn)
        link_events(incident.incident_id, ["ev-1", "ev-2"], conn=conn)

        assert len(get_linked_events(incident.incident_id, conn=conn)) == 2

        delete_incident(incident.incident_id, conn=conn)

        assert len(get_linked_events(incident.incident_id, conn=conn)) == 0

    def test_delete_cascade_removes_snapshots(self, conn):
        """Test that deleting an incident also removes attached snapshots."""
        incident = create_incident_draft(title="Cascade snap", conn=conn)

        snap = SystemSnapshot(
            os="Ubuntu", kernel="6.8", uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5), mem_total_mb=1000,
            mem_free_mb=500, mem_available_mb=600,
        )
        attach_snapshot(incident.incident_id, "pre", snap, conn=conn)

        delete_incident(incident.incident_id, conn=conn)

        result = get_snapshot(incident.incident_id, "pre", conn=conn)
        assert result is None
