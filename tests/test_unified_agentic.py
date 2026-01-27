"""Tests for the unified agentic system.

Tests the core components:
- AgenticInput: Unified input model
- RetrievalContext: Multi-source retrieval
- AuditRecord: Audit trail recording
- AgenticLoop integration (with mocked LLM)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# AgenticInput Tests
# =============================================================================


def test_agentic_input_from_user_message():
    """Test creating AgenticInput from user message."""
    from elle.cli.agentic.unified_input import AgenticInput, InputSource

    input = AgenticInput.from_user_message(
        "why is nginx failing?",
        session_id="test-session",
        entities=("service:nginx",),
        domain_hint="service",
    )

    assert input.source == InputSource.USER
    assert input.user_message == "why is nginx failing?"
    assert input.session_id == "test-session"
    assert input.entities == ("service:nginx",)
    assert input.domain_hint == "service"
    assert input.query_text == "why is nginx failing?"
    assert input.is_task is False  # Question, not task


def test_agentic_input_from_user_message_task():
    """Test creating AgenticInput from a task request."""
    from elle.cli.agentic.unified_input import AgenticInput, InputSource

    input = AgenticInput.from_user_message("restart nginx")

    assert input.source == InputSource.USER
    assert input.is_task is True  # Contains "restart"


def test_agentic_input_from_incident():
    """Test creating AgenticInput from incident."""
    from elle.cli.agentic.unified_input import AgenticInput, InputSource

    input = AgenticInput.from_incident(
        "incident-123",
        auto_triggered=True,
        session_id="test-session",
    )

    assert input.source == InputSource.INCIDENT_AUTO
    assert input.incident_id == "incident-123"
    assert input.session_id == "test-session"
    assert input.is_task is True  # Incidents are always tasks
    assert input.query_text == "incident:incident-123"


def test_agentic_input_from_failed_command():
    """Test creating AgenticInput from failed command."""
    from elle.cli.agentic.unified_input import AgenticInput, InputSource

    input = AgenticInput.from_failed_command(
        "nginx -t",
        exit_code=1,
        stdout="",
        stderr="nginx: configuration file test failed",
    )

    assert input.source == InputSource.COMMAND_FAILURE
    assert input.failed_command is not None
    assert input.failed_command.command == "nginx -t"
    assert input.failed_command.exit_code == 1
    assert "configuration file test failed" in input.failed_command.stderr
    assert input.is_task is True
    assert "nginx -t" in input.query_text


def test_system_fingerprint_from_empty():
    """Test creating empty system fingerprint."""
    from elle.cli.agentic.unified_input import SystemFingerprint

    fp = SystemFingerprint.empty()

    assert fp.memory_pressure == 0.0
    assert fp.disk_pressure == 0.0
    assert fp.cpu_pressure == 0.0
    assert fp.failed_services == ()
    assert fp.unhealthy_containers == ()


# =============================================================================
# RetrievalContext Tests
# =============================================================================


def test_retrieval_context_empty():
    """Test empty retrieval context."""
    from elle.cli.agentic.retrieval_pipeline import RetrievalContext
    from elle.cli.agentic.unified_input import AgenticInput

    input = AgenticInput.from_user_message("test query")
    context = RetrievalContext(input=input)

    assert context.has_prior_art is False
    assert context.has_documentation is False
    assert context.has_capabilities is False
    assert context.best_prior_incident is None


def test_retrieval_context_with_incidents():
    """Test retrieval context with prior incidents."""
    from elle.cli.agentic.retrieval_pipeline import IncidentMatch, RetrievalContext
    from elle.cli.agentic.unified_input import AgenticInput

    input = AgenticInput.from_user_message("nginx failing")
    incidents = (
        IncidentMatch(
            incident_id="inc-1",
            title="Nginx crash",
            summary="Service crashed due to config error",
            domain="service",
            outcome="resolved",
            outcome_weight=0.9,
            similarity_score=0.85,
            days_ago=5,
            root_cause="Invalid config syntax",
            decision="Fixed config and restarted",
        ),
    )

    context = RetrievalContext(input=input, prior_incidents=incidents)

    assert context.has_prior_art is True
    assert context.best_prior_incident is not None
    assert context.best_prior_incident.incident_id == "inc-1"


def test_retrieval_context_format_for_prompt():
    """Test formatting retrieval context for LLM prompt."""
    from elle.cli.agentic.retrieval_pipeline import (
        CapabilityMatch,
        IncidentMatch,
        ManSnippet,
        RetrievalContext,
    )
    from elle.cli.agentic.unified_input import AgenticInput

    input = AgenticInput.from_user_message("restart nginx")

    incidents = (
        IncidentMatch(
            incident_id="inc-1",
            title="Nginx restart issue",
            summary="Service failed to restart",
            domain="service",
            outcome="resolved",
            outcome_weight=0.9,
            similarity_score=0.8,
            days_ago=3,
        ),
    )

    capabilities = (
        CapabilityMatch(
            capability_name="service.restart",
            domain="service",
            description="Restart a systemd service",
            risk_level="medium",
            match_score=0.9,
            success_rate=0.95,
            is_approved=True,
        ),
    )

    snippets = (
        ManSnippet(
            name="systemctl",
            section="1",
            snippet="systemctl restart UNIT - Restart one or more units",
            score=0.8,
        ),
    )

    context = RetrievalContext(
        input=input,
        prior_incidents=incidents,
        capability_matches=capabilities,
        man_vault_snippets=snippets,
    )

    formatted = context.format_for_prompt()

    assert "Similar Past Incidents" in formatted
    assert "Nginx restart issue" in formatted
    assert "Available Capabilities" in formatted
    assert "service.restart" in formatted
    assert "Documentation" in formatted
    assert "systemctl" in formatted


# =============================================================================
# AuditRecord Tests
# =============================================================================


def test_audit_record_creation():
    """Test creating audit records."""
    from elle.cli.agentic.audit import AuditRecord
    from elle.cli.agentic.unified_input import AgenticInput

    input = AgenticInput.from_user_message("test query", session_id="test-session")
    record = AuditRecord(trigger=input, linked_incident_id=None)

    assert record.execution_id is not None
    assert record.trigger == input
    assert record.success is False  # Default
    assert record.iterations == 0
    assert record.tool_calls == ()


def test_audit_record_provenance():
    """Test building provenance from audit record."""
    from elle.cli.agentic.audit import AuditRecord
    from elle.cli.agentic.retrieval_pipeline import IncidentMatch, ManSnippet, RetrievalContext
    from elle.cli.agentic.unified_input import AgenticInput

    input = AgenticInput.from_user_message("test")

    incidents = (
        IncidentMatch(
            incident_id="inc-1",
            title="Test",
            summary="Test",
            domain="service",
            outcome="resolved",
            outcome_weight=0.9,
            similarity_score=0.8,
            days_ago=1,
        ),
    )

    snippets = (
        ManSnippet(name="test", section="1", snippet="Test doc", score=0.9),
    )

    context = RetrievalContext(
        input=input,
        prior_incidents=incidents,
        man_vault_snippets=snippets,
    )

    record = AuditRecord(
        trigger=input,
        retrieval_contexts=(context,),
    )

    provenance = record.provenance

    assert "inc-1" in provenance.incident_ids
    assert "test(1)" in provenance.man_pages


def test_audit_recorder_flow():
    """Test audit recorder start/complete flow."""
    from elle.cli.agentic.audit import AuditRecorder, ToolCallRecord
    from elle.cli.agentic.unified_input import AgenticInput

    # Use in-memory database
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    try:
        recorder = AuditRecorder(db_path)
        input = AgenticInput.from_user_message("test query")

        # Start recording
        record = recorder.start(input)
        assert record.execution_id is not None

        # Add tool call
        tool_call = ToolCallRecord(
            call_id="call-1",
            tool_name="search_man_vault",
            arguments={"query": "test"},
            result_success=True,
            result_output="Found 3 results",
            duration_ms=50,
        )
        recorder.add_tool_call(record.execution_id, tool_call)

        # Complete
        final_record = recorder.complete(
            record.execution_id,
            "This is the final response",
            success=True,
            iterations=1,
        )

        assert final_record is not None
        assert final_record.success is True
        assert final_record.final_response == "This is the final response"
        assert len(final_record.tool_calls) == 1

    finally:
        db_path.unlink(missing_ok=True)


# =============================================================================
# CapabilityRetriever Tests
# =============================================================================


def test_capability_search_result_model():
    """Test CapabilitySearchResult model."""
    from elle.capabilities.autogen.retriever import CapabilitySearchResult

    result = CapabilitySearchResult(
        capability_name="service.restart",
        domain="service",
        description="Restart a systemd service",
        risk_level="medium",
        match_type="hybrid",
        score=0.85,
        trust_weight=1.0,
        success_rate=0.9,
        is_approved=True,
        is_enabled=True,
    )

    assert result.capability_name == "service.restart"
    assert result.domain == "service"
    assert result.score == 0.85


# =============================================================================
# AgenticLoop Integration Tests (with mocks)
# =============================================================================


@pytest.mark.asyncio
async def test_agentic_loop_disabled():
    """Test that loop falls back when disabled."""
    from elle.cli.agentic.loop import is_agentic_loop_enabled

    # By default, the loop is disabled
    assert not is_agentic_loop_enabled()


@pytest.mark.asyncio
async def test_agentic_loop_result_model():
    """Test AgenticLoopResult model."""
    from elle.cli.agentic.loop import AgenticLoopResult, ToolCallRecord
    from elle.cli.agentic.tools import ToolResult

    tool_result = ToolResult(
        tool_name="search_man_vault",
        success=True,
        output="Found results",
        duration_ms=100,
    )

    tool_call = ToolCallRecord(
        tool_name="search_man_vault",
        arguments={"query": "test"},
        result=tool_result,
        iteration=1,
        verified=False,
    )

    result = AgenticLoopResult(
        response="This is the response",
        success=True,
        iterations=2,
        total_duration_ms=500,
        tool_calls=(tool_call,),
        execution_id="exec-123",
    )

    assert result.response == "This is the response"
    assert result.tool_call_count == 1
    assert result.total_tokens is None  # No token info
    assert result.execution_id == "exec-123"


# =============================================================================
# Tools Tests
# =============================================================================


def test_search_capabilities_tool_spec():
    """Test that search_capabilities tool is registered."""
    from elle.cli.agentic.tools import get_tool_registry

    registry = get_tool_registry()
    tool_info = registry.get("search_capabilities")

    assert tool_info is not None
    spec, handler = tool_info
    assert spec.name == "search_capabilities"
    assert "natural language" in spec.description.lower()
    assert not spec.is_mutating


def test_all_tools_registered():
    """Test that all expected tools are registered."""
    from elle.cli.agentic.tools import get_tool_registry

    registry = get_tool_registry()

    expected_tools = [
        "search_man_vault",
        "search_incidents",
        "execute_capability",
        "get_system_info",
        "shell_command",
        "list_capabilities",
        "search_capabilities",
    ]

    for tool_name in expected_tools:
        tool_info = registry.get(tool_name)
        assert tool_info is not None, f"Tool {tool_name} not found"


# =============================================================================
# Import Tests
# =============================================================================


def test_imports_from_main_module():
    """Test that all new exports are importable."""
    from elle.cli.agentic import (
        AgenticInput,
        AgenticLoop,
        AgenticLoopResult,
        AuditRecord,
        AuditRecorder,
        CapabilityMatch,
        FailedCommand,
        IncidentMatch,
        InputSource,
        ManSnippet,
        Provenance,
        RetrievalContext,
        SystemFingerprint,
        UnifiedRetrievalPipeline,
        VerificationResult,
        get_audit_recorder,
        get_retrieval_pipeline,
        is_agentic_loop_enabled,
        retrieve_context,
        run_agentic_loop,
        run_for_failed_command,
        run_for_incident,
    )

    # All should be importable without error
    assert AgenticInput is not None
    assert AgenticLoop is not None
    assert AuditRecorder is not None
    assert UnifiedRetrievalPipeline is not None
    assert InputSource is not None


def test_daemon_agentic_handler_import():
    """Test that daemon agentic handler is importable."""
    from elle.daemon.incidents.agentic_handler import (
        IncidentAgenticHandler,
        IncidentHandlingResult,
        get_agentic_handler,
        handle_incident,
    )

    assert IncidentAgenticHandler is not None
    assert IncidentHandlingResult is not None


def test_correlator_has_callback():
    """Test that correlator supports on_incident_created callback."""
    from elle.daemon.incidents.correlator import IncidentCorrelator

    callback_called = []

    def test_callback(incident_id: str) -> None:
        callback_called.append(incident_id)

    correlator = IncidentCorrelator(on_incident_created=test_callback)

    # The callback should be stored
    assert correlator._on_incident_created == test_callback


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
