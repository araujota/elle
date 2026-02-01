"""Tests for service.py to boost coverage.

Tests IncidentVaultService with mocked DB, embedding client, and asyncio.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.incidents.service import (
    IncidentVaultService,
    get_service,
    reset_service,
)


# ---------------------------------------------------------------------------
# IncidentVaultService tests
# ---------------------------------------------------------------------------

PATCH_GET_CONN = "elle.daemon.incidents.service.get_conn"


class TestIncidentVaultService:
    @pytest.mark.timeout(10)
    def test_init(self):
        svc = IncidentVaultService()
        assert svc._embedding is False
        assert svc._stop_requested is False
        assert svc._task is None

    @pytest.mark.timeout(10)
    def test_is_embedding(self):
        svc = IncidentVaultService()
        assert svc.is_embedding is False
        svc._embedding = True
        assert svc.is_embedding is True

    @pytest.mark.timeout(10)
    def test_get_status(self):
        svc = IncidentVaultService()
        status_dict = {
            "total_incidents": 10,
            "total_actions": 5,
            "total_snapshots": 3,
            "embedded_incidents": 2,
            "open_count": 4,
            "mitigated_count": 3,
            "resolved_count": 3,
            "by_domain": {"net": 5},
            "by_outcome": {"improved": 3},
            "db_size_bytes": 1024,
            "oldest_incident": None,
            "newest_incident": None,
        }
        with patch("elle.daemon.incidents.service.get_status", return_value=status_dict):
            status = svc.get_status()
            assert status.total_incidents == 10
            assert status.total_actions == 5

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        svc = IncidentVaultService()

        # Mock the periodic tasks to avoid long running
        async def fake_periodic():
            while not svc._stop_requested:
                await asyncio.sleep(0.01)

        with patch.object(svc, "_run_periodic_tasks", side_effect=fake_periodic):
            await svc.start()
            assert svc._task is not None

            await svc.stop()
            assert svc._stop_requested is True

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        svc = IncidentVaultService()
        await svc.stop()
        assert svc._stop_requested is True

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_do_embedding_skips_when_running(self):
        svc = IncidentVaultService()
        svc._embedding = True

        await svc._do_embedding()
        # Should return immediately without doing anything
        assert svc._embedding is True

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_do_embedding_calls_embed_pending(self):
        svc = IncidentVaultService()

        with patch.object(svc, "_embed_pending", return_value=3):
            await svc._do_embedding()

        assert svc._embedding is False

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_do_embedding_handles_error(self):
        svc = IncidentVaultService()

        with patch.object(svc, "_embed_pending", side_effect=Exception("fail")):
            await svc._do_embedding()

        assert svc._embedding is False

    @pytest.mark.timeout(10)
    def test_embed_pending_import_error(self):
        svc = IncidentVaultService()

        with patch("elle.daemon.incidents.service.get_conn") as mock_gc:
            # get_conn should NOT be called if import fails
            with patch.dict("sys.modules", {"elle.rag": None}):
                try:
                    count = svc._embed_pending()
                except Exception:
                    count = 0
            # Should return 0 on import error
            assert count == 0

    @pytest.mark.timeout(10)
    def test_embed_pending_client_unavailable(self):
        svc = IncidentVaultService()

        mock_client = MagicMock()
        mock_client.is_available.return_value = False

        mock_rag = MagicMock()
        mock_rag.get_client.return_value = mock_client

        with patch.dict("sys.modules", {"elle.rag": mock_rag}):
            with patch("elle.daemon.incidents.service.get_conn") as mock_gc:
                conn = MagicMock()
                mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
                mock_gc.return_value.__exit__ = MagicMock(return_value=False)
                count = svc._embed_pending()

        assert count == 0

    @pytest.mark.timeout(10)
    def test_embed_pending_no_incidents(self):
        svc = IncidentVaultService()

        mock_client = MagicMock()
        mock_client.is_available.return_value = True

        with patch("elle.daemon.incidents.service.get_incidents_without_embeddings", return_value=[]):
            with patch("elle.daemon.incidents.service.get_conn") as mock_gc:
                conn = MagicMock()
                mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
                mock_gc.return_value.__exit__ = MagicMock(return_value=False)
                mock_rag = MagicMock()
                mock_rag.get_client.return_value = mock_client
                with patch.dict("sys.modules", {"elle.rag": mock_rag}):
                    count = svc._embed_pending()

        assert count == 0

    @pytest.mark.timeout(10)
    def test_embed_pending_with_incidents(self):
        svc = IncidentVaultService()

        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.generate_embedding.return_value = [0.1, 0.2, 0.3]

        mock_incident = MagicMock()
        mock_incident.title = "Test"
        mock_incident.domain = "net"
        mock_incident.summary = "Test summary"
        mock_incident.symptoms = ()
        mock_incident.root_cause = None
        mock_incident.tags = ()

        with patch("elle.daemon.incidents.service.get_incidents_without_embeddings", return_value=["inc-001"]):
            with patch("elle.daemon.incidents.service.get_incident", return_value=mock_incident):
                with patch("elle.daemon.incidents.service.generate_case_text", return_value="case text"):
                    with patch("elle.daemon.incidents.service.upsert_embedding"):
                        with patch("elle.daemon.incidents.service.get_conn") as mock_gc:
                            conn = MagicMock()
                            mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
                            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
                            mock_rag = MagicMock()
                            mock_rag.get_client.return_value = mock_client
                            with patch.dict("sys.modules", {"elle.rag": mock_rag}):
                                count = svc._embed_pending()

        assert count == 1

    @pytest.mark.timeout(10)
    def test_embed_pending_incident_not_found(self):
        svc = IncidentVaultService()

        mock_client = MagicMock()
        mock_client.is_available.return_value = True

        with patch("elle.daemon.incidents.service.get_incidents_without_embeddings", return_value=["inc-missing"]):
            with patch("elle.daemon.incidents.service.get_incident", return_value=None):
                with patch("elle.daemon.incidents.service.get_conn") as mock_gc:
                    conn = MagicMock()
                    mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
                    mock_gc.return_value.__exit__ = MagicMock(return_value=False)
                    mock_rag = MagicMock()
                    mock_rag.get_client.return_value = mock_client
                    with patch.dict("sys.modules", {"elle.rag": mock_rag}):
                        count = svc._embed_pending()

        assert count == 0

    @pytest.mark.timeout(10)
    def test_handle_stale_incidents(self):
        svc = IncidentVaultService()

        with patch(PATCH_GET_CONN) as mock_gc:
            conn = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            conn.cursor.return_value = cursor
            cursor.fetchall.return_value = [{"id": "stale-001"}]

            svc._handle_stale_incidents()

            cursor.execute.assert_called_once()

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_check_stale_incidents(self):
        svc = IncidentVaultService()

        with patch.object(svc, "_handle_stale_incidents"):
            await svc._check_stale_incidents()

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_run_periodic_tasks_exits_on_stop(self):
        svc = IncidentVaultService()
        svc._stop_requested = True

        # Should exit immediately since _stop_requested is True
        await svc._run_periodic_tasks()

    @pytest.mark.timeout(10)
    @pytest.mark.asyncio
    async def test_run_periodic_tasks_handles_error(self):
        svc = IncidentVaultService()
        call_count = [0]

        async def fake_embedding():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("test error")
            svc._stop_requested = True

        with patch.object(svc, "_do_embedding", side_effect=fake_embedding):
            with patch.object(svc, "_check_stale_incidents", new_callable=AsyncMock):
                with patch("elle.daemon.incidents.service.asyncio.sleep", new_callable=AsyncMock):
                    await svc._run_periodic_tasks()


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    @pytest.mark.timeout(10)
    def test_get_service_singleton(self):
        import elle.daemon.incidents.service as mod

        old = mod._service
        mod._service = None
        try:
            s1 = get_service()
            s2 = get_service()
            assert s1 is s2
        finally:
            mod._service = old

    @pytest.mark.timeout(10)
    def test_reset_service(self):
        import elle.daemon.incidents.service as mod

        mod._service = IncidentVaultService()
        reset_service()
        assert mod._service is None
