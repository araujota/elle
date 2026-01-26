"""Tests for daemon REST API endpoints."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

# Skip tests if fastapi not installed
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from elle.daemon.api.app import create_app
from elle.daemon.config import ApiConfig, Config
from elle.daemon.main import ElledDaemon
from elle.daemon.telemetry.models import DaemonStatus, QueueStats


@pytest.fixture
def mock_daemon():
    """Create a mock daemon for testing."""
    daemon = MagicMock(spec=ElledDaemon)
    daemon.config = Config(
        api=ApiConfig(enabled=True),
    )
    daemon.get_status.return_value = DaemonStatus(
        started_at=datetime.now(UTC),
        uptime_sec=100,
        pid=12345,
        journal_active=True,
        kernel_active=True,
        probes_active=True,
        api_active=True,
        raw_queue=QueueStats(name="raw", size=10, max_size=10000, dropped=0, processed=100),
        event_queue=QueueStats(name="events", size=5, max_size=5000, dropped=0, processed=95),
        events_total=500,
        incidents_total=5,
        healthy=True,
        errors=(),
    )
    return daemon


@pytest.fixture
def client(mock_daemon):
    """Create test client with mock daemon."""
    app = create_app(mock_daemon)
    return TestClient(app)


class TestStatusEndpoint:
    """Tests for GET /v1/status endpoint."""

    def test_status_returns_200(self, client):
        """Status endpoint should return 200."""
        response = client.get("/v1/status")
        assert response.status_code == 200

    def test_status_contains_expected_fields(self, client):
        """Status response should contain expected fields."""
        response = client.get("/v1/status")
        data = response.json()

        assert "started_at" in data
        assert "uptime_sec" in data
        assert "pid" in data
        assert "journal_active" in data
        assert "kernel_active" in data
        assert "probes_active" in data
        assert "api_active" in data
        assert "healthy" in data

    def test_status_reflects_daemon_state(self, client, mock_daemon):
        """Status should reflect daemon state."""
        response = client.get("/v1/status")
        data = response.json()

        assert data["pid"] == 12345
        assert data["uptime_sec"] == 100
        assert data["journal_active"] is True
        assert data["kernel_active"] is True
        assert data["probes_active"] is True
        assert data["healthy"] is True


class TestHealthEndpoint:
    """Tests for GET /v1/health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200."""
        response = client.get("/v1/health")
        assert response.status_code == 200

    def test_health_contains_status(self, client):
        """Health response should contain status field."""
        response = client.get("/v1/health")
        data = response.json()

        assert "status" in data
        assert data["status"] == "healthy"


class TestEventsEndpoint:
    """Tests for GET /v1/events endpoint."""

    def test_events_returns_200(self, client):
        """Events endpoint should return 200."""
        with patch("elle.daemon.api.routes.query_events") as mock_query:
            mock_query.return_value = []
            with patch("elle.daemon.api.routes.count_events") as mock_count:
                mock_count.return_value = 0
                response = client.get("/v1/events")

        assert response.status_code == 200

    def test_events_respects_limit(self, client):
        """Events endpoint should respect limit parameter."""
        with patch("elle.daemon.api.routes.query_events") as mock_query:
            mock_query.return_value = []
            with patch("elle.daemon.api.routes.count_events") as mock_count:
                mock_count.return_value = 0
                response = client.get("/v1/events?limit=50")

        data = response.json()
        assert data["limit"] == 50

    def test_events_validates_limit(self, client):
        """Events endpoint should validate limit range."""
        with patch("elle.daemon.api.routes.query_events") as mock_query:
            mock_query.return_value = []
            with patch("elle.daemon.api.routes.count_events") as mock_count:
                mock_count.return_value = 0

                # Limit too high
                response = client.get("/v1/events?limit=5000")
                assert response.status_code == 422

                # Limit too low
                response = client.get("/v1/events?limit=0")
                assert response.status_code == 422


class TestIncidentsEndpoint:
    """Tests for GET /v1/incidents endpoint."""

    def test_incidents_returns_200(self, client):
        """Incidents endpoint should return 200."""
        with patch("elle.daemon.api.routes.list_incidents") as mock_list:
            mock_list.return_value = []
            response = client.get("/v1/incidents")

        assert response.status_code == 200

    def test_incidents_empty_list(self, client):
        """Incidents endpoint should return empty list when no incidents."""
        with patch("elle.daemon.api.routes.list_incidents") as mock_list:
            mock_list.return_value = []
            response = client.get("/v1/incidents")

        data = response.json()
        assert data["incidents"] == []
        assert data["total"] == 0


class TestIncidentDetailEndpoint:
    """Tests for GET /v1/incident/{id} endpoint."""

    def test_incident_not_found(self, client):
        """Should return 404 for non-existent incident."""
        with patch("elle.daemon.api.routes.get_incident") as mock_get:
            mock_get.return_value = None
            response = client.get("/v1/incident/non-existent-id")

        assert response.status_code == 404


class TestRootEndpoint:
    """Tests for root endpoint."""

    def test_root_returns_200(self, client):
        """Root endpoint should return 200."""
        response = client.get("/")
        assert response.status_code == 200

    def test_root_contains_message(self, client):
        """Root should contain welcome message."""
        response = client.get("/")
        data = response.json()
        assert "message" in data


class TestApiDocs:
    """Tests for API documentation endpoints."""

    def test_docs_available(self, client):
        """OpenAPI docs should be available."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_schema(self, client):
        """OpenAPI schema should be available."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data
