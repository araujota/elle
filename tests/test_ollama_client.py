"""Tests for OllamaClient generate, generate_json, embeddings, and lifecycle."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from elle.rag.ollama_client import (
    OllamaClient,
    OllamaConfig,
    OllamaError,
    OllamaResponse,
    OllamaUnavailableError,
    get_client,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_httpx_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    if status_code >= 400:
        request = httpx.Request("POST", "http://localhost:11434/api/generate")
        real_resp = httpx.Response(status_code, request=request, text=text)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=request,
            response=real_resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ============================================================================
# OllamaResponse
# ============================================================================


class TestOllamaResponse:
    """Tests for OllamaResponse model."""

    def test_duration_ms_present(self) -> None:
        resp = OllamaResponse(
            content="hi",
            model="m",
            done=True,
            total_duration_ns=5_000_000,
        )
        assert resp.duration_ms == 5.0

    def test_duration_ms_none(self) -> None:
        resp = OllamaResponse(content="hi", model="m", done=True)
        assert resp.duration_ms is None

    def test_duration_ms_zero(self) -> None:
        resp = OllamaResponse(
            content="", model="m", done=True, total_duration_ns=0
        )
        assert resp.duration_ms == 0.0


# ============================================================================
# OllamaError / OllamaUnavailableError
# ============================================================================


class TestOllamaErrors:
    """Tests for error classes."""

    def test_ollama_error_message(self) -> None:
        err = OllamaError("bad stuff", status_code=500)
        assert err.message == "bad stuff"
        assert err.status_code == 500
        assert str(err) == "bad stuff"

    def test_ollama_error_no_status(self) -> None:
        err = OllamaError("fail")
        assert err.status_code is None

    def test_unavailable_is_ollama_error(self) -> None:
        err = OllamaUnavailableError("down")
        assert isinstance(err, OllamaError)
        assert err.message == "down"


# ============================================================================
# OllamaClient -- lifecycle
# ============================================================================


class TestOllamaClientLifecycle:
    """Tests for OllamaClient init, close, context manager."""

    def test_default_config(self) -> None:
        client = OllamaClient()
        assert client.config.host == "http://localhost:11434"
        client.close()

    def test_custom_config(self) -> None:
        cfg = OllamaConfig(host="http://host:1234", timeout=60.0)
        client = OllamaClient(config=cfg)
        assert client.config.host == "http://host:1234"
        client.close()

    def test_context_manager(self) -> None:
        with OllamaClient() as client:
            assert client is not None

    def test_close_closes_httpx(self) -> None:
        client = OllamaClient()
        client._client = MagicMock()
        client.close()
        client._client.close.assert_called_once()


# ============================================================================
# OllamaClient.is_available()
# ============================================================================


class TestOllamaClientIsAvailable:
    """Tests for is_available()."""

    def test_available_success(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(200, {"models": []})
        client._client = MagicMock()
        client._client.get.return_value = mock_resp

        assert client.is_available() is True
        assert client._available is True

    def test_available_non_200(self) -> None:
        client = OllamaClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        client._client = MagicMock()
        client._client.get.return_value = mock_resp

        assert client.is_available() is False
        assert client._available is False

    def test_available_connection_error(self) -> None:
        client = OllamaClient()
        client._client = MagicMock()
        client._client.get.side_effect = httpx.ConnectError("refused")

        assert client.is_available() is False

    def test_cached_result(self) -> None:
        client = OllamaClient()
        client._available = True
        # Should not make a network call
        assert client.is_available() is True

    def test_force_check(self) -> None:
        client = OllamaClient()
        client._available = True
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        client._client = MagicMock()
        client._client.get.return_value = mock_resp

        assert client.is_available(force_check=True) is True
        client._client.get.assert_called_once()


# ============================================================================
# OllamaClient.list_models()
# ============================================================================


class TestOllamaClientListModels:
    """Tests for list_models()."""

    def test_list_models_success(self) -> None:
        client = OllamaClient()
        data = {"models": [{"name": "m1"}, {"name": "m2"}]}
        mock_resp = _mock_httpx_response(200, data)
        client._client = MagicMock()
        client._client.get.return_value = mock_resp

        models = client.list_models()
        assert models == ["m1", "m2"]

    def test_list_models_empty(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(200, {"models": []})
        client._client = MagicMock()
        client._client.get.return_value = mock_resp

        assert client.list_models() == []

    def test_list_models_connection_error(self) -> None:
        client = OllamaClient()
        client._client = MagicMock()
        client._client.get.side_effect = httpx.ConnectError("refused")

        with pytest.raises(OllamaUnavailableError, match="Cannot connect"):
            client.list_models()


# ============================================================================
# OllamaClient.generate()
# ============================================================================


class TestOllamaClientGenerate:
    """Tests for generate()."""

    def test_generate_basic(self) -> None:
        client = OllamaClient()
        resp_data = {
            "response": "hello world",
            "model": "test",
            "done": True,
            "total_duration": 1_000_000,
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        mock_resp = _mock_httpx_response(200, resp_data)
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        result = client.generate("test", "prompt")
        assert result.content == "hello world"
        assert result.model == "test"
        assert result.done is True
        assert result.total_duration_ns == 1_000_000
        assert result.prompt_eval_count == 10
        assert result.eval_count == 5

    def test_generate_with_system_prompt(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(200, {"response": "ok", "model": "m", "done": True})
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        client.generate("m", "prompt", system="You are helpful.")
        payload = client._client.post.call_args[1]["json"]
        assert payload["system"] == "You are helpful."

    def test_generate_json_mode(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(200, {"response": "{}", "model": "m", "done": True})
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        client.generate("m", "prompt", json_mode=True)
        payload = client._client.post.call_args[1]["json"]
        assert payload["format"] == "json"

    def test_generate_custom_params(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(200, {"response": "ok", "model": "m", "done": True})
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        client.generate(
            "m", "prompt",
            max_tokens=500,
            temperature=0.9,
            keep_alive="1h",
            num_ctx=8192,
        )
        payload = client._client.post.call_args[1]["json"]
        assert payload["options"]["num_predict"] == 500
        assert payload["options"]["temperature"] == 0.9
        assert payload["keep_alive"] == "1h"
        assert payload["options"]["num_ctx"] == 8192

    def test_generate_connect_error(self) -> None:
        client = OllamaClient()
        client._client = MagicMock()
        client._client.post.side_effect = httpx.ConnectError("refused")

        with pytest.raises(OllamaUnavailableError, match="Cannot connect"):
            client.generate("m", "prompt")
        assert client._available is False

    def test_generate_timeout(self) -> None:
        client = OllamaClient()
        client._client = MagicMock()
        client._client.post.side_effect = httpx.ReadTimeout("timeout")

        with pytest.raises(OllamaError, match="timed out"):
            client.generate("m", "prompt")

    def test_generate_http_error(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(500, text="Internal Server Error")
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        with pytest.raises(OllamaError, match="API error"):
            client.generate("m", "prompt")


# ============================================================================
# OllamaClient.generate_json()
# ============================================================================


class TestOllamaClientGenerateJson:
    """Tests for generate_json()."""

    def test_generate_json_success(self) -> None:
        client = OllamaClient()
        json_str = json.dumps({"key": "value"})
        with patch.object(
            client,
            "generate",
            return_value=OllamaResponse(content=json_str, model="m", done=True),
        ):
            result = client.generate_json("m", "prompt")
        assert result == {"key": "value"}

    def test_generate_json_parse_failure_retry_success(self) -> None:
        client = OllamaClient()
        bad_resp = OllamaResponse(content="not json", model="m", done=True)
        good_resp = OllamaResponse(
            content=json.dumps({"ok": True}), model="m", done=True,
        )
        with patch.object(client, "generate", side_effect=[bad_resp, good_resp]):
            result = client.generate_json("m", "prompt")
        assert result == {"ok": True}

    def test_generate_json_parse_failure_retry_fails(self) -> None:
        client = OllamaClient()
        bad_resp = OllamaResponse(content="still bad", model="m", done=True)
        with patch.object(client, "generate", side_effect=[bad_resp, bad_resp]):
            with pytest.raises(OllamaError, match="Failed to parse JSON after retry"):
                client.generate_json("m", "prompt")

    def test_generate_json_no_retry(self) -> None:
        client = OllamaClient()
        bad_resp = OllamaResponse(content="not json", model="m", done=True)
        with patch.object(client, "generate", return_value=bad_resp):
            with pytest.raises(OllamaError, match="Failed to parse JSON response"):
                client.generate_json("m", "prompt", retry_on_parse_error=False)

    def test_generate_json_custom_params(self) -> None:
        client = OllamaClient()
        json_str = json.dumps({"result": 42})
        with patch.object(
            client,
            "generate",
            return_value=OllamaResponse(content=json_str, model="m", done=True),
        ) as mock_gen:
            client.generate_json(
                "m", "prompt",
                system="sys",
                max_tokens=200,
                temperature=0.3,
                keep_alive="10m",
                num_ctx=16384,
            )
        mock_gen.assert_called_once_with(
            "m", "prompt",
            system="sys",
            json_mode=True,
            max_tokens=200,
            temperature=0.3,
            keep_alive="10m",
            num_ctx=16384,
        )


# ============================================================================
# OllamaClient.generate_embedding()
# ============================================================================


class TestOllamaClientEmbedding:
    """Tests for generate_embedding()."""

    def test_embedding_success(self) -> None:
        client = OllamaClient()
        data = {"embedding": [0.1, 0.2, 0.3]}
        mock_resp = _mock_httpx_response(200, data)
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        result = client.generate_embedding("nomic-embed-text", "hello")
        assert result == [0.1, 0.2, 0.3]

    def test_embedding_empty(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(200, {})
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        result = client.generate_embedding("model", "text")
        assert result == []

    def test_embedding_connect_error(self) -> None:
        client = OllamaClient()
        client._client = MagicMock()
        client._client.post.side_effect = httpx.ConnectError("refused")

        with pytest.raises(OllamaUnavailableError):
            client.generate_embedding("model", "text")
        assert client._available is False

    def test_embedding_timeout(self) -> None:
        client = OllamaClient()
        client._client = MagicMock()
        client._client.post.side_effect = httpx.ReadTimeout("timeout")

        with pytest.raises(OllamaError, match="Embedding request timed out"):
            client.generate_embedding("model", "text")

    def test_embedding_http_error(self) -> None:
        client = OllamaClient()
        mock_resp = _mock_httpx_response(404, text="Not Found")
        client._client = MagicMock()
        client._client.post.return_value = mock_resp

        with pytest.raises(OllamaError, match="embedding API error"):
            client.generate_embedding("model", "text")


# ============================================================================
# OllamaClient.generate_embeddings_batch()
# ============================================================================


class TestOllamaClientEmbeddingsBatch:
    """Tests for generate_embeddings_batch()."""

    def test_batch_success(self) -> None:
        client = OllamaClient()
        with patch.object(
            client,
            "generate_embedding",
            side_effect=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
        ):
            result = client.generate_embeddings_batch("model", ["a", "b", "c"])
        assert len(result) == 3
        assert result[0] == [0.1, 0.2]

    def test_batch_empty(self) -> None:
        client = OllamaClient()
        result = client.generate_embeddings_batch("model", [])
        assert result == []


# ============================================================================
# get_client() singleton
# ============================================================================


class TestGetClient:
    """Tests for the module-level get_client singleton."""

    def test_get_client_creates_instance(self) -> None:
        import elle.rag.ollama_client as mod

        original = mod._client
        try:
            mod._client = None
            client = get_client()
            assert isinstance(client, OllamaClient)
            assert mod._client is client
        finally:
            mod._client = original

    def test_get_client_returns_same_instance(self) -> None:
        import elle.rag.ollama_client as mod

        original = mod._client
        try:
            mod._client = None
            c1 = get_client()
            c2 = get_client()
            assert c1 is c2
        finally:
            mod._client = original
