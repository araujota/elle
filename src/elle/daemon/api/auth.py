"""Authentication middleware for elled daemon API.

Provides three authentication mechanisms (in order of precedence):
1. Session Token - Required for elle CLI access (generated on daemon startup)
2. UDS Peer Credentials - Uses SO_PEERCRED for local Unix socket connections
3. API Key - Bearer token authentication for persistent integrations

The session token is the primary auth mechanism for elle CLI. It is generated
on daemon startup and written to a file readable only by the daemon's user.
This ensures only the elle CLI running as the same user can access the API.

Authentication determines which execution modes a client is allowed to use.

Storage backend: PostgreSQL via psycopg (schema ``api``).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, cast

# Request must be imported at runtime for FastAPI dependency injection
# Import unconditionally - if FastAPI isn't installed, these modules won't be used anyway
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

import psycopg

from elle.daemon.api.openai_models import ExecutionMode
from elle.storage.engine import get_conn
from elle.storage.migrate import register_migration

if TYPE_CHECKING:
    from elle.daemon.config import ApiAuthConfig

logger = logging.getLogger(__name__)

PG_SCHEMA = "api"


# =============================================================================
# Migration registration
# =============================================================================


def _migrate_v1(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Create initial API auth tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            allowed_modes TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            last_used_at TIMESTAMPTZ,
            revoked BOOLEAN DEFAULT FALSE
        )
    """)


register_migration(PG_SCHEMA, 1, _migrate_v1)


class AuthContext(BaseModel):
    """Authentication context for a request.

    Contains information about how the request was authenticated
    and what execution modes are allowed.
    """

    model_config = ConfigDict(frozen=True)

    source: Literal["uds_peer", "api_key", "anonymous"] = Field(description="How the request was authenticated")
    uid: int | None = Field(
        default=None,
        description="Unix UID for UDS peer authentication",
    )
    gid: int | None = Field(
        default=None,
        description="Unix GID for UDS peer authentication",
    )
    api_key_id: str | None = Field(
        default=None,
        description="API key identifier for key-based auth",
    )
    allowed_modes: tuple[ExecutionMode, ...] = Field(description="Execution modes this auth context allows")

    def can_use_mode(self, mode: ExecutionMode) -> bool:
        """Check if this auth context allows a specific execution mode."""
        return mode in self.allowed_modes


class ApiKey(BaseModel):
    """Stored API key record."""

    model_config = ConfigDict(frozen=True)

    key_id: str = Field(description="Unique key identifier")
    key_hash: str = Field(description="SHA-256 hash of the key")
    name: str = Field(description="Human-readable name for the key")
    allowed_modes: tuple[ExecutionMode, ...] = Field(description="Modes this key allows")
    created_at: datetime = Field(description="When the key was created")
    last_used_at: datetime | None = Field(default=None, description="Last time the key was used")
    revoked: bool = Field(default=False, description="Whether the key is revoked")


# Anonymous auth context - only readonly allowed
ANONYMOUS_AUTH = AuthContext(
    source="anonymous",
    allowed_modes=(ExecutionMode.READONLY,),
)

# Full access for root
ROOT_AUTH = AuthContext(
    source="uds_peer",
    uid=0,
    gid=0,
    allowed_modes=(
        ExecutionMode.READONLY,
        ExecutionMode.EXECUTE,
        ExecutionMode.CAPABILITIES_ONLY,
    ),
)


def _hash_key(key: str) -> str:
    """Hash an API key using SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key.

    Returns:
        Tuple of (key_id, full_key). The full_key should be shown
        to the user once and then stored as a hash.
    """
    key_id = secrets.token_hex(8)
    key_secret = secrets.token_urlsafe(32)
    full_key = f"elle_{key_id}_{key_secret}"
    return key_id, full_key


class ApiKeyStore:
    """PostgreSQL-backed storage for API keys."""

    def __init__(self) -> None:
        """Initialize the API key store."""

    def create_key(
        self,
        name: str,
        allowed_modes: tuple[ExecutionMode, ...] | None = None,
    ) -> tuple[str, str]:
        """Create a new API key.

        Args:
            name: Human-readable name for the key.
            allowed_modes: Modes this key allows. Defaults to readonly only.

        Returns:
            Tuple of (key_id, full_key). The full_key must be shown to
            the user once - it cannot be retrieved later.
        """
        if allowed_modes is None:
            allowed_modes = (ExecutionMode.READONLY,)

        key_id, full_key = generate_api_key()
        key_hash = _hash_key(full_key)
        modes_str = ",".join(m.value for m in allowed_modes)

        with get_conn(schema=PG_SCHEMA) as conn:
            conn.execute(
                """
                INSERT INTO api_keys (key_id, key_hash, name, allowed_modes, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (key_id, key_hash, name, modes_str, datetime.now(timezone.utc).isoformat()),
            )

        return key_id, full_key

    def validate_key(self, key: str) -> ApiKey | None:
        """Validate an API key and return its record.

        Args:
            key: The full API key to validate.

        Returns:
            ApiKey record if valid, None if invalid or revoked.
        """
        key_hash = _hash_key(key)

        with get_conn(schema=PG_SCHEMA) as conn:
            row = conn.execute(
                """
                SELECT key_id, key_hash, name, allowed_modes, created_at,
                       last_used_at, revoked
                FROM api_keys
                WHERE key_hash = %s AND revoked = FALSE
                """,
                (key_hash,),
            ).fetchone()

            if not row:
                return None

            # Update last_used_at
            conn.execute(
                "UPDATE api_keys SET last_used_at = %s WHERE key_id = %s",
                (datetime.now(timezone.utc).isoformat(), row["key_id"]),
            )

            # Parse allowed modes
            modes_str = row["allowed_modes"]
            allowed_modes = tuple(ExecutionMode(m) for m in modes_str.split(",") if m)

            created_at = row["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            last_used_at = row["last_used_at"]
            if isinstance(last_used_at, str):
                last_used_at = datetime.fromisoformat(last_used_at)

            return ApiKey(
                key_id=row["key_id"],
                key_hash=row["key_hash"],
                name=row["name"],
                allowed_modes=allowed_modes,
                created_at=created_at,
                last_used_at=last_used_at,
                revoked=bool(row["revoked"]),
            )

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key.

        Args:
            key_id: The key ID to revoke.

        Returns:
            True if the key was revoked, False if not found.
        """
        with get_conn(schema=PG_SCHEMA) as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET revoked = TRUE WHERE key_id = %s",
                (key_id,),
            )
            return bool(cursor.rowcount > 0)

    def list_keys(self, include_revoked: bool = False) -> list[ApiKey]:
        """List all API keys.

        Args:
            include_revoked: Whether to include revoked keys.

        Returns:
            List of ApiKey records (without the actual key values).
        """
        query = "SELECT * FROM api_keys"
        if not include_revoked:
            query += " WHERE revoked = FALSE"
        query += " ORDER BY created_at DESC"

        with get_conn(schema=PG_SCHEMA) as conn:
            rows = conn.execute(query).fetchall()
            result: list[ApiKey] = []
            for row in rows:
                created_at = row["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at)

                last_used_at = row["last_used_at"]
                if isinstance(last_used_at, str):
                    last_used_at = datetime.fromisoformat(last_used_at)

                result.append(
                    ApiKey(
                        key_id=row["key_id"],
                        key_hash=row["key_hash"],
                        name=row["name"],
                        allowed_modes=tuple(ExecutionMode(m) for m in row["allowed_modes"].split(",") if m),
                        created_at=created_at,
                        last_used_at=last_used_at,
                        revoked=bool(row["revoked"]),
                    )
                )
            return result


def _get_peer_credentials(request: Request) -> tuple[int, int] | None:
    """Extract peer credentials from a Unix domain socket connection.

    Uses SO_PEERCRED socket option to get the connecting process's UID/GID.

    Args:
        request: The FastAPI request object.

    Returns:
        Tuple of (uid, gid) or None if not available.
    """
    try:
        # Get the underlying socket from the ASGI transport
        transport = request.scope.get("transport")
        if transport is None:
            return None

        # Try to get the socket
        socket = getattr(transport, "get_extra_info", lambda x: None)("socket")
        if socket is None:
            return None

        # Check if it's a Unix socket
        import socket as socket_module

        if socket.family != socket_module.AF_UNIX:
            return None

        # Get peer credentials using SO_PEERCRED
        import struct

        SO_PEERCRED = 17  # Linux-specific
        creds = socket.getsockopt(
            socket_module.SOL_SOCKET,
            SO_PEERCRED,
            struct.calcsize("3i"),
        )
        pid, uid, gid = struct.unpack("3i", creds)
        return uid, gid

    except Exception as e:
        logger.debug(f"Failed to get peer credentials: {e}")
        return None


def _extract_bearer_token(request: Request) -> str | None:
    """Extract bearer token from Authorization header.

    Args:
        request: The FastAPI request object.

    Returns:
        The bearer token or None if not present.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return str(auth_header[7:].strip())
    return None


class AuthMiddleware:
    """FastAPI dependency for authentication.

    Examines each request and produces an AuthContext based on:
    1. Session token (X-Elle-Token header or Bearer token starting with 'elle_session_')
    2. UDS peer credentials (for Unix socket connections)
    3. API key (Bearer token)
    4. Falls back to anonymous (readonly only) if allowed
    """

    def __init__(
        self,
        config: ApiAuthConfig,
        key_store: ApiKeyStore | None = None,
        session_token: str | None = None,
    ) -> None:
        """Initialize the auth middleware.

        Args:
            config: API authentication configuration.
            key_store: Optional API key store. Creates one if not provided.
            session_token: The current daemon session token.
        """
        self._config = config
        self._key_store = key_store
        self._session_token = session_token

    def set_session_token(self, token: str) -> None:
        """Update the session token.

        Args:
            token: New session token.
        """
        self._session_token = token

    @property
    def key_store(self) -> ApiKeyStore | None:
        """Get the API key store (lazy initialization)."""
        if self._key_store is None and self._config.api_keys_db_path:
            self._key_store = ApiKeyStore()
        return self._key_store

    async def __call__(self, request: Request) -> AuthContext:
        """Authenticate a request and return its auth context.

        Args:
            request: The incoming request.

        Returns:
            AuthContext describing the authentication.
        """
        from fastapi import HTTPException

        # Check for session token first (primary auth for elle CLI)
        session_auth = self._auth_from_session_token(request)
        if session_auth is not None:
            return session_auth

        # Try UDS peer credentials
        peer_creds = _get_peer_credentials(request)
        if peer_creds is not None:
            uid, gid = peer_creds
            return self._auth_from_peer(uid, gid)

        # Try API key authentication
        token = _extract_bearer_token(request)
        if token:
            auth = self._auth_from_key(token)
            if auth:
                return auth
            # Invalid key - reject
            raise HTTPException(
                status_code=401,
                detail="Invalid API key or session token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Fall back to anonymous
        if self._config.allow_anonymous:
            return ANONYMOUS_AUTH

        # No authentication and anonymous not allowed
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Ensure elled is running and you have access.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _auth_from_session_token(self, request: Request) -> AuthContext | None:
        """Authenticate using session token.

        Checks X-Elle-Token header or Bearer token for session token.

        Args:
            request: The incoming request.

        Returns:
            AuthContext if session token is valid, None otherwise.
        """
        import secrets

        if self._session_token is None:
            return None

        # Check X-Elle-Token header first
        token = request.headers.get("X-Elle-Token")

        # Fall back to Bearer token
        if not token:
            token = _extract_bearer_token(request)

        if not token:
            return None

        # Validate using constant-time comparison
        if secrets.compare_digest(token, self._session_token):
            return AuthContext(
                source="uds_peer",  # Treat as trusted local client
                uid=None,
                gid=None,
                allowed_modes=(
                    ExecutionMode.READONLY,
                    ExecutionMode.EXECUTE,
                    ExecutionMode.CAPABILITIES_ONLY,
                ),
            )

        return None

    def _auth_from_peer(self, uid: int, gid: int) -> AuthContext:
        """Create auth context from peer credentials.

        Args:
            uid: User ID of the connecting process.
            gid: Group ID of the connecting process.

        Returns:
            AuthContext for this peer.
        """
        # Root gets full access
        if uid == 0:
            return ROOT_AUTH

        # Check if this is the elle user (by UID from config)
        if self._config.elle_uid is not None and uid == self._config.elle_uid:
            return AuthContext(
                source="uds_peer",
                uid=uid,
                gid=gid,
                allowed_modes=(
                    ExecutionMode.READONLY,
                    ExecutionMode.EXECUTE,
                    ExecutionMode.CAPABILITIES_ONLY,
                ),
            )

        # Other local users get readonly by default
        return AuthContext(
            source="uds_peer",
            uid=uid,
            gid=gid,
            allowed_modes=(ExecutionMode.READONLY,),
        )

    def _auth_from_key(self, key: str) -> AuthContext | None:
        """Create auth context from API key.

        Args:
            key: The API key to validate.

        Returns:
            AuthContext if key is valid, None otherwise.
        """
        if self.key_store is None:
            return None

        api_key = self.key_store.validate_key(key)
        if api_key is None:
            return None

        return AuthContext(
            source="api_key",
            api_key_id=api_key.key_id,
            allowed_modes=api_key.allowed_modes,
        )


# FastAPI dependency for getting auth context
async def get_auth_context(request: Request) -> AuthContext:
    """FastAPI dependency to get the auth context for a request.

    This is set up by the app during initialization. If no auth
    middleware is configured, returns anonymous auth.
    """
    # Check if auth middleware has already been run
    auth = getattr(request.state, "auth_context", None)
    if auth is not None:
        return cast(AuthContext, auth)

    # No auth configured - return anonymous
    return ANONYMOUS_AUTH
