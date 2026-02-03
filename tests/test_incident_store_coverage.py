"""Mock-based tests for Incident Vault store operations.

Unlike test_incident_store.py which uses real PostgreSQL and gets skipped
in CI, these tests mock get_conn so they run everywhere without a database.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.models import (
    ConfidenceBreakdown,
    ConfigFileState,
    DecisionRecord,
    Fingerprint,
    IncidentAction,
    IncidentReport,
    IncidentSnapshot,
    Precondition,
    Provenance,
    SystemSnapshot,
)
from elle.daemon.incidents.store import (
    _parse_provenance,
    _row_to_action,
    _row_to_config_state,
    _row_to_decision_record,
    _row_to_incident,
    _row_to_snapshot,
    append_action,
    attach_snapshot,
    create_incident_draft,
    delete_incident,
    finalize_outcome,
    get_action_count,
    get_actions,
    get_all_embeddings,
    get_config_state_by_path,
    get_config_states,
    get_decision_record,
    get_embedding,
    get_incident,
    get_incident_count,
    get_incidents_without_embeddings,
    get_linked_events,
    get_prepared_plan,
    get_snapshot,
    get_snapshot_count,
    get_snapshots,
    link_events,
    list_incidents,
    store_config_state,
    store_decision_record,
    update_incident,
    upsert_embedding,
)

# ---------------------------------------------------------------------------
# Patch target
# ---------------------------------------------------------------------------
PATCH_GET_CONN = "elle.daemon.incidents.store.get_conn"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_conn():
    """Reusable mock for get_conn context manager returning mock connection."""
    with patch(PATCH_GET_CONN) as mock_gc:
        conn = MagicMock()
        mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        yield conn


def _make_incident_row(overrides=None):
    """Build a realistic incident row dict for _row_to_incident tests."""
    row = {
        "id": "test-id-123",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T01:00:00+00:00",
        "domain": "net",
        "severity": "warning",
        "status": "open",
        "title": "Test incident",
        "summary": "A test",
        "symptoms_json": '["symptom1"]',
        "suspected_causes_json": '["cause1"]',
        "root_cause": None,
        "event_ids_json": "[]",
        "log_snippets_json": "[]",
        "metrics_json": "{}",
        "decision_json": "{}",
        "preconditions_json": "[]",
        "outcome": "unknown",
        "verification_steps_json": "[]",
        "time_to_mitigate_sec": None,
        "time_to_resolve_sec": None,
        "fingerprint_json": "{}",
        "tags_json": "[]",
        "confidence": 0.5,
        "trigger_source": "manual",
        "trigger_command": None,
    }
    if overrides:
        row.update(overrides)
    return row


def _make_action_row(overrides=None):
    """Build a realistic action row dict."""
    row = {
        "id": 1,
        "incident_id": "inc-001",
        "step_index": 0,
        "kind": "shell",
        "command": "systemctl restart nginx",
        "payload_json": "{}",
        "exit_code": 0,
        "stdout": "ok",
        "stderr": "",
        "success": True,
        "privileged": False,
        "created_at": "2024-01-01T00:00:00+00:00",
        "duration_ms": 150,
    }
    if overrides:
        row.update(overrides)
    return row


def _make_snapshot_row(overrides=None):
    """Build a realistic snapshot row dict."""
    snapshot_data = {
        "os": "Ubuntu 24.04",
        "kernel": "6.8.0-41-generic",
        "uptime_sec": 3600,
        "hostname": "testhost",
        "cpu_load": [1.0, 0.8, 0.5],
        "mem_total_mb": 16384,
        "mem_free_mb": 8192,
        "mem_available_mb": 10240,
        "swap_total_mb": 0,
        "swap_used_mb": 0,
        "disks": [{"mount": "/", "used_pct": 50}],
        "interfaces": [{"name": "eth0", "state": "up"}],
        "services": [{"name": "nginx", "active": True}],
        "docker_running": 0,
        "docker_exited": 0,
        "docker_containers": [],
        "temps": [{"sensor": "cpu", "celsius": 55}],
        "smart": [{"dev": "sda", "health": "PASSED"}],
        "collected_at": "2024-01-01T00:05:00+00:00",
    }
    row = {
        "id": 10,
        "incident_id": "inc-001",
        "which": "pre",
        "snapshot_json": json.dumps(snapshot_data),
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    if overrides:
        row.update(overrides)
    return row


# ============================================================================
# _row_to_incident tests
# ============================================================================


class TestRowToIncident:
    """Tests for _row_to_incident converter."""

    @pytest.mark.timeout(10)
    def test_basic_row(self):
        row = _make_incident_row()
        result = _row_to_incident(row)

        assert isinstance(result, IncidentReport)
        assert result.incident_id == "test-id-123"
        assert result.domain == "net"
        assert result.severity == "warning"
        assert result.status == "open"
        assert result.title == "Test incident"
        assert result.summary == "A test"
        assert result.symptoms == ("symptom1",)
        assert result.suspected_causes == ("cause1",)
        assert result.outcome == "unknown"
        assert result.confidence == 0.5
        assert result.trigger_source == "manual"
        assert result.trigger_command is None

    @pytest.mark.timeout(10)
    def test_preconditions_as_dicts(self):
        precond_data = [
            {"expression": "disk./.used_pct > 90", "description": "High disk", "required": True},
            {"expression": "mem > 0.8", "description": "Memory", "required": False},
        ]
        row = _make_incident_row({"preconditions_json": json.dumps(precond_data)})
        result = _row_to_incident(row)

        assert len(result.preconditions) == 2
        assert isinstance(result.preconditions[0], Precondition)
        assert result.preconditions[0].expression == "disk./.used_pct > 90"
        assert result.preconditions[1].required is False

    @pytest.mark.timeout(10)
    def test_preconditions_non_list(self):
        """Non-list preconditions_json should produce empty tuple."""
        row = _make_incident_row({"preconditions_json": '"not a list"'})
        result = _row_to_incident(row)
        assert result.preconditions == ()

    @pytest.mark.timeout(10)
    def test_fingerprint_null(self):
        """Null fingerprint_json produces default Fingerprint."""
        row = _make_incident_row({"fingerprint_json": "null"})
        result = _row_to_incident(row)
        assert isinstance(result.fingerprint, Fingerprint)
        assert result.fingerprint.disk_pressure == 0.0

    @pytest.mark.timeout(10)
    def test_fingerprint_empty(self):
        """Empty dict fingerprint_json produces default Fingerprint."""
        row = _make_incident_row({"fingerprint_json": "{}"})
        result = _row_to_incident(row)
        assert isinstance(result.fingerprint, Fingerprint)

    @pytest.mark.timeout(10)
    def test_created_at_as_datetime(self):
        """Datetime objects in row should pass through without parsing."""
        dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        row = _make_incident_row({"created_at": dt, "updated_at": dt})
        result = _row_to_incident(row)
        assert result.created_at == dt
        assert result.updated_at == dt

    @pytest.mark.timeout(10)
    def test_created_at_as_string(self):
        """ISO string timestamps should be parsed correctly."""
        row = _make_incident_row(
            {
                "created_at": "2024-06-15T12:00:00+00:00",
                "updated_at": "2024-06-15T13:00:00+00:00",
            }
        )
        result = _row_to_incident(row)
        assert result.created_at.year == 2024
        assert result.created_at.month == 6
        assert result.updated_at.hour == 13


# ============================================================================
# create_incident_draft tests
# ============================================================================


class TestCreateIncidentDraft:
    @pytest.mark.timeout(10)
    def test_creates_incident(self, mock_conn):
        result = create_incident_draft(
            title="Test",
            domain="net",
            severity="error",
            trigger_source="manual",
        )

        assert isinstance(result, IncidentReport)
        assert result.title == "Test"
        assert result.domain == "net"
        assert result.severity == "error"
        assert result.status == "open"
        assert result.trigger_source == "manual"
        mock_conn.execute.assert_called_once()
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO incidents" in sql_arg


# ============================================================================
# update_incident tests
# ============================================================================


class TestUpdateIncident:
    @pytest.mark.timeout(10)
    def test_with_provided_conn(self):
        """When conn is passed directly, get_conn should NOT be called."""
        ext_conn = MagicMock()
        # The UPDATE
        update_result = MagicMock()
        update_result.rowcount = 1
        # The SELECT after update (_get_incident_with_conn)
        select_result = MagicMock()
        select_result.fetchone.return_value = _make_incident_row({"summary": "updated"})
        ext_conn.execute.side_effect = [update_result, select_result]

        with patch(PATCH_GET_CONN) as mock_gc:
            result = update_incident("test-id-123", summary="updated", conn=ext_conn)
            mock_gc.assert_not_called()

        assert result is not None
        assert result.summary == "updated"

    @pytest.mark.timeout(10)
    def test_without_conn_uses_get_conn(self, mock_conn):
        update_result = MagicMock()
        update_result.rowcount = 1
        select_result = MagicMock()
        select_result.fetchone.return_value = _make_incident_row()
        mock_conn.execute.side_effect = [update_result, select_result]

        result = update_incident("test-id-123", summary="new")
        assert result is not None

    @pytest.mark.timeout(10)
    def test_rowcount_zero_returns_none(self, mock_conn):
        update_result = MagicMock()
        update_result.rowcount = 0
        mock_conn.execute.return_value = update_result

        result = update_incident("nonexistent", summary="x")
        assert result is None

    @pytest.mark.timeout(10)
    def test_multiple_fields(self, mock_conn):
        update_result = MagicMock()
        update_result.rowcount = 1
        select_result = MagicMock()
        select_result.fetchone.return_value = _make_incident_row(
            {
                "summary": "multi",
                "status": "mitigated",
            }
        )
        mock_conn.execute.side_effect = [update_result, select_result]

        result = update_incident(
            "test-id-123",
            summary="multi",
            status="mitigated",
            tags=["urgent"],
            confidence=0.9,
        )
        assert result is not None
        # Verify the SQL includes multiple SET clauses
        sql_arg = str(mock_conn.execute.call_args_list[0][0][0])
        assert "status = %s" in sql_arg
        assert "summary = %s" in sql_arg
        assert "tags_json = %s" in sql_arg
        assert "confidence = %s" in sql_arg

    @pytest.mark.timeout(10)
    def test_all_field_branches(self, mock_conn):
        """Exercise every update field branch in update_incident."""
        update_result = MagicMock()
        update_result.rowcount = 1
        select_result = MagicMock()
        select_result.fetchone.return_value = _make_incident_row()
        mock_conn.execute.side_effect = [update_result, select_result]

        result = update_incident(
            "test-id-123",
            status="mitigated",
            summary="updated summary",
            symptoms=["symptom1", "symptom2"],
            suspected_causes=["cause1"],
            root_cause="root cause found",
            event_ids=["ev-1", "ev-2"],
            log_snippets=["log line 1"],
            metrics={"cpu": 90.0},
            decision={"approach": "restart"},
            preconditions=[Precondition(expression="disk > 90")],
            outcome="improved",
            verification_steps=["check 1", "check 2"],
            time_to_mitigate_sec=120,
            time_to_resolve_sec=300,
            fingerprint=Fingerprint(disk_pressure=0.9),
            tags=["urgent"],
            confidence=0.95,
        )
        assert result is not None
        sql_arg = str(mock_conn.execute.call_args_list[0][0][0])
        assert "symptoms_json = %s" in sql_arg
        assert "suspected_causes_json = %s" in sql_arg
        assert "root_cause = %s" in sql_arg
        assert "event_ids_json = %s" in sql_arg
        assert "log_snippets_json = %s" in sql_arg
        assert "metrics_json = %s" in sql_arg
        assert "decision_json = %s" in sql_arg
        assert "preconditions_json = %s" in sql_arg
        assert "outcome = %s" in sql_arg
        assert "verification_steps_json = %s" in sql_arg
        assert "time_to_mitigate_sec = %s" in sql_arg
        assert "time_to_resolve_sec = %s" in sql_arg
        assert "fingerprint_json = %s" in sql_arg
        assert "tags_json = %s" in sql_arg
        assert "confidence = %s" in sql_arg


# ============================================================================
# get_incident tests
# ============================================================================


class TestGetIncident:
    @pytest.mark.timeout(10)
    def test_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = _make_incident_row()

        result = get_incident("test-id-123")
        assert result is not None
        assert result.incident_id == "test-id-123"

    @pytest.mark.timeout(10)
    def test_not_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None

        result = get_incident("missing")
        assert result is None


# ============================================================================
# list_incidents tests
# ============================================================================


class TestListIncidents:
    @pytest.mark.timeout(10)
    def test_no_filters(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            _make_incident_row({"id": "a"}),
            _make_incident_row({"id": "b"}),
        ]

        result = list_incidents()
        assert len(result) == 2

    @pytest.mark.timeout(10)
    def test_with_status_filter(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            _make_incident_row({"status": "open"}),
        ]

        result = list_incidents(status="open")
        assert len(result) == 1
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "AND status = %s" in sql_arg

    @pytest.mark.timeout(10)
    def test_with_domain_filter(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = []

        result = list_incidents(domain="disk")
        assert result == []
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "AND domain = %s" in sql_arg


# ============================================================================
# delete_incident tests
# ============================================================================


class TestDeleteIncident:
    @pytest.mark.timeout(10)
    def test_found(self, mock_conn):
        mock_conn.execute.return_value.rowcount = 1
        assert delete_incident("test-id") is True

    @pytest.mark.timeout(10)
    def test_not_found(self, mock_conn):
        mock_conn.execute.return_value.rowcount = 0
        assert delete_incident("missing") is False


# ============================================================================
# get_prepared_plan tests
# ============================================================================


class TestGetPreparedPlan:
    @pytest.mark.timeout(10)
    def test_with_plan(self, mock_conn):
        plan_data = {"steps": ["step1"], "approach": "restart"}
        mock_conn.execute.return_value.fetchone.return_value = {
            "prepared_plan_json": json.dumps(plan_data),
        }

        result = get_prepared_plan("inc-001")
        assert result == plan_data

    @pytest.mark.timeout(10)
    def test_with_null_plan(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = {
            "prepared_plan_json": None,
        }

        result = get_prepared_plan("inc-001")
        assert result is None

    @pytest.mark.timeout(10)
    def test_row_not_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None

        result = get_prepared_plan("missing")
        assert result is None


# ============================================================================
# append_action tests
# ============================================================================


class TestAppendAction:
    @pytest.mark.timeout(10)
    def test_computes_step_index(self, mock_conn):
        # First call: COALESCE query for step index
        step_result = MagicMock()
        step_result.fetchone.return_value = {"coalesce": 3}
        # Second call: INSERT RETURNING id
        insert_result = MagicMock()
        insert_result.fetchone.return_value = {"id": 42}
        mock_conn.execute.side_effect = [step_result, insert_result]

        action = append_action("inc-001", kind="shell", command="apt update", success=True)

        assert action.step_index == 3
        assert action.id == 42
        assert action.kind == "shell"
        assert action.command == "apt update"
        assert mock_conn.execute.call_count == 2

    @pytest.mark.timeout(10)
    def test_first_action_step_zero(self, mock_conn):
        step_result = MagicMock()
        step_result.fetchone.return_value = {"coalesce": 0}
        insert_result = MagicMock()
        insert_result.fetchone.return_value = {"id": 1}
        mock_conn.execute.side_effect = [step_result, insert_result]

        action = append_action("inc-001", kind="verify")
        assert action.step_index == 0


# ============================================================================
# get_actions tests
# ============================================================================


class TestGetActions:
    @pytest.mark.timeout(10)
    def test_multiple_rows(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            _make_action_row({"step_index": 0}),
            _make_action_row({"step_index": 1, "command": "cmd2"}),
        ]

        result = get_actions("inc-001")
        assert len(result) == 2
        assert result[0].step_index == 0
        assert result[1].step_index == 1

    @pytest.mark.timeout(10)
    def test_empty(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = []

        result = get_actions("inc-001")
        assert result == []


# ============================================================================
# _row_to_action tests
# ============================================================================


class TestRowToAction:
    @pytest.mark.timeout(10)
    def test_with_datetime_string(self):
        row = _make_action_row()
        result = _row_to_action(row)

        assert isinstance(result, IncidentAction)
        assert result.kind == "shell"
        assert result.success is True
        assert result.created_at.year == 2024

    @pytest.mark.timeout(10)
    def test_with_datetime_object(self):
        dt = datetime(2024, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        row = _make_action_row({"created_at": dt})
        result = _row_to_action(row)

        assert result.created_at == dt


# ============================================================================
# Snapshot CRUD tests
# ============================================================================


class TestAttachSnapshot:
    @pytest.mark.timeout(10)
    def test_attach(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = {"id": 77}
        snap = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=3600,
            cpu_load=(1.0, 0.8, 0.5),
            mem_total_mb=16384,
            mem_free_mb=8192,
            mem_available_mb=10240,
        )

        result = attach_snapshot("inc-001", "pre", snap)
        assert isinstance(result, IncidentSnapshot)
        assert result.which == "pre"
        assert result.id == 77
        assert result.snapshot.os == "Ubuntu 24.04"


class TestGetSnapshot:
    @pytest.mark.timeout(10)
    def test_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = _make_snapshot_row()

        result = get_snapshot("inc-001", "pre")
        assert result is not None
        assert result.which == "pre"
        assert result.snapshot.os == "Ubuntu 24.04"

    @pytest.mark.timeout(10)
    def test_not_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None

        result = get_snapshot("inc-001", "pre")
        assert result is None


class TestGetSnapshots:
    @pytest.mark.timeout(10)
    def test_returns_dict(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            _make_snapshot_row({"which": "pre"}),
            _make_snapshot_row({"which": "post"}),
        ]

        result = get_snapshots("inc-001")
        assert "pre" in result
        assert "post" in result


# ============================================================================
# _row_to_snapshot tests
# ============================================================================


class TestRowToSnapshot:
    @pytest.mark.timeout(10)
    def test_tuple_conversion(self):
        """Lists in snapshot JSON should be converted to tuples."""
        row = _make_snapshot_row()
        result = _row_to_snapshot(row)

        assert isinstance(result.snapshot.cpu_load, tuple)
        assert isinstance(result.snapshot.disks, tuple)
        assert isinstance(result.snapshot.interfaces, tuple)
        assert isinstance(result.snapshot.services, tuple)
        assert isinstance(result.snapshot.docker_containers, tuple)
        assert isinstance(result.snapshot.temps, tuple)
        assert isinstance(result.snapshot.smart, tuple)

    @pytest.mark.timeout(10)
    def test_created_at_string(self):
        row = _make_snapshot_row()
        result = _row_to_snapshot(row)
        assert result.created_at.year == 2024

    @pytest.mark.timeout(10)
    def test_created_at_datetime(self):
        dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        row = _make_snapshot_row({"created_at": dt})
        result = _row_to_snapshot(row)
        assert result.created_at == dt


# ============================================================================
# link_events / get_linked_events tests
# ============================================================================


class TestEventLinks:
    @pytest.mark.timeout(10)
    def test_link_events(self, mock_conn):
        result_obj = MagicMock()
        result_obj.rowcount = 1
        mock_conn.execute.return_value = result_obj

        count = link_events("inc-001", ["ev-1", "ev-2", "ev-3"])
        assert count == 3
        assert mock_conn.execute.call_count == 3

    @pytest.mark.timeout(10)
    def test_get_linked_events(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            {"event_id": "ev-1"},
            {"event_id": "ev-2"},
        ]

        result = get_linked_events("inc-001")
        assert result == ["ev-1", "ev-2"]


# ============================================================================
# Embedding CRUD tests
# ============================================================================


class TestEmbeddings:
    @pytest.mark.timeout(10)
    def test_upsert_embedding(self, mock_conn):
        upsert_embedding("inc-001", [0.1, 0.2, 0.3], "test-model")
        mock_conn.execute.assert_called_once()
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO incident_embeddings" in sql_arg

    @pytest.mark.timeout(10)
    def test_get_embedding_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = {
            "embedding": [0.1, 0.2, 0.3],
        }

        result = get_embedding("inc-001")
        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.timeout(10)
    def test_get_embedding_not_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None

        result = get_embedding("missing")
        assert result is None

    @pytest.mark.timeout(10)
    def test_get_all_embeddings(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            {"incident_id": "a", "embedding": [0.1]},
            {"incident_id": "b", "embedding": [0.2]},
        ]

        result = get_all_embeddings()
        assert result == {"a": [0.1], "b": [0.2]}

    @pytest.mark.timeout(10)
    def test_get_incidents_without_embeddings(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            {"id": "inc-no-embed-1"},
            {"id": "inc-no-embed-2"},
        ]

        result = get_incidents_without_embeddings(limit=50)
        assert result == ["inc-no-embed-1", "inc-no-embed-2"]


# ============================================================================
# finalize_outcome tests
# ============================================================================


class TestFinalizeOutcome:
    def _setup_finalize(self, mock_conn, outcome, existing_row=None):
        """Helper to set up mocks for finalize_outcome.

        The function calls _get_incident_with_conn (SELECT), then
        update_incident (UPDATE + SELECT).
        """
        if existing_row is None:
            existing_row = _make_incident_row()
        # 1st execute: SELECT for _get_incident_with_conn
        select_result = MagicMock()
        select_result.fetchone.return_value = existing_row
        # 2nd execute: UPDATE in update_incident
        update_result = MagicMock()
        update_result.rowcount = 1
        # 3rd execute: SELECT in _get_incident_with_conn (after update)
        post_update_result = MagicMock()
        post_update_result.fetchone.return_value = _make_incident_row(
            {
                "outcome": outcome,
                "status": {
                    "improved": "resolved",
                    "partial": "mitigated",
                    "worse": "open",
                    "no_change": "open",
                }.get(outcome, "open"),
            }
        )
        mock_conn.execute.side_effect = [select_result, update_result, post_update_result]

    @pytest.mark.timeout(10)
    def test_improved_resolves(self, mock_conn):
        self._setup_finalize(mock_conn, "improved")

        with patch("elle.daemon.incidents.efficacy_tracker.record_outcome"):
            result = finalize_outcome("test-id-123", outcome="improved")

        assert result is not None
        assert result.status == "resolved"
        assert result.outcome == "improved"

    @pytest.mark.timeout(10)
    def test_partial_mitigates(self, mock_conn):
        self._setup_finalize(mock_conn, "partial")

        with patch("elle.daemon.incidents.efficacy_tracker.record_outcome", create=True):
            result = finalize_outcome("test-id-123", outcome="partial")

        assert result is not None
        assert result.status == "mitigated"

    @pytest.mark.timeout(10)
    def test_worse_stays_open(self, mock_conn):
        self._setup_finalize(mock_conn, "worse")

        result = finalize_outcome("test-id-123", outcome="worse")

        assert result is not None
        assert result.status == "open"

    @pytest.mark.timeout(10)
    def test_no_change_stays_open(self, mock_conn):
        self._setup_finalize(mock_conn, "no_change")

        result = finalize_outcome("test-id-123", outcome="no_change")

        assert result is not None
        assert result.status == "open"

    @pytest.mark.timeout(10)
    def test_nonexistent_incident(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None

        result = finalize_outcome("missing", outcome="improved")
        assert result is None

    @pytest.mark.timeout(10)
    def test_unknown_outcome_else_branch(self, mock_conn):
        """Outcome 'unknown' (valid Literal but not improved/partial/worse/no_change)
        hits the else branch, preserving current status and timing."""
        # Re-assign: SELECT, UPDATE, SELECT
        select_result = MagicMock()
        select_result.fetchone.return_value = _make_incident_row()
        update_result = MagicMock()
        update_result.rowcount = 1
        post_update_result = MagicMock()
        post_update_result.fetchone.return_value = _make_incident_row(
            {
                "outcome": "unknown",
                "status": "open",
            }
        )
        mock_conn.execute.side_effect = [select_result, update_result, post_update_result]

        result = finalize_outcome("test-id-123", outcome="unknown")
        assert result is not None
        assert result.status == "open"
        assert result.outcome == "unknown"


# ============================================================================
# Statistics tests
# ============================================================================


class TestStatistics:
    @pytest.mark.timeout(10)
    def test_get_incident_count(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 42}
        assert get_incident_count() == 42

    @pytest.mark.timeout(10)
    def test_get_incident_count_no_row(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None
        assert get_incident_count() == 0

    @pytest.mark.timeout(10)
    def test_get_action_count(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 7}
        assert get_action_count() == 7

    @pytest.mark.timeout(10)
    def test_get_action_count_no_row(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None
        assert get_action_count() == 0

    @pytest.mark.timeout(10)
    def test_get_snapshot_count(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 3}
        assert get_snapshot_count() == 3

    @pytest.mark.timeout(10)
    def test_get_snapshot_count_no_row(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None
        assert get_snapshot_count() == 0


# ============================================================================
# Decision record tests
# ============================================================================


class TestDecisionRecords:
    @pytest.mark.timeout(10)
    def test_store_decision_record(self, mock_conn):
        confidence = ConfidenceBreakdown(overall=0.85, from_man_vault=0.3)
        provenance = Provenance(primary_source="man_vault")
        record = DecisionRecord(
            chosen_approach="Restart nginx",
            rationale="Man page says so",
            confidence=confidence,
            provenance=provenance,
            planned_commands=("systemctl restart nginx",),
        )

        store_decision_record("inc-001", record)
        mock_conn.execute.assert_called_once()
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO decision_records" in sql_arg

    @pytest.mark.timeout(10)
    def test_get_decision_record_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = {
            "chosen_approach": "Restart",
            "rationale": "Prior incident",
            "confidence_json": json.dumps({"overall": 0.85}),
            "provenance_json": json.dumps({"primary_source": "incident_vault"}),
            "planned_commands_json": json.dumps(["cmd1", "cmd2"]),
            "decided_at": "2024-01-01T00:00:00+00:00",
        }

        result = get_decision_record("inc-001")
        assert result is not None
        assert result.chosen_approach == "Restart"
        assert result.confidence.overall == 0.85
        assert result.provenance.primary_source == "incident_vault"
        assert result.planned_commands == ("cmd1", "cmd2")

    @pytest.mark.timeout(10)
    def test_get_decision_record_not_found(self, mock_conn):
        mock_conn.execute.return_value.fetchone.return_value = None

        result = get_decision_record("missing")
        assert result is None


class TestRowToDecisionRecord:
    @pytest.mark.timeout(10)
    def test_full_data(self):
        row = {
            "chosen_approach": "Clear disk space",
            "rationale": "Disk full",
            "confidence_json": json.dumps(
                {
                    "overall": 0.9,
                    "from_man_vault": 0.2,
                    "from_incident_vault": 0.5,
                    "from_llm": 0.2,
                    "dominant_tier": "semantic",
                }
            ),
            "provenance_json": json.dumps(
                {
                    "man_pages": [{"name": "du", "section": "1", "snippet": "estimate file space"}],
                    "prior_incidents": [],
                    "primary_source": "man_vault",
                }
            ),
            "planned_commands_json": json.dumps(["du -sh /var", "apt clean"]),
            "decided_at": "2024-02-01T10:00:00+00:00",
        }

        result = _row_to_decision_record(row)
        assert isinstance(result, DecisionRecord)
        assert result.chosen_approach == "Clear disk space"
        assert result.confidence.overall == 0.9
        assert result.confidence.dominant_tier == "semantic"
        assert len(result.provenance.man_pages) == 1
        assert result.provenance.man_pages[0].name == "du"
        assert result.planned_commands == ("du -sh /var", "apt clean")

    @pytest.mark.timeout(10)
    def test_decided_at_datetime_object(self):
        dt = datetime(2024, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
        row = {
            "chosen_approach": "x",
            "rationale": "",
            "confidence_json": json.dumps({"overall": 0.5}),
            "provenance_json": "{}",
            "planned_commands_json": "[]",
            "decided_at": dt,
        }
        result = _row_to_decision_record(row)
        assert result.decided_at == dt


# ============================================================================
# _parse_provenance tests
# ============================================================================


class TestParseProvenance:
    @pytest.mark.timeout(10)
    def test_empty_data(self):
        result = _parse_provenance({})
        assert isinstance(result, Provenance)
        assert result.man_pages == ()
        assert result.prior_incidents == ()
        assert result.triggering_events is None

    @pytest.mark.timeout(10)
    def test_none_data(self):
        result = _parse_provenance(None)
        assert isinstance(result, Provenance)

    @pytest.mark.timeout(10)
    def test_with_man_pages(self):
        data = {
            "man_pages": [
                {"name": "systemctl", "section": "1", "snippet": "control services"},
            ],
            "primary_source": "man_vault",
        }
        result = _parse_provenance(data)
        assert len(result.man_pages) == 1
        assert result.man_pages[0].name == "systemctl"
        assert result.primary_source == "man_vault"

    @pytest.mark.timeout(10)
    def test_with_prior_incidents(self):
        data = {
            "prior_incidents": [
                {
                    "incident_id": "prior-001",
                    "title": "nginx crash",
                    "outcome": "improved",
                    "similarity_score": 0.8,
                    "match_type": "fingerprint",
                    "successful_actions": ["systemctl restart nginx"],
                },
            ],
        }
        result = _parse_provenance(data)
        assert len(result.prior_incidents) == 1
        inc = result.prior_incidents[0]
        assert inc.incident_id == "prior-001"
        assert isinstance(inc.successful_actions, tuple)
        assert inc.successful_actions == ("systemctl restart nginx",)

    @pytest.mark.timeout(10)
    def test_with_triggering_events(self):
        data = {
            "triggering_events": {
                "event_ids": ["ev-1", "ev-2"],
                "event_summaries": ["High CPU", "OOM kill"],
                "trigger_time": "2024-01-01T00:00:00+00:00",
            },
        }
        result = _parse_provenance(data)
        assert result.triggering_events is not None
        assert isinstance(result.triggering_events.event_ids, tuple)
        assert result.triggering_events.event_ids == ("ev-1", "ev-2")


# ============================================================================
# Config state tests
# ============================================================================


class TestConfigStates:
    @pytest.mark.timeout(10)
    def test_store_config_state_with_mtime(self, mock_conn):
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        state = ConfigFileState(
            path="/etc/nginx/nginx.conf",
            sha256_before="aaa",
            sha256_after="bbb",
            size_bytes=2048,
            mtime=dt,
            backup_path="/backups/nginx.conf",
        )

        store_config_state("inc-001", "pre", state)
        mock_conn.execute.assert_called_once()
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO config_states" in sql_arg

    @pytest.mark.timeout(10)
    def test_store_config_state_without_mtime(self, mock_conn):
        state = ConfigFileState(
            path="/etc/hosts",
            size_bytes=256,
        )

        store_config_state("inc-001", "post", state)
        params = mock_conn.execute.call_args[0][1]
        # mtime param should be None
        assert params[6] is None

    @pytest.mark.timeout(10)
    def test_get_config_states_with_which(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            {
                "path": "/etc/hosts",
                "sha256_before": None,
                "sha256_after": "abc",
                "size_bytes": 100,
                "mtime": None,
                "backup_path": None,
            },
        ]

        result = get_config_states("inc-001", which="pre")
        assert len(result) == 1
        assert result[0].path == "/etc/hosts"
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "snapshot_which = %s" in sql_arg

    @pytest.mark.timeout(10)
    def test_get_config_states_all(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = []

        result = get_config_states("inc-001")
        assert result == []
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "snapshot_which" not in sql_arg or "ORDER BY snapshot_which" in sql_arg

    @pytest.mark.timeout(10)
    def test_get_config_state_by_path(self, mock_conn):
        mock_conn.execute.return_value.fetchall.return_value = [
            {
                "incident_id": "inc-001",
                "snapshot_which": "pre",
                "path": "/etc/nginx/nginx.conf",
                "sha256_before": "v1",
                "sha256_after": "v2",
                "size_bytes": 500,
                "mtime": None,
                "backup_path": None,
            },
        ]

        result = get_config_state_by_path("/etc/nginx/nginx.conf", limit=5)
        assert len(result) == 1
        incident_id, which, state = result[0]
        assert incident_id == "inc-001"
        assert which == "pre"
        assert state.path == "/etc/nginx/nginx.conf"


class TestRowToConfigState:
    @pytest.mark.timeout(10)
    def test_with_mtime_string(self):
        row = {
            "path": "/etc/hosts",
            "sha256_before": "aa",
            "sha256_after": "bb",
            "size_bytes": 100,
            "mtime": "2024-01-01T00:00:00+00:00",
            "backup_path": None,
        }
        result = _row_to_config_state(row)
        assert result.mtime is not None
        assert result.mtime.year == 2024

    @pytest.mark.timeout(10)
    def test_with_mtime_datetime(self):
        dt = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        row = {
            "path": "/etc/hosts",
            "sha256_before": None,
            "sha256_after": None,
            "size_bytes": 0,
            "mtime": dt,
            "backup_path": None,
        }
        result = _row_to_config_state(row)
        assert result.mtime == dt

    @pytest.mark.timeout(10)
    def test_with_null_mtime(self):
        row = {
            "path": "/etc/hosts",
            "sha256_before": None,
            "sha256_after": None,
            "size_bytes": 0,
            "mtime": None,
            "backup_path": None,
        }
        result = _row_to_config_state(row)
        assert result.mtime is None


# ============================================================================
# Telemetry snapshot tests
# ============================================================================


class TestTelemetrySnapshots:
    @pytest.mark.timeout(10)
    def test_attach_telemetry_snapshot(self, mock_conn):
        from elle.daemon.incidents.telemetry_snapshot import (
            CPUPressure,
            DiskIOPressure,
            GPUMetrics,
            KernelHardwareSignals,
            MemoryPressure,
            NetworkLiveness,
            TelemetrySnapshot,
        )

        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.5, load_5m=1.0, load_15m=0.8),
            memory=MemoryPressure(total_mb=16384, available_mb=8192),
            disk=DiskIOPressure(),
            network=NetworkLiveness(),
            kernel=KernelHardwareSignals(),
            gpu=GPUMetrics(),
            hostname="host1",
            uptime_sec=3600,
        )

        from elle.daemon.incidents.store import attach_telemetry_snapshot

        attach_telemetry_snapshot("inc-001", "pre", snap)
        mock_conn.execute.assert_called_once()
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO telemetry_snapshots" in sql_arg

    @pytest.mark.timeout(10)
    def test_get_telemetry_snapshot_with_conn(self):
        """When conn is passed directly, get_conn should not be called."""
        from elle.daemon.incidents.store import get_telemetry_snapshot

        ext_conn = MagicMock()
        ext_conn.execute.return_value.fetchone.return_value = {
            "snapshot_json": json.dumps(
                {
                    "cpu": {"load_1m": 2.0, "load_5m": 1.5, "load_15m": 1.0},
                    "memory": {"total_mb": 8192, "available_mb": 4096},
                    "disk": {},
                    "network": {},
                    "kernel": {},
                    "gpu": {},
                    "services": [],
                    "hostname": "myhost",
                    "uptime_sec": 100,
                }
            ),
        }

        with patch(PATCH_GET_CONN) as mock_gc:
            result = get_telemetry_snapshot("inc-001", "pre", conn=ext_conn)
            mock_gc.assert_not_called()

        assert result is not None
        assert result.cpu.load_1m == 2.0

    @pytest.mark.timeout(10)
    def test_get_telemetry_snapshot_without_conn(self, mock_conn):
        from elle.daemon.incidents.store import get_telemetry_snapshot

        mock_conn.execute.return_value.fetchone.return_value = {
            "snapshot_json": json.dumps(
                {
                    "cpu": {"load_1m": 1.0, "load_5m": 0.5, "load_15m": 0.3},
                    "memory": {"total_mb": 16384, "available_mb": 8192},
                    "disk": {},
                    "network": {},
                    "kernel": {},
                    "gpu": {},
                    "services": [],
                    "hostname": "host",
                    "uptime_sec": 50,
                }
            ),
        }

        result = get_telemetry_snapshot("inc-001", "pre")
        assert result is not None

    @pytest.mark.timeout(10)
    def test_get_telemetry_snapshot_not_found(self, mock_conn):
        from elle.daemon.incidents.store import get_telemetry_snapshot

        mock_conn.execute.return_value.fetchone.return_value = None
        result = get_telemetry_snapshot("inc-001", "pre")
        assert result is None

    @pytest.mark.timeout(10)
    def test_get_telemetry_snapshots(self, mock_conn):
        from elle.daemon.incidents.store import get_telemetry_snapshots

        snap_json = json.dumps(
            {
                "cpu": {"load_1m": 1.0, "load_5m": 0.5, "load_15m": 0.3},
                "memory": {"total_mb": 16384, "available_mb": 8192},
                "disk": {},
                "network": {},
                "kernel": {},
                "gpu": {},
                "services": [],
                "hostname": "h",
                "uptime_sec": 10,
            }
        )
        mock_conn.execute.return_value.fetchall.return_value = [
            {"which": "pre", "snapshot_json": snap_json},
            {"which": "post", "snapshot_json": snap_json},
        ]

        result = get_telemetry_snapshots("inc-001")
        assert "pre" in result
        assert "post" in result


class TestParseTelemetrySnapshot:
    @pytest.mark.timeout(10)
    def test_valid_data(self):
        from elle.daemon.incidents.store import _parse_telemetry_snapshot

        data = {
            "cpu": {"load_1m": 1.0, "load_5m": 0.5, "load_15m": 0.3},
            "memory": {"total_mb": 8192, "available_mb": 4096},
            "disk": {"disks": [], "io_wait_pct": 0.0},
            "network": {"interfaces": []},
            "kernel": {},
            "gpu": {},
            "services": [],
            "hostname": "testhost",
            "uptime_sec": 3600,
            "collected_at": "2024-01-01T00:00:00+00:00",
        }
        result = _parse_telemetry_snapshot(data)
        assert result is not None
        assert result.hostname == "testhost"
        assert result.cpu.load_1m == 1.0

    @pytest.mark.timeout(10)
    def test_empty_data(self):
        from elle.daemon.incidents.store import _parse_telemetry_snapshot

        result = _parse_telemetry_snapshot({})
        assert result is None

    @pytest.mark.timeout(10)
    def test_none_data(self):
        from elle.daemon.incidents.store import _parse_telemetry_snapshot

        result = _parse_telemetry_snapshot(None)
        assert result is None


# ============================================================================
# Control surface snapshot tests
# ============================================================================


class TestControlSurfaceSnapshots:
    @pytest.mark.timeout(10)
    def test_attach_control_surface_snapshot(self, mock_conn):
        from elle.daemon.incidents.control_surface import (
            ControlSurfaceSnapshot,
            HardwareIdentitySurface,
            KernelPolicySurface,
            NetworkControlSurface,
            PackageSurface,
            PrivilegeSurface,
            SchedulerSurface,
        )
        from elle.daemon.incidents.store import attach_control_surface_snapshot

        snap = ControlSurfaceSnapshot(
            packages=PackageSurface(),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(),
            hardware=HardwareIdentitySurface(),
        )

        attach_control_surface_snapshot("inc-001", "pre", snap)
        mock_conn.execute.assert_called_once()
        sql_arg = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO control_surface_snapshots" in sql_arg

    @pytest.mark.timeout(10)
    def test_get_control_surface_snapshot_found(self, mock_conn):
        from elle.daemon.incidents.store import get_control_surface_snapshot

        snap_data = {
            "services": [],
            "configs": [],
            "packages": {"packages": []},
            "scheduler": {},
            "privilege": {},
            "network_control": {},
            "kernel_policy": {},
            "hardware": {},
            "collected_at": "2024-01-01T00:00:00+00:00",
        }
        mock_conn.execute.return_value.fetchone.return_value = {
            "snapshot_json": json.dumps(snap_data),
        }

        result = get_control_surface_snapshot("inc-001", "pre")
        assert result is not None

    @pytest.mark.timeout(10)
    def test_get_control_surface_snapshot_not_found(self, mock_conn):
        from elle.daemon.incidents.store import get_control_surface_snapshot

        mock_conn.execute.return_value.fetchone.return_value = None
        result = get_control_surface_snapshot("inc-001", "pre")
        assert result is None

    @pytest.mark.timeout(10)
    def test_get_control_surface_snapshots(self, mock_conn):
        from elle.daemon.incidents.store import get_control_surface_snapshots

        snap_json = json.dumps(
            {
                "services": [],
                "configs": [],
                "packages": {"packages": []},
                "scheduler": {},
                "privilege": {},
                "network_control": {},
                "kernel_policy": {},
                "hardware": {},
            }
        )
        mock_conn.execute.return_value.fetchall.return_value = [
            {"which": "pre", "snapshot_json": snap_json},
        ]

        result = get_control_surface_snapshots("inc-001")
        assert "pre" in result

    @pytest.mark.timeout(10)
    def test_get_surface_hashes_found(self, mock_conn):
        from elle.daemon.incidents.store import get_surface_hashes

        mock_conn.execute.return_value.fetchone.return_value = {
            "surface_hashes": json.dumps({"packages": "abc123", "scheduler": "def456"}),
        }

        result = get_surface_hashes("inc-001", "pre")
        assert result == {"packages": "abc123", "scheduler": "def456"}

    @pytest.mark.timeout(10)
    def test_get_surface_hashes_not_found(self, mock_conn):
        from elle.daemon.incidents.store import get_surface_hashes

        mock_conn.execute.return_value.fetchone.return_value = None
        result = get_surface_hashes("inc-001", "pre")
        assert result is None

    @pytest.mark.timeout(10)
    def test_get_surface_hashes_with_conn(self):
        """When conn is passed directly, get_conn should not be called."""
        from elle.daemon.incidents.store import get_surface_hashes

        ext_conn = MagicMock()
        ext_conn.execute.return_value.fetchone.return_value = {
            "surface_hashes": json.dumps({"key": "val"}),
        }

        with patch(PATCH_GET_CONN) as mock_gc:
            result = get_surface_hashes("inc-001", "pre", conn=ext_conn)
            mock_gc.assert_not_called()

        assert result == {"key": "val"}


class TestParseControlSurfaceSnapshot:
    @pytest.mark.timeout(10)
    def test_valid_data(self):
        from elle.daemon.incidents.store import _parse_control_surface_snapshot

        data = {
            "services": [],
            "configs": [],
            "packages": {"packages": []},
            "scheduler": {},
            "privilege": {},
            "network_control": {},
            "kernel_policy": {},
            "hardware": {},
            "collected_at": "2024-01-01T00:00:00+00:00",
        }
        result = _parse_control_surface_snapshot(data)
        assert result is not None

    @pytest.mark.timeout(10)
    def test_empty_data(self):
        from elle.daemon.incidents.store import _parse_control_surface_snapshot

        result = _parse_control_surface_snapshot({})
        assert result is None

    @pytest.mark.timeout(10)
    def test_none_data(self):
        from elle.daemon.incidents.store import _parse_control_surface_snapshot

        result = _parse_control_surface_snapshot(None)
        assert result is None


# ============================================================================
# get_anonymized_incident tests
# ============================================================================


class TestGetAnonymizedIncident:
    @pytest.mark.timeout(10)
    def test_found(self, mock_conn):
        from elle.daemon.incidents.store import get_anonymized_incident

        # 1st: SELECT for incident
        inc_select = MagicMock()
        inc_select.fetchone.return_value = _make_incident_row()
        # 2nd: SELECT for actions
        actions_select = MagicMock()
        actions_select.fetchall.return_value = []
        # 3rd: SELECT for telemetry_pre
        tel_pre = MagicMock()
        tel_pre.fetchone.return_value = None
        # 4th: SELECT for telemetry_post
        tel_post = MagicMock()
        tel_post.fetchone.return_value = None
        # 5th: SELECT for surface_hashes_pre
        sh_pre = MagicMock()
        sh_pre.fetchone.return_value = None
        # 6th: SELECT for surface_hashes_post
        sh_post = MagicMock()
        sh_post.fetchone.return_value = None
        mock_conn.execute.side_effect = [
            inc_select,
            actions_select,
            tel_pre,
            tel_post,
            sh_pre,
            sh_post,
        ]

        result = get_anonymized_incident("test-id-123")
        assert result is not None
        assert result.incident_id == "test-id-123"

    @pytest.mark.timeout(10)
    def test_not_found(self, mock_conn):
        from elle.daemon.incidents.store import get_anonymized_incident

        mock_conn.execute.return_value.fetchone.return_value = None

        result = get_anonymized_incident("missing")
        assert result is None
