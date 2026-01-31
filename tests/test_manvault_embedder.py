from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.manvault.embedder import ManVaultEmbedder, get_embedder
from elle.daemon.manvault.models import ManChunk

# ---------------------------------------------------------------------------
# Test-only utility functions
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        raise ValueError("Vectors must have the same length")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """Compute Euclidean distance between two vectors."""
    if len(a) != len(b):
        raise ValueError("Vectors must have the same length")
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_norm(self):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert cosine_similarity(a, b) == 0.0

    def test_different_dimensions(self):
        with pytest.raises(ValueError):
            cosine_similarity([1.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# euclidean_distance
# ---------------------------------------------------------------------------


class TestEuclideanDistance:
    def test_same_point(self):
        v = [1.0, 2.0, 3.0]
        assert euclidean_distance(v, v) == pytest.approx(0.0)

    def test_unit_distance(self):
        a = [0.0, 0.0]
        b = [3.0, 4.0]
        assert euclidean_distance(a, b) == pytest.approx(5.0)

    def test_different_dimensions(self):
        with pytest.raises(ValueError):
            euclidean_distance([1.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# ManVaultEmbedder
# ---------------------------------------------------------------------------


class TestManVaultEmbedder:
    def test_init_defaults(self):
        e = ManVaultEmbedder()
        assert e._client is None
        assert e._model is None

    def test_init_with_client(self):
        mock_client = MagicMock()
        e = ManVaultEmbedder(client=mock_client, model="test-model")
        assert e._model == "test-model"
        assert e.model == "test-model"

    def test_client_lazy(self):
        with patch("elle.daemon.manvault.embedder.get_client") as mock_get:
            mock_c = MagicMock()
            mock_get.return_value = mock_c
            e = ManVaultEmbedder()
            assert e.client is mock_c

    def test_model_configured(self):
        e = ManVaultEmbedder(model="my-embed")
        assert e.model == "my-embed"

    def test_model_detected_cached(self):
        e = ManVaultEmbedder()
        e._detected_model = "cached"
        assert e.model == "cached"

    def test_model_auto_detect(self):
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.list_models.return_value = ["nomic-embed-text:latest"]
        e = ManVaultEmbedder(client=mock_client)
        assert e.model == "nomic-embed-text"

    def test_model_auto_detect_fallback(self):
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.list_models.return_value = ["all-minilm:latest"]
        e = ManVaultEmbedder(client=mock_client)
        assert e.model == "all-minilm"

    def test_model_auto_detect_none_available(self):
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.list_models.return_value = ["unrelated"]
        e = ManVaultEmbedder(client=mock_client)
        assert e.model == "nomic-embed-text"

    def test_model_auto_detect_unavailable(self):
        mock_client = MagicMock()
        mock_client.is_available.return_value = False
        e = ManVaultEmbedder(client=mock_client)
        assert e.model == "nomic-embed-text"

    def test_is_available(self):
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        e = ManVaultEmbedder(client=mock_client)
        assert e.is_available() is True

    def test_embed_text(self):
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = [0.1, 0.2, 0.3]
        e = ManVaultEmbedder(client=mock_client, model="m")
        result = e.embed_text("hello")
        assert result == [0.1, 0.2, 0.3]

    def test_embed_texts_batch(self):
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = [0.1]
        e = ManVaultEmbedder(client=mock_client, model="m")
        results = e.embed_texts_batch(["a", "b", "c"])
        assert len(results) == 3

    def test_embed_texts_batch_with_progress(self):
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = [0.1]
        e = ManVaultEmbedder(client=mock_client, model="m")
        calls = []
        e.embed_texts_batch(
            ["a"] * 5,
            batch_size=2,
            progress_callback=lambda c, t: calls.append((c, t)),
        )
        assert len(calls) >= 1

    def test_embed_chunk_no_id(self):
        e = ManVaultEmbedder(model="m")
        chunk = ManChunk(id=None, doc_id=1, chunk_index=0, text="hello")
        result = e.embed_chunk(chunk)
        assert result is False

    def test_embed_chunk_already_embedded(self):
        mock_client = MagicMock()
        e = ManVaultEmbedder(client=mock_client, model="m")
        chunk = ManChunk(id=42, doc_id=1, chunk_index=0, text="hello")
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_embedding", return_value=[0.1]),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            result = e.embed_chunk(chunk)
            assert result is True

    def test_embed_chunk_success(self):
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = [0.1, 0.2]
        e = ManVaultEmbedder(client=mock_client, model="m")
        chunk = ManChunk(id=42, doc_id=1, chunk_index=0, text="hello")
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_embedding", return_value=None),
            patch("elle.daemon.manvault.embedder.upsert_embedding"),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            result = e.embed_chunk(chunk)
            assert result is True

    def test_embed_chunk_error(self):
        mock_client = MagicMock()
        from elle.rag.ollama_client import OllamaError

        mock_client.generate_embedding.side_effect = OllamaError("fail")
        e = ManVaultEmbedder(client=mock_client, model="m")
        chunk = ManChunk(id=42, doc_id=1, chunk_index=0, text="hello")
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_embedding", return_value=None),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            result = e.embed_chunk(chunk)
            assert result is False

    def test_embed_document_no_chunks(self):
        mock_client = MagicMock()
        e = ManVaultEmbedder(client=mock_client, model="m")
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_chunks_for_doc", return_value=[]),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            count = e.embed_document(1)
            assert count == 0

    def test_embed_document_with_chunks(self):
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = [0.1]
        e = ManVaultEmbedder(client=mock_client, model="m")
        chunks = [
            ManChunk(id=1, doc_id=1, chunk_index=0, text="hello"),
            ManChunk(id=2, doc_id=1, chunk_index=1, text="world"),
            ManChunk(id=None, doc_id=1, chunk_index=2, text="skip"),
        ]
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_chunks_for_doc", return_value=chunks),
            patch("elle.daemon.manvault.embedder.get_embedding", return_value=None),
            patch("elle.daemon.manvault.embedder.upsert_embeddings_batch"),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            count = e.embed_document(1)
            assert count == 2

    def test_embed_document_already_embedded(self):
        mock_client = MagicMock()
        e = ManVaultEmbedder(client=mock_client, model="m")
        chunks = [ManChunk(id=1, doc_id=1, chunk_index=0, text="hello")]
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_chunks_for_doc", return_value=chunks),
            patch("elle.daemon.manvault.embedder.get_embedding", return_value=[0.1]),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            count = e.embed_document(1)
            assert count == 1

    def test_embed_all_pending_empty(self):
        mock_client = MagicMock()
        e = ManVaultEmbedder(client=mock_client, model="m")
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_chunks_without_embeddings", return_value=[]),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            count = e.embed_all_pending()
            assert count == 0

    def test_embed_all_pending_with_limit(self):
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = [0.1]
        e = ManVaultEmbedder(client=mock_client, model="m")

        call_count = [0]

        def fake_get_chunks(conn, limit):
            call_count[0] += 1
            if call_count[0] == 1:
                return [ManChunk(id=1, doc_id=1, chunk_index=0, text="a")]
            return []

        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_chunks_without_embeddings", side_effect=fake_get_chunks),
            patch("elle.daemon.manvault.embedder.upsert_embeddings_batch"),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            count = e.embed_all_pending(limit=1)
            assert count == 1

    def test_embed_all_pending_with_progress(self):
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = [0.1]
        e = ManVaultEmbedder(client=mock_client, model="m")

        call_count = [0]

        def fake_get_chunks(conn, limit):
            call_count[0] += 1
            if call_count[0] == 1:
                return [ManChunk(id=1, doc_id=1, chunk_index=0, text="a")]
            return []

        progress_calls = []
        with (
            patch("elle.daemon.manvault.embedder.get_conn") as mock_conn,

            patch("elle.daemon.manvault.embedder.get_chunks_without_embeddings", side_effect=fake_get_chunks),
            patch("elle.daemon.manvault.embedder.upsert_embeddings_batch"),
        ):
            mock_c = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_c
            count = e.embed_all_pending(
                limit=5,
                progress_callback=lambda c, t: progress_calls.append((c, t)),
            )
            assert count == 1
            assert len(progress_calls) == 1


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------


class TestGetEmbedder:
    def test_singleton(self):
        import elle.daemon.manvault.embedder as mod

        old = mod._embedder
        mod._embedder = None
        try:
            e1 = get_embedder()
            e2 = get_embedder()
            assert e1 is e2
        finally:
            mod._embedder = old
