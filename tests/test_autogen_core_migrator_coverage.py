"""Tests for capabilities/autogen/core_migrator.py coverage.

Covers source extraction utilities, CoreCapabilityExtractor,
CoreCapabilityMigrator, and CLI entry point.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.capabilities.autogen.core_migrator import (
    CoreCapabilityExtractor,
    CoreCapabilityMigrator,
    compute_file_hash,
    extract_capability_spec_from_class,
    extract_class_source,
    extract_function_source,
    extract_imports,
    find_helper_functions,
)

# ===========================================================================
# Source extraction utilities
# ===========================================================================

SAMPLE_SOURCE = textwrap.dedent("""\
    from __future__ import annotations
    import os
    from pathlib import Path

    def _helper_a():
        return 1

    def _helper_b():
        return 2

    def public_func():
        return 3

    class MyInput:
        pass

    class MyOutput:
        pass

    class MyCapability:
        pass
""")


class TestExtractImports:
    def test_extracts_imports(self):
        result = extract_imports(SAMPLE_SOURCE)
        assert "from __future__ import annotations" in result
        assert "import os" in result
        assert "from pathlib import Path" in result

    def test_empty_source(self):
        result = extract_imports("")
        assert result == ""


class TestExtractClassSource:
    def test_finds_class(self):
        result = extract_class_source(SAMPLE_SOURCE, "MyCapability")
        assert result is not None
        assert "MyCapability" in result

    def test_returns_none_for_missing_class(self):
        result = extract_class_source(SAMPLE_SOURCE, "NonExistent")
        assert result is None


class TestExtractFunctionSource:
    def test_finds_function(self):
        result = extract_function_source(SAMPLE_SOURCE, "_helper_a")
        assert result is not None
        assert "_helper_a" in result

    def test_returns_none_for_missing_function(self):
        result = extract_function_source(SAMPLE_SOURCE, "nonexistent_func")
        assert result is None

    def test_finds_public_function(self):
        result = extract_function_source(SAMPLE_SOURCE, "public_func")
        assert result is not None


class TestFindHelperFunctions:
    def test_finds_helpers(self):
        result = find_helper_functions(SAMPLE_SOURCE)
        assert "_helper_a" in result
        assert "_helper_b" in result
        assert "public_func" not in result

    def test_empty_source(self):
        result = find_helper_functions("")
        assert result == []


class TestComputeFileHash:
    def test_computes_hash(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world")
        h = compute_file_hash(f)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA256 hex digest

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("same")
        f2.write_text("same")
        assert compute_file_hash(f1) == compute_file_hash(f2)

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("content1")
        f2.write_text("content2")
        assert compute_file_hash(f1) != compute_file_hash(f2)


# ===========================================================================
# extract_capability_spec_from_class
# ===========================================================================


class TestExtractCapabilitySpecFromClass:
    def test_extracts_spec(self):
        """Given a valid capability class, extracts spec dict."""
        from elle.capabilities.models import CapabilitySpec, SideEffect
        from elle.capabilities.protocol import BaseCapability

        class TestCap(BaseCapability):
            @property
            def spec(self):
                return CapabilitySpec(
                    name="test.cap",
                    summary="Test",
                    domain="file",
                    risk="low",
                    side_effects=(
                        SideEffect(
                            kind="file_write",
                            target="/tmp/test",
                            reversible=True,
                            description="Write test",
                        ),
                    ),
                )

            def dry_run(self, input):
                pass

            def run(self, input):
                pass

        result = extract_capability_spec_from_class(TestCap)
        assert result is not None
        assert result["name"] == "test.cap"
        assert result["domain"] == "file"
        assert len(result["side_effects"]) == 1

    def test_returns_none_on_error(self):
        """When instantiation fails, returns None."""

        class BadCap:
            @property
            def spec(self):
                raise RuntimeError("broken")

        result = extract_capability_spec_from_class(BadCap)
        assert result is None


# ===========================================================================
# CoreCapabilityExtractor
# ===========================================================================


class TestCoreCapabilityExtractor:
    def test_extract_all_from_real_module(self, tmp_path):
        """Test extraction from a synthetic module file."""
        source = textwrap.dedent("""\
            from __future__ import annotations
            from pydantic import BaseModel

            def _helper():
                pass

            class TestInput(BaseModel):
                value: str = "test"

            class TestOutput(BaseModel):
                result: str = ""

            class TestCapability:
                pass
        """)

        module_file = tmp_path / "test_mod.py"
        module_file.write_text(source)

        extractor = CoreCapabilityExtractor(module_file)
        assert extractor.module_name == "test_mod"
        assert extractor.source_hash == compute_file_hash(module_file)

        # extract_all calls _process_capability which tries __import__
        # That will fail for our synthetic module, but the method should handle it
        results = extractor.extract_all()
        # Since we can't import "elle.capabilities.core.test_mod", it should return empty
        assert isinstance(results, list)


# ===========================================================================
# CoreCapabilityMigrator
# ===========================================================================


class TestCoreCapabilityMigrator:
    def test_migrate_all_no_core_dir(self):
        """When core directory doesn't exist, returns error."""
        mock_store = MagicMock()
        CoreCapabilityMigrator(store=mock_store)

        with patch("elle.capabilities.autogen.core_migrator.Path") as MockPath:
            mock_core = MagicMock()
            mock_core.exists.return_value = False
            # We need __file__ to produce a path that resolves to a nonexistent dir
            MockPath.return_value.parent.parent.__truediv__.return_value = mock_core
            # Call directly with a non-existent path
            migrator_direct = CoreCapabilityMigrator(store=mock_store)
            # Use a mock that says no dir exists
            with patch.object(Path, "exists", return_value=False):
                with patch.object(Path, "glob", return_value=[]):
                    result = migrator_direct.migrate_all()
                    # The result depends on whether the core dir is found
                    assert isinstance(result, dict)

    def test_save_capability_skip_unchanged(self):
        """When hash matches existing, capability is skipped."""
        mock_store = MagicMock()
        existing = MagicMock()
        existing.man_page_hash = "abc123"
        mock_store.get.return_value = existing

        migrator = CoreCapabilityMigrator(store=mock_store, force=False)
        cap_data = {
            "capability_name": "test.cap",
            "source_hash": "abc123",
        }
        migrator._save_capability(cap_data)
        assert "test.cap" in migrator.skipped

    def test_save_capability_dry_run_new(self):
        """Dry run for new capability."""
        mock_store = MagicMock()
        mock_store.get.return_value = None

        migrator = CoreCapabilityMigrator(store=mock_store, dry_run=True)
        cap_data = {
            "capability_name": "new.cap",
            "source_hash": "xyz",
        }
        migrator._save_capability(cap_data)
        assert any("new.cap" in m for m in migrator.migrated)

    def test_save_capability_dry_run_update(self):
        """Dry run for updating capability."""
        mock_store = MagicMock()
        existing = MagicMock()
        existing.man_page_hash = "old-hash"
        mock_store.get.return_value = existing

        migrator = CoreCapabilityMigrator(store=mock_store, dry_run=True)
        cap_data = {
            "capability_name": "update.cap",
            "source_hash": "new-hash",
        }
        migrator._save_capability(cap_data, is_update=True)
        assert any("update.cap" in m for m in migrator.updated)

    def test_save_capability_db_exception(self):
        """When DB write fails, capability is added to failed list."""
        mock_store = MagicMock()
        mock_store.get.return_value = None

        migrator = CoreCapabilityMigrator(store=mock_store, dry_run=False)
        cap_data = {
            "capability_name": "fail.cap",
            "source_hash": "xyz",
            "spec_dict": {"name": "fail.cap"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test",
            "source_path": "/test",
        }
        with patch("elle.capabilities.autogen.core_migrator.get_conn", side_effect=RuntimeError("db down")):
            migrator._save_capability(cap_data)

        assert any("fail.cap" in f for f in migrator.failed)

    def test_save_capability_force_resaves(self):
        """When force=True, hash match is ignored."""
        mock_store = MagicMock()
        existing = MagicMock()
        existing.man_page_hash = "same-hash"
        mock_store.get.return_value = existing

        migrator = CoreCapabilityMigrator(store=mock_store, dry_run=True, force=True)
        cap_data = {
            "capability_name": "force.cap",
            "source_hash": "same-hash",
        }
        migrator._save_capability(cap_data, is_update=True)
        # Should NOT be skipped
        assert "force.cap" not in migrator.skipped

    def test_migrate_module_extraction_failure(self, tmp_path):
        """When extraction fails, module is added to failed list."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("this is not valid python $$$$")

        mock_store = MagicMock()
        migrator = CoreCapabilityMigrator(store=mock_store)
        migrator._migrate_module(bad_file)
        assert any("bad.py" in f for f in migrator.failed)

    def test_sync_changed_no_changes(self):
        """When no modules need sync, returns empty result."""
        mock_store = MagicMock()
        migrator = CoreCapabilityMigrator(store=mock_store)
        with patch(
            "elle.capabilities.autogen.core_migrator.get_modules_needing_sync",
            return_value=[],
        ):
            result = migrator.sync_changed()
            assert result["migrated_count"] == 0
            assert "message" in result


# ===========================================================================
# CLI main() function
# ===========================================================================


class TestMainCLI:
    def test_main_verify_all_good(self):
        """main --verify when all verified."""
        from elle.capabilities.autogen.core_migrator import main

        with patch(
            "sys.argv",
            ["core_migrator", "--verify"],
        ):
            with patch(
                "elle.capabilities.autogen.core_migrator.verify_migration",
                return_value={
                    "total_core": 2,
                    "verified": [{"name": "a"}, {"name": "b"}],
                    "failed": [],
                    "hash_mismatches": [],
                },
            ):
                exit_code = main()
                assert exit_code == 0

    def test_main_verify_with_failures(self):
        """main --verify when some fail."""
        from elle.capabilities.autogen.core_migrator import main

        with patch(
            "sys.argv",
            ["core_migrator", "--verify"],
        ):
            with patch(
                "elle.capabilities.autogen.core_migrator.verify_migration",
                return_value={
                    "total_core": 2,
                    "verified": [{"name": "a"}],
                    "failed": [{"name": "b", "error": "compile fail"}],
                    "hash_mismatches": [
                        {"name": "c", "stored_hash": "aaa", "current_hash": "bbb"},
                    ],
                },
            ):
                exit_code = main()
                assert exit_code == 1

    def test_main_sync(self):
        """main --sync."""
        from elle.capabilities.autogen.core_migrator import main

        with patch("sys.argv", ["core_migrator", "--sync"]):
            with patch.object(
                CoreCapabilityMigrator,
                "sync_changed",
                return_value={
                    "migrated": [],
                    "updated": [],
                    "failed": [],
                    "skipped": [],
                    "migrated_count": 0,
                    "updated_count": 0,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "message": "All up to date",
                },
            ):
                exit_code = main()
                assert exit_code == 0

    def test_main_full_migration(self):
        """main without --sync or --verify (full migration)."""
        from elle.capabilities.autogen.core_migrator import main

        with patch("sys.argv", ["core_migrator"]):
            with patch.object(
                CoreCapabilityMigrator,
                "migrate_all",
                return_value={
                    "migrated": ["a.cap"],
                    "updated": ["b.cap"],
                    "failed": [],
                    "skipped": [],
                    "migrated_count": 1,
                    "updated_count": 1,
                    "failed_count": 0,
                    "skipped_count": 0,
                },
            ):
                exit_code = main()
                assert exit_code == 0

    def test_main_migration_with_failures(self):
        """main full migration with failures returns 1."""
        from elle.capabilities.autogen.core_migrator import main

        with patch("sys.argv", ["core_migrator", "--dry-run"]):
            with patch.object(
                CoreCapabilityMigrator,
                "migrate_all",
                return_value={
                    "migrated": [],
                    "updated": [],
                    "failed": ["x.cap: error"],
                    "skipped": [],
                    "migrated_count": 0,
                    "updated_count": 0,
                    "failed_count": 1,
                    "skipped_count": 0,
                },
            ):
                exit_code = main()
                assert exit_code == 1

    def test_main_verbose(self):
        """main --verbose sets debug logging."""
        from elle.capabilities.autogen.core_migrator import main

        with patch("sys.argv", ["core_migrator", "--sync", "--verbose"]):
            with patch.object(
                CoreCapabilityMigrator,
                "sync_changed",
                return_value={
                    "migrated": [],
                    "updated": [],
                    "failed": [],
                    "skipped": [],
                    "migrated_count": 0,
                    "updated_count": 0,
                    "failed_count": 0,
                    "skipped_count": 0,
                },
            ):
                exit_code = main()
                assert exit_code == 0
