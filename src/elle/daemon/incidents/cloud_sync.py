"""Cloud sync client for anonymized incident submission and retrieval.

Enables ELLE installations to:
- Submit anonymized incidents to a shared vault
- Query for similar incidents across all installations
- Retrieve resolution statistics for collective learning

The anonymization layer (anonymize.py) ensures all submitted data
is privacy-safe before transmission.
"""

from __future__ import annotations

import asyncio
import ssl
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

try:
    import structlog

    logger = structlog.get_logger()
except ImportError:
    import logging

    logger = logging.getLogger(__name__)  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.incidents.anonymize import AnonymizedIncidentReport
from elle.daemon.incidents.models import Fingerprint

if TYPE_CHECKING:
    import httpx

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class CloudIncidentMatch(BaseModel):
    """A matching incident from the cloud knowledge base."""

    model_config = ConfigDict(frozen=True)

    cloud_id: str = Field(description="Cloud-assigned incident ID")
    fingerprint_similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="Similarity score based on fingerprint matching",
    )
    surface_similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="Similarity score based on surface hash matching",
    )
    combined_score: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Combined similarity score",
    )
    domain: str = Field(default="other", description="Incident domain")
    outcome: str = Field(default="unknown", description="Incident outcome")
    confidence: float = Field(default=0.0, description="Resolution confidence")
    installation_count: int = Field(
        ge=0,
        default=1,
        description="Number of installations that reported similar incidents",
    )
    resolution_stats: dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregate resolution statistics",
    )


class CloudSubmissionResult(BaseModel):
    """Result of submitting an incident to the cloud."""

    model_config = ConfigDict(frozen=True)

    cloud_id: str = Field(description="Cloud-assigned ID for this submission")
    accepted: bool = Field(description="Whether the submission was accepted")
    duplicate_of: str | None = Field(
        default=None,
        description="If duplicate, the ID of the existing incident",
    )
    similar_count: int = Field(
        ge=0,
        default=0,
        description="Number of similar incidents in the cloud",
    )


class CloudQueryResult(BaseModel):
    """Result of querying the cloud for similar incidents."""

    model_config = ConfigDict(frozen=True)

    matches: tuple[CloudIncidentMatch, ...] = Field(
        default_factory=tuple,
        description="Matching incidents from the cloud",
    )
    total_searched: int = Field(
        ge=0,
        default=0,
        description="Total incidents searched in the cloud",
    )
    query_time_ms: int = Field(
        ge=0,
        default=0,
        description="Query execution time in milliseconds",
    )


# =============================================================================
# Cloud Sync Client
# =============================================================================


class CloudSyncClient:
    """Client for synchronizing anonymized incidents with the cloud.

    Connects to an ELLE Cloud vault for:
    - Submitting anonymized incidents for aggregate analysis
    - Querying for similar incidents across all installations
    - Retrieving resolution statistics and recommendations
    """

    def __init__(
        self,
        endpoint: str | None = None,
        ca_cert_path: str | Path | None = None,
        client_cert_path: str | Path | None = None,
        client_key_path: str | Path | None = None,
        timeout_sec: int = 30,
    ) -> None:
        """Initialize the cloud sync client.

        Args:
            endpoint: Cloud API endpoint URL (e.g., https://vault.example.com:8443).
            ca_cert_path: Path to CA certificate for mTLS.
            client_cert_path: Path to client certificate for mTLS.
            client_key_path: Path to client private key for mTLS.
            timeout_sec: Request timeout in seconds.
        """
        self._endpoint = endpoint.rstrip("/") if endpoint else None
        self._ca_cert_path = Path(ca_cert_path) if ca_cert_path else None
        self._client_cert_path = Path(client_cert_path) if client_cert_path else None
        self._client_key_path = Path(client_key_path) if client_key_path else None
        self._timeout_sec = timeout_sec
        self._client: httpx.AsyncClient | None = None

    def _get_ssl_context(self) -> ssl.SSLContext | None:
        """Create SSL context for mTLS."""
        if not all([self._ca_cert_path, self._client_cert_path, self._client_key_path]):
            return None

        if not all(
            [
                self._ca_cert_path and self._ca_cert_path.exists(),
                self._client_cert_path and self._client_cert_path.exists(),
                self._client_key_path and self._client_key_path.exists(),
            ]
        ):
            return None

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_verify_locations(str(self._ca_cert_path))
        ctx.load_cert_chain(
            certfile=str(self._client_cert_path),
            keyfile=str(self._client_key_path),
        )
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not installed. Run: pip install httpx")

        if self._client is None:
            ssl_context = self._get_ssl_context()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_sec),
                verify=ssl_context if ssl_context else True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def submit_incident(
        self,
        incident: AnonymizedIncidentReport,
    ) -> CloudSubmissionResult:
        """Submit an anonymized incident to the cloud.

        Args:
            incident: The anonymized incident report.

        Returns:
            CloudSubmissionResult with submission status.

        Raises:
            RuntimeError: If cloud is not configured.
            httpx.HTTPError: If request fails.
        """
        if not self.is_configured():
            raise RuntimeError("Cloud sync not configured")

        client = await self._get_client()
        url = f"{self._endpoint}/v1/incidents"

        try:
            response = await client.post(
                url,
                json=incident.model_dump(mode="json"),
            )
            response.raise_for_status()
            data = response.json()

            return CloudSubmissionResult(
                cloud_id=data.get("cloud_id", ""),
                accepted=data.get("accepted", False),
                duplicate_of=data.get("duplicate_of"),
                similar_count=data.get("similar_count", 0),
            )
        except Exception as e:
            logger.warning("Cloud submission failed", error=str(e), endpoint=self._endpoint)
            raise

    async def query_similar(
        self,
        fingerprint: Fingerprint,
        domain: str | None = None,
        surface_hashes: dict[str, str] | None = None,
        limit: int = 10,
        min_similarity: float = 0.3,
    ) -> CloudQueryResult:
        """Query the cloud for similar incidents.

        Args:
            fingerprint: Incident fingerprint for matching.
            domain: Optional domain filter.
            surface_hashes: Optional surface hashes for drift matching.
            limit: Maximum number of matches to return.
            min_similarity: Minimum similarity threshold.

        Returns:
            CloudQueryResult with matching incidents.

        Raises:
            RuntimeError: If cloud is not configured.
            httpx.HTTPError: If request fails.
        """
        if not self.is_configured():
            raise RuntimeError("Cloud sync not configured")

        client = await self._get_client()
        url = f"{self._endpoint}/v1/incidents/similar"

        try:
            start_time = datetime.utcnow()

            payload = {
                "fingerprint": fingerprint.model_dump(mode="json"),
                "limit": limit,
                "min_similarity": min_similarity,
            }
            if domain:
                payload["domain"] = domain
            if surface_hashes:
                payload["surface_hashes"] = surface_hashes

            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            query_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Parse matches
            matches = []
            for m in data.get("matches", []):
                matches.append(
                    CloudIncidentMatch(
                        cloud_id=m.get("cloud_id", ""),
                        fingerprint_similarity=m.get("fingerprint_similarity", 0.0),
                        surface_similarity=m.get("surface_similarity", 0.0),
                        combined_score=m.get("combined_score", 0.0),
                        domain=m.get("domain", "other"),
                        outcome=m.get("outcome", "unknown"),
                        confidence=m.get("confidence", 0.0),
                        installation_count=m.get("installation_count", 1),
                        resolution_stats=m.get("resolution_stats", {}),
                    )
                )

            return CloudQueryResult(
                matches=tuple(matches),
                total_searched=data.get("total_searched", 0),
                query_time_ms=query_time_ms,
            )

        except Exception as e:
            logger.debug("Cloud query failed", error=str(e), endpoint=self._endpoint)
            raise

    async def get_resolution_stats(
        self,
        domain: str,
        fingerprint: Fingerprint | None = None,
    ) -> dict[str, Any]:
        """Get aggregate resolution statistics for a domain.

        Args:
            domain: Incident domain to query.
            fingerprint: Optional fingerprint for more specific matching.

        Returns:
            Dict with resolution statistics:
            - outcome_distribution: {outcome: percentage}
            - avg_time_to_resolve_sec: Average resolution time
            - common_approaches: Most successful approaches
            - sample_size: Number of incidents in the sample

        Raises:
            RuntimeError: If cloud is not configured.
            httpx.HTTPError: If request fails.
        """
        if not self.is_configured():
            raise RuntimeError("Cloud sync not configured")

        client = await self._get_client()
        url = f"{self._endpoint}/v1/stats/{domain}"

        try:
            response = await client.get(url)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
        except Exception as e:
            logger.debug("Cloud stats query failed", error=str(e), endpoint=self._endpoint)
            raise

    async def health_check(self) -> bool:
        """Check if the cloud endpoint is reachable.

        Returns:
            True if healthy, False otherwise.
        """
        if not self.is_configured():
            return False

        try:
            client = await self._get_client()
            response = await client.get(f"{self._endpoint}/health")
            return response.status_code == 200
        except Exception:
            return False

    def is_configured(self) -> bool:
        """Check if cloud sync is configured.

        Returns:
            True if endpoint and certificates are configured, False otherwise.
        """
        return bool(self._endpoint and self._ca_cert_path and self._client_cert_path and self._client_key_path)

    def is_available(self) -> bool:
        """Check if cloud sync is available (synchronous check).

        Returns:
            True if configured with valid cert paths, False otherwise.
        """
        if not self.is_configured():
            return False

        # Check that cert files exist
        return all(
            [
                self._ca_cert_path and self._ca_cert_path.exists(),
                self._client_cert_path and self._client_cert_path.exists(),
                self._client_key_path and self._client_key_path.exists(),
            ]
        )


# =============================================================================
# Module-level singleton
# =============================================================================

_cloud_client: CloudSyncClient | None = None


def get_cloud_client() -> CloudSyncClient:
    """Get the global cloud sync client instance.

    Returns:
        The CloudSyncClient singleton.
    """
    global _cloud_client
    if _cloud_client is None:
        _cloud_client = CloudSyncClient()
    return _cloud_client


def configure_cloud_client(
    endpoint: str,
    ca_cert_path: str | Path,
    client_cert_path: str | Path,
    client_key_path: str | Path,
    timeout_sec: int = 30,
) -> CloudSyncClient:
    """Configure the global cloud sync client.

    Args:
        endpoint: Cloud API endpoint URL.
        ca_cert_path: Path to CA certificate.
        client_cert_path: Path to client certificate.
        client_key_path: Path to client key.
        timeout_sec: Request timeout in seconds.

    Returns:
        The configured CloudSyncClient.
    """
    global _cloud_client
    _cloud_client = CloudSyncClient(
        endpoint=endpoint,
        ca_cert_path=ca_cert_path,
        client_cert_path=client_cert_path,
        client_key_path=client_key_path,
        timeout_sec=timeout_sec,
    )
    return _cloud_client


def reset_cloud_client() -> None:
    """Reset the global cloud sync client (for testing)."""
    global _cloud_client
    _cloud_client = None


# =============================================================================
# Async helpers for sync contexts
# =============================================================================


async def submit_incident_with_retry(
    incident: AnonymizedIncidentReport,
    original_hash: str,
) -> CloudSubmissionResult | None:
    """Submit an incident to the cloud, falling back to queue on failure.

    Tries a direct submission first. If the cloud is unreachable or
    returns a server error, enqueues the incident for automatic retry.

    Args:
        incident: The anonymized incident report to submit.
        original_hash: Hash for deduplication in the retry queue.

    Returns:
        CloudSubmissionResult if direct submission succeeded, None if queued.
    """
    client = get_cloud_client()
    if not client.is_configured():
        return None

    try:
        result = await client.submit_incident(incident)
        return result
    except Exception as e:
        logger.warning(
            "Direct cloud submission failed, enqueuing for retry",
            error=str(e),
            incident_id=incident.incident_id,
        )

        # Enqueue for retry
        try:
            from elle.daemon.incidents.cloud_queue import get_cloud_queue

            queue = get_cloud_queue()
            payload = incident.model_dump(mode="json")
            queue.enqueue(
                incident_id=incident.incident_id,
                payload=payload,
                original_hash=original_hash,
            )
        except RuntimeError:
            # Queue not configured -- this is fine during startup
            logger.debug("Cloud queue not configured, cannot enqueue for retry")
        except Exception as queue_err:
            logger.error(
                "Failed to enqueue incident for retry",
                error=str(queue_err),
                incident_id=incident.incident_id,
            )

        return None


def query_cloud_sync(
    fingerprint: Fingerprint,
    domain: str | None = None,
    surface_hashes: dict[str, str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.3,
) -> CloudQueryResult | None:
    """Synchronous wrapper for cloud query.

    Runs the async query in a new event loop if needed.

    Args:
        fingerprint: Incident fingerprint for matching.
        domain: Optional domain filter.
        surface_hashes: Optional surface hashes.
        limit: Maximum matches.
        min_similarity: Minimum similarity threshold.

    Returns:
        CloudQueryResult or None if cloud unavailable/error.
    """
    client = get_cloud_client()
    if not client.is_available():
        return None

    try:
        # Try to get existing event loop
        try:
            asyncio.get_running_loop()
            # If we're in an async context, run in thread pool
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    client.query_similar(
                        fingerprint=fingerprint,
                        domain=domain,
                        surface_hashes=surface_hashes,
                        limit=limit,
                        min_similarity=min_similarity,
                    ),
                )
                return future.result(timeout=client._timeout_sec)
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            return asyncio.run(
                client.query_similar(
                    fingerprint=fingerprint,
                    domain=domain,
                    surface_hashes=surface_hashes,
                    limit=limit,
                    min_similarity=min_similarity,
                )
            )
    except Exception as e:
        logger.debug("Cloud query failed", error=str(e))
        return None
