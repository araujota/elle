from __future__ import annotations

import sys
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch heavy third-party imports BEFORE importing the module under test
# so that psycopg / psycopg_pool / pgvector are never loaded for real.
# ---------------------------------------------------------------------------

_mock_psycopg = MagicMock()
_mock_psycopg_rows = MagicMock()
_mock_psycopg_pool = MagicMock()
_mock_pgvector = MagicMock()

sys.modules.setdefault("psycopg", _mock_psycopg)
sys.modules.setdefault("psycopg.rows", _mock_psycopg_rows)
sys.modules.setdefault("psycopg_pool", _mock_psycopg_pool)
sys.modules.setdefault("pgvector", _mock_pgvector)
sys.modules.setdefault("pgvector.psycopg", _mock_pgvector)

# Now we can safely import the module under test
from elle.storage import engine as engine_mod  # noqa: E402
from elle.storage.config import PostgresConfig  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Ensure every test starts with clean module-level pool singletons."""
    engine_mod.reset_pools()
    yield
    engine_mod.reset_pools()


@pytest.fixture()
def cfg() -> PostgresConfig:
    """Return a minimal PostgresConfig for testing."""
    return PostgresConfig(
        host="localhost",
        port=5432,
        dbname="testdb",
        user="testuser",
        password="secret",
    )


# ===========================================================================
# _on_connect / _on_connect_async
# ===========================================================================


class TestOnConnect:
    """Tests for the sync _on_connect callback."""

    def test_registers_vector_on_success(self):
        mock_conn = MagicMock()
        mock_register = MagicMock()
        with patch.dict(
            "sys.modules",
            {"pgvector.psycopg": MagicMock(register_vector=mock_register)},
        ):
            engine_mod._on_connect(mock_conn)
        mock_register.assert_called_once_with(mock_conn)

    def test_skips_gracefully_on_import_error(self):
        mock_conn = MagicMock()
        with patch.dict("sys.modules", {"pgvector.psycopg": None}):
            # When the sub-module is set to None, import will raise ImportError
            engine_mod._on_connect(mock_conn)
        # Should not raise -- the except branch logs and continues

    def test_skips_gracefully_on_runtime_error(self):
        mock_conn = MagicMock()
        bad_mod = MagicMock()
        bad_mod.register_vector.side_effect = RuntimeError("pg ext missing")
        with patch.dict("sys.modules", {"pgvector.psycopg": bad_mod}):
            engine_mod._on_connect(mock_conn)
        # Should swallow the RuntimeError


class TestOnConnectAsync:
    """Tests for the async _on_connect_async callback."""

    @pytest.mark.asyncio
    async def test_registers_vector_async_on_success(self):
        mock_conn = AsyncMock()
        mock_register = AsyncMock()
        with patch.dict(
            "sys.modules",
            {"pgvector.psycopg": MagicMock(register_vector_async=mock_register)},
        ):
            await engine_mod._on_connect_async(mock_conn)
        mock_register.assert_awaited_once_with(mock_conn)

    @pytest.mark.asyncio
    async def test_skips_gracefully_on_import_error(self):
        mock_conn = AsyncMock()
        with patch.dict("sys.modules", {"pgvector.psycopg": None}):
            await engine_mod._on_connect_async(mock_conn)

    @pytest.mark.asyncio
    async def test_skips_gracefully_on_runtime_error(self):
        mock_conn = AsyncMock()
        bad_mod = MagicMock()
        bad_mod.register_vector_async = AsyncMock(side_effect=RuntimeError("pg ext missing"))
        with patch.dict("sys.modules", {"pgvector.psycopg": bad_mod}):
            await engine_mod._on_connect_async(mock_conn)


# ===========================================================================
# configure_pool
# ===========================================================================


class TestConfigurePool:
    """Tests for configure_pool()."""

    @patch.object(engine_mod, "ConnectionPool")
    def test_creates_pool_on_first_call(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        mock_pool_instance = MagicMock()
        mock_pool_cls.return_value = mock_pool_instance

        result = engine_mod.configure_pool(cfg)

        assert result is mock_pool_instance
        mock_pool_cls.assert_called_once()
        # Verify conninfo was passed through
        call_kwargs = mock_pool_cls.call_args
        assert call_kwargs.kwargs["conninfo"] == cfg.conninfo
        assert call_kwargs.kwargs["min_size"] == cfg.min_pool_size
        assert call_kwargs.kwargs["max_size"] == cfg.max_pool_size

    @patch.object(engine_mod, "ConnectionPool")
    def test_returns_existing_pool_on_second_call(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        first = engine_mod.configure_pool(cfg)
        second = engine_mod.configure_pool(cfg)

        assert first is second
        # Constructor called only once
        assert mock_pool_cls.call_count == 1

    @patch.object(engine_mod, "ConnectionPool")
    def test_uses_default_config_when_none(self, mock_pool_cls: MagicMock):
        mock_pool_cls.return_value = MagicMock()
        result = engine_mod.configure_pool(None)

        assert result is not None
        call_kwargs = mock_pool_cls.call_args
        default_cfg = PostgresConfig()
        assert call_kwargs.kwargs["conninfo"] == default_cfg.conninfo

    @patch.object(engine_mod, "ConnectionPool")
    def test_stores_config_in_module(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        mock_pool_cls.return_value = MagicMock()
        engine_mod.configure_pool(cfg)
        assert engine_mod._config is cfg


# ===========================================================================
# configure_async_pool
# ===========================================================================


class TestConfigureAsyncPool:
    """Tests for configure_async_pool()."""

    @patch.object(engine_mod, "AsyncConnectionPool")
    def test_creates_async_pool_on_first_call(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        mock_pool_instance = MagicMock()
        mock_pool_cls.return_value = mock_pool_instance

        result = engine_mod.configure_async_pool(cfg)

        assert result is mock_pool_instance
        mock_pool_cls.assert_called_once()
        call_kwargs = mock_pool_cls.call_args
        assert call_kwargs.kwargs["conninfo"] == cfg.conninfo

    @patch.object(engine_mod, "AsyncConnectionPool")
    def test_returns_existing_async_pool_on_second_call(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        first = engine_mod.configure_async_pool(cfg)
        second = engine_mod.configure_async_pool(cfg)

        assert first is second
        assert mock_pool_cls.call_count == 1

    @patch.object(engine_mod, "AsyncConnectionPool")
    def test_uses_default_config_when_none(self, mock_pool_cls: MagicMock):
        mock_pool_cls.return_value = MagicMock()
        result = engine_mod.configure_async_pool(None)

        assert result is not None
        call_kwargs = mock_pool_cls.call_args
        default_cfg = PostgresConfig()
        assert call_kwargs.kwargs["conninfo"] == default_cfg.conninfo

    @patch.object(engine_mod, "AsyncConnectionPool")
    @patch.object(engine_mod, "ConnectionPool")
    def test_falls_back_to_existing_config(
        self,
        mock_sync_cls: MagicMock,
        mock_async_cls: MagicMock,
        cfg: PostgresConfig,
    ):
        """When config=None but _config was set by configure_pool, reuse it."""
        mock_sync_cls.return_value = MagicMock()
        mock_async_cls.return_value = MagicMock()

        # First configure sync pool (stores cfg in _config)
        engine_mod.configure_pool(cfg)

        # Now configure async pool with config=None -- should reuse cfg
        engine_mod.configure_async_pool(None)

        async_call_kwargs = mock_async_cls.call_args
        assert async_call_kwargs.kwargs["conninfo"] == cfg.conninfo

    @patch.object(engine_mod, "AsyncConnectionPool")
    def test_stores_config_in_module(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        mock_pool_cls.return_value = MagicMock()
        engine_mod.configure_async_pool(cfg)
        assert engine_mod._config is cfg


# ===========================================================================
# get_pool / get_async_pool
# ===========================================================================


class TestGetPool:
    """Tests for get_pool()."""

    def test_raises_when_not_configured(self):
        with pytest.raises(RuntimeError, match="sync pool not configured"):
            engine_mod.get_pool()

    @patch.object(engine_mod, "ConnectionPool")
    def test_returns_pool_after_configure(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        mock_pool_cls.return_value = MagicMock()
        engine_mod.configure_pool(cfg)
        pool = engine_mod.get_pool()
        assert pool is mock_pool_cls.return_value


class TestGetAsyncPool:
    """Tests for get_async_pool()."""

    def test_raises_when_not_configured(self):
        with pytest.raises(RuntimeError, match="async pool not configured"):
            engine_mod.get_async_pool()

    @patch.object(engine_mod, "AsyncConnectionPool")
    def test_returns_pool_after_configure(self, mock_pool_cls: MagicMock, cfg: PostgresConfig):
        mock_pool_cls.return_value = MagicMock()
        engine_mod.configure_async_pool(cfg)
        pool = engine_mod.get_async_pool()
        assert pool is mock_pool_cls.return_value


# ===========================================================================
# close_pools
# ===========================================================================


class TestClosePools:
    """Tests for close_pools()."""

    def test_close_when_no_pools_configured(self):
        # Should not raise when both pools are None
        engine_mod.close_pools()
        assert engine_mod._pool is None
        assert engine_mod._async_pool is None

    def test_close_sync_pool(self):
        mock_pool = MagicMock()
        engine_mod._pool = mock_pool

        engine_mod.close_pools()

        mock_pool.close.assert_called_once()
        assert engine_mod._pool is None

    def test_close_async_pool(self):
        mock_pool = MagicMock()
        engine_mod._async_pool = mock_pool

        engine_mod.close_pools()

        mock_pool.close.assert_called_once()
        assert engine_mod._async_pool is None

    def test_close_both_pools(self):
        mock_sync = MagicMock()
        mock_async = MagicMock()
        engine_mod._pool = mock_sync
        engine_mod._async_pool = mock_async

        engine_mod.close_pools()

        mock_sync.close.assert_called_once()
        mock_async.close.assert_called_once()
        assert engine_mod._pool is None
        assert engine_mod._async_pool is None

    def test_close_handles_sync_pool_exception(self):
        mock_pool = MagicMock()
        mock_pool.close.side_effect = Exception("close failed")
        engine_mod._pool = mock_pool

        # Should swallow exception
        engine_mod.close_pools()
        assert engine_mod._pool is None

    def test_close_handles_async_pool_exception(self):
        mock_pool = MagicMock()
        mock_pool.close.side_effect = Exception("close failed")
        engine_mod._async_pool = mock_pool

        engine_mod.close_pools()
        assert engine_mod._async_pool is None

    def test_close_handles_both_pool_exceptions(self):
        mock_sync = MagicMock()
        mock_sync.close.side_effect = Exception("sync close failed")
        mock_async = MagicMock()
        mock_async.close.side_effect = Exception("async close failed")
        engine_mod._pool = mock_sync
        engine_mod._async_pool = mock_async

        engine_mod.close_pools()
        assert engine_mod._pool is None
        assert engine_mod._async_pool is None


# ===========================================================================
# reset_pools
# ===========================================================================


class TestResetPools:
    """Tests for reset_pools()."""

    def test_clears_all_singletons(self):
        engine_mod._pool = MagicMock()
        engine_mod._async_pool = MagicMock()
        engine_mod._config = MagicMock()

        engine_mod.reset_pools()

        assert engine_mod._pool is None
        assert engine_mod._async_pool is None
        assert engine_mod._config is None


# ===========================================================================
# get_conn (sync context manager)
# ===========================================================================


class TestGetConn:
    """Tests for the get_conn() sync context manager."""

    def test_sets_search_path_and_commits(self):
        mock_conn = MagicMock()

        # Build a pool whose .connection() is a context manager yielding mock_conn
        mock_pool = MagicMock()
        mock_pool.connection = MagicMock(return_value=contextmanager(lambda: (yield mock_conn))())
        engine_mod._pool = mock_pool

        with engine_mod.get_conn(schema="incidents") as conn:
            assert conn is mock_conn

        # Verify search_path SET was executed
        mock_conn.execute.assert_called_once_with("SET search_path TO %s, public", ("incidents",))
        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_not_called()

    def test_uses_public_schema_by_default(self):
        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection = MagicMock(return_value=contextmanager(lambda: (yield mock_conn))())
        engine_mod._pool = mock_pool

        with engine_mod.get_conn():
            pass

        mock_conn.execute.assert_called_once_with("SET search_path TO %s, public", ("public",))

    def test_rolls_back_on_exception(self):
        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection = MagicMock(return_value=contextmanager(lambda: (yield mock_conn))())
        engine_mod._pool = mock_pool

        with pytest.raises(ValueError, match="oops"):
            with engine_mod.get_conn(schema="telemetry"):
                raise ValueError("oops")

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_raises_runtime_error_when_pool_missing(self):
        with pytest.raises(RuntimeError, match="sync pool not configured"):
            with engine_mod.get_conn():
                pass


# ===========================================================================
# get_async_conn (async context manager)
# ===========================================================================


class TestGetAsyncConn:
    """Tests for the get_async_conn() async context manager."""

    @pytest.mark.asyncio
    async def test_sets_search_path_and_commits(self):
        mock_conn = AsyncMock()

        mock_pool = MagicMock()

        @asynccontextmanager
        async def _fake_connection():
            yield mock_conn

        mock_pool.connection = _fake_connection
        engine_mod._async_pool = mock_pool

        async with engine_mod.get_async_conn(schema="manvault") as conn:
            assert conn is mock_conn

        mock_conn.execute.assert_awaited_once_with("SET search_path TO %s, public", ("manvault",))
        mock_conn.commit.assert_awaited_once()
        mock_conn.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_public_schema_by_default(self):
        mock_conn = AsyncMock()
        mock_pool = MagicMock()

        @asynccontextmanager
        async def _fake_connection():
            yield mock_conn

        mock_pool.connection = _fake_connection
        engine_mod._async_pool = mock_pool

        async with engine_mod.get_async_conn():
            pass

        mock_conn.execute.assert_awaited_once_with("SET search_path TO %s, public", ("public",))

    @pytest.mark.asyncio
    async def test_rolls_back_on_exception(self):
        mock_conn = AsyncMock()
        mock_pool = MagicMock()

        @asynccontextmanager
        async def _fake_connection():
            yield mock_conn

        mock_pool.connection = _fake_connection
        engine_mod._async_pool = mock_pool

        with pytest.raises(ValueError, match="async oops"):
            async with engine_mod.get_async_conn(schema="reactive"):
                raise ValueError("async oops")

        mock_conn.rollback.assert_awaited_once()
        mock_conn.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_pool_missing(self):
        with pytest.raises(RuntimeError, match="async pool not configured"):
            async with engine_mod.get_async_conn():
                pass


# ===========================================================================
# Integration-style: full lifecycle
# ===========================================================================


class TestPoolLifecycle:
    """End-to-end lifecycle: configure -> use -> close -> reset."""

    @patch.object(engine_mod, "AsyncConnectionPool")
    @patch.object(engine_mod, "ConnectionPool")
    def test_full_sync_lifecycle(
        self,
        mock_sync_cls: MagicMock,
        mock_async_cls: MagicMock,
        cfg: PostgresConfig,
    ):
        mock_sync_pool = MagicMock()
        mock_sync_cls.return_value = mock_sync_pool

        # 1. configure
        pool = engine_mod.configure_pool(cfg)
        assert pool is mock_sync_pool

        # 2. get_pool
        assert engine_mod.get_pool() is mock_sync_pool

        # 3. close
        engine_mod.close_pools()
        mock_sync_pool.close.assert_called_once()
        assert engine_mod._pool is None

        # 4. get_pool should fail now
        with pytest.raises(RuntimeError):
            engine_mod.get_pool()

    @patch.object(engine_mod, "AsyncConnectionPool")
    @patch.object(engine_mod, "ConnectionPool")
    def test_full_async_lifecycle(
        self,
        mock_sync_cls: MagicMock,
        mock_async_cls: MagicMock,
        cfg: PostgresConfig,
    ):
        mock_async_pool = MagicMock()
        mock_async_cls.return_value = mock_async_pool

        pool = engine_mod.configure_async_pool(cfg)
        assert pool is mock_async_pool
        assert engine_mod.get_async_pool() is mock_async_pool

        engine_mod.close_pools()
        mock_async_pool.close.assert_called_once()

        with pytest.raises(RuntimeError):
            engine_mod.get_async_pool()

    @patch.object(engine_mod, "AsyncConnectionPool")
    @patch.object(engine_mod, "ConnectionPool")
    def test_configure_pool_kwargs(
        self,
        mock_sync_cls: MagicMock,
        mock_async_cls: MagicMock,
        cfg: PostgresConfig,
    ):
        """Verify the exact kwargs passed to the pool constructors."""
        mock_sync_cls.return_value = MagicMock()
        mock_async_cls.return_value = MagicMock()

        engine_mod.configure_pool(cfg)
        sync_kwargs = mock_sync_cls.call_args.kwargs
        assert sync_kwargs["conninfo"] == cfg.conninfo
        assert sync_kwargs["min_size"] == cfg.min_pool_size
        assert sync_kwargs["max_size"] == cfg.max_pool_size
        assert sync_kwargs["max_idle"] == cfg.max_idle_sec
        assert sync_kwargs["kwargs"]["autocommit"] is False
        assert sync_kwargs["configure"] is engine_mod._on_connect

        engine_mod.reset_pools()

        engine_mod.configure_async_pool(cfg)
        async_kwargs = mock_async_cls.call_args.kwargs
        assert async_kwargs["conninfo"] == cfg.conninfo
        assert async_kwargs["configure"] is engine_mod._on_connect_async
