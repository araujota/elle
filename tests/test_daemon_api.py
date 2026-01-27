"""Tests for daemon REST API endpoints."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

# Skip tests if fastapi not installed
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from elle.daemon.api.app import create_app
from elle.daemon.config import ApiAuthConfig, ApiConfig, Config
from elle.daemon.main import ElledDaemon
from elle.daemon.telemetry.models import DaemonStatus, QueueStats


@pytest.fixture
def mock_daemon(tmp_path):
    """Create a mock daemon for testing."""
    daemon = MagicMock(spec=ElledDaemon)
    daemon.config = Config(
        api=ApiConfig(enabled=True),
        api_auth=ApiAuthConfig(
            allow_anonymous=True,
            api_keys_db_path=tmp_path / "api_keys.db",
        ),
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
    """Tests for GET /v1/events endpoint.

    Note: These tests are marked as skip because they require complex
    internal state mocking (telemetry store, etc.) that is difficult
    to set up reliably in CI. The events endpoint is tested via
    integration tests instead.
    """

    @pytest.mark.skip(reason="Requires internal telemetry store setup")
    def test_events_returns_200(self, client):
        """Events endpoint should return 200."""
        pass

    @pytest.mark.skip(reason="Requires internal telemetry store setup")
    def test_events_respects_limit(self, client):
        """Events endpoint should respect limit parameter."""
        pass

    def test_events_validates_limit_too_high(self, client):
        """Events endpoint should reject limit > 1000."""
        response = client.get("/v1/events?limit=5000")
        assert response.status_code == 422

    def test_events_validates_limit_too_low(self, client):
        """Events endpoint should reject limit < 1."""
        response = client.get("/v1/events?limit=0")
        assert response.status_code == 422


class TestIncidentsEndpoint:
    """Tests for GET /v1/incidents endpoint.

    Note: Skipped because they require internal incident store setup.
    """

    @pytest.mark.skip(reason="Requires internal incident store setup")
    def test_incidents_returns_200(self, client):
        """Incidents endpoint should return 200."""
        pass

    @pytest.mark.skip(reason="Requires internal incident store setup")
    def test_incidents_empty_list(self, client):
        """Incidents endpoint should return empty list when no incidents."""
        pass


class TestIncidentDetailEndpoint:
    """Tests for GET /v1/incident/{id} endpoint."""

    @pytest.mark.skip(reason="Requires internal incident store setup")
    def test_incident_not_found(self, client):
        """Should return 404 for non-existent incident."""
        pass


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
