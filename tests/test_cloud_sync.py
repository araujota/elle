"""Tests for cloud sync client for anonymized incident submission and retrieval.

Covers:
- CloudSyncClient initialization and configuration
- Incident submission (success, errors, retries)
- Similar incident queries (success, empty, errors)
- SSL/mTLS context creation
- Health check endpoint
- CloudIncidentMatch Pydantic model validation
"""

from __future__ import annotations

import ssl
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from elle.daemon.incidents.anonymize import AnonymizedIncidentReport
from elle.daemon.incidents.cloud_sync import (
    CloudIncidentMatch,
    CloudQueryResult,
    CloudSubmissionResult,
    CloudSyncClient,
    reset_cloud_client,
    submit_incident_with_retry,
)
from elle.daemon.incidents.models import Fingerprint

# =============================================================================
# Helpers / Fixtures
# =============================================================================


def _make_anonymized_incident(**overrides: object) -> AnonymizedIncidentReport:
    """Build a minimal AnonymizedIncidentReport for testing."""
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    defaults: dict[str, object] = {
        "incident_id": "test-incident-001",
        "created_at_hour": now,
        "updated_at_hour": now,
        "domain": "other",
        "severity": "warning",
        "status": "open",
        "outcome": "unknown",
        "trigger_source": "manual",
        "fingerprint": Fingerprint(),
        "anonymization_version": "1.0",
        "detail_level": "hashes",
        "original_hash": "abc123",
    }
    defaults.update(overrides)
    return AnonymizedIncidentReport(**defaults)  # type: ignore[arg-type]


def _make_fingerprint(**overrides: object) -> Fingerprint:
    """Build a minimal Fingerprint for testing."""
    defaults: dict[str, object] = {
        "disk_pressure": 0.5,
        "mem_pressure": 0.3,
    }
    defaults.update(overrides)
    return Fingerprint(**defaults)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Reset the module-level cloud client singleton after every test."""
    yield  # type: ignore[misc]
    reset_cloud_client()


# =============================================================================
# TestCloudSyncClientInit
# =============================================================================


class TestCloudSyncClientInit:
    """Tests for CloudSyncClient initialisation."""

    def test_default_config(self) -> None:
        """CloudSyncClient() with no args is not configured."""
        client = CloudSyncClient()
        assert client.is_configured() is False

    def test_custom_endpoint(self) -> None:
        """Passing an endpoint (without certs) stores it but stays unconfigured."""
        client = CloudSyncClient(endpoint="https://cloud.example.com")
        # Endpoint alone is not enough: certs are also required.
        assert client._endpoint == "https://cloud.example.com"
        assert client.is_configured() is False

    def test_missing_certs(self, tmp_path: Path) -> None:
        """Client with endpoint but missing cert files is not available."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path=tmp_path / "ca.pem",
            client_cert_path=tmp_path / "client.pem",
            client_key_path=tmp_path / "client-key.pem",
        )
        # is_configured is True (paths are set), but is_available is False
        # because the files do not exist on disk.
        assert client.is_configured() is True
        assert client.is_available() is False

    def test_invalid_cert_paths(self) -> None:
        """Paths that do not exist cause is_available to return False."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/nonexistent/ca.pem",
            client_cert_path="/nonexistent/client.pem",
            client_key_path="/nonexistent/key.pem",
        )
        assert client.is_configured() is True
        assert client.is_available() is False

    def test_httpx_unavailable(self) -> None:
        """When httpx is unavailable, _get_client raises RuntimeError."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )
        with patch("elle.daemon.incidents.cloud_sync.HTTPX_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="httpx not installed"):
                # _get_client is async, run it via the event loop
                import asyncio

                asyncio.get_event_loop().run_until_complete(client._get_client())


# =============================================================================
# TestSubmitIncident
# =============================================================================


class TestSubmitIncident:
    """Tests for CloudSyncClient.submit_incident."""

    async def test_successful_submit(self) -> None:
        """A 200 response returns a properly parsed CloudSubmissionResult."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "cloud_id": "cloud-abc",
            "accepted": True,
            "duplicate_of": None,
            "similar_count": 5,
        }

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        incident = _make_anonymized_incident()
        result = await client.submit_incident(incident)

        assert isinstance(result, CloudSubmissionResult)
        assert result.cloud_id == "cloud-abc"
        assert result.accepted is True
        assert result.duplicate_of is None
        assert result.similar_count == 5

    async def test_server_500(self) -> None:
        """A 500 response causes raise_for_status to propagate an exception."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        incident = _make_anonymized_incident()
        with pytest.raises(httpx.HTTPStatusError):
            await client.submit_incident(incident)

    async def test_timeout(self) -> None:
        """A timeout from httpx propagates as an exception."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        client._client = mock_http_client

        incident = _make_anonymized_incident()
        with pytest.raises(httpx.TimeoutException):
            await client.submit_incident(incident)

    async def test_connection_refused(self) -> None:
        """A connection error from httpx propagates as an exception."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused"),
        )
        client._client = mock_http_client

        incident = _make_anonymized_incident()
        with pytest.raises(httpx.ConnectError):
            await client.submit_incident(incident)

    async def test_invalid_cert(self) -> None:
        """An SSL error propagates as an exception."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(
            side_effect=ssl.SSLError("CERTIFICATE_VERIFY_FAILED"),
        )
        client._client = mock_http_client

        incident = _make_anonymized_incident()
        with pytest.raises(ssl.SSLError):
            await client.submit_incident(incident)

    async def test_malformed_response(self) -> None:
        """A response with invalid JSON causes an exception."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        incident = _make_anonymized_incident()
        with pytest.raises(ValueError, match="Invalid JSON"):
            await client.submit_incident(incident)

    async def test_retry_submit_with_backoff(self) -> None:
        """submit_incident_with_retry returns None and enqueues on failure."""
        from elle.daemon.incidents.cloud_sync import configure_cloud_client

        configure_cloud_client(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        incident = _make_anonymized_incident()

        with (
            patch.object(
                CloudSyncClient,
                "submit_incident",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("down"),
            ),
            patch(
                "elle.daemon.incidents.cloud_queue.get_cloud_queue",
                side_effect=RuntimeError("queue not configured"),
            ),
        ):
            result = await submit_incident_with_retry(incident, original_hash="hash123")
            assert result is None

    async def test_rate_limiting(self) -> None:
        """A 429 response is handled as an HTTP status error."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Too Many Requests",
            request=MagicMock(),
            response=mock_response,
        )

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        incident = _make_anonymized_incident()
        with pytest.raises(httpx.HTTPStatusError):
            await client.submit_incident(incident)


# =============================================================================
# TestQuerySimilar
# =============================================================================


class TestQuerySimilar:
    """Tests for CloudSyncClient.query_similar."""

    async def test_successful_query(self) -> None:
        """A 200 response with matches returns a properly parsed CloudQueryResult."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "matches": [
                {
                    "cloud_id": "match-1",
                    "fingerprint_similarity": 0.85,
                    "surface_similarity": 0.72,
                    "combined_score": 0.79,
                    "domain": "disk",
                    "outcome": "improved",
                    "confidence": 0.9,
                    "installation_count": 3,
                    "resolution_stats": {"avg_time": 120},
                },
            ],
            "total_searched": 1000,
        }

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        fp = _make_fingerprint()
        result = await client.query_similar(fp)

        assert isinstance(result, CloudQueryResult)
        assert len(result.matches) == 1
        assert result.matches[0].cloud_id == "match-1"
        assert result.matches[0].fingerprint_similarity == 0.85
        assert result.total_searched == 1000

    async def test_no_matches(self) -> None:
        """An empty matches list returns a CloudQueryResult with empty tuple."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "matches": [],
            "total_searched": 500,
        }

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        fp = _make_fingerprint()
        result = await client.query_similar(fp)

        assert isinstance(result, CloudQueryResult)
        assert len(result.matches) == 0
        assert result.total_searched == 500

    async def test_server_error(self) -> None:
        """A 500 response propagates as an exception."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        fp = _make_fingerprint()
        with pytest.raises(httpx.HTTPStatusError):
            await client.query_similar(fp)

    async def test_partial_results(self) -> None:
        """Some matches returned; each is correctly parsed."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "matches": [
                {
                    "cloud_id": "m1",
                    "fingerprint_similarity": 0.9,
                    "surface_similarity": 0.8,
                    "combined_score": 0.85,
                    "domain": "net",
                    "outcome": "improved",
                },
                {
                    "cloud_id": "m2",
                    "fingerprint_similarity": 0.4,
                    "surface_similarity": 0.3,
                    "combined_score": 0.35,
                    "domain": "disk",
                    "outcome": "partial",
                },
            ],
            "total_searched": 2000,
        }

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        fp = _make_fingerprint()
        result = await client.query_similar(fp)

        assert len(result.matches) == 2
        assert result.matches[0].cloud_id == "m1"
        assert result.matches[1].cloud_id == "m2"

    async def test_similarity_threshold(self) -> None:
        """The min_similarity parameter is sent in the request payload."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"matches": [], "total_searched": 0}

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        fp = _make_fingerprint()
        await client.query_similar(fp, min_similarity=0.8)

        # Verify the request payload contains our threshold
        call_args = mock_http_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["min_similarity"] == 0.8

    async def test_empty_fingerprint(self) -> None:
        """Querying with a default Fingerprint() still works."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"matches": [], "total_searched": 0}

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        fp = Fingerprint()  # all defaults
        result = await client.query_similar(fp)

        assert isinstance(result, CloudQueryResult)
        assert len(result.matches) == 0


# =============================================================================
# TestSSLContext
# =============================================================================


class TestSSLContext:
    """Tests for CloudSyncClient._get_ssl_context."""

    def test_valid_mtls_setup(self, tmp_path: Path) -> None:
        """With all cert files present, returns a valid SSLContext."""
        ca = tmp_path / "ca.pem"
        cert = tmp_path / "client.pem"
        key = tmp_path / "client-key.pem"

        # Create dummy files so existence checks pass
        ca.write_text("ca")
        cert.write_text("cert")
        key.write_text("key")

        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path=ca,
            client_cert_path=cert,
            client_key_path=key,
        )

        # ssl.SSLContext.load_verify_locations and load_cert_chain will fail on
        # dummy files, so we mock them out.
        with (
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch.object(ssl.SSLContext, "load_cert_chain"),
        ):
            ctx = client._get_ssl_context()
            assert ctx is not None
            assert isinstance(ctx, ssl.SSLContext)

    def test_missing_ca_cert(self, tmp_path: Path) -> None:
        """If the CA cert file does not exist, returns None."""
        cert = tmp_path / "client.pem"
        key = tmp_path / "client-key.pem"
        cert.write_text("cert")
        key.write_text("key")

        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path=tmp_path / "missing-ca.pem",
            client_cert_path=cert,
            client_key_path=key,
        )

        ctx = client._get_ssl_context()
        assert ctx is None

    def test_no_certs_configured(self) -> None:
        """With no cert paths set, returns None."""
        client = CloudSyncClient(endpoint="https://cloud.example.com")
        ctx = client._get_ssl_context()
        assert ctx is None

    def test_ssl_context_protocol(self, tmp_path: Path) -> None:
        """The created SSLContext uses PROTOCOL_TLS_CLIENT."""
        ca = tmp_path / "ca.pem"
        cert = tmp_path / "client.pem"
        key = tmp_path / "client-key.pem"
        ca.write_text("ca")
        cert.write_text("cert")
        key.write_text("key")

        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path=ca,
            client_cert_path=cert,
            client_key_path=key,
        )

        with (
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch.object(ssl.SSLContext, "load_cert_chain"),
        ):
            ctx = client._get_ssl_context()
            assert ctx is not None
            assert ctx.protocol == ssl.PROTOCOL_TLS_CLIENT

    def test_cert_path_permissions(self, tmp_path: Path) -> None:
        """Cert paths are converted to Path objects and validated for existence."""
        ca = tmp_path / "ca.pem"
        cert = tmp_path / "client.pem"
        key = tmp_path / "client-key.pem"
        ca.write_text("ca")
        cert.write_text("cert")
        # key intentionally missing

        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path=str(ca),
            client_cert_path=str(cert),
            client_key_path=str(key),
        )

        # Paths were stored as Path objects
        assert isinstance(client._ca_cert_path, Path)
        assert isinstance(client._client_cert_path, Path)
        assert isinstance(client._client_key_path, Path)

        # key missing -> ssl context is None
        ctx = client._get_ssl_context()
        assert ctx is None


# =============================================================================
# TestHealthCheck
# =============================================================================


class TestHealthCheck:
    """Tests for CloudSyncClient.health_check."""

    async def test_healthy(self) -> None:
        """A successful GET /health returns True."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        assert await client.health_check() is True

    async def test_unhealthy(self) -> None:
        """A non-200 GET /health returns False."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        assert await client.health_check() is False

    async def test_timeout(self) -> None:
        """A timeout on GET /health returns False."""
        client = CloudSyncClient(
            endpoint="https://cloud.example.com",
            ca_cert_path="/a",
            client_cert_path="/b",
            client_key_path="/c",
        )

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        client._client = mock_http_client

        assert await client.health_check() is False


# =============================================================================
# TestCloudIncidentMatch
# =============================================================================


class TestCloudIncidentMatch:
    """Tests for the CloudIncidentMatch Pydantic model."""

    def test_valid_model(self) -> None:
        """A CloudIncidentMatch with valid scores is created successfully."""
        match = CloudIncidentMatch(
            cloud_id="cloud-123",
            fingerprint_similarity=0.85,
            surface_similarity=0.72,
            combined_score=0.79,
            domain="disk",
            outcome="improved",
            confidence=0.9,
            installation_count=3,
        )
        assert match.cloud_id == "cloud-123"
        assert match.fingerprint_similarity == 0.85
        assert match.surface_similarity == 0.72
        assert match.combined_score == 0.79

    def test_boundary_scores(self) -> None:
        """0.0 and 1.0 are valid boundary values for all score fields."""
        match_zero = CloudIncidentMatch(
            cloud_id="low",
            fingerprint_similarity=0.0,
            surface_similarity=0.0,
            combined_score=0.0,
        )
        assert match_zero.fingerprint_similarity == 0.0
        assert match_zero.surface_similarity == 0.0
        assert match_zero.combined_score == 0.0

        match_one = CloudIncidentMatch(
            cloud_id="high",
            fingerprint_similarity=1.0,
            surface_similarity=1.0,
            combined_score=1.0,
        )
        assert match_one.fingerprint_similarity == 1.0
        assert match_one.surface_similarity == 1.0
        assert match_one.combined_score == 1.0

    def test_score_out_of_range(self) -> None:
        """A score greater than 1.0 is rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            CloudIncidentMatch(
                cloud_id="bad",
                fingerprint_similarity=1.5,
                surface_similarity=0.5,
            )
