"""Tests for engine incident handling."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from elle.cli.engine import Engine, EngineResult
from elle.common.session import create_session
from elle.daemon.incidents.models import IncidentReport


class TestEngineIncidentCommands:
    """Tests for incident-related commands in the engine."""

    @pytest.fixture
    def engine(self):
        """Create engine instance."""
        return Engine()

    @pytest.fixture
    def session(self):
        """Create session instance."""
        return create_session()

    @pytest.fixture
    def sample_incidents(self):
        """Create sample incidents for testing."""
        return [
            IncidentReport(
                incident_id="abc123def456",
                title="nginx.service failed to start",
                domain="service",
                severity="error",
                status="resolved",
                outcome="improved",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            IncidentReport(
                incident_id="xyz789abc012",
                title="/dev/sda low space warning",
                domain="disk",
                severity="warning",
                status="open",
                outcome="unknown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        ]

    def test_is_incident_command_basic(self, engine):
        """Test incident command detection."""
        assert engine._is_incident_command("incidents")
        assert engine._is_incident_command("incident abc123")
        assert engine._is_incident_command("show me recent incidents")
        assert engine._is_incident_command("list incidents")
        assert engine._is_incident_command("incidents search disk")

    def test_is_incident_command_negative(self, engine):
        """Test non-incident commands are rejected."""
        assert not engine._is_incident_command("help")
        assert not engine._is_incident_command("status")
        assert not engine._is_incident_command("ls -la")

    def test_list_incidents_empty(self, engine, session):
        """Test listing incidents when none exist."""
        with patch("elle.daemon.incidents.store.list_incidents") as mock_list:
            mock_list.return_value = []
            result = engine._list_incidents(session)

        assert result.success
        assert "No incidents" in result.output

    def test_list_incidents_with_data(self, engine, session, sample_incidents):
        """Test listing incidents when data exists."""
        with patch("elle.daemon.incidents.store.list_incidents") as mock_list:
            mock_list.return_value = sample_incidents
            result = engine._list_incidents(session)

        assert result.success
        assert "nginx" in result.output or "abc123" in result.output

    def test_show_incident_detail_not_found(self, engine, session):
        """Test showing incident that doesn't exist."""
        with patch("elle.daemon.incidents.store.get_incident") as mock_get:
            mock_get.return_value = None
            with patch("elle.daemon.incidents.store.list_incidents") as mock_list:
                mock_list.return_value = []
                result = engine._show_incident_detail("nonexistent", session)

        assert not result.success
        assert "not found" in result.output.lower()

    def test_show_incident_detail_found(self, engine, session, sample_incidents):
        """Test showing existing incident."""
        with patch("elle.daemon.incidents.store.get_incident") as mock_get:
            mock_get.return_value = sample_incidents[0]
            with patch("elle.daemon.incidents.store.get_actions") as mock_actions:
                mock_actions.return_value = []
                with patch("elle.daemon.incidents.store.get_snapshots") as mock_snapshots:
                    mock_snapshots.return_value = {}
                    result = engine._show_incident_detail("abc123def456", session)

        assert result.success
        assert "nginx" in result.output

    def test_search_incidents_no_results(self, engine, session):
        """Test searching incidents with no results."""
        with patch("elle.daemon.incidents.retriever.search") as mock_search:
            mock_search.return_value = []
            result = engine._search_incidents("nonexistent", session)

        assert result.success
        assert "No incidents found" in result.output

    def test_handle_incidents_routes_to_list(self, engine, session, sample_incidents):
        """Test that 'incidents' routes to list."""
        with patch.object(engine, "_list_incidents") as mock_list:
            mock_list.return_value = EngineResult(output="test", session=session)
            engine._handle_incidents("incidents", session)

        mock_list.assert_called_once()

    def test_handle_incidents_routes_to_detail(self, engine, session):
        """Test that 'incident <id>' routes to detail."""
        with patch.object(engine, "_show_incident_detail") as mock_detail:
            mock_detail.return_value = EngineResult(output="test", session=session)
            engine._handle_incidents("incident abc123", session)

        mock_detail.assert_called_once()
        # Check that the incident ID was extracted
        args = mock_detail.call_args
        assert "abc123" in args[0]

    def test_handle_incidents_routes_to_search(self, engine, session):
        """Test that 'incidents search <term>' routes to search."""
        with patch.object(engine, "_search_incidents") as mock_search:
            mock_search.return_value = EngineResult(output="test", session=session)
            engine._handle_incidents("incidents search disk full", session)

        mock_search.assert_called_once()
        args = mock_search.call_args
        assert "disk full" in args[0]


class TestEngineIncidentNaturalLanguage:
    """Tests for natural language incident commands."""

    @pytest.fixture
    def engine(self):
        """Create engine instance."""
        return Engine()

    def test_show_me_recent_incidents(self, engine):
        """Test natural language detection."""
        assert engine._is_incident_command("show me recent incidents")

    def test_what_incidents_do_we_have(self, engine):
        """Test question-style incident query."""
        assert engine._is_incident_command("what incidents do we have")

    def test_any_recent_incidents(self, engine):
        """Test abbreviated query."""
        assert engine._is_incident_command("any recent incidents")

    def test_export_my_incident_reports(self, engine):
        """Test export command detection."""
        assert engine._is_incident_command("export my incident reports")

    def test_find_incidents_about(self, engine):
        """Test find command detection."""
        assert engine._is_incident_command("find incidents about disk")
