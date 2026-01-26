"""Tests for Man Vault embeddings."""

import pytest

from elle.daemon.manvault.embedder import cosine_similarity, euclidean_distance


class TestCosineSimilarity:
    """Tests for cosine similarity computation."""

    def test_identical_vectors(self):
        """Test similarity of identical vectors."""
        v = [1.0, 2.0, 3.0]
        sim = cosine_similarity(v, v)
        assert abs(sim - 1.0) < 0.0001

    def test_orthogonal_vectors(self):
        """Test similarity of orthogonal vectors."""
        v1 = [1.0, 0.0, 0.0]
        v2 = [0.0, 1.0, 0.0]
        sim = cosine_similarity(v1, v2)
        assert abs(sim - 0.0) < 0.0001

    def test_opposite_vectors(self):
        """Test similarity of opposite vectors."""
        v1 = [1.0, 2.0, 3.0]
        v2 = [-1.0, -2.0, -3.0]
        sim = cosine_similarity(v1, v2)
        assert abs(sim - (-1.0)) < 0.0001

    def test_similar_vectors(self):
        """Test similarity of similar vectors."""
        v1 = [1.0, 2.0, 3.0]
        v2 = [1.1, 2.1, 3.1]
        sim = cosine_similarity(v1, v2)
        assert sim > 0.99  # Very similar

    def test_different_lengths_raises(self):
        """Test that different length vectors raise error."""
        v1 = [1.0, 2.0]
        v2 = [1.0, 2.0, 3.0]
        with pytest.raises(ValueError):
            cosine_similarity(v1, v2)

    def test_zero_vector(self):
        """Test handling of zero vector."""
        v1 = [0.0, 0.0, 0.0]
        v2 = [1.0, 2.0, 3.0]
        sim = cosine_similarity(v1, v2)
        assert sim == 0.0


class TestEuclideanDistance:
    """Tests for Euclidean distance computation."""

    def test_identical_vectors(self):
        """Test distance of identical vectors."""
        v = [1.0, 2.0, 3.0]
        dist = euclidean_distance(v, v)
        assert abs(dist - 0.0) < 0.0001

    def test_unit_distance(self):
        """Test unit distance."""
        v1 = [0.0, 0.0]
        v2 = [1.0, 0.0]
        dist = euclidean_distance(v1, v2)
        assert abs(dist - 1.0) < 0.0001

    def test_pythagorean(self):
        """Test 3-4-5 triangle."""
        v1 = [0.0, 0.0]
        v2 = [3.0, 4.0]
        dist = euclidean_distance(v1, v2)
        assert abs(dist - 5.0) < 0.0001

    def test_different_lengths_raises(self):
        """Test that different length vectors raise error."""
        v1 = [1.0, 2.0]
        v2 = [1.0, 2.0, 3.0]
        with pytest.raises(ValueError):
            euclidean_distance(v1, v2)


class TestEmbedderAvailability:
    """Tests for embedder availability checking."""

    def test_embedder_creation(self):
        """Test that embedder can be created."""
        from elle.daemon.manvault.embedder import ManVaultEmbedder

        embedder = ManVaultEmbedder()
        assert embedder is not None

    def test_get_embedder_singleton(self):
        """Test that get_embedder returns singleton."""
        from elle.daemon.manvault.embedder import get_embedder

        e1 = get_embedder()
        e2 = get_embedder()
        assert e1 is e2


class TestEmbedderModel:
    """Tests for embedder model selection."""

    def test_default_model(self):
        """Test default embedding model."""
        from elle.daemon.manvault.embedder import DEFAULT_EMBEDDING_MODEL

        assert DEFAULT_EMBEDDING_MODEL == "nomic-embed-text"

    def test_fallback_models(self):
        """Test fallback models list."""
        from elle.daemon.manvault.embedder import FALLBACK_MODELS

        assert len(FALLBACK_MODELS) > 0
        assert "all-minilm" in FALLBACK_MODELS
