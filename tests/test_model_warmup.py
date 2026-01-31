from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from elle.rag.model_warmup import (
    ModelStatus,
    ModelWarmupService,
    WarmupResult,
    get_warmup_service,
)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_model_status(self):
        ms = ModelStatus(name="qwen", loaded=True, size_bytes=1000)
        assert ms.name == "qwen"
        assert ms.loaded is True

    def test_warmup_result(self):
        wr = WarmupResult(success=True, model="test", message="ok", duration_ms=100.0)
        assert wr.success is True
        assert wr.duration_ms == 100.0


# ---------------------------------------------------------------------------
# ModelWarmupService
# ---------------------------------------------------------------------------


class TestModelWarmupService:
    def test_init(self):
        svc = ModelWarmupService(llm_model="test-model", host="http://localhost:1234")
        assert svc.llm_model == "test-model"
        assert svc.host == "http://localhost:1234"
        assert svc._client is None

    @pytest.mark.asyncio
    async def test_get_client(self):
        svc = ModelWarmupService()
        client = await svc._get_client()
        assert client is not None
        # Second call returns same instance
        client2 = await svc._get_client()
        assert client is client2
        await svc.close()

    @pytest.mark.asyncio
    async def test_close(self):
        svc = ModelWarmupService()
        _ = await svc._get_client()
        await svc.close()
        assert svc._client is None

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with ModelWarmupService() as svc:
            assert svc is not None
        assert svc._client is None

    # -- Availability --

    @pytest.mark.asyncio
    async def test_is_ollama_available_true(self):
        svc = ModelWarmupService()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        svc._client = mock_client
        assert await svc.is_ollama_available() is True

    @pytest.mark.asyncio
    async def test_is_ollama_available_false(self):
        svc = ModelWarmupService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("no"))
        svc._client = mock_client
        assert await svc.is_ollama_available() is False

    @pytest.mark.asyncio
    async def test_list_models(self):
        svc = ModelWarmupService()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "a"}, {"name": "b"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        svc._client = mock_client
        models = await svc.list_models()
        assert models == ["a", "b"]

    @pytest.mark.asyncio
    async def test_list_models_error(self):
        svc = ModelWarmupService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("no"))
        svc._client = mock_client
        models = await svc.list_models()
        assert models == []

    @pytest.mark.asyncio
    async def test_get_running_models(self):
        svc = ModelWarmupService()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "qwen", "size": 1000, "size_vram": 800, "expires_at": "2025-01-01"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        svc._client = mock_client
        models = await svc.get_running_models()
        assert len(models) == 1
        assert models[0].name == "qwen"
        assert models[0].loaded is True

    @pytest.mark.asyncio
    async def test_get_running_models_error(self):
        svc = ModelWarmupService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("no"))
        svc._client = mock_client
        models = await svc.get_running_models()
        assert models == []

    @pytest.mark.asyncio
    async def test_is_model_loaded_true(self):
        svc = ModelWarmupService()
        with patch.object(
            svc,
            "get_running_models",
            return_value=[
                ModelStatus(name="qwen2.5:7b-instruct-q8_0", loaded=True),
            ],
        ):
            assert await svc.is_model_loaded("qwen2.5:7b-instruct-q8_0") is True

    @pytest.mark.asyncio
    async def test_is_model_loaded_false(self):
        svc = ModelWarmupService()
        with patch.object(svc, "get_running_models", return_value=[]):
            assert await svc.is_model_loaded("qwen2.5:7b-instruct-q8_0") is False

    # -- Model Management --

    @pytest.mark.asyncio
    async def test_ensure_model_exists_already_available(self):
        svc = ModelWarmupService()
        with patch.object(svc, "list_models", return_value=["qwen2.5:7b"]):
            assert await svc.ensure_model_exists("qwen2.5:7b") is True

    @pytest.mark.asyncio
    async def test_ensure_model_exists_pull(self):
        svc = ModelWarmupService()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        svc._client = mock_client
        with patch.object(svc, "list_models", return_value=[]):
            assert await svc.ensure_model_exists("new-model") is True

    @pytest.mark.asyncio
    async def test_ensure_model_exists_pull_fails(self):
        svc = ModelWarmupService()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("no"))
        svc._client = mock_client
        with patch.object(svc, "list_models", return_value=[]):
            assert await svc.ensure_model_exists("new-model") is False

    @pytest.mark.asyncio
    async def test_ensure_model_ready(self):
        svc = ModelWarmupService(llm_model="mymodel")
        with patch.object(svc, "ensure_model_exists", return_value=True) as mock_ensure:
            assert await svc.ensure_model_ready() is True
            mock_ensure.assert_called_once_with("mymodel")

    # -- Warmup --

    @pytest.mark.asyncio
    async def test_warm_model_success(self):
        svc = ModelWarmupService()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        svc._client = mock_client

        result = await svc._warm_model("test", "-1", 4096)
        assert result.success is True
        assert result.duration_ms is not None

    @pytest.mark.asyncio
    async def test_warm_model_timeout(self):
        svc = ModelWarmupService()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        svc._client = mock_client

        result = await svc._warm_model("test", "-1", 4096)
        assert result.success is False
        assert result.error == "timeout"

    @pytest.mark.asyncio
    async def test_warm_model_request_error(self):
        svc = ModelWarmupService()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("no"))
        svc._client = mock_client

        result = await svc._warm_model("test", "-1", 4096)
        assert result.success is False
        assert "no" in result.error

    @pytest.mark.asyncio
    async def test_warm_llm(self):
        svc = ModelWarmupService(llm_model="testmodel")
        with patch.object(svc, "_warm_model", return_value=WarmupResult(success=True, model="testmodel", message="ok")):
            result = await svc.warm_llm()
            assert result.success is True
            assert result.model == "testmodel"

    # -- Periodic Warmup --

    @pytest.mark.asyncio
    async def test_start_periodic_warmup(self):
        svc = ModelWarmupService()
        with patch.object(svc, "_periodic_warmup_loop", new_callable=AsyncMock):
            await svc.start_periodic_warmup(interval=1)
            assert svc._warmup_task is not None
            assert svc.is_periodic_warmup_running is True
            await svc.stop_periodic_warmup()
            assert svc._warmup_task is None

    @pytest.mark.asyncio
    async def test_start_periodic_warmup_already_running(self):
        svc = ModelWarmupService()
        with patch.object(svc, "_periodic_warmup_loop", new_callable=AsyncMock):
            await svc.start_periodic_warmup(interval=1)
            # Second call should log warning but not start another
            await svc.start_periodic_warmup(interval=1)
            await svc.stop_periodic_warmup()

    @pytest.mark.asyncio
    async def test_stop_periodic_warmup_not_started(self):
        svc = ModelWarmupService()
        await svc.stop_periodic_warmup()  # should not raise

    def test_is_periodic_warmup_running_false(self):
        svc = ModelWarmupService()
        assert svc.is_periodic_warmup_running is False

    # -- VRAM Detection --

    def test_get_available_vram_nvidia(self):
        svc = ModelWarmupService()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "4096\n"
        with patch("elle.rag.model_warmup.subprocess.run", return_value=mock_result):
            vram = svc.get_available_vram_gb()
            assert vram == pytest.approx(4.0)

    def test_get_available_vram_nvidia_fails(self):
        svc = ModelWarmupService()
        with patch("elle.rag.model_warmup.subprocess.run", side_effect=FileNotFoundError("no nvidia")):
            # Falls through to rocm-smi check
            with patch("elle.rag.model_warmup.subprocess.run", side_effect=FileNotFoundError("no rocm")):
                # Falls through to /proc/meminfo
                svc.get_available_vram_gb()
                # Might be None on macOS
                # Just check it doesn't crash

    def test_get_available_vram_rocm(self):
        svc = ModelWarmupService()

        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FileNotFoundError("no nvidia")
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Total VRAM: 8192 MB"
            return result

        with patch("elle.rag.model_warmup.subprocess.run", side_effect=fake_run):
            vram = svc.get_available_vram_gb()
            assert vram == pytest.approx(8.0)

    def test_get_available_vram_proc_meminfo(self):
        svc = ModelWarmupService()

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("no gpu")

        with (
            patch("elle.rag.model_warmup.subprocess.run", side_effect=fake_run),
            patch("builtins.open", MagicMock()) as mock_open,
        ):
            mock_open.return_value.__enter__ = MagicMock(
                return_value=iter(
                    [
                        "MemTotal:       16000000 kB\n",
                        "MemFree:         8000000 kB\n",
                        "MemAvailable:   12000000 kB\n",
                    ]
                )
            )
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            vram = svc.get_available_vram_gb()
            assert vram is not None
            assert vram > 0

    def test_get_available_vram_all_fail(self):
        svc = ModelWarmupService()

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("no gpu")

        with (
            patch("elle.rag.model_warmup.subprocess.run", side_effect=fake_run),
            patch("builtins.open", side_effect=FileNotFoundError("no proc")),
        ):
            vram = svc.get_available_vram_gb()
            assert vram is None


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_warmup_service(self):
        import elle.rag.model_warmup as mod

        old = mod._warmup_service
        mod._warmup_service = None
        try:
            s1 = get_warmup_service()
            s2 = get_warmup_service()
            assert s1 is s2
        finally:
            mod._warmup_service = old
