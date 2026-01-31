"""Tests for daemon API authentication middleware and key management.

Covers:
- AuthContext model behaviour and mode checks
- ApiKey model construction
- _hash_key / generate_api_key helpers
- ApiKeyStore CRUD (create, validate, revoke, list)
- _get_peer_credentials edge cases
- _extract_bearer_token parsing
- AuthMiddleware session token auth
- AuthMiddleware UDS peer auth (root, elle-user, other)
- AuthMiddleware API key auth
- AuthMiddleware anonymous / rejection fallback
- get_auth_context FastAPI dependency
- Migration registration
- Pre-defined ANONYMOUS_AUTH and ROOT_AUTH constants
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.api.auth import (
    ANONYMOUS_AUTH,
    ROOT_AUTH,
    ApiKey,
    ApiKeyStore,
    AuthContext,
    AuthMiddleware,
    _extract_bearer_token,
    _get_peer_credentials,
    _hash_key,
    generate_api_key,
    get_auth_context,
)
from elle.daemon.api.openai_models import ExecutionMode
from elle.daemon.config import ApiAuthConfig

# Patch target prefix for storage engine
_ENGINE = "elle.daemon.api.auth.get_conn"


# =============================================================================
# Helpers
# =============================================================================


def _make_request(
    headers: dict[str, str] | None = None,
    transport: object | None = None,
) -> MagicMock:
    """Build a mock FastAPI Request with optional headers and transport."""
    req = MagicMock()
    _headers = headers or {}
    req.headers = _headers
    req.scope = {"transport": transport}
    return req


def _make_config(
    allow_anonymous: bool = False,
    api_keys_db_path: str | None = "/var/lib/elle/api_keys.db",
    elle_uid: int | None = None,
) -> ApiAuthConfig:
    """Build an ApiAuthConfig with sensible defaults for tests."""
    from pathlib import Path

    return ApiAuthConfig(
        allow_anonymous=allow_anonymous,
        api_keys_db_path=Path(api_keys_db_path) if api_keys_db_path else None,
        elle_uid=elle_uid,
    )


# =============================================================================
# TestAuthContextModel
# =============================================================================


class TestAuthContextModel:
    """Tests for AuthContext pydantic model."""

    def test_can_use_mode_returns_true_when_allowed(self):
        """can_use_mode returns True when mode is in allowed_modes."""
        ctx = AuthContext(
            source="uds_peer",
            allowed_modes=(ExecutionMode.READONLY, ExecutionMode.EXECUTE),
        )
        assert ctx.can_use_mode(ExecutionMode.READONLY) is True
        assert ctx.can_use_mode(ExecutionMode.EXECUTE) is True

    def test_can_use_mode_returns_false_when_not_allowed(self):
        """can_use_mode returns False when mode is not in allowed_modes."""
        ctx = AuthContext(
            source="anonymous",
            allowed_modes=(ExecutionMode.READONLY,),
        )
        assert ctx.can_use_mode(ExecutionMode.EXECUTE) is False
        assert ctx.can_use_mode(ExecutionMode.CAPABILITIES_ONLY) is False

    def test_frozen_model_cannot_be_mutated(self):
        """AuthContext is frozen and rejects attribute assignment."""
        ctx = AuthContext(source="anonymous", allowed_modes=(ExecutionMode.READONLY,))
        with pytest.raises(Exception):
            ctx.source = "uds_peer"  # type: ignore[misc]


# =============================================================================
# TestPredefinedAuthConstants
# =============================================================================


class TestPredefinedAuthConstants:
    """Tests for module-level ANONYMOUS_AUTH and ROOT_AUTH."""

    def test_anonymous_auth_is_readonly_only(self):
        """ANONYMOUS_AUTH allows only readonly mode."""
        assert ANONYMOUS_AUTH.source == "anonymous"
        assert ANONYMOUS_AUTH.allowed_modes == (ExecutionMode.READONLY,)
        assert ANONYMOUS_AUTH.can_use_mode(ExecutionMode.EXECUTE) is False

    def test_root_auth_has_full_access(self):
        """ROOT_AUTH grants all three execution modes."""
        assert ROOT_AUTH.source == "uds_peer"
        assert ROOT_AUTH.uid == 0
        assert ROOT_AUTH.gid == 0
        assert ROOT_AUTH.can_use_mode(ExecutionMode.READONLY) is True
        assert ROOT_AUTH.can_use_mode(ExecutionMode.EXECUTE) is True
        assert ROOT_AUTH.can_use_mode(ExecutionMode.CAPABILITIES_ONLY) is True


# =============================================================================
# TestHashKeyAndGenerateApiKey
# =============================================================================


class TestHashKeyAndGenerateApiKey:
    """Tests for _hash_key and generate_api_key helpers."""

    def test_hash_key_returns_sha256_hex(self):
        """_hash_key produces a SHA-256 hex digest."""
        expected = hashlib.sha256(b"test_key").hexdigest()
        assert _hash_key("test_key") == expected

    def test_hash_key_deterministic(self):
        """Same input always produces the same hash."""
        assert _hash_key("abc") == _hash_key("abc")

    def test_generate_api_key_format(self):
        """generate_api_key returns (key_id, full_key) with 'elle_' prefix."""
        key_id, full_key = generate_api_key()
        assert full_key.startswith(f"elle_{key_id}_")
        # key_id is 16-char hex (8 bytes)
        assert len(key_id) == 16

    def test_generate_api_key_uniqueness(self):
        """Two calls produce different keys."""
        _, key_a = generate_api_key()
        _, key_b = generate_api_key()
        assert key_a != key_b


# =============================================================================
# TestApiKeyStore
# =============================================================================


class TestApiKeyStore:
    """Tests for ApiKeyStore CRUD operations backed by mocked DB."""

    def test_create_key_inserts_row(self):
        """create_key generates a key, hashes it, and inserts into the DB."""
        store = ApiKeyStore()
        mock_conn = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            key_id, full_key = store.create_key("test-integration")

        assert full_key.startswith("elle_")
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO api_keys" in call_args[0][0]

    def test_create_key_defaults_to_readonly(self):
        """create_key with no modes defaults to readonly."""
        store = ApiKeyStore()
        mock_conn = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            store.create_key("readonly-key")

        params = mock_conn.execute.call_args[0][1]
        modes_str = params[3]  # 4th param is modes_str
        assert modes_str == "readonly"

    def test_validate_key_returns_api_key_on_match(self):
        """validate_key returns ApiKey when hash matches an active row."""
        store = ApiKeyStore()
        full_key = "elle_abc123_secretsecret"
        key_hash = _hash_key(full_key)
        now_iso = datetime.now(timezone.utc).isoformat()

        mock_row = {
            "key_id": "abc123",
            "key_hash": key_hash,
            "name": "test",
            "allowed_modes": "readonly,execute",
            "created_at": now_iso,
            "last_used_at": None,
            "revoked": False,
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            result = store.validate_key(full_key)

        assert result is not None
        assert result.key_id == "abc123"
        assert result.name == "test"
        assert ExecutionMode.READONLY in result.allowed_modes
        assert ExecutionMode.EXECUTE in result.allowed_modes
        assert result.revoked is False

    def test_validate_key_returns_none_when_not_found(self):
        """validate_key returns None when no matching row exists."""
        store = ApiKeyStore()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            result = store.validate_key("elle_bad_key")

        assert result is None

    def test_validate_key_updates_last_used_at(self):
        """validate_key updates last_used_at on successful match."""
        store = ApiKeyStore()
        full_key = "elle_abc_secret"
        key_hash = _hash_key(full_key)
        now_iso = datetime.now(timezone.utc).isoformat()

        mock_row = {
            "key_id": "abc",
            "key_hash": key_hash,
            "name": "ts",
            "allowed_modes": "readonly",
            "created_at": now_iso,
            "last_used_at": None,
            "revoked": False,
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            store.validate_key(full_key)

        # Second call to execute is the UPDATE
        assert mock_conn.execute.call_count == 2
        update_sql = mock_conn.execute.call_args_list[1][0][0]
        assert "UPDATE api_keys SET last_used_at" in update_sql

    def test_validate_key_parses_datetime_strings(self):
        """validate_key converts string timestamps to datetime objects."""
        store = ApiKeyStore()
        full_key = "elle_ts_parsesecret"
        key_hash = _hash_key(full_key)
        ts = "2024-06-15T12:30:00+00:00"

        mock_row = {
            "key_id": "ts",
            "key_hash": key_hash,
            "name": "ts",
            "allowed_modes": "readonly",
            "created_at": ts,
            "last_used_at": ts,
            "revoked": False,
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            result = store.validate_key(full_key)

        assert isinstance(result.created_at, datetime)
        assert isinstance(result.last_used_at, datetime)

    def test_validate_key_handles_native_datetime(self):
        """validate_key passes through native datetime objects unchanged."""
        store = ApiKeyStore()
        full_key = "elle_dt_natsecret"
        key_hash = _hash_key(full_key)
        now = datetime.now(timezone.utc)

        mock_row = {
            "key_id": "dt",
            "key_hash": key_hash,
            "name": "nat",
            "allowed_modes": "readonly",
            "created_at": now,
            "last_used_at": now,
            "revoked": False,
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            result = store.validate_key(full_key)

        assert result.created_at is now
        assert result.last_used_at is now

    def test_revoke_key_returns_true_on_success(self):
        """revoke_key returns True when rowcount > 0."""
        store = ApiKeyStore()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            assert store.revoke_key("abc123") is True

    def test_revoke_key_returns_false_when_not_found(self):
        """revoke_key returns False when rowcount is 0."""
        store = ApiKeyStore()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            assert store.revoke_key("nonexistent") is False

    def test_list_keys_excludes_revoked_by_default(self):
        """list_keys with include_revoked=False adds WHERE clause."""
        store = ApiKeyStore()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            result = store.list_keys(include_revoked=False)

        assert result == []
        sql = mock_conn.execute.call_args[0][0]
        assert "WHERE revoked = FALSE" in sql

    def test_list_keys_includes_revoked_when_requested(self):
        """list_keys with include_revoked=True omits WHERE clause."""
        store = ApiKeyStore()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            store.list_keys(include_revoked=True)

        sql = mock_conn.execute.call_args[0][0]
        assert "WHERE revoked = FALSE" not in sql

    def test_list_keys_parses_rows_into_api_key_models(self):
        """list_keys converts DB rows to ApiKey Pydantic models."""
        store = ApiKeyStore()
        ts = "2024-06-15T12:00:00+00:00"
        mock_rows = [
            {
                "key_id": "k1",
                "key_hash": "h1",
                "name": "key-one",
                "allowed_modes": "readonly",
                "created_at": ts,
                "last_used_at": None,
                "revoked": False,
            },
            {
                "key_id": "k2",
                "key_hash": "h2",
                "name": "key-two",
                "allowed_modes": "readonly,execute",
                "created_at": ts,
                "last_used_at": ts,
                "revoked": False,
            },
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_rows

        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_ENGINE, return_value=mock_ctx):
            result = store.list_keys()

        assert len(result) == 2
        assert result[0].key_id == "k1"
        assert result[0].last_used_at is None
        assert result[1].key_id == "k2"
        assert ExecutionMode.EXECUTE in result[1].allowed_modes
        assert isinstance(result[1].last_used_at, datetime)


# =============================================================================
# TestExtractBearerToken
# =============================================================================


class TestExtractBearerToken:
    """Tests for _extract_bearer_token helper."""

    def test_extracts_bearer_token(self):
        """Returns token from 'Bearer <token>' header."""
        req = _make_request(headers={"Authorization": "Bearer my_secret_token"})
        assert _extract_bearer_token(req) == "my_secret_token"

    def test_case_insensitive_bearer_prefix(self):
        """Bearer prefix is matched case-insensitively."""
        req = _make_request(headers={"Authorization": "BEARER my_token"})
        assert _extract_bearer_token(req) == "my_token"

    def test_returns_none_when_no_auth_header(self):
        """Returns None when Authorization header is missing."""
        req = _make_request(headers={})
        assert _extract_bearer_token(req) is None

    def test_returns_none_for_non_bearer_scheme(self):
        """Returns None for Basic auth scheme."""
        req = _make_request(headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert _extract_bearer_token(req) is None

    def test_strips_whitespace_from_token(self):
        """Token is stripped of leading/trailing whitespace."""
        req = _make_request(headers={"Authorization": "Bearer   padded_token   "})
        assert _extract_bearer_token(req) == "padded_token"


# =============================================================================
# TestGetPeerCredentials
# =============================================================================


class TestGetPeerCredentials:
    """Tests for _get_peer_credentials helper."""

    def test_returns_none_when_no_transport(self):
        """Returns None when scope has no transport."""
        req = _make_request()
        req.scope = {}
        assert _get_peer_credentials(req) is None

    def test_returns_none_when_transport_is_none(self):
        """Returns None when transport is explicitly None."""
        req = _make_request(transport=None)
        assert _get_peer_credentials(req) is None

    def test_returns_none_when_socket_is_none(self):
        """Returns None when get_extra_info('socket') returns None."""
        transport = MagicMock()
        transport.get_extra_info.return_value = None
        req = _make_request(transport=transport)
        assert _get_peer_credentials(req) is None

    def test_returns_none_for_non_unix_socket(self):
        """Returns None when socket family is not AF_UNIX."""
        import socket as socket_module

        mock_sock = MagicMock()
        mock_sock.family = socket_module.AF_INET  # TCP socket, not Unix
        transport = MagicMock()
        transport.get_extra_info.return_value = mock_sock
        req = _make_request(transport=transport)

        assert _get_peer_credentials(req) is None

    def test_returns_uid_gid_for_unix_socket(self):
        """Returns (uid, gid) for a valid Unix socket with SO_PEERCRED."""
        import socket as socket_module
        import struct

        mock_sock = MagicMock()
        mock_sock.family = socket_module.AF_UNIX
        # Pack pid=100, uid=1000, gid=1000
        creds = struct.pack("3i", 100, 1000, 1000)
        mock_sock.getsockopt.return_value = creds
        transport = MagicMock()
        transport.get_extra_info.return_value = mock_sock
        req = _make_request(transport=transport)

        result = _get_peer_credentials(req)
        assert result == (1000, 1000)

    def test_returns_none_on_getsockopt_exception(self):
        """Returns None when getsockopt raises an exception."""
        import socket as socket_module

        mock_sock = MagicMock()
        mock_sock.family = socket_module.AF_UNIX
        mock_sock.getsockopt.side_effect = OSError("No peer credentials")
        transport = MagicMock()
        transport.get_extra_info.return_value = mock_sock
        req = _make_request(transport=transport)

        assert _get_peer_credentials(req) is None


# =============================================================================
# TestAuthMiddlewareSessionToken
# =============================================================================


class TestAuthMiddlewareSessionToken:
    """Tests for AuthMiddleware session token authentication path."""

    @pytest.mark.asyncio
    async def test_valid_session_token_via_x_elle_header(self):
        """Valid session token in X-Elle-Token header grants full access."""
        config = _make_config()
        mw = AuthMiddleware(config, session_token="session_secret_123")
        req = _make_request(headers={"X-Elle-Token": "session_secret_123"})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            ctx = await mw(req)

        assert ctx.source == "uds_peer"
        assert ctx.can_use_mode(ExecutionMode.EXECUTE) is True
        assert ctx.can_use_mode(ExecutionMode.READONLY) is True
        assert ctx.can_use_mode(ExecutionMode.CAPABILITIES_ONLY) is True

    @pytest.mark.asyncio
    async def test_valid_session_token_via_bearer(self):
        """Valid session token in Bearer header grants full access."""
        config = _make_config()
        mw = AuthMiddleware(config, session_token="session_tok_456")
        req = _make_request(headers={"Authorization": "Bearer session_tok_456"})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            ctx = await mw(req)

        assert ctx.source == "uds_peer"
        assert ctx.can_use_mode(ExecutionMode.EXECUTE) is True

    @pytest.mark.asyncio
    async def test_invalid_session_token_falls_through(self):
        """Wrong session token does not match; falls through to next method."""
        config = _make_config(allow_anonymous=True)
        mw = AuthMiddleware(config, session_token="real_token")
        req = _make_request(headers={"X-Elle-Token": "wrong_token"})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            ctx = await mw(req)

        # Falls through to anonymous since allow_anonymous=True
        assert ctx.source == "anonymous"

    @pytest.mark.asyncio
    async def test_no_session_token_configured_falls_through(self):
        """When no session_token is set, session auth is skipped."""
        config = _make_config(allow_anonymous=True)
        mw = AuthMiddleware(config, session_token=None)
        req = _make_request(headers={"X-Elle-Token": "any_value"})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            ctx = await mw(req)

        assert ctx.source == "anonymous"

    def test_set_session_token_updates_token(self):
        """set_session_token replaces the stored token."""
        config = _make_config()
        mw = AuthMiddleware(config, session_token="old_token")
        mw.set_session_token("new_token")
        assert mw._session_token == "new_token"


# =============================================================================
# TestAuthMiddlewarePeerAuth
# =============================================================================


class TestAuthMiddlewarePeerAuth:
    """Tests for AuthMiddleware UDS peer authentication path."""

    @pytest.mark.asyncio
    async def test_root_peer_gets_full_access(self):
        """Root UID (0) returns ROOT_AUTH with all modes."""
        config = _make_config()
        mw = AuthMiddleware(config, session_token=None)
        req = _make_request()

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=(0, 0)):
            ctx = await mw(req)

        assert ctx is ROOT_AUTH
        assert ctx.uid == 0

    @pytest.mark.asyncio
    async def test_elle_uid_gets_full_access(self):
        """Peer matching elle_uid gets full access."""
        config = _make_config(elle_uid=1001)
        mw = AuthMiddleware(config, session_token=None)
        req = _make_request()

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=(1001, 1001)):
            ctx = await mw(req)

        assert ctx.source == "uds_peer"
        assert ctx.uid == 1001
        assert ctx.can_use_mode(ExecutionMode.EXECUTE) is True
        assert ctx.can_use_mode(ExecutionMode.CAPABILITIES_ONLY) is True

    @pytest.mark.asyncio
    async def test_other_local_user_gets_readonly(self):
        """Non-root, non-elle local user gets readonly only."""
        config = _make_config(elle_uid=1001)
        mw = AuthMiddleware(config, session_token=None)
        req = _make_request()

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=(2000, 2000)):
            ctx = await mw(req)

        assert ctx.source == "uds_peer"
        assert ctx.uid == 2000
        assert ctx.can_use_mode(ExecutionMode.READONLY) is True
        assert ctx.can_use_mode(ExecutionMode.EXECUTE) is False


# =============================================================================
# TestAuthMiddlewareApiKey
# =============================================================================


class TestAuthMiddlewareApiKey:
    """Tests for AuthMiddleware API key authentication path."""

    @pytest.mark.asyncio
    async def test_valid_api_key_returns_api_key_context(self):
        """Valid API key produces an api_key AuthContext."""
        mock_store = MagicMock(spec=ApiKeyStore)
        mock_store.validate_key.return_value = ApiKey(
            key_id="k1",
            key_hash="h1",
            name="test",
            allowed_modes=(ExecutionMode.READONLY, ExecutionMode.EXECUTE),
            created_at=datetime.now(timezone.utc),
        )
        config = _make_config()
        mw = AuthMiddleware(config, key_store=mock_store, session_token=None)
        req = _make_request(headers={"Authorization": "Bearer elle_k1_secret"})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            ctx = await mw(req)

        assert ctx.source == "api_key"
        assert ctx.api_key_id == "k1"
        assert ctx.can_use_mode(ExecutionMode.EXECUTE) is True
        mock_store.validate_key.assert_called_once_with("elle_k1_secret")

    @pytest.mark.asyncio
    async def test_invalid_api_key_raises_401(self):
        """Invalid API key raises HTTPException with 401."""
        from fastapi import HTTPException

        mock_store = MagicMock(spec=ApiKeyStore)
        mock_store.validate_key.return_value = None
        config = _make_config()
        mw = AuthMiddleware(config, key_store=mock_store, session_token=None)
        req = _make_request(headers={"Authorization": "Bearer elle_bad_key"})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await mw(req)

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_no_key_store_with_bearer_raises_401(self):
        """When key_store is None but a Bearer token is present, raises 401."""
        from fastapi import HTTPException

        config = _make_config(api_keys_db_path=None, allow_anonymous=True)
        mw = AuthMiddleware(config, key_store=None, session_token=None)
        req = _make_request(headers={"Authorization": "Bearer elle_anykey"})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await mw(req)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_key_store_no_token_falls_to_anonymous(self):
        """When key_store is None and no Bearer token, falls through to anonymous."""
        config = _make_config(api_keys_db_path=None, allow_anonymous=True)
        mw = AuthMiddleware(config, key_store=None, session_token=None)
        req = _make_request(headers={})

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            ctx = await mw(req)

        assert ctx.source == "anonymous"


# =============================================================================
# TestAuthMiddlewareAnonymousAndRejection
# =============================================================================


class TestAuthMiddlewareAnonymousAndRejection:
    """Tests for fallback to anonymous or rejection."""

    @pytest.mark.asyncio
    async def test_anonymous_allowed(self):
        """When allow_anonymous=True and no auth, returns ANONYMOUS_AUTH."""
        config = _make_config(allow_anonymous=True)
        mw = AuthMiddleware(config, session_token=None)
        req = _make_request()

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            ctx = await mw(req)

        assert ctx is ANONYMOUS_AUTH
        assert ctx.source == "anonymous"

    @pytest.mark.asyncio
    async def test_anonymous_not_allowed_raises_401(self):
        """When allow_anonymous=False and no auth, raises HTTPException 401."""
        from fastapi import HTTPException

        config = _make_config(allow_anonymous=False)
        mw = AuthMiddleware(config, session_token=None)
        req = _make_request()

        with patch("elle.daemon.api.auth._get_peer_credentials", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await mw(req)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail


# =============================================================================
# TestAuthMiddlewareKeyStoreProperty
# =============================================================================


class TestAuthMiddlewareKeyStoreProperty:
    """Tests for lazy key_store initialization."""

    def test_key_store_lazy_init_when_db_path_set(self):
        """key_store property creates ApiKeyStore when db_path is set."""
        config = _make_config(api_keys_db_path="/var/lib/elle/api_keys.db")
        mw = AuthMiddleware(config, key_store=None, session_token=None)
        store = mw.key_store
        assert store is not None
        assert isinstance(store, ApiKeyStore)

    def test_key_store_returns_none_when_no_db_path(self):
        """key_store property returns None when api_keys_db_path is None."""
        config = _make_config(api_keys_db_path=None)
        mw = AuthMiddleware(config, key_store=None, session_token=None)
        assert mw.key_store is None

    def test_key_store_returns_injected_store(self):
        """key_store property returns the explicitly provided store."""
        mock_store = MagicMock(spec=ApiKeyStore)
        config = _make_config()
        mw = AuthMiddleware(config, key_store=mock_store, session_token=None)
        assert mw.key_store is mock_store


# =============================================================================
# TestGetAuthContextDependency
# =============================================================================


class TestGetAuthContextDependency:
    """Tests for get_auth_context FastAPI dependency function."""

    @pytest.mark.asyncio
    async def test_returns_existing_auth_context_from_state(self):
        """Returns auth_context if already set on request.state."""
        expected = AuthContext(
            source="uds_peer",
            uid=500,
            gid=500,
            allowed_modes=(ExecutionMode.READONLY,),
        )
        req = MagicMock()
        req.state.auth_context = expected

        result = await get_auth_context(req)
        assert result is expected

    @pytest.mark.asyncio
    async def test_returns_anonymous_when_no_state(self):
        """Returns ANONYMOUS_AUTH when request.state has no auth_context."""
        req = MagicMock()
        req.state = MagicMock(spec=[])  # No auth_context attribute

        result = await get_auth_context(req)
        assert result is ANONYMOUS_AUTH

    @pytest.mark.asyncio
    async def test_returns_anonymous_when_state_is_none(self):
        """Returns ANONYMOUS_AUTH when auth_context is explicitly None."""
        req = MagicMock()
        req.state.auth_context = None

        result = await get_auth_context(req)
        assert result is ANONYMOUS_AUTH


# =============================================================================
# TestMigrationRegistration
# =============================================================================


class TestMigrationRegistration:
    """Tests for migration registration side-effect at module import."""

    def test_migration_v1_registered(self):
        """Module registers a v1 migration for the 'api' schema."""
        from elle.storage.migrate import _migrations

        assert "api" in _migrations
        versions = [v for v, _ in _migrations["api"]]
        assert 1 in versions

    def test_migrate_v1_creates_api_keys_table(self):
        """_migrate_v1 executes CREATE TABLE IF NOT EXISTS for api_keys."""
        from elle.daemon.api.auth import _migrate_v1

        mock_conn = MagicMock()
        _migrate_v1(mock_conn)

        sql = mock_conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS api_keys" in sql
        assert "key_id TEXT PRIMARY KEY" in sql
        assert "key_hash TEXT NOT NULL" in sql
        assert "revoked BOOLEAN" in sql
