"""Tests for API authentication middleware."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from elle.daemon.api.auth import (
    ANONYMOUS_AUTH,
    ROOT_AUTH,
    ApiKey,
    ApiKeyStore,
    AuthContext,
    AuthMiddleware,
    _hash_key,
    generate_api_key,
)
from elle.daemon.api.openai_models import ExecutionMode
from elle.daemon.config import ApiAuthConfig


class TestAuthContext:
    """Tests for AuthContext model."""

    def test_anonymous_auth(self):
        """Anonymous auth should only allow readonly."""
        assert ANONYMOUS_AUTH.source == "anonymous"
        assert ANONYMOUS_AUTH.allowed_modes == (ExecutionMode.READONLY,)
        assert ANONYMOUS_AUTH.uid is None

    def test_root_auth(self):
        """Root auth should allow all modes."""
        assert ROOT_AUTH.source == "uds_peer"
        assert ROOT_AUTH.uid == 0
        assert ExecutionMode.EXECUTE in ROOT_AUTH.allowed_modes
        assert ExecutionMode.READONLY in ROOT_AUTH.allowed_modes
        assert ExecutionMode.CAPABILITIES_ONLY in ROOT_AUTH.allowed_modes

    def test_can_use_mode_allowed(self):
        """can_use_mode should return True for allowed modes."""
        auth = AuthContext(
            source="api_key",
            allowed_modes=(ExecutionMode.READONLY,),
        )
        assert auth.can_use_mode(ExecutionMode.READONLY) is True

    def test_can_use_mode_disallowed(self):
        """can_use_mode should return False for disallowed modes."""
        auth = AuthContext(
            source="api_key",
            allowed_modes=(ExecutionMode.READONLY,),
        )
        assert auth.can_use_mode(ExecutionMode.EXECUTE) is False

    def test_auth_context_frozen(self):
        """AuthContext should be immutable."""
        auth = AuthContext(
            source="anonymous",
            allowed_modes=(ExecutionMode.READONLY,),
        )
        with pytest.raises(Exception):
            auth.source = "api_key"


class TestApiKey:
    """Tests for ApiKey model."""

    def test_basic_api_key(self):
        """Basic API key should be valid."""
        key = ApiKey(
            key_id="abc123",
            key_hash="sha256hash",
            name="Test Key",
            allowed_modes=(ExecutionMode.READONLY,),
            created_at=datetime.now(timezone.utc),
        )
        assert key.key_id == "abc123"
        assert key.name == "Test Key"
        assert key.revoked is False

    def test_revoked_key(self):
        """Revoked key should have revoked=True."""
        key = ApiKey(
            key_id="abc123",
            key_hash="sha256hash",
            name="Revoked Key",
            allowed_modes=(ExecutionMode.READONLY,),
            created_at=datetime.now(timezone.utc),
            revoked=True,
        )
        assert key.revoked is True


class TestGenerateApiKey:
    """Tests for API key generation."""

    def test_generates_key_id_and_full_key(self):
        """Should generate both key_id and full_key."""
        key_id, full_key = generate_api_key()
        assert key_id is not None
        assert full_key is not None
        assert len(key_id) == 16  # hex of 8 bytes

    def test_full_key_format(self):
        """Full key should have elle_ prefix."""
        key_id, full_key = generate_api_key()
        assert full_key.startswith("elle_")
        assert key_id in full_key

    def test_keys_are_unique(self):
        """Generated keys should be unique."""
        keys = [generate_api_key() for _ in range(10)]
        key_ids = [k[0] for k in keys]
        full_keys = [k[1] for k in keys]
        assert len(set(key_ids)) == 10
        assert len(set(full_keys)) == 10


class TestHashKey:
    """Tests for key hashing."""

    def test_hash_is_consistent(self):
        """Same key should produce same hash."""
        key = "elle_test_key_12345"
        hash1 = _hash_key(key)
        hash2 = _hash_key(key)
        assert hash1 == hash2

    def test_different_keys_different_hashes(self):
        """Different keys should produce different hashes."""
        hash1 = _hash_key("key1")
        hash2 = _hash_key("key2")
        assert hash1 != hash2

    def test_hash_is_sha256(self):
        """Hash should be SHA-256 (64 hex chars)."""
        hash_value = _hash_key("test")
        assert len(hash_value) == 64


class TestApiKeyStore:
    """Tests for API key storage."""

    @pytest.fixture
    def store(self, api_conn):
        """Create a key store using conftest api connection."""
        return ApiKeyStore(conn=api_conn)

    def test_create_key(self, store):
        """Should create and store a key."""
        key_id, full_key = store.create_key("Test Key")
        assert key_id is not None
        assert full_key.startswith("elle_")

    def test_validate_valid_key(self, store):
        """Should validate a valid key."""
        key_id, full_key = store.create_key("Test Key")
        result = store.validate_key(full_key)
        assert result is not None
        assert result.key_id == key_id
        assert result.name == "Test Key"

    def test_validate_invalid_key(self, store):
        """Should return None for invalid key."""
        result = store.validate_key("invalid_key")
        assert result is None

    def test_key_with_custom_modes(self, store):
        """Should store custom allowed modes."""
        key_id, full_key = store.create_key(
            "Full Access Key",
            allowed_modes=(ExecutionMode.EXECUTE, ExecutionMode.READONLY),
        )
        result = store.validate_key(full_key)
        assert ExecutionMode.EXECUTE in result.allowed_modes
        assert ExecutionMode.READONLY in result.allowed_modes

    def test_revoke_key(self, store):
        """Should revoke a key."""
        key_id, full_key = store.create_key("To Revoke")

        # Key should work before revocation
        assert store.validate_key(full_key) is not None

        # Revoke
        success = store.revoke_key(key_id)
        assert success is True

        # Key should not work after revocation
        assert store.validate_key(full_key) is None

    def test_revoke_nonexistent_key(self, store):
        """Should return False when revoking nonexistent key."""
        success = store.revoke_key("nonexistent")
        assert success is False

    def test_list_keys(self, store):
        """Should list all keys."""
        store.create_key("Key 1")
        store.create_key("Key 2")

        keys = store.list_keys()
        assert len(keys) == 2
        names = [k.name for k in keys]
        assert "Key 1" in names
        assert "Key 2" in names

    def test_list_keys_excludes_revoked(self, store):
        """Should exclude revoked keys by default."""
        key_id, _ = store.create_key("To Revoke")
        store.create_key("Active")
        store.revoke_key(key_id)

        keys = store.list_keys()
        assert len(keys) == 1
        assert keys[0].name == "Active"

    def test_list_keys_includes_revoked_when_requested(self, store):
        """Should include revoked keys when requested."""
        key_id, _ = store.create_key("To Revoke")
        store.create_key("Active")
        store.revoke_key(key_id)

        keys = store.list_keys(include_revoked=True)
        assert len(keys) == 2

    def test_updates_last_used(self, store):
        """Should update last_used_at on validation."""
        key_id, full_key = store.create_key("Test")

        # Initially no last_used
        keys = store.list_keys()
        assert keys[0].last_used_at is None

        # Validate the key
        store.validate_key(full_key)

        # Now has last_used
        keys = store.list_keys()
        assert keys[0].last_used_at is not None


class TestAuthMiddleware:
    """Tests for authentication middleware."""

    @pytest.fixture
    def config(self, api_conn):
        """Create auth config."""
        return ApiAuthConfig(
            allow_anonymous=True,
            api_conn=api_conn,
            elle_uid=1000,
        )

    @pytest.fixture
    def middleware(self, config):
        """Create middleware with config."""
        return AuthMiddleware(config)

    def test_auth_from_peer_root(self, middleware):
        """Root user should get full access."""
        auth = middleware._auth_from_peer(uid=0, gid=0)
        assert auth.source == "uds_peer"
        assert auth.uid == 0
        assert ExecutionMode.EXECUTE in auth.allowed_modes

    def test_auth_from_peer_elle_user(self, middleware):
        """Elle user should get full access."""
        auth = middleware._auth_from_peer(uid=1000, gid=1000)
        assert auth.source == "uds_peer"
        assert auth.uid == 1000
        assert ExecutionMode.EXECUTE in auth.allowed_modes

    def test_auth_from_peer_other_user(self, middleware):
        """Other users should get readonly only."""
        auth = middleware._auth_from_peer(uid=5000, gid=5000)
        assert auth.source == "uds_peer"
        assert auth.allowed_modes == (ExecutionMode.READONLY,)

    def test_auth_from_key_valid(self, middleware):
        """Valid API key should authenticate."""
        key_id, full_key = middleware.key_store.create_key(
            "Test",
            allowed_modes=(ExecutionMode.READONLY,),
        )
        auth = middleware._auth_from_key(full_key)
        assert auth is not None
        assert auth.source == "api_key"
        assert auth.api_key_id == key_id

    def test_auth_from_key_invalid(self, middleware):
        """Invalid API key should return None."""
        auth = middleware._auth_from_key("invalid_key")
        assert auth is None

    @pytest.mark.asyncio
    async def test_middleware_anonymous_allowed(self, config, api_conn):
        """Should allow anonymous when configured."""
        pytest.importorskip("fastapi")

        config_anon = ApiAuthConfig(
            allow_anonymous=True,
            api_conn=api_conn,
        )
        middleware = AuthMiddleware(config_anon)

        # Mock request with no auth
        request = MagicMock()
        request.headers = {}
        request.scope = {}

        auth = await middleware(request)
        assert auth.source == "anonymous"
        assert auth.allowed_modes == (ExecutionMode.READONLY,)

    @pytest.mark.asyncio
    async def test_middleware_anonymous_denied(self, api_conn):
        """Should reject when anonymous not allowed."""
        fastapi = pytest.importorskip("fastapi")
        HTTPException = fastapi.HTTPException

        config = ApiAuthConfig(
            allow_anonymous=False,
            api_conn=api_conn,
        )
        middleware = AuthMiddleware(config)

        # Mock request with no auth
        request = MagicMock()
        request.headers = {}
        request.scope = {}

        with pytest.raises(HTTPException) as exc_info:
            await middleware(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_middleware_bearer_token(self, middleware):
        """Should authenticate with bearer token."""
        pytest.importorskip("fastapi")

        # Create a key
        key_id, full_key = middleware.key_store.create_key(
            "Test",
            allowed_modes=(ExecutionMode.READONLY, ExecutionMode.EXECUTE),
        )

        # Mock request with bearer token
        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {full_key}"}
        request.scope = {}

        auth = await middleware(request)
        assert auth.source == "api_key"
        assert auth.api_key_id == key_id
        assert ExecutionMode.EXECUTE in auth.allowed_modes

    @pytest.mark.asyncio
    async def test_middleware_invalid_bearer_token(self, middleware):
        """Should reject invalid bearer token."""
        fastapi = pytest.importorskip("fastapi")
        HTTPException = fastapi.HTTPException

        request = MagicMock()
        request.headers = {"Authorization": "Bearer invalid_token"}
        request.scope = {}

        with pytest.raises(HTTPException) as exc_info:
            await middleware(request)
        assert exc_info.value.status_code == 401
