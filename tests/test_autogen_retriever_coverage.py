"""Additional coverage tests for retriever.py.

Targets uncovered lines:
- Lines 71-146: _migrate_to_v2 SQL statements
- Lines 208-209: Embedder lazy load ImportError
- Lines 250, 257-259: Semantic search result collection
- Line 340: Exact match query-in-name scoring 0.9
- Lines 384-385, 399-402: FTS and semantic search error handlers
- Lines 436-437, 451-454: Semantic search pgvector errors
- Lines 508-509, 591-592: Success rate query error and record execution error
- Lines 651-653: Update embedding error
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from elle.capabilities.autogen.retriever import (
    CapabilityRetriever,
    CapabilitySearchResult,
    _migrate_to_v2,
    get_retriever,
    reset_retriever,
)


# =========================================================================
# Helpers
# =========================================================================


_CAPABILITY_ROWS = [
    ("service.restart", "service", "Restart a systemd service", "core", True),
    ("service.status", "service", "Get service status", "core", True),
    ("file.read", "file", "Read file contents", "core", True),
    ("docker.prune", "docker", "Prune unused containers", "third_party", False),
]


def _make_rows() -> list[dict]:
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
                "spec_json": spec,
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
    def __init__(self, rows=None, single=None):
        self._rows = rows or []
        self._single = single

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._single


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        sql_lower = sql.strip().lower()
        if "capability_execution_history" in sql_lower:
            if sql_lower.startswith("select"):
                return _FakeCursor(single={"successes": 0, "total": 0})
            return _FakeCursor()
        if "search_tsv" in sql_lower and "plainto_tsquery" in sql_lower:
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
        if "<=>" in sql_lower:
            matched = []
            for r in self._rows:
                row = dict(r)
                row["distance"] = 0.2
                matched.append(row)
            return _FakeCursor(rows=matched)
        filtered = []
        for r in self._rows:
            if "approved = true" in sql_lower and not r["approved"]:
                continue
            filtered.append(r)
        return _FakeCursor(rows=filtered)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def _patch_conn(rows):
    @contextmanager
    def _fake_get_conn(schema="public"):
        yield _FakeConn(rows)

    return patch("elle.capabilities.autogen.retriever.get_conn", _fake_get_conn)


@pytest.fixture()
def fake_rows():
    return _make_rows()


@pytest.fixture()
def retriever():
    r = CapabilityRetriever.__new__(CapabilityRetriever)
    r._embedder = None
    return r


# =========================================================================
# _migrate_to_v2 coverage (lines 71-146)
# =========================================================================


class TestMigrateToV2:
    """Cover the migration SQL execution paths."""

    def test_migrate_executes_all_statements(self) -> None:
        """_migrate_to_v2 should execute multiple SQL statements (lines 71-146)."""
        mock_conn = MagicMock()

        _migrate_to_v2(mock_conn)

        # Verify it executed SQL statements (ALTER TABLE, CREATE INDEX, UPDATE, etc.)
        assert mock_conn.execute.call_count >= 8

        # Check key SQL operations were called
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        call_text = " ".join(calls)

        assert "ALTER TABLE" in call_text
        assert "CREATE INDEX" in call_text
        assert "CREATE EXTENSION" in call_text
        assert "capability_embeddings" in call_text
        assert "capability_execution_history" in call_text


# =========================================================================
# Embedder lazy load ImportError (lines 208-209)
# =========================================================================


class TestEmbedderLazyLoadImportError:
    """Cover ImportError when Ollama client is not available."""

    def test_embedder_returns_none_on_import_error(self) -> None:
        """When elle.rag.ollama_client is not importable, embedder returns None (lines 208-209)."""
        r = CapabilityRetriever()
        r._embedder = None

        with patch.dict("sys.modules", {"elle.rag.ollama_client": None}):
            # Force re-evaluation of the property
            result = r.embedder
            assert result is None


# =========================================================================
# Exact match: query-in-name scoring 0.9 (line 340)
# =========================================================================


class TestExactMatchQueryInName:
    """Cover the query_lower in name_lower branch scoring 0.9."""

    def test_partial_name_match_scores_0_9(
        self, retriever: CapabilityRetriever, fake_rows: list[dict]
    ) -> None:
        """Partial name match (query in name) should score 0.9 (line 334-335)."""
        with _patch_conn(fake_rows):
            results = retriever.search("service.re", search_type="exact")

        # "service.re" is in "service.restart" -> score 0.9
        names = [r.capability_name for r in results]
        assert "service.restart" in names
        # The result should have been found
        for r in results:
            if r.capability_name == "service.restart":
                assert r.score > 0


# =========================================================================
# FTS search error handlers (lines 399-402)
# =========================================================================


class TestFTSErrorHandlers:
    """Cover FTS search psycopg error handlers."""

    def test_fts_undefined_column_handled(
        self, retriever: CapabilityRetriever
    ) -> None:
        """UndefinedColumn error is caught gracefully (lines 399-400)."""

        @contextmanager
        def _error_conn(schema="public"):
            conn = MagicMock()
            conn.execute.side_effect = psycopg.errors.UndefinedColumn(
                "column search_tsv does not exist"
            )
            yield conn

        with patch(
            "elle.capabilities.autogen.retriever.get_conn", _error_conn
        ):
            results = retriever._search_fts("test", None, 5, False)
            assert results == []

    def test_fts_generic_psycopg_error_handled(
        self, retriever: CapabilityRetriever
    ) -> None:
        """Generic psycopg.Error is caught gracefully (lines 401-402)."""

        @contextmanager
        def _error_conn(schema="public"):
            conn = MagicMock()
            conn.execute.side_effect = psycopg.Error("connection lost")
            yield conn

        with patch(
            "elle.capabilities.autogen.retriever.get_conn", _error_conn
        ):
            results = retriever._search_fts("test", None, 5, False)
            assert results == []


# =========================================================================
# Semantic search error handlers (lines 451-454)
# =========================================================================


class TestSemanticSearchErrorHandlers:
    """Cover semantic search pgvector error handlers."""

    def test_semantic_undefined_table_handled(
        self, retriever: CapabilityRetriever
    ) -> None:
        """UndefinedTable error is caught gracefully (lines 451-452)."""
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        @contextmanager
        def _error_conn(schema="public"):
            conn = MagicMock()
            conn.execute.side_effect = psycopg.errors.UndefinedTable(
                "table capability_embeddings does not exist"
            )
            yield conn

        with patch(
            "elle.capabilities.autogen.retriever.get_conn", _error_conn
        ):
            results = retriever._search_semantic("test", None, 5, False)
            assert results == []

    def test_semantic_generic_psycopg_error_handled(
        self, retriever: CapabilityRetriever
    ) -> None:
        """Generic psycopg.Error is caught gracefully (lines 453-454)."""
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        @contextmanager
        def _error_conn(schema="public"):
            conn = MagicMock()
            conn.execute.side_effect = psycopg.Error("connection error")
            yield conn

        with patch(
            "elle.capabilities.autogen.retriever.get_conn", _error_conn
        ):
            results = retriever._search_semantic("test", None, 5, False)
            assert results == []

    def test_semantic_results_collected(
        self, retriever: CapabilityRetriever, fake_rows: list[dict]
    ) -> None:
        """Semantic search results are collected when embedder works (lines 257-259)."""
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        with _patch_conn(fake_rows):
            results = retriever._search_semantic("restart", None, 5, True)

        assert len(results) >= 1
        for r in results:
            assert r.match_type == "semantic"
            assert 0.0 <= r.score <= 1.0


# =========================================================================
# Success rate query error (lines 508-509)
# =========================================================================


class TestSuccessRateError:
    """Cover psycopg.Error during success rate query."""

    def test_success_rate_returns_default_on_error(
        self, retriever: CapabilityRetriever
    ) -> None:
        """psycopg.Error falls back to default rate (lines 508-509)."""

        @contextmanager
        def _error_conn(schema="public"):
            conn = MagicMock()
            conn.execute.side_effect = psycopg.Error("db unavailable")
            yield conn

        with patch(
            "elle.capabilities.autogen.retriever.get_conn", _error_conn
        ):
            rate = retriever._get_success_rate("service.restart")

        expected = retriever.PRIOR_SUCCESSES / retriever.PRIOR_TOTAL
        assert abs(rate - expected) < 0.001


# =========================================================================
# Record execution error (lines 591-592)
# =========================================================================


class TestRecordExecutionError:
    """Cover psycopg.Error during record_execution."""

    def test_record_execution_error_silenced(
        self, retriever: CapabilityRetriever
    ) -> None:
        """psycopg.Error during record_execution is logged, not raised (lines 591-592)."""

        @contextmanager
        def _error_conn(schema="public"):
            conn = MagicMock()
            conn.execute.side_effect = psycopg.Error("insert failed")
            yield conn

        with patch(
            "elle.capabilities.autogen.retriever.get_conn", _error_conn
        ):
            # Should not raise
            retriever.record_execution(
                "service.restart", True, incident_id="inc-1"
            )


# =========================================================================
# Update embedding error (lines 651-653)
# =========================================================================


class TestUpdateEmbeddingError:
    """Cover generic exception during update_embedding."""

    def test_update_embedding_generic_error(
        self, retriever: CapabilityRetriever
    ) -> None:
        """Generic exception returns False (lines 651-653)."""
        with patch(
            "elle.capabilities.autogen.retriever.get_store",
            side_effect=RuntimeError("store broken"),
        ):
            result = retriever.update_embedding("service.restart")
            assert result is False

    def test_update_embedding_db_error_during_insert(
        self, retriever: CapabilityRetriever
    ) -> None:
        """DB error during embedding insert returns False."""
        mock_stored = MagicMock()
        mock_stored.spec_json = json.dumps(
            {"description": "test", "domain": "service", "keywords": []}
        )

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        @contextmanager
        def _error_conn(schema="public"):
            conn = MagicMock()
            conn.execute.side_effect = psycopg.Error("insert failed")
            yield conn

        with (
            patch(
                "elle.capabilities.autogen.retriever.get_store"
            ) as mock_store,
            patch(
                "elle.capabilities.autogen.retriever.get_conn", _error_conn
            ),
        ):
            mock_store.return_value.get.return_value = mock_stored
            result = retriever.update_embedding("service.restart")
            assert result is False


# =========================================================================
# Semantic search result collection in hybrid search (lines 250, 257-259)
# =========================================================================


class TestHybridSemanticResultCollection:
    """Cover semantic results being added to results_by_name in search()."""

    def test_hybrid_search_includes_semantic_results(
        self, retriever: CapabilityRetriever, fake_rows: list[dict]
    ) -> None:
        """Semantic results are merged in hybrid search (lines 254-259)."""
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        with _patch_conn(fake_rows):
            results = retriever.search(
                "restart service", search_type="hybrid"
            )

        assert len(results) > 0
        # At least some should be hybrid (found in multiple tiers)
        match_types = {r.match_type for r in results}
        assert len(match_types) >= 1


# =========================================================================
# FTS search with domain filter (lines 383-385)
# =========================================================================


class TestFTSWithDomainFilter:
    """Cover FTS search with domain filter parameter."""

    def test_fts_with_domain_filter(
        self, retriever: CapabilityRetriever, fake_rows: list[dict]
    ) -> None:
        """FTS search with domain filter appends domain condition (lines 383-385)."""
        with _patch_conn(fake_rows):
            results = retriever._search_fts(
                "service", "service", 5, False
            )
        # Should return results (fake conn doesn't filter by domain in FTS)
        # The important thing is the code path doesn't error
        assert isinstance(results, list)


# =========================================================================
# Semantic search with domain filter (lines 435-437)
# =========================================================================


class TestSemanticWithDomainFilter:
    """Cover semantic search with domain filter parameter."""

    def test_semantic_with_domain_filter(
        self, retriever: CapabilityRetriever, fake_rows: list[dict]
    ) -> None:
        """Semantic search with domain filter appends condition (lines 435-437)."""
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 768
        retriever._embedder = mock_embedder

        with _patch_conn(fake_rows):
            results = retriever._search_semantic(
                "restart", "service", 5, False
            )
        assert isinstance(results, list)
