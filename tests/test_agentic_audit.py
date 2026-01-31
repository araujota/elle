"""Tests for elle.cli.agentic.audit -- Audit trail recording.

Covers:
- ToolCallRecord model and factory
- VerificationResult model
- Provenance model and from_context()
- AuditRecord model, properties, and provenance aggregation
- AuditRecorder lifecycle: start/add_*/complete/fail
- AuditRecorder persistence and retrieval: _persist, get, list_recent, list_by_incident
- AuditRecorder _row_to_record deserialization
- Module-level singleton: get_audit_recorder / reset_audit_recorder
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.agentic.audit import (
    DEFAULT_AUDIT_SCHEMA,
    AuditRecord,
    AuditRecorder,
    Provenance,
    ToolCallRecord,
    VerificationResult,
    get_audit_recorder,
    reset_audit_recorder,
)
from elle.cli.agentic.retrieval_pipeline import (
    CapabilityMatch,
    IncidentMatch,
    ManSnippet,
    RetrievalContext,
)
from elle.cli.agentic.unified_input import AgenticInput, InputSource

# =============================================================================
# Helpers
# =============================================================================


def _make_input(**kwargs: Any) -> AgenticInput:
    defaults: dict[str, Any] = {
        "source": InputSource.USER,
        "user_message": "why is nginx failing?",
    }
    defaults.update(kwargs)
    return AgenticInput(**defaults)


def _make_tool_result(
    success: bool = True,
    output: str = "ok",
    error: str | None = None,
    duration_ms: int = 50,
) -> MagicMock:
    mock = MagicMock()
    mock.success = success
    mock.output = output
    mock.error = error
    mock.duration_ms = duration_ms
    return mock


# =============================================================================
# ToolCallRecord
# =============================================================================


class TestToolCallRecord:
    def test_basic_creation(self) -> None:
        record = ToolCallRecord(
            call_id="test-id",
            tool_name="shell_command",
            arguments={"cmd": "ls"},
            result_success=True,
            result_output="output",
        )
        assert record.call_id == "test-id"
        assert record.tool_name == "shell_command"
        assert record.result_success is True
        assert record.result_output == "output"
        assert record.result_error is None
        assert record.duration_ms == 0
        assert record.iteration == 1

    def test_from_tool_result_success(self) -> None:
        result = _make_tool_result(success=True, output="done", duration_ms=120)
        record = ToolCallRecord.from_tool_result(
            tool_name="execute_capability",
            arguments={"cap": "service.restart"},
            result=result,
            iteration=3,
        )
        assert record.tool_name == "execute_capability"
        assert record.arguments == {"cap": "service.restart"}
        assert record.result_success is True
        assert record.result_output == "done"
        assert record.result_error is None
        assert record.duration_ms == 120
        assert record.iteration == 3
        assert record.call_id  # UUID is generated

    def test_from_tool_result_failure(self) -> None:
        result = _make_tool_result(success=False, output="", error="timeout")
        record = ToolCallRecord.from_tool_result(
            tool_name="shell_command",
            arguments={"cmd": "slow_cmd"},
            result=result,
        )
        assert record.result_success is False
        assert record.result_error == "timeout"

    def test_from_tool_result_missing_attrs(self) -> None:
        """When the result object lacks attributes, defaults are used."""
        bare_result = object()
        record = ToolCallRecord.from_tool_result(
            tool_name="test",
            arguments={},
            result=bare_result,
        )
        assert record.result_success is False
        assert record.result_output == ""
        assert record.result_error is None
        assert record.duration_ms == 0

    def test_frozen_model(self) -> None:
        record = ToolCallRecord(
            call_id="id",
            tool_name="test",
            result_success=True,
        )
        with pytest.raises(Exception):
            record.tool_name = "changed"  # type: ignore[misc]


# =============================================================================
# VerificationResult
# =============================================================================


class TestVerificationResult:
    def test_basic_creation(self) -> None:
        vr = VerificationResult(
            verification_id="v-1",
            tool_call_id="tc-1",
            passed=True,
            method="exit_code_check",
            evidence="exit code was 0",
        )
        assert vr.passed is True
        assert vr.method == "exit_code_check"
        assert vr.evidence == "exit code was 0"

    def test_failed_verification(self) -> None:
        vr = VerificationResult(
            verification_id="v-2",
            tool_call_id="tc-2",
            passed=False,
            method="pattern_match",
            evidence="expected 'active' but got 'inactive'",
        )
        assert vr.passed is False

    def test_default_evidence(self) -> None:
        vr = VerificationResult(
            verification_id="v-3",
            tool_call_id="tc-3",
            passed=True,
            method="auto",
        )
        assert vr.evidence == ""


# =============================================================================
# Provenance
# =============================================================================


class TestProvenance:
    def test_empty_provenance(self) -> None:
        p = Provenance()
        assert p.incident_ids == ()
        assert p.man_pages == ()
        assert p.capabilities_considered == ()
        assert p.reasoning_steps == ()

    def test_populated_provenance(self) -> None:
        p = Provenance(
            incident_ids=("inc-1", "inc-2"),
            man_pages=("nginx(8)", "systemctl(1)"),
            capabilities_considered=("service.restart",),
            reasoning_steps=("Step 1", "Step 2"),
        )
        assert len(p.incident_ids) == 2
        assert "nginx(8)" in p.man_pages

    def test_from_context(self) -> None:
        """Test Provenance.from_context with a mocked RetrievalContext."""
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-abc"

        mock_snippet = MagicMock()
        mock_snippet.name = "nginx"
        mock_snippet.section = "8"

        mock_cap = MagicMock()
        mock_cap.capability_name = "service.restart"

        mock_ctx = MagicMock()
        mock_ctx.prior_incidents = [mock_incident]
        mock_ctx.man_vault_snippets = [mock_snippet]
        mock_ctx.capability_matches = [mock_cap]

        prov = Provenance.from_context(mock_ctx)
        assert "inc-abc" in prov.incident_ids
        assert "nginx(8)" in prov.man_pages
        assert "service.restart" in prov.capabilities_considered


# =============================================================================
# AuditRecord
# =============================================================================


class TestAuditRecord:
    def _make_audit_record(self, **kwargs: Any) -> AuditRecord:
        defaults: dict[str, Any] = {
            "trigger": _make_input(),
        }
        defaults.update(kwargs)
        return AuditRecord(**defaults)

    def test_default_creation(self) -> None:
        record = self._make_audit_record()
        assert record.execution_id  # UUID generated
        assert record.started_at is not None
        assert record.completed_at is None
        assert record.success is False
        assert record.iterations == 0

    def test_duration_ms_no_completion(self) -> None:
        record = self._make_audit_record()
        assert record.duration_ms == 0

    def test_duration_ms_with_completion(self) -> None:
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 0, 0, 2, 500000, tzinfo=timezone.utc)
        record = self._make_audit_record(started_at=start, completed_at=end)
        assert record.duration_ms == 2500

    def test_tool_call_count(self) -> None:
        tc1 = ToolCallRecord(call_id="1", tool_name="a", result_success=True)
        tc2 = ToolCallRecord(call_id="2", tool_name="b", result_success=False)
        record = self._make_audit_record(tool_calls=(tc1, tc2))
        assert record.tool_call_count == 2

    def test_successful_tool_calls(self) -> None:
        tc1 = ToolCallRecord(call_id="1", tool_name="a", result_success=True)
        tc2 = ToolCallRecord(call_id="2", tool_name="b", result_success=False)
        tc3 = ToolCallRecord(call_id="3", tool_name="c", result_success=True)
        record = self._make_audit_record(tool_calls=(tc1, tc2, tc3))
        successful = record.successful_tool_calls
        assert len(successful) == 2
        assert all(tc.result_success for tc in successful)

    def test_provenance_aggregation(self) -> None:
        """Test that provenance aggregates across multiple retrieval contexts."""
        inp = _make_input()
        ctx1 = RetrievalContext(
            input=inp,
            prior_incidents=(
                IncidentMatch(
                    incident_id="inc-1", title="t", summary="s", domain="net",
                    outcome="resolved", outcome_weight=1.0, similarity_score=0.9, days_ago=1,
                ),
            ),
            man_vault_snippets=(
                ManSnippet(name="nginx", section="8", snippet="web", score=0.9),
            ),
        )
        ctx2 = RetrievalContext(
            input=inp,
            prior_incidents=(
                IncidentMatch(
                    incident_id="inc-2", title="t2", summary="s2", domain="disk",
                    outcome="partial", outcome_weight=0.5, similarity_score=0.7, days_ago=3,
                ),
            ),
            capability_matches=(
                CapabilityMatch(
                    capability_name="service.restart", domain="service",
                    description="Restart a service", risk_level="medium",
                    match_score=0.8, success_rate=0.95, is_approved=True,
                ),
            ),
        )
        record = self._make_audit_record(retrieval_contexts=(ctx1, ctx2))
        prov = record.provenance
        assert "inc-1" in prov.incident_ids
        assert "inc-2" in prov.incident_ids
        assert "nginx(8)" in prov.man_pages
        assert "service.restart" in prov.capabilities_considered

    def test_provenance_deduplication(self) -> None:
        """Provenance deduplicates incident IDs, man pages, and capabilities."""
        inp = _make_input()
        incident = IncidentMatch(
            incident_id="inc-dup", title="t", summary="s", domain="net",
            outcome="resolved", outcome_weight=1.0, similarity_score=0.9, days_ago=1,
        )
        ctx1 = RetrievalContext(input=inp, prior_incidents=(incident,))
        ctx2 = RetrievalContext(input=inp, prior_incidents=(incident,))

        record = self._make_audit_record(retrieval_contexts=(ctx1, ctx2))
        prov = record.provenance
        # Sets deduplicate
        assert prov.incident_ids.count("inc-dup") == 1

    def test_provenance_empty_contexts(self) -> None:
        record = self._make_audit_record()
        prov = record.provenance
        assert prov.incident_ids == ()
        assert prov.man_pages == ()
        assert prov.capabilities_considered == ()


# =============================================================================
# AuditRecorder - init and schema
# =============================================================================


class TestAuditRecorderInit:
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_default_schema(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        assert recorder._schema == DEFAULT_AUDIT_SCHEMA
        mock_ensure.assert_called_once()

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_custom_schema(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder(schema="custom_schema")
        assert recorder._schema == "custom_schema"

    @patch("elle.storage.engine.get_conn")
    def test_ensure_schema_success(self, mock_get_conn: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        AuditRecorder()
        assert mock_conn.execute.called

    @patch("elle.storage.engine.get_conn", side_effect=RuntimeError("DB unavailable"))
    def test_ensure_schema_failure_logs(self, mock_get_conn: MagicMock, caplog: Any) -> None:
        import logging

        with caplog.at_level(logging.ERROR):
            AuditRecorder()
        assert "Failed to initialize audit database" in caplog.text


# =============================================================================
# AuditRecorder - lifecycle: start/add/complete/fail
# =============================================================================


class TestAuditRecorderLifecycle:
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_start_creates_record(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        inp = _make_input()
        record = recorder.start(inp)
        assert isinstance(record, AuditRecord)
        assert record.trigger == inp
        assert record.execution_id in recorder._active_records

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_start_with_incident_id(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        inp = _make_input(incident_id="inc-999")
        record = recorder.start(inp)
        assert record.linked_incident_id == "inc-999"

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_add_retrieval(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        record = recorder.start(_make_input())
        mock_ctx = MagicMock()
        recorder.add_retrieval(record.execution_id, mock_ctx)
        assert mock_ctx in recorder._active_records[record.execution_id]["retrieval_contexts"]

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_add_retrieval_unknown_id_ignored(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        mock_ctx = MagicMock()
        recorder.add_retrieval("unknown-id", mock_ctx)
        # No error raised

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_add_tool_call(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        record = recorder.start(_make_input())
        tc = ToolCallRecord(call_id="tc-1", tool_name="test", result_success=True)
        recorder.add_tool_call(record.execution_id, tc)
        assert tc in recorder._active_records[record.execution_id]["tool_calls"]

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_add_tool_call_unknown_id(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        tc = ToolCallRecord(call_id="tc-1", tool_name="test", result_success=True)
        recorder.add_tool_call("unknown", tc)
        # No error

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_add_verification(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        record = recorder.start(_make_input())
        vr = VerificationResult(
            verification_id="v-1",
            tool_call_id="tc-1",
            passed=True,
            method="check",
        )
        recorder.add_verification(record.execution_id, vr)
        assert vr in recorder._active_records[record.execution_id]["verifications"]

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_add_verification_unknown_id(self, mock_ensure: MagicMock) -> None:
        recorder = AuditRecorder()
        vr = VerificationResult(
            verification_id="v-1",
            tool_call_id="tc-1",
            passed=True,
            method="check",
        )
        recorder.add_verification("unknown", vr)
        # No error

    @patch("elle.cli.agentic.audit.AuditRecorder._persist")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_complete_returns_record(self, mock_ensure: MagicMock, mock_persist: MagicMock) -> None:
        recorder = AuditRecorder()
        base = recorder.start(_make_input())
        completed = recorder.complete(
            base.execution_id,
            final_response="Done",
            success=True,
            iterations=3,
        )
        assert completed is not None
        assert completed.final_response == "Done"
        assert completed.success is True
        assert completed.iterations == 3
        assert completed.completed_at is not None
        # Record should be removed from active
        assert base.execution_id not in recorder._active_records
        mock_persist.assert_called_once()

    @patch("elle.cli.agentic.audit.AuditRecorder._persist")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_complete_unknown_id_returns_none(self, mock_ensure: MagicMock, mock_persist: MagicMock) -> None:
        recorder = AuditRecorder()
        result = recorder.complete("nonexistent", final_response="X")
        assert result is None
        mock_persist.assert_not_called()

    @patch("elle.cli.agentic.audit.AuditRecorder._persist")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_complete_includes_accumulated_data(self, mock_ensure: MagicMock, mock_persist: MagicMock) -> None:
        recorder = AuditRecorder()
        base = recorder.start(_make_input())
        tc = ToolCallRecord(call_id="tc-1", tool_name="test", result_success=True)
        recorder.add_tool_call(base.execution_id, tc)
        vr = VerificationResult(
            verification_id="v-1",
            tool_call_id="tc-1",
            passed=True,
            method="auto",
        )
        recorder.add_verification(base.execution_id, vr)
        completed = recorder.complete(base.execution_id, final_response="ok")
        assert completed is not None
        assert len(completed.tool_calls) == 1
        assert len(completed.verifications) == 1

    @patch("elle.cli.agentic.audit.AuditRecorder._persist")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_fail_returns_record(self, mock_ensure: MagicMock, mock_persist: MagicMock) -> None:
        recorder = AuditRecorder()
        base = recorder.start(_make_input())
        failed = recorder.fail(base.execution_id, error="timeout", iterations=2)
        assert failed is not None
        assert failed.success is False
        assert failed.error == "timeout"
        assert failed.iterations == 2
        assert failed.completed_at is not None
        assert base.execution_id not in recorder._active_records
        mock_persist.assert_called_once()

    @patch("elle.cli.agentic.audit.AuditRecorder._persist")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_fail_unknown_id_returns_none(self, mock_ensure: MagicMock, mock_persist: MagicMock) -> None:
        recorder = AuditRecorder()
        result = recorder.fail("nonexistent", error="error")
        assert result is None
        mock_persist.assert_not_called()

    @patch("elle.cli.agentic.audit.AuditRecorder._persist")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_fail_includes_accumulated_data(self, mock_ensure: MagicMock, mock_persist: MagicMock) -> None:
        recorder = AuditRecorder()
        base = recorder.start(_make_input())
        tc = ToolCallRecord(call_id="tc-1", tool_name="failed_tool", result_success=False, result_error="kaboom")
        recorder.add_tool_call(base.execution_id, tc)
        failed = recorder.fail(base.execution_id, error="fatal")
        assert failed is not None
        assert len(failed.tool_calls) == 1
        assert failed.tool_calls[0].tool_name == "failed_tool"


# =============================================================================
# AuditRecorder - _persist
# =============================================================================


class TestAuditRecorderPersist:
    @patch("elle.storage.engine.get_conn")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_persist_success(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        recorder = AuditRecorder()
        record = AuditRecord(
            trigger=_make_input(),
            completed_at=datetime.now(timezone.utc),
            success=True,
            final_response="all good",
        )
        recorder._persist(record)
        mock_conn.execute.assert_called_once()

    @patch("elle.storage.engine.get_conn", side_effect=RuntimeError("DB error"))
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_persist_failure_logs(self, mock_ensure: MagicMock, mock_get_conn: MagicMock, caplog: Any) -> None:
        import logging

        recorder = AuditRecorder()
        record = AuditRecord(
            trigger=_make_input(),
            completed_at=datetime.now(timezone.utc),
        )
        with caplog.at_level(logging.ERROR):
            recorder._persist(record)
        assert "Failed to persist audit record" in caplog.text


# =============================================================================
# AuditRecorder - get
# =============================================================================


class TestAuditRecorderGet:
    @patch("elle.storage.engine.get_conn")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_get_found(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        inp = _make_input()
        row = {
            "execution_id": "exec-1",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "trigger_json": inp.model_dump_json(),
            "linked_incident_id": None,
            "retrieval_contexts_json": "[]",
            "tool_calls_json": "[]",
            "verifications_json": "[]",
            "final_response": "done",
            "success": 1,
            "error": None,
            "iterations": 2,
            "provenance_json": "{}",
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = row
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        recorder = AuditRecorder()
        result = recorder.get("exec-1")
        assert result is not None
        assert result.execution_id == "exec-1"
        assert result.success is True

    @patch("elle.storage.engine.get_conn")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_get_not_found(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        recorder = AuditRecorder()
        result = recorder.get("nonexistent")
        assert result is None

    @patch("elle.storage.engine.get_conn", side_effect=RuntimeError("DB error"))
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_get_exception_returns_none(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        recorder = AuditRecorder()
        result = recorder.get("exec-1")
        assert result is None


# =============================================================================
# AuditRecorder - list_recent
# =============================================================================


class TestAuditRecorderListRecent:
    @patch("elle.storage.engine.get_conn")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_list_recent_returns_records(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        inp = _make_input()
        row = {
            "execution_id": "exec-recent",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "trigger_json": inp.model_dump_json(),
            "linked_incident_id": None,
            "retrieval_contexts_json": "[]",
            "tool_calls_json": "[]",
            "verifications_json": "[]",
            "final_response": "done",
            "success": 1,
            "error": None,
            "iterations": 1,
            "provenance_json": "{}",
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [row]
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        recorder = AuditRecorder()
        records = recorder.list_recent(limit=10)
        assert len(records) == 1
        assert records[0].execution_id == "exec-recent"

    @patch("elle.storage.engine.get_conn")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_list_recent_empty(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        recorder = AuditRecorder()
        records = recorder.list_recent()
        assert records == []

    @patch("elle.storage.engine.get_conn", side_effect=RuntimeError("DB"))
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_list_recent_exception(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        recorder = AuditRecorder()
        records = recorder.list_recent()
        assert records == []


# =============================================================================
# AuditRecorder - list_by_incident
# =============================================================================


class TestAuditRecorderListByIncident:
    @patch("elle.storage.engine.get_conn")
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_list_by_incident(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        inp = _make_input()
        row = {
            "execution_id": "exec-inc",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "trigger_json": inp.model_dump_json(),
            "linked_incident_id": "inc-123",
            "retrieval_contexts_json": "[]",
            "tool_calls_json": "[]",
            "verifications_json": "[]",
            "final_response": None,
            "success": 0,
            "error": None,
            "iterations": 0,
            "provenance_json": "{}",
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [row]
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        recorder = AuditRecorder()
        records = recorder.list_by_incident("inc-123")
        assert len(records) == 1
        assert records[0].linked_incident_id == "inc-123"

    @patch("elle.storage.engine.get_conn", side_effect=RuntimeError("DB"))
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_list_by_incident_exception(self, mock_ensure: MagicMock, mock_get_conn: MagicMock) -> None:
        recorder = AuditRecorder()
        records = recorder.list_by_incident("inc-fail")
        assert records == []


# =============================================================================
# AuditRecorder - _row_to_record
# =============================================================================


class TestRowToRecord:
    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_row_to_record_success(self, mock_ensure: MagicMock) -> None:
        inp = _make_input()
        tc_data = [
            {
                "call_id": "tc-1",
                "tool_name": "test",
                "arguments": {},
                "result_success": True,
                "result_output": "ok",
                "result_error": None,
                "duration_ms": 10,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "iteration": 1,
            }
        ]
        vr_data = [
            {
                "verification_id": "v-1",
                "tool_call_id": "tc-1",
                "passed": True,
                "method": "auto",
                "evidence": "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
        row = {
            "execution_id": "exec-row",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "trigger_json": inp.model_dump_json(),
            "linked_incident_id": "inc-linked",
            "retrieval_contexts_json": "[]",
            "tool_calls_json": json.dumps(tc_data, default=str),
            "verifications_json": json.dumps(vr_data, default=str),
            "final_response": "result",
            "success": 1,
            "error": None,
            "iterations": 5,
            "provenance_json": "{}",
        }
        recorder = AuditRecorder()
        record = recorder._row_to_record(row)
        assert record is not None
        assert record.execution_id == "exec-row"
        assert record.linked_incident_id == "inc-linked"
        assert record.success is True
        assert record.iterations == 5
        assert len(record.tool_calls) == 1
        assert len(record.verifications) == 1

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_row_to_record_invalid_json(self, mock_ensure: MagicMock) -> None:
        row = {
            "execution_id": "bad",
            "started_at": "not-a-date",
            "completed_at": None,
            "trigger_json": "INVALID_JSON",
            "linked_incident_id": None,
            "retrieval_contexts_json": "[]",
            "tool_calls_json": "[]",
            "verifications_json": "[]",
            "final_response": None,
            "success": 0,
            "error": None,
            "iterations": 0,
            "provenance_json": "{}",
        }
        recorder = AuditRecorder()
        record = recorder._row_to_record(row)
        assert record is None

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_row_to_record_null_tool_calls(self, mock_ensure: MagicMock) -> None:
        inp = _make_input()
        row = {
            "execution_id": "exec-null-tc",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "trigger_json": inp.model_dump_json(),
            "linked_incident_id": None,
            "retrieval_contexts_json": "[]",
            "tool_calls_json": None,
            "verifications_json": None,
            "final_response": None,
            "success": 0,
            "error": None,
            "iterations": 0,
            "provenance_json": "{}",
        }
        recorder = AuditRecorder()
        record = recorder._row_to_record(row)
        assert record is not None
        assert record.tool_calls == ()
        assert record.verifications == ()


# =============================================================================
# Module-level singleton
# =============================================================================


class TestAuditSingleton:
    def setup_method(self) -> None:
        reset_audit_recorder()

    def teardown_method(self) -> None:
        reset_audit_recorder()

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_get_audit_recorder(self, mock_ensure: MagicMock) -> None:
        recorder = get_audit_recorder()
        assert isinstance(recorder, AuditRecorder)

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_get_returns_same_instance(self, mock_ensure: MagicMock) -> None:
        a = get_audit_recorder()
        b = get_audit_recorder()
        assert a is b

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_reset_clears(self, mock_ensure: MagicMock) -> None:
        a = get_audit_recorder()
        reset_audit_recorder()
        b = get_audit_recorder()
        assert a is not b

    @patch("elle.cli.agentic.audit.AuditRecorder._ensure_schema")
    def test_custom_schema_creates_new(self, mock_ensure: MagicMock) -> None:
        a = get_audit_recorder()
        b = get_audit_recorder(schema="different_schema")
        assert a is not b
        assert b._schema == "different_schema"
