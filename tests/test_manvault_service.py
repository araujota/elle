from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ManVaultService tests
# ---------------------------------------------------------------------------


class TestManVaultService:
    def _make_service(self):
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.ensure_schema"),
        ):
            mock_conn.return_value = MagicMock()
            from elle.daemon.manvault.service import ManVaultService

            return ManVaultService()

    def test_init(self):
        svc = self._make_service()
        assert svc.is_indexing is False
        assert svc.is_embedding is False
        assert svc._task is None

    def test_embedder_lazy(self):
        svc = self._make_service()
        with patch("elle.daemon.manvault.service.get_embedder") as mock_get:
            mock_embedder = MagicMock()
            mock_get.return_value = mock_embedder
            assert svc.embedder is mock_embedder

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        svc = self._make_service()
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.ensure_schema"),
            patch("elle.daemon.manvault.service.is_core_seeded", return_value=True),
        ):
            mock_c = MagicMock()
            mock_c.close = MagicMock()
            mock_conn.return_value = mock_c

            # Override _run_periodic_tasks to return immediately
            async def fake_tasks():
                await asyncio.sleep(0.01)

            with patch.object(svc, "_run_periodic_tasks", side_effect=fake_tasks):
                await svc.start()
                assert svc._task is not None
                await asyncio.sleep(0.05)
                await svc.stop()
                assert svc._task is None

    @pytest.mark.asyncio
    async def test_start_seeds_core(self):
        svc = self._make_service()
        seed_called = False

        async def fake_seed():
            nonlocal seed_called
            seed_called = True
            return 5

        async def fake_periodic_tasks():
            return None

        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.ensure_schema"),
            patch("elle.daemon.manvault.service.is_core_seeded", return_value=False),
            patch.object(svc, "_do_core_seed", side_effect=fake_seed),
            patch.object(svc, "_run_periodic_tasks", side_effect=fake_periodic_tasks),
        ):
            mock_c = MagicMock()
            mock_c.close = MagicMock()
            mock_conn.return_value = mock_c
            await svc.start()
            await asyncio.sleep(0.01)
            assert seed_called

    @pytest.mark.asyncio
    async def test_do_core_seed(self):
        svc = self._make_service()
        with patch("elle.daemon.manvault.service.seed_core_commands", return_value=10):
            count = await svc._do_core_seed()
            assert count == 10
            assert svc.is_indexing is False

    @pytest.mark.asyncio
    async def test_do_core_seed_failure(self):
        svc = self._make_service()
        with patch("elle.daemon.manvault.service.seed_core_commands", side_effect=RuntimeError("fail")):
            count = await svc._do_core_seed()
            assert count == 0

    @pytest.mark.asyncio
    async def test_do_full_index(self):
        svc = self._make_service()
        with patch("elle.daemon.manvault.service.index_all", return_value=42):
            count = await svc._do_full_index()
            assert count == 42

    @pytest.mark.asyncio
    async def test_do_full_index_failure(self):
        svc = self._make_service()
        with patch("elle.daemon.manvault.service.index_all", side_effect=RuntimeError("fail")):
            count = await svc._do_full_index()
            assert count == 0

    @pytest.mark.asyncio
    async def test_do_incremental_index(self):
        svc = self._make_service()
        with patch("elle.daemon.manvault.service.index_incremental", return_value=(3, 2)):
            added, updated = await svc._do_incremental_index()
            assert added == 3
            assert updated == 2
            assert svc._last_incremental is not None

    @pytest.mark.asyncio
    async def test_do_incremental_index_failure(self):
        svc = self._make_service()
        with patch("elle.daemon.manvault.service.index_incremental", side_effect=RuntimeError("fail")):
            added, updated = await svc._do_incremental_index()
            assert added == 0
            assert updated == 0

    @pytest.mark.asyncio
    async def test_do_embedding(self):
        svc = self._make_service()
        mock_embedder = MagicMock()
        mock_embedder.embed_all_pending.return_value = 5
        svc._embedder = mock_embedder
        count = await svc._do_embedding()
        assert count == 5

    @pytest.mark.asyncio
    async def test_do_embedding_unavailable(self):
        svc = self._make_service()
        from elle.rag.ollama_client import OllamaUnavailableError

        mock_embedder = MagicMock()
        mock_embedder.embed_all_pending.side_effect = OllamaUnavailableError("nope")
        svc._embedder = mock_embedder
        count = await svc._do_embedding()
        assert count == 0

    @pytest.mark.asyncio
    async def test_do_embedding_failure(self):
        svc = self._make_service()
        mock_embedder = MagicMock()
        mock_embedder.embed_all_pending.side_effect = RuntimeError("fail")
        svc._embedder = mock_embedder
        count = await svc._do_embedding()
        assert count == 0

    @pytest.mark.asyncio
    async def test_trigger_reindex_while_indexing(self):
        svc = self._make_service()
        svc._indexing = True
        await svc.trigger_reindex()
        # Should not start another index

    @pytest.mark.asyncio
    async def test_trigger_reindex(self):
        svc = self._make_service()
        svc._indexing = False
        with patch.object(svc, "_do_full_index", new_callable=AsyncMock, return_value=10):
            await svc.trigger_reindex()
            await asyncio.sleep(0.05)

    def test_needs_initial_index_empty(self):
        svc = self._make_service()
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.count_docs", return_value=0),
        ):
            mock_c = MagicMock()
            mock_c.close = MagicMock()
            mock_conn.return_value = mock_c
            assert svc._needs_initial_index() is True

    def test_needs_initial_index_has_docs(self):
        svc = self._make_service()
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.count_docs", return_value=100),
        ):
            mock_c = MagicMock()
            mock_c.close = MagicMock()
            mock_conn.return_value = mock_c
            assert svc._needs_initial_index() is False

    def test_needs_incremental_no_meta(self):
        svc = self._make_service()
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.get_meta", return_value=None),
        ):
            mock_c = MagicMock()
            mock_c.close = MagicMock()
            mock_conn.return_value = mock_c
            assert svc._needs_incremental_index() is True

    def test_needs_incremental_recent(self):
        svc = self._make_service()
        recent = datetime.now(timezone.utc).isoformat()
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.get_meta", return_value=recent),
        ):
            mock_c = MagicMock()
            mock_c.close = MagicMock()
            mock_conn.return_value = mock_c
            assert svc._needs_incremental_index() is False

    def test_needs_incremental_old(self):
        svc = self._make_service()
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.get_meta", return_value=old),
        ):
            mock_c = MagicMock()
            mock_c.close = MagicMock()
            mock_conn.return_value = mock_c
            assert svc._needs_incremental_index() is True

    def test_get_status(self):
        svc = self._make_service()
        mock_embedder = MagicMock()
        mock_embedder.is_available.return_value = True
        mock_embedder.model = "nomic-embed-text"
        svc._embedder = mock_embedder

        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.count_docs", return_value=100),
            patch("elle.daemon.manvault.service.count_chunks", return_value=500),
            patch("elle.daemon.manvault.service.count_embeddings", return_value=200),
            patch("elle.daemon.manvault.service.get_meta", return_value=None),
            patch("elle.daemon.manvault.service.get_section_counts", return_value={"1": 80, "8": 20}),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c

            status = svc.get_status()
            assert status.total_docs == 100
            assert status.total_chunks == 500
            assert status.embedded_chunks == 200
            assert status.embedding_model == "nomic-embed-text"


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_get_service_singleton(self):
        import elle.daemon.manvault.service as svc_mod

        old = svc_mod._service
        svc_mod._service = None
        try:
            s1 = svc_mod.get_service()
            s2 = svc_mod.get_service()
            assert s1 is s2
        finally:
            svc_mod._service = old

    def test_get_status_func(self):
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.ensure_schema"),
            patch("elle.daemon.manvault.service.count_docs", return_value=0),
            patch("elle.daemon.manvault.service.count_chunks", return_value=0),
            patch("elle.daemon.manvault.service.count_embeddings", return_value=0),
            patch("elle.daemon.manvault.service.get_meta", return_value=None),
            patch("elle.daemon.manvault.service.get_section_counts", return_value={}),
            patch("elle.daemon.manvault.service.get_embedder") as mock_emb,
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            mock_emb.return_value = MagicMock(is_available=MagicMock(return_value=False))
            from elle.daemon.manvault.service import get_status

            status = get_status()
            assert status.total_docs == 0

    def test_get_status_embedder_exception(self):
        with (
            patch("elle.daemon.manvault.service.get_conn") as mock_conn,
            patch("elle.daemon.manvault.service.ensure_schema"),
            patch("elle.daemon.manvault.service.count_docs", return_value=0),
            patch("elle.daemon.manvault.service.count_chunks", return_value=0),
            patch("elle.daemon.manvault.service.count_embeddings", return_value=0),
            patch("elle.daemon.manvault.service.get_meta", return_value=None),
            patch("elle.daemon.manvault.service.get_section_counts", return_value={}),
            patch("elle.daemon.manvault.service.get_embedder", side_effect=RuntimeError("no")),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            from elle.daemon.manvault.service import get_status

            status = get_status()
            assert status.embedding_model is None

    @pytest.mark.asyncio
    async def test_stop_timeout(self):
        from elle.daemon.manvault.service import ManVaultService

        svc = ManVaultService()

        async def never_ends():
            while True:
                await asyncio.sleep(100)

        svc._task = asyncio.create_task(never_ends())
        svc._stop_requested = True

        # Patch asyncio.wait_for inside the service module to use a very
        # short timeout so the test does not block for the full 5 seconds.
        original_wait_for = asyncio.wait_for

        async def fast_wait_for(fut, *, timeout=None):
            return await original_wait_for(fut, timeout=0.1)

        with patch("asyncio.wait_for", side_effect=fast_wait_for):
            await svc.stop()
        assert svc._task is None
