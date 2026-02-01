"""Tests for ExecutionStore and module-level functions in execution.py.

Covers:
- LoopExecution properties: chosen_hypothesis, rejected_hypotheses
- ExecutionStore: _init_schema, store, get, get_recent, get_by_incident, get_summary
- Module-level functions: get_execution_store, store_execution, get_execution,
  get_recent_executions, get_execution_summaries

The Pydantic model tests (LoopExecution, ToolCallSummary, StageTransition) are
already covered in ``test_agentic_execution.py``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.agentic.execution import (
    ExecutionStore,
    LoopExecution,
    get_execution,
    get_execution_store,
    get_execution_summaries,
    get_recent_executions,
    store_execution,
)
from elle.cli.agentic.stages import Hypothesis

PATCH_GET_CONN = "elle.storage.engine.get_conn"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _make_execution(**overrides) -> LoopExecution:
    """Build a LoopExecution with sensible defaults, allowing overrides."""
    defaults = {
        "started_at": _NOW,
        "user_input": "test input",
        "classified_intent": "system_question",
    }
    defaults.update(overrides)
    return LoopExecution(**defaults)


def _make_execution_json(**overrides) -> str:
    """Build a JSON string from a LoopExecution with sensible defaults."""
    ex = _make_execution(**overrides)
    return json.dumps(ex.model_dump(mode="json"))


def _make_hypothesis(
    hypothesis_id: str = "h1",
    rejected: bool = False,
    chosen: bool = False,
) -> Hypothesis:
    """Build a Hypothesis with sensible defaults."""
    return Hypothesis(
        hypothesis_id=hypothesis_id,
        text="disk is full",
        confidence=0.8,
        source="llm",
        rejected=rejected,
        chosen=chosen,
    )


def _make_mock_conn(rows=None, fetchone_val=None):
    """Build a mock connection + cursor returned by get_conn.

    Parameters
    ----------
    rows : list[dict] | None
        Rows returned by cursor.fetchall().
    fetchone_val : dict | None
        Value returned by cursor.fetchone().
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_val
    mock_cursor.fetchall.return_value = rows or []

    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor

    @contextmanager
    def _ctx_mgr(schema=None):
        yield mock_conn

    return mock_conn, _ctx_mgr


# ---------------------------------------------------------------------------
# Fixture: reset module-level singleton between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_store_singleton():
    """Reset the module-level _store singleton before and after each test."""
    import elle.cli.agentic.execution as mod

    mod._store = None
    yield
    mod._store = None


# ==========================================================================
# LoopExecution property tests
# ==========================================================================


class TestChosenHypothesis:
    """Tests for LoopExecution.chosen_hypothesis property."""

    def test_no_chosen_id_returns_none(self) -> None:
        """When chosen_hypothesis_id is None, chosen_hypothesis returns None."""
        ex = _make_execution(chosen_hypothesis_id=None)
        assert ex.chosen_hypothesis is None

    def test_matching_hypothesis_returned(self) -> None:
        """When chosen_hypothesis_id matches a hypothesis, it is returned."""
        h1 = _make_hypothesis(hypothesis_id="h1", chosen=True)
        h2 = _make_hypothesis(hypothesis_id="h2")
        ex = _make_execution(
            hypotheses=(h1, h2),
            chosen_hypothesis_id="h1",
        )
        result = ex.chosen_hypothesis
        assert result is not None
        assert result.hypothesis_id == "h1"

    def test_no_matching_hypothesis_returns_none(self) -> None:
        """When chosen_hypothesis_id does not match any hypothesis, returns None."""
        h1 = _make_hypothesis(hypothesis_id="h1")
        ex = _make_execution(
            hypotheses=(h1,),
            chosen_hypothesis_id="nonexistent",
        )
        assert ex.chosen_hypothesis is None


class TestRejectedHypotheses:
    """Tests for LoopExecution.rejected_hypotheses property."""

    def test_returns_rejected(self) -> None:
        """Rejected hypotheses are returned as a tuple."""
        h1 = _make_hypothesis(hypothesis_id="h1", rejected=True)
        h2 = _make_hypothesis(hypothesis_id="h2", rejected=False)
        h3 = _make_hypothesis(hypothesis_id="h3", rejected=True)
        ex = _make_execution(hypotheses=(h1, h2, h3))

        rejected = ex.rejected_hypotheses
        assert len(rejected) == 2
        assert rejected[0].hypothesis_id == "h1"
        assert rejected[1].hypothesis_id == "h3"

    def test_none_rejected_returns_empty(self) -> None:
        """When no hypotheses are rejected, returns empty tuple."""
        h1 = _make_hypothesis(hypothesis_id="h1", rejected=False)
        ex = _make_execution(hypotheses=(h1,))
        assert ex.rejected_hypotheses == ()


# ==========================================================================
# ExecutionStore tests
# ==========================================================================


class TestExecutionStoreInitSchema:
    """Tests for ExecutionStore._init_schema."""

    def test_init_schema_creates_table_and_indexes(self) -> None:
        """_init_schema executes CREATE TABLE and two CREATE INDEX statements."""
        mock_conn, ctx_mgr = _make_mock_conn()

        with patch(PATCH_GET_CONN, ctx_mgr):
            store = ExecutionStore(schema="test_schema")

        assert store._schema == "test_schema"
        # 3 calls: CREATE TABLE, CREATE INDEX (started), CREATE INDEX (incident)
        assert mock_conn.execute.call_count == 3
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("CREATE TABLE" in c for c in calls)
        assert any("idx_executions_started" in c for c in calls)
        assert any("idx_executions_incident" in c for c in calls)


class TestExecutionStoreStore:
    """Tests for ExecutionStore.store method."""

    def _make_store(self, mock_ctx_mgr):
        """Create an ExecutionStore with mocked init."""
        with patch(PATCH_GET_CONN, mock_ctx_mgr):
            return ExecutionStore()

    def test_store_with_completed_at(self) -> None:
        """store() passes completed_at as isoformat when set."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        completed = datetime.now(timezone.utc)
        ex = _make_execution(
            completed_at=completed,
            capabilities_executed=("service.restart", "file.read"),
        )

        store_conn, store_ctx = _make_mock_conn()
        with patch(PATCH_GET_CONN, store_ctx):
            store.store(ex)

        # Verify the INSERT call was made
        store_conn.execute.assert_called_once()
        args = store_conn.execute.call_args
        params = args[0][1]  # Second positional argument is the tuple of params

        # execution_id, started_at, completed_at, ...
        assert params[0] == ex.execution_id
        assert params[1] == ex.started_at.isoformat()
        assert params[2] == completed.isoformat()
        # capabilities_executed joined
        assert params[12] == "service.restart,file.read"
        # data_json is valid JSON
        data = json.loads(params[13])
        assert data["user_input"] == "test input"

    def test_store_without_completed_at(self) -> None:
        """store() passes None for completed_at when not set."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        ex = _make_execution(completed_at=None)

        store_conn, store_ctx = _make_mock_conn()
        with patch(PATCH_GET_CONN, store_ctx):
            store.store(ex)

        store_conn.execute.assert_called_once()
        args = store_conn.execute.call_args
        params = args[0][1]

        # completed_at should be None
        assert params[2] is None


class TestExecutionStoreGet:
    """Tests for ExecutionStore.get method."""

    def _make_store(self, mock_ctx_mgr):
        """Create an ExecutionStore with mocked init."""
        with patch(PATCH_GET_CONN, mock_ctx_mgr):
            return ExecutionStore()

    def test_get_found(self) -> None:
        """get() returns LoopExecution when row with data_json exists."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        data_json = _make_execution_json(execution_id="exec-123")
        get_conn, get_ctx = _make_mock_conn(fetchone_val={"data_json": data_json})

        with patch(PATCH_GET_CONN, get_ctx):
            result = store.get("exec-123")

        assert result is not None
        assert isinstance(result, LoopExecution)
        assert result.execution_id == "exec-123"

    def test_get_not_found(self) -> None:
        """get() returns None when no row exists."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        get_conn, get_ctx = _make_mock_conn(fetchone_val=None)

        with patch(PATCH_GET_CONN, get_ctx):
            result = store.get("nonexistent")

        assert result is None

    def test_get_row_with_null_data_json(self) -> None:
        """get() returns None when row exists but data_json is None."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        get_conn, get_ctx = _make_mock_conn(fetchone_val={"data_json": None})

        with patch(PATCH_GET_CONN, get_ctx):
            result = store.get("exec-456")

        assert result is None


class TestExecutionStoreGetRecent:
    """Tests for ExecutionStore.get_recent method."""

    def _make_store(self, mock_ctx_mgr):
        """Create an ExecutionStore with mocked init."""
        with patch(PATCH_GET_CONN, mock_ctx_mgr):
            return ExecutionStore()

    def test_get_recent_multiple_rows(self) -> None:
        """get_recent() returns list of LoopExecution from multiple rows."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        json1 = _make_execution_json(execution_id="e1")
        json2 = _make_execution_json(execution_id="e2")
        rows = [{"data_json": json1}, {"data_json": json2}]
        recent_conn, recent_ctx = _make_mock_conn(rows=rows)

        with patch(PATCH_GET_CONN, recent_ctx):
            results = store.get_recent(limit=5)

        assert len(results) == 2
        assert results[0].execution_id == "e1"
        assert results[1].execution_id == "e2"

    def test_get_recent_empty(self) -> None:
        """get_recent() returns empty list when no rows exist."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        recent_conn, recent_ctx = _make_mock_conn(rows=[])

        with patch(PATCH_GET_CONN, recent_ctx):
            results = store.get_recent(limit=10)

        assert results == []


class TestExecutionStoreGetByIncident:
    """Tests for ExecutionStore.get_by_incident method."""

    def _make_store(self, mock_ctx_mgr):
        """Create an ExecutionStore with mocked init."""
        with patch(PATCH_GET_CONN, mock_ctx_mgr):
            return ExecutionStore()

    def test_get_by_incident_found(self) -> None:
        """get_by_incident() returns matching executions."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        json1 = _make_execution_json(execution_id="e1", incident_id="inc-1")
        json2 = _make_execution_json(execution_id="e2", incident_id="inc-1")
        rows = [{"data_json": json1}, {"data_json": json2}]
        inc_conn, inc_ctx = _make_mock_conn(rows=rows)

        with patch(PATCH_GET_CONN, inc_ctx):
            results = store.get_by_incident("inc-1")

        assert len(results) == 2
        assert results[0].execution_id == "e1"
        assert results[1].execution_id == "e2"

    def test_get_by_incident_empty(self) -> None:
        """get_by_incident() returns empty list when no matches."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        inc_conn, inc_ctx = _make_mock_conn(rows=[])

        with patch(PATCH_GET_CONN, inc_ctx):
            results = store.get_by_incident("nonexistent")

        assert results == []


class TestExecutionStoreGetSummary:
    """Tests for ExecutionStore.get_summary method."""

    def _make_store(self, mock_ctx_mgr):
        """Create an ExecutionStore with mocked init."""
        with patch(PATCH_GET_CONN, mock_ctx_mgr):
            return ExecutionStore()

    def test_get_summary_with_capabilities(self) -> None:
        """get_summary() splits capabilities_executed string into list."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        rows = [
            {
                "execution_id": "e1",
                "started_at": "2025-01-01T00:00:00",
                "user_input": "restart nginx",
                "classified_intent": "system_task",
                "success": 1,
                "incident_id": "inc-1",
                "iterations": 2,
                "total_tool_calls": 3,
                "capabilities_executed": "service.restart,file.read",
                "total_duration_ms": 1500,
            },
        ]
        sum_conn, sum_ctx = _make_mock_conn(rows=rows)

        with patch(PATCH_GET_CONN, sum_ctx):
            results = store.get_summary(limit=10)

        assert len(results) == 1
        summary = results[0]
        assert summary["execution_id"] == "e1"
        assert summary["success"] is True
        assert summary["capabilities_executed"] == ["service.restart", "file.read"]
        assert summary["iterations"] == 2
        assert summary["total_tool_calls"] == 3
        assert summary["total_duration_ms"] == 1500
        assert summary["incident_id"] == "inc-1"

    def test_get_summary_empty_capabilities(self) -> None:
        """get_summary() returns empty list for empty capabilities_executed."""
        init_conn, init_ctx = _make_mock_conn()
        store = self._make_store(init_ctx)

        rows = [
            {
                "execution_id": "e2",
                "started_at": "2025-01-01T00:00:00",
                "user_input": "check disk",
                "classified_intent": "system_question",
                "success": 1,
                "incident_id": None,
                "iterations": 1,
                "total_tool_calls": 0,
                "capabilities_executed": "",
                "total_duration_ms": 200,
            },
        ]
        sum_conn, sum_ctx = _make_mock_conn(rows=rows)

        with patch(PATCH_GET_CONN, sum_ctx):
            results = store.get_summary(limit=10)

        assert len(results) == 1
        summary = results[0]
        assert summary["capabilities_executed"] == []
        assert summary["incident_id"] is None


# ==========================================================================
# Module-level function tests
# ==========================================================================


class TestGetExecutionStore:
    """Tests for get_execution_store() singleton."""

    def test_creates_singleton(self) -> None:
        """get_execution_store() creates an ExecutionStore on first call."""
        mock_conn, ctx_mgr = _make_mock_conn()

        with patch(PATCH_GET_CONN, ctx_mgr):
            store = get_execution_store()

        assert isinstance(store, ExecutionStore)

    def test_returns_same_instance(self) -> None:
        """get_execution_store() returns the same instance on subsequent calls."""
        mock_conn, ctx_mgr = _make_mock_conn()

        with patch(PATCH_GET_CONN, ctx_mgr):
            store1 = get_execution_store()
            store2 = get_execution_store()

        assert store1 is store2


class TestModuleLevelDelegation:
    """Tests that module-level functions delegate to ExecutionStore."""

    def test_store_execution_delegates(self) -> None:
        """store_execution() calls store.store()."""
        mock_store = MagicMock(spec=ExecutionStore)
        ex = _make_execution()

        with patch("elle.cli.agentic.execution.get_execution_store", return_value=mock_store):
            store_execution(ex)

        mock_store.store.assert_called_once_with(ex)

    def test_get_execution_delegates(self) -> None:
        """get_execution() calls store.get()."""
        mock_store = MagicMock(spec=ExecutionStore)
        mock_store.get.return_value = None

        with patch("elle.cli.agentic.execution.get_execution_store", return_value=mock_store):
            result = get_execution("some-id")

        mock_store.get.assert_called_once_with("some-id")
        assert result is None

    def test_get_recent_executions_delegates(self) -> None:
        """get_recent_executions() calls store.get_recent()."""
        mock_store = MagicMock(spec=ExecutionStore)
        mock_store.get_recent.return_value = []

        with patch("elle.cli.agentic.execution.get_execution_store", return_value=mock_store):
            result = get_recent_executions(limit=5)

        mock_store.get_recent.assert_called_once_with(5)
        assert result == []

    def test_get_execution_summaries_delegates(self) -> None:
        """get_execution_summaries() calls store.get_summary()."""
        mock_store = MagicMock(spec=ExecutionStore)
        mock_store.get_summary.return_value = []

        with patch("elle.cli.agentic.execution.get_execution_store", return_value=mock_store):
            result = get_execution_summaries(limit=3)

        mock_store.get_summary.assert_called_once_with(3)
        assert result == []
