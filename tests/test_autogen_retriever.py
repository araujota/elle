from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autogen.retriever import (
    CapabilityRetriever,
    CapabilitySearchResult,
    get_retriever,
    reset_retriever,
    search_capabilities,
)

# =========================================================================
# Fixtures
# =========================================================================

_CAPABILITY_ROWS = [
    ("service.restart", "service", "Restart a systemd service", "core", True),
    ("service.status", "service", "Get service status", "core", True),
    ("file.read", "file", "Read file contents", "core", True),
    ("docker.prune", "docker", "Prune unused containers", "third_party", False),
    ("network.diagnose", "network", "Diagnose network issues", "official", True),
]


def _make_rows() -> list[dict]:
    """Build fake dict-rows matching the generated_capabilities schema."""
    rows = []
    for i, (name, domain, desc, trust, approved) in enumerate(_CAPABILITY_ROWS):
        spec = {
            "name": name,
            "domain": domain,
            "description": desc,
            "summary": desc[:50],
            "risk": "low",
            "keywords": [domain, name.split(".")[1]],
        }
        rows.append(
            {
                "id": f"id-{i}",
                "capability_name": name,
                "spec_json": spec,  # JSONB returns dict
                "input_model_code": "",
                "output_model_code": "",
                "capability_class_code": "",
                "source_command": name.split(".")[0],
                "man_page_hash": "hash",
                "generated_at": "2024-01-01T00:00:00+00:00",
                "trust_level": trust,
                "approved": approved,
                "enabled": True,
                "validation_json": None,
                "source_package": None,
                "package_version": None,
                "binary_path": None,
                "search_tsv": None,
            }
        )
    return rows


class _FakeCursor:
    """Minimal cursor stand-in for conn.execute()."""

    def __init__(self, rows: list[dict] | None = None, single: dict | None = None) -> None:
        self._rows = rows or []
        self._single = single

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._single


class _FakeConn:
    """Minimal connection stand-in that returns pre-built rows."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self, sql: str, params: tuple | list | None = None) -> _FakeCursor:
        sql_lower = sql.strip().lower()

        # --- capability_execution_history queries ---
        if "capability_execution_history" in sql_lower:
            if sql_lower.startswith("select"):
                return _FakeCursor(single={"successes": 0, "total": 0})
            return _FakeCursor()

        # --- FTS (tsvector) search ---
        if "search_tsv" in sql_lower and "plainto_tsquery" in sql_lower:
            # Return capabilities whose description contains the query token
            if params:
                query_text = str(params[0]).lower()
                matched = []
                for r in self._rows:
                    spec = r["spec_json"] if isinstance(r["spec_json"], dict) else {}
                    desc = spec.get("description", "").lower()
                    name = r["capability_name"].lower()
                    if query_text in desc or query_text in name:
                        row = dict(r)
                        row["rank"] = 0.5
                        matched.append(row)
                return _FakeCursor(rows=matched)
            return _FakeCursor()

        # --- Semantic (pgvector) search ---
        if "<=>" in sql_lower:
            matched = []
            for r in self._rows:
                row = dict(r)
                row["distance"] = 0.2
                matched.append(row)
            return _FakeCursor(rows=matched)

        # --- Default: exact search or generic SELECT * ---
        filtered = []
        for r in self._rows:
            if "approved = true" in sql_lower.replace("= true", "= true"):
                if not r["approved"]:
                    continue
            filtered.append(r)
        return _FakeCursor(rows=filtered)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN204
        pass


@pytest.fixture()
def fake_rows() -> list[dict]:
    return _make_rows()


@pytest.fixture()
def retriever(fake_rows: list[dict]) -> CapabilityRetriever:
    """Create a retriever with get_conn patched to use fake rows."""
    r = CapabilityRetriever.__new__(CapabilityRetriever)
    r._embedder = None
    # We patch get_conn at usage sites; this fixture just builds the object.
    return r


def _patch_conn(rows: list[dict]):
    """Return a context-manager patch that replaces get_conn with _FakeConn."""
    from contextlib import contextmanager

    @contextmanager
    def _fake_get_conn(schema: str = "public"):  # noqa: ANN202, ARG001
        yield _FakeConn(rows)

    return patch("elle.capabilities.autogen.retriever.get_conn", _fake_get_conn)


# =========================================================================
# CapabilitySearchResult model tests
# =========================================================================


class TestCapabilitySearchResult:
    def test_create_result(self) -> None:
        r = CapabilitySearchResult(
            capability_name="test.cap",
            domain="service",
            description="test",
            risk_level="low",
            match_type="exact",
            score=0.9,
            trust_weight=1.0,
            success_rate=0.8,
            is_approved=True,
            is_enabled=True,
        )
        assert r.capability_name == "test.cap"
        assert r.score == 0.9

    def test_frozen_model(self) -> None:
        r = CapabilitySearchResult(
            capability_name="test",
            domain="file",
            description="desc",
            risk_level="none",
            match_type="lexical",
            score=0.5,
            trust_weight=1.0,
            success_rate=1.0,
            is_approved=True,
            is_enabled=True,
        )
        with pytest.raises(Exception):
            r.score = 0.1  # type: ignore[misc]


# =========================================================================
# CapabilityRetriever init tests
# =========================================================================


class TestRetrieverInit:
    def test_creates_without_args(self) -> None:
        r = CapabilityRetriever()
        assert r._embedder is None

    def test_embedder_lazy_load(self) -> None:
        r = CapabilityRetriever()
        r._embedder = None
        with patch(
            "elle.capabilities.autogen.retriever.CapabilityRetriever.embedder",
            new_callable=lambda: property(lambda self: None),
        ):
            assert r.embedder is None


# =========================================================================
# Exact search tests
# =========================================================================


class TestExactSearch:
    def test_exact_name_match(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("service.restart", search_type="exact")
        assert len(results) >= 1
        assert results[0].capability_name == "service.restart"

    def test_partial_name_match(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("service", search_type="exact")
        names = [r.capability_name for r in results]
        assert "service.restart" in names
        assert "service.status" in names

    def test_keyword_match(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("restart", search_type="exact")
        names = [r.capability_name for r in results]
        assert "service.restart" in names

    def test_description_match(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("Restart a systemd", search_type="exact")
        names = [r.capability_name for r in results]
        assert "service.restart" in names

    def test_domain_filter(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("service", search_type="exact", domain="file")
        names = [r.capability_name for r in results]
        assert "service.restart" not in names

    def test_exclude_unapproved(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("docker", search_type="exact", include_unapproved=False)
        names = [r.capability_name for r in results]
        assert "docker.prune" not in names

    def test_include_unapproved(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("docker", search_type="exact", include_unapproved=True)
        names = [r.capability_name for r in results]
        assert "docker.prune" in names

    def test_no_match(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("zzz_nonexistent_capability", search_type="exact")
        assert len(results) == 0


# =========================================================================
# Hybrid search tests
# =========================================================================


class TestHybridSearch:
    def test_hybrid_returns_results(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("restart service", search_type="hybrid")
        assert len(results) > 0

    def test_k_limits_results(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("service", search_type="exact", k=1)
        assert len(results) <= 1

    def test_hybrid_sets_match_type(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever.search("service.restart", search_type="hybrid")
        if len(results) > 0:
            assert results[0].match_type in ("exact", "lexical", "hybrid")


# =========================================================================
# Semantic search tests
# =========================================================================


class TestSemanticSearch:
    def test_semantic_no_embedder(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            results = retriever._search_semantic("test", None, 5, False)
        assert results == []

    def test_semantic_with_embeddings(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        with _patch_conn(fake_rows):
            results = retriever._search_semantic("restart", None, 5, False)
        assert len(results) >= 1


# =========================================================================
# Helper method tests
# =========================================================================


class TestHelperMethods:
    def test_domain_boost_matching(self, retriever: CapabilityRetriever) -> None:
        boost = retriever._domain_boost("restart the service", "service")
        assert boost == 1.2

    def test_domain_boost_no_match(self, retriever: CapabilityRetriever) -> None:
        boost = retriever._domain_boost("hello world", "service")
        assert boost == 1.0

    def test_domain_boost_docker(self, retriever: CapabilityRetriever) -> None:
        boost = retriever._domain_boost("docker container prune", "docker")
        assert boost == 1.2

    def test_domain_boost_unknown(self, retriever: CapabilityRetriever) -> None:
        boost = retriever._domain_boost("anything", "unknown_domain")
        assert boost == 1.0

    def test_parse_spec_json_valid_string(self, retriever: CapabilityRetriever) -> None:
        result = retriever._parse_spec_json('{"name": "test"}')
        assert result == {"name": "test"}

    def test_parse_spec_json_dict(self, retriever: CapabilityRetriever) -> None:
        result = retriever._parse_spec_json({"name": "test"})
        assert result == {"name": "test"}

    def test_parse_spec_json_invalid(self, retriever: CapabilityRetriever) -> None:
        result = retriever._parse_spec_json("not json")
        assert result == {}

    def test_get_embedding_no_embedder(self, retriever: CapabilityRetriever) -> None:
        retriever._embedder = None
        with patch.object(
            type(retriever),
            "embedder",
            new_callable=lambda: property(lambda self: None),
        ):
            assert retriever._get_embedding("test") is None

    def test_get_embedding_with_embedder(self, retriever: CapabilityRetriever) -> None:
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2]
        retriever._embedder = mock_embedder
        result = retriever._get_embedding("test")
        assert result == [0.1, 0.2]

    def test_get_embedding_error(self, retriever: CapabilityRetriever) -> None:
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("fail")
        retriever._embedder = mock_embedder
        result = retriever._get_embedding("test")
        assert result is None


# =========================================================================
# Success rate tests
# =========================================================================


class TestSuccessRate:
    def test_default_rate(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            rate = retriever._get_success_rate("service.restart")
        expected = retriever.PRIOR_SUCCESSES / retriever.PRIOR_TOTAL
        assert abs(rate - expected) < 0.001

    def test_rate_with_history(self, retriever: CapabilityRetriever) -> None:
        """Simulate 8 successes out of 10 executions via patched connection."""
        from contextlib import contextmanager

        @contextmanager
        def _fake_conn(schema: str = "public"):  # noqa: ANN202, ARG001
            class _Conn:
                def execute(self, sql, params=None):  # noqa: ANN001, ANN201, ARG002
                    return _FakeCursor(single={"successes": 8, "total": 10})

                def commit(self):  # noqa: ANN201
                    pass

                def rollback(self):  # noqa: ANN201
                    pass

            yield _Conn()

        with patch("elle.capabilities.autogen.retriever.get_conn", _fake_conn):
            rate = retriever._get_success_rate("service.restart")

        expected = (8 + retriever.PRIOR_SUCCESSES) / (10 + retriever.PRIOR_TOTAL)
        assert abs(rate - expected) < 0.001


# =========================================================================
# Record execution tests
# =========================================================================


class TestRecordExecution:
    def test_record_success(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            # Should not raise
            retriever.record_execution("service.restart", True, incident_id="inc-1")

    def test_record_failure(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        with _patch_conn(fake_rows):
            retriever.record_execution("service.restart", False)


# =========================================================================
# Update embedding tests
# =========================================================================


class TestUpdateEmbedding:
    def test_update_no_stored(self, retriever: CapabilityRetriever) -> None:
        with patch("elle.capabilities.autogen.retriever.get_store") as mock_store:
            mock_store.return_value.get.return_value = None
            result = retriever.update_embedding("nonexistent")
            assert result is False

    def test_update_no_embedder(self, retriever: CapabilityRetriever) -> None:
        mock_stored = MagicMock()
        mock_stored.spec_json = json.dumps({"description": "test", "domain": "service"})

        with patch("elle.capabilities.autogen.retriever.get_store") as mock_store:
            mock_store.return_value.get.return_value = mock_stored
            retriever._embedder = None
            with patch.object(
                type(retriever),
                "embedder",
                new_callable=lambda: property(lambda self: None),
            ):
                result = retriever.update_embedding("service.restart")
                assert result is False

    def test_update_success(self, retriever: CapabilityRetriever, fake_rows: list[dict]) -> None:
        mock_stored = MagicMock()
        mock_stored.spec_json = json.dumps(
            {
                "description": "Restart a service",
                "summary": "Restart",
                "keywords": ["restart"],
                "domain": "service",
            }
        )

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        with (
            patch("elle.capabilities.autogen.retriever.get_store") as mock_store,
            _patch_conn(fake_rows),
        ):
            mock_store.return_value.get.return_value = mock_stored
            result = retriever.update_embedding("service.restart")
            assert result is True


# =========================================================================
# Module-level function tests
# =========================================================================


class TestModuleFunctions:
    def test_reset_retriever(self) -> None:
        reset_retriever()
        # After reset, get_retriever creates a new one. Just check no crash.

    def test_get_retriever_creates_instance(self) -> None:
        reset_retriever()
        r = get_retriever()
        assert isinstance(r, CapabilityRetriever)

    def test_get_retriever_reuses(self) -> None:
        reset_retriever()
        r1 = get_retriever()
        r2 = get_retriever()
        assert r1 is r2
        reset_retriever()

    def test_search_capabilities_convenience(self, fake_rows: list[dict]) -> None:
        reset_retriever()
        with _patch_conn(fake_rows):
            results = search_capabilities("service", domain=None, k=5, search_type="exact")
        assert len(results) > 0
        reset_retriever()
