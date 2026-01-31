"""Tests for cli/dependencies.py - dependency registry and health checking."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestDependencyStatus:
    def test_values(self):
        from elle.cli.dependencies import DependencyStatus

        assert DependencyStatus.AVAILABLE.value == "available"
        assert DependencyStatus.UNAVAILABLE.value == "unavailable"
        assert DependencyStatus.DEGRADED.value == "degraded"


class TestDegradedMode:
    def test_values(self):
        from elle.cli.dependencies import DegradedMode

        assert DegradedMode.FULL.value == "full"
        assert DegradedMode.NO_LLM.value == "no_llm"
        assert DegradedMode.NO_DAEMON.value == "no_daemon"
        assert DegradedMode.OFFLINE.value == "offline"


# ---------------------------------------------------------------------------
# DependencyCheckResult
# ---------------------------------------------------------------------------


class TestDependencyCheckResult:
    def test_creation(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyStatus

        result = DependencyCheckResult(
            name="test",
            status=DependencyStatus.AVAILABLE,
            available=True,
            message="OK",
        )
        assert result.name == "test"
        assert result.available is True

    def test_is_degraded_true(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyStatus

        result = DependencyCheckResult(
            name="test",
            status=DependencyStatus.DEGRADED,
            available=False,
        )
        assert result.is_degraded is True

    def test_is_degraded_false(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyStatus

        result = DependencyCheckResult(
            name="test",
            status=DependencyStatus.AVAILABLE,
            available=True,
        )
        assert result.is_degraded is False

    def test_default_fields(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyStatus

        result = DependencyCheckResult(
            name="test",
            status=DependencyStatus.UNAVAILABLE,
            available=False,
        )
        assert result.message == ""
        assert result.fallback_message == ""
        assert result.latency_ms is None
        assert result.details == {}


# ---------------------------------------------------------------------------
# SystemCapabilities
# ---------------------------------------------------------------------------


class TestSystemCapabilities:
    def test_defaults(self):
        from elle.cli.dependencies import DegradedMode, SystemCapabilities

        caps = SystemCapabilities()
        assert caps.mode == DegradedMode.FULL
        assert caps.llm_available is True
        assert caps.daemon_available is True

    def test_can_classify_with_llm(self):
        from elle.cli.dependencies import SystemCapabilities

        caps = SystemCapabilities(llm_available=True)
        assert caps.can_classify_with_llm is True

    def test_cannot_classify_without_llm(self):
        from elle.cli.dependencies import SystemCapabilities

        caps = SystemCapabilities(llm_available=False)
        assert caps.can_classify_with_llm is False

    def test_can_generate_plans(self):
        from elle.cli.dependencies import SystemCapabilities

        caps = SystemCapabilities(llm_available=True)
        assert caps.can_generate_plans is True

    def test_can_run_agentic_loop(self):
        from elle.cli.dependencies import SystemCapabilities

        caps = SystemCapabilities(llm_available=False)
        assert caps.can_run_agentic_loop is False

    def test_can_record_incidents(self):
        from elle.cli.dependencies import SystemCapabilities

        caps = SystemCapabilities(incident_db_available=True)
        assert caps.can_record_incidents is True

    def test_cannot_record_incidents(self):
        from elle.cli.dependencies import SystemCapabilities

        caps = SystemCapabilities(incident_db_available=False)
        assert caps.can_record_incidents is False


# ---------------------------------------------------------------------------
# OllamaChecker
# ---------------------------------------------------------------------------


class TestOllamaChecker:
    def test_name(self):
        from elle.cli.dependencies import OllamaChecker

        checker = OllamaChecker()
        assert checker.name == "ollama"

    def test_fallback_message(self):
        from elle.cli.dependencies import OllamaChecker

        checker = OllamaChecker()
        assert "Ollama" in checker.fallback_message

    @pytest.mark.asyncio
    async def test_check_success(self):
        from elle.cli.dependencies import DependencyStatus, OllamaChecker

        checker = OllamaChecker()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "qwen2.5"}]}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is True
            assert result.status == DependencyStatus.AVAILABLE
            assert "1 models" in result.message

    @pytest.mark.asyncio
    async def test_check_bad_status(self):
        from elle.cli.dependencies import DependencyStatus, OllamaChecker

        checker = OllamaChecker()
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is False
            assert result.status == DependencyStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_check_connect_error(self):
        from elle.cli.dependencies import OllamaChecker

        checker = OllamaChecker()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is False
            assert "Cannot connect" in result.message

    @pytest.mark.asyncio
    async def test_check_timeout(self):
        from elle.cli.dependencies import DependencyStatus, OllamaChecker

        checker = OllamaChecker()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is False
            assert result.status == DependencyStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_check_generic_exception(self):
        from elle.cli.dependencies import OllamaChecker

        checker = OllamaChecker()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=RuntimeError("kaboom"))

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is False


# ---------------------------------------------------------------------------
# DaemonChecker
# ---------------------------------------------------------------------------


class TestDaemonChecker:
    def test_name(self):
        from elle.cli.dependencies import DaemonChecker

        checker = DaemonChecker()
        assert checker.name == "daemon"

    def test_fallback_message(self):
        from elle.cli.dependencies import DaemonChecker

        checker = DaemonChecker()
        assert "daemon" in checker.fallback_message.lower()

    @pytest.mark.asyncio
    async def test_check_success(self):
        from elle.cli.dependencies import DaemonChecker, DependencyStatus

        checker = DaemonChecker()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is True
            assert result.status == DependencyStatus.AVAILABLE

    @pytest.mark.asyncio
    async def test_check_connect_error(self):
        from elle.cli.dependencies import DaemonChecker

        checker = DaemonChecker()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is False

    @pytest.mark.asyncio
    async def test_check_generic_exception(self):
        from elle.cli.dependencies import DaemonChecker

        checker = DaemonChecker()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=RuntimeError("oops"))

        with patch("elle.cli.dependencies.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check()
            assert result.available is False


# ---------------------------------------------------------------------------
# DatabaseChecker
# ---------------------------------------------------------------------------


class TestDatabaseChecker:
    def test_name(self):
        from elle.cli.dependencies import DatabaseChecker

        checker = DatabaseChecker(db_path="/tmp/test.db", db_name="test")
        assert checker.name == "database_test"

    def test_fallback_message(self):
        from elle.cli.dependencies import DatabaseChecker

        checker = DatabaseChecker(db_path="/tmp/test.db", db_name="mydb")
        assert "mydb" in checker.fallback_message

    @pytest.mark.asyncio
    async def test_check_file_not_exists(self, tmp_path):
        from elle.cli.dependencies import DatabaseChecker

        checker = DatabaseChecker(db_path=tmp_path / "nonexistent.db", db_name="test")
        result = await checker.check()
        assert result.available is False
        assert "does not exist" in result.message

    @pytest.mark.asyncio
    async def test_check_path_is_directory(self, tmp_path):
        from elle.cli.dependencies import DatabaseChecker

        checker = DatabaseChecker(db_path=tmp_path, db_name="test")
        result = await checker.check()
        assert result.available is False
        assert "not a file" in result.message

    @pytest.mark.asyncio
    async def test_check_valid_database(self, deps_conn):
        from elle.cli.dependencies import DatabaseChecker, DependencyStatus

        checker = DatabaseChecker(conn=deps_conn, db_name="test")
        result = await checker.check()
        assert result.available is True
        assert result.status == DependencyStatus.AVAILABLE


# ---------------------------------------------------------------------------
# DependencyRegistry
# ---------------------------------------------------------------------------


class TestDependencyRegistry:
    def test_register_and_check_unknown(self):
        from elle.cli.dependencies import DependencyRegistry

        registry = DependencyRegistry()
        # Check unregistered dependency
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(registry.check("unknown"))
        assert result.available is False
        assert "No checker registered" in result.message

    def test_register_and_unregister(self):
        from elle.cli.dependencies import DependencyRegistry

        registry = DependencyRegistry()
        mock_checker = MagicMock()
        mock_checker.name = "test_dep"
        registry.register(mock_checker)
        assert "test_dep" in registry._checkers
        registry.unregister("test_dep")
        assert "test_dep" not in registry._checkers

    @pytest.mark.asyncio
    async def test_check_caches_result(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyRegistry, DependencyStatus

        registry = DependencyRegistry(cache_ttl_seconds=60)

        mock_checker = AsyncMock()
        mock_checker.name = "cached_dep"
        check_result = DependencyCheckResult(
            name="cached_dep",
            status=DependencyStatus.AVAILABLE,
            available=True,
        )
        mock_checker.check.return_value = check_result
        registry.register(mock_checker)

        r1 = await registry.check("cached_dep")
        r2 = await registry.check("cached_dep")
        assert r1.available is True
        assert r2.available is True
        # Should only have called check once due to caching
        mock_checker.check.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_force_bypasses_cache(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyRegistry, DependencyStatus

        registry = DependencyRegistry(cache_ttl_seconds=60)

        mock_checker = AsyncMock()
        mock_checker.name = "force_dep"
        check_result = DependencyCheckResult(
            name="force_dep",
            status=DependencyStatus.AVAILABLE,
            available=True,
        )
        mock_checker.check.return_value = check_result
        registry.register(mock_checker)

        await registry.check("force_dep")
        await registry.check("force_dep", force=True)
        assert mock_checker.check.call_count == 2

    @pytest.mark.asyncio
    async def test_check_all_empty(self):
        from elle.cli.dependencies import DependencyRegistry

        registry = DependencyRegistry()
        results = await registry.check_all()
        assert results == []

    @pytest.mark.asyncio
    async def test_check_all_with_checkers(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyRegistry, DependencyStatus

        registry = DependencyRegistry()

        for name in ["dep_a", "dep_b"]:
            mock_checker = AsyncMock()
            mock_checker.name = name
            mock_checker.check.return_value = DependencyCheckResult(
                name=name,
                status=DependencyStatus.AVAILABLE,
                available=True,
            )
            registry.register(mock_checker)

        results = await registry.check_all()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_require_available(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyRegistry, DependencyStatus

        registry = DependencyRegistry()
        mock_checker = AsyncMock()
        mock_checker.name = "req_dep"
        mock_checker.check.return_value = DependencyCheckResult(
            name="req_dep",
            status=DependencyStatus.AVAILABLE,
            available=True,
        )
        registry.register(mock_checker)
        assert await registry.require("req_dep") is True

    @pytest.mark.asyncio
    async def test_require_unavailable(self):
        from elle.cli.dependencies import DependencyCheckResult, DependencyRegistry, DependencyStatus

        registry = DependencyRegistry()
        mock_checker = AsyncMock()
        mock_checker.name = "req_dep"
        mock_checker.check.return_value = DependencyCheckResult(
            name="req_dep",
            status=DependencyStatus.UNAVAILABLE,
            available=False,
        )
        registry.register(mock_checker)
        assert await registry.require("req_dep") is False

    def test_get_fallback_message_registered(self):
        from elle.cli.dependencies import DependencyRegistry

        registry = DependencyRegistry()
        mock_checker = MagicMock()
        mock_checker.name = "fb_dep"
        mock_checker.fallback_message = "Install fb_dep first"
        registry.register(mock_checker)
        assert registry.get_fallback_message("fb_dep") == "Install fb_dep first"

    def test_get_fallback_message_unregistered(self):
        from elle.cli.dependencies import DependencyRegistry

        registry = DependencyRegistry()
        msg = registry.get_fallback_message("unknown")
        assert "unknown" in msg

    def test_clear_cache(self):
        from elle.cli.dependencies import DependencyRegistry

        registry = DependencyRegistry()
        registry._cache["test"] = (datetime.utcnow(), MagicMock())
        registry.clear_cache()
        assert len(registry._cache) == 0


# ---------------------------------------------------------------------------
# check_startup_dependencies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCheckStartupDependencies:
    async def test_all_available(self):
        from elle.cli.dependencies import (
            DegradedMode,
            DependencyCheckResult,
            DependencyStatus,
            check_startup_dependencies,
            reset_dependency_registry,
        )

        reset_dependency_registry()

        results = [
            DependencyCheckResult(name="ollama", status=DependencyStatus.AVAILABLE, available=True),
            DependencyCheckResult(name="daemon", status=DependencyStatus.AVAILABLE, available=True),
            DependencyCheckResult(name="database_incidents", status=DependencyStatus.AVAILABLE, available=True),
            DependencyCheckResult(name="database_manvault", status=DependencyStatus.AVAILABLE, available=True),
        ]

        with patch("elle.cli.dependencies.get_dependency_registry") as mock_reg:
            mock_reg.return_value.check_all = AsyncMock(return_value=results)
            caps = await check_startup_dependencies()
            assert caps.mode == DegradedMode.FULL
            assert caps.llm_available is True
            assert caps.daemon_available is True

    async def test_no_llm(self):
        from elle.cli.dependencies import (
            DegradedMode,
            DependencyCheckResult,
            DependencyStatus,
            check_startup_dependencies,
            reset_dependency_registry,
        )

        reset_dependency_registry()

        results = [
            DependencyCheckResult(
                name="ollama",
                status=DependencyStatus.UNAVAILABLE,
                available=False,
                fallback_message="Start Ollama",
            ),
            DependencyCheckResult(name="daemon", status=DependencyStatus.AVAILABLE, available=True),
        ]

        with patch("elle.cli.dependencies.get_dependency_registry") as mock_reg:
            mock_reg.return_value.check_all = AsyncMock(return_value=results)
            caps = await check_startup_dependencies()
            assert caps.mode == DegradedMode.NO_LLM
            assert caps.llm_available is False
            assert "Start Ollama" in caps.messages

    async def test_offline_mode(self):
        from elle.cli.dependencies import (
            DegradedMode,
            DependencyCheckResult,
            DependencyStatus,
            check_startup_dependencies,
            reset_dependency_registry,
        )

        reset_dependency_registry()

        results = [
            DependencyCheckResult(
                name="ollama", status=DependencyStatus.UNAVAILABLE, available=False, fallback_message="x"
            ),
            DependencyCheckResult(
                name="daemon", status=DependencyStatus.UNAVAILABLE, available=False, fallback_message="y"
            ),
        ]

        with patch("elle.cli.dependencies.get_dependency_registry") as mock_reg:
            mock_reg.return_value.check_all = AsyncMock(return_value=results)
            caps = await check_startup_dependencies()
            assert caps.mode == DegradedMode.OFFLINE


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestRegistrySingleton:
    def test_get_dependency_registry_returns_same_instance(self):
        from elle.cli.dependencies import get_dependency_registry, reset_dependency_registry

        reset_dependency_registry()
        r1 = get_dependency_registry()
        r2 = get_dependency_registry()
        assert r1 is r2
        reset_dependency_registry()

    def test_reset_clears_registry(self):
        from elle.cli.dependencies import get_dependency_registry, reset_dependency_registry

        r1 = get_dependency_registry()
        reset_dependency_registry()
        r2 = get_dependency_registry()
        assert r1 is not r2
        reset_dependency_registry()
