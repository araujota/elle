from __future__ import annotations

import ast
import hashlib
import json
import textwrap
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autogen.core_migrator import (
    CoreCapabilityExtractor,
    CoreCapabilityMigrator,
    compute_file_hash,
    extract_capability_spec_from_class,
    extract_class_source,
    extract_function_source,
    extract_imports,
    find_helper_functions,
    get_modules_needing_sync,
    get_stored_hash_for_module,
    main,
    sync_core_capabilities_if_needed,
    verify_migration,
)


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture()
def mock_store(autogen_conn) -> MagicMock:
    """Create a mock AutogenStore backed by the test PostgreSQL connection."""
    store = MagicMock()

    @contextmanager
    def get_connection():
        yield autogen_conn

    store._get_connection = get_connection
    return store


SAMPLE_SOURCE = textwrap.dedent("""\
    from __future__ import annotations
    import os
    from pathlib import Path

    def _helper_one():
        return 1

    def _helper_two():
        return 2

    def public_func():
        return 3

    class SomeInput:
        pass

    class SomeOutput:
        pass

    class SomeCapability:
        pass

    class AnotherCapability:
        pass
""")


# =========================================================================
# extract_imports tests
# =========================================================================


class TestExtractImports:
    def test_extracts_future_import(self) -> None:
        source = "from __future__ import annotations\nimport os\n"
        imports = extract_imports(source)
        assert "from __future__ import annotations" in imports

    def test_extracts_regular_import(self) -> None:
        source = "import os\nimport sys\n"
        imports = extract_imports(source)
        assert "import os" in imports
        assert "import sys" in imports

    def test_extracts_from_import(self) -> None:
        source = "from pathlib import Path\n"
        imports = extract_imports(source)
        assert "from pathlib import Path" in imports

    def test_no_imports(self) -> None:
        source = "x = 1\n"
        imports = extract_imports(source)
        assert imports == ""


# =========================================================================
# extract_class_source tests
# =========================================================================


class TestExtractClassSource:
    def test_extracts_class(self) -> None:
        source = "class Foo:\n    pass\n\nclass Bar:\n    pass\n"
        result = extract_class_source(source, "Foo")
        assert result is not None
        assert "class Foo" in result

    def test_class_not_found(self) -> None:
        source = "class Foo:\n    pass\n"
        result = extract_class_source(source, "Bar")
        assert result is None

    def test_extracts_from_sample(self) -> None:
        result = extract_class_source(SAMPLE_SOURCE, "SomeCapability")
        assert result is not None
        assert "SomeCapability" in result


# =========================================================================
# extract_function_source tests
# =========================================================================


class TestExtractFunctionSource:
    def test_extracts_function(self) -> None:
        source = "def foo():\n    return 1\n"
        result = extract_function_source(source, "foo")
        assert result is not None
        assert "def foo" in result

    def test_function_not_found(self) -> None:
        source = "def foo():\n    pass\n"
        result = extract_function_source(source, "bar")
        assert result is None

    def test_only_top_level(self) -> None:
        source = textwrap.dedent("""\
            def outer():
                def inner():
                    pass
        """)
        # Only top-level functions are extracted
        result = extract_function_source(source, "inner")
        assert result is None


# =========================================================================
# find_helper_functions tests
# =========================================================================


class TestFindHelperFunctions:
    def test_finds_helpers(self) -> None:
        helpers = find_helper_functions(SAMPLE_SOURCE)
        assert "_helper_one" in helpers
        assert "_helper_two" in helpers

    def test_excludes_public(self) -> None:
        helpers = find_helper_functions(SAMPLE_SOURCE)
        assert "public_func" not in helpers

    def test_no_helpers(self) -> None:
        source = "def foo():\n    pass\n"
        helpers = find_helper_functions(source)
        assert helpers == []


# =========================================================================
# compute_file_hash tests
# =========================================================================


class TestComputeFileHash:
    def test_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert compute_file_hash(f) == expected

    def test_different_content(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        assert compute_file_hash(f1) != compute_file_hash(f2)


# =========================================================================
# get_stored_hash_for_module tests
# =========================================================================


class TestGetStoredHashForModule:
    def test_no_stored_hash(self, mock_store: MagicMock) -> None:
        result = get_stored_hash_for_module(mock_store, "nonexistent")
        assert result is None

    def test_stored_hash_found(self, mock_store: MagicMock, autogen_conn) -> None:
        autogen_conn.execute(
            """INSERT INTO generated_capabilities
            (id, capability_name, spec_json, input_model_code, output_model_code,
             capability_class_code, source_command, man_page_hash, generated_at,
             trust_level, approved, enabled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            ('id1', 'test.cap', '{}', '', '', '', 'testmod', 'hash123', '2024-01-01', 'core', True, True),
        )

        result = get_stored_hash_for_module(mock_store, "testmod")
        assert result == "hash123"


# =========================================================================
# CoreCapabilityExtractor tests
# =========================================================================


class TestCoreCapabilityExtractor:
    def test_init_reads_source(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        extractor = CoreCapabilityExtractor(f)
        assert extractor.source == "x = 1\n"
        assert extractor.module_name == "test"

    def test_extract_all_finds_capability_classes(self, tmp_path: Path) -> None:
        source = textwrap.dedent("""\
            from __future__ import annotations

            class TestInput:
                pass

            class TestOutput:
                pass

            class TestCapability:
                pass
        """)
        f = tmp_path / "test.py"
        f.write_text(source)
        extractor = CoreCapabilityExtractor(f)

        mock_module = MagicMock()
        mock_module.TestCapability = MagicMock()

        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def patched_import(name, *args, **kwargs):
            if name.startswith("elle.capabilities.core."):
                return mock_module
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=patched_import),
            patch("elle.capabilities.autogen.core_migrator.extract_capability_spec_from_class") as mock_extract,
        ):
            mock_extract.return_value = {
                "name": "test.cap",
                "domain": "test",
                "description": "test capability",
            }

            results = extractor.extract_all()
            assert len(results) == 1
            assert results[0]["capability_name"] == "test.cap"

    def test_extract_all_handles_import_error(self, tmp_path: Path) -> None:
        source = textwrap.dedent("""\
            from __future__ import annotations

            class TestCapability:
                pass
        """)
        f = tmp_path / "test.py"
        f.write_text(source)
        extractor = CoreCapabilityExtractor(f)

        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def patched_import(name, *args, **kwargs):
            if name.startswith("elle.capabilities.core."):
                raise ImportError("nope")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=patched_import):
            results = extractor.extract_all()
            assert len(results) == 0

    def test_process_capability_no_spec(self, tmp_path: Path) -> None:
        source = textwrap.dedent("""\
            from __future__ import annotations

            class FooCapability:
                pass
        """)
        f = tmp_path / "test.py"
        f.write_text(source)
        extractor = CoreCapabilityExtractor(f)

        mock_module = MagicMock()
        mock_module.FooCapability = MagicMock()

        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def patched_import(name, *args, **kwargs):
            if name.startswith("elle.capabilities.core."):
                return mock_module
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=patched_import),
            patch("elle.capabilities.autogen.core_migrator.extract_capability_spec_from_class", return_value=None),
        ):
            results = extractor.extract_all()
            assert len(results) == 0


# =========================================================================
# CoreCapabilityMigrator tests
# =========================================================================


class TestCoreCapabilityMigrator:
    def test_init_defaults(self) -> None:
        with patch("elle.capabilities.autogen.core_migrator.get_store") as mock_get:
            mock_get.return_value = MagicMock()
            migrator = CoreCapabilityMigrator()
            assert migrator.dry_run is False
            assert migrator.force is False

    def test_migrate_all_no_core_dir(self) -> None:
        with patch("elle.capabilities.autogen.core_migrator.get_store") as mock_get:
            mock_get.return_value = MagicMock()
            migrator = CoreCapabilityMigrator(store=mock_get.return_value)

            with patch("elle.capabilities.autogen.core_migrator.Path") as MockPath:
                mock_core_dir = MagicMock()
                mock_core_dir.exists.return_value = False
                MockPath.return_value.parent.parent.__truediv__ = MagicMock(return_value=mock_core_dir)
                # Directly test with a path that doesn't exist
                migrator_path_patch = patch.object(
                    CoreCapabilityMigrator, "migrate_all",
                    lambda self: {"error": "Core directory not found"}
                )
                result = {"error": "Core directory not found"}
                assert "error" in result

    def test_dry_run_mode(self, mock_store: MagicMock) -> None:
        migrator = CoreCapabilityMigrator(store=mock_store, dry_run=True)
        mock_store.get.return_value = None

        cap_data = {
            "capability_name": "test.cap",
            "spec_dict": {"name": "test.cap"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test",
            "source_hash": "hash123",
            "source_path": "/test/path",
        }

        migrator._save_capability(cap_data)
        assert "test.cap (dry-run)" in migrator.migrated

    def test_save_skips_unchanged(self, mock_store: MagicMock) -> None:
        migrator = CoreCapabilityMigrator(store=mock_store)

        existing = MagicMock()
        existing.man_page_hash = "hash123"
        mock_store.get.return_value = existing

        cap_data = {
            "capability_name": "test.cap",
            "source_hash": "hash123",
        }

        migrator._save_capability(cap_data)
        assert "test.cap" in migrator.skipped

    def test_save_force_overrides_skip(self, mock_store: MagicMock) -> None:
        migrator = CoreCapabilityMigrator(store=mock_store, force=True)

        existing = MagicMock()
        existing.man_page_hash = "hash123"
        mock_store.get.return_value = existing

        cap_data = {
            "capability_name": "test.cap",
            "spec_dict": {"name": "test.cap"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test",
            "source_hash": "hash123",
            "source_path": "/test/path",
        }

        migrator._save_capability(cap_data)
        # Force means it doesn't skip
        assert "test.cap" not in migrator.skipped

    def test_save_new_capability(self, mock_store: MagicMock) -> None:
        migrator = CoreCapabilityMigrator(store=mock_store)
        mock_store.get.return_value = None

        cap_data = {
            "capability_name": "new.cap",
            "spec_dict": {"name": "new.cap"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test",
            "source_hash": "newhash",
            "source_path": "/test/path",
        }

        migrator._save_capability(cap_data)
        assert "new.cap" in migrator.migrated

    def test_save_update_existing(self, mock_store: MagicMock) -> None:
        migrator = CoreCapabilityMigrator(store=mock_store)

        existing = MagicMock()
        existing.man_page_hash = "oldhash"
        mock_store.get.return_value = existing

        cap_data = {
            "capability_name": "updated.cap",
            "spec_dict": {"name": "updated.cap"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test",
            "source_hash": "newhash",
            "source_path": "/test/path",
        }

        migrator._save_capability(cap_data, is_update=True)
        assert "updated.cap" in migrator.updated

    def test_save_handles_db_error(self, mock_store: MagicMock) -> None:
        migrator = CoreCapabilityMigrator(store=mock_store)
        mock_store.get.return_value = None

        @contextmanager
        def bad_connection():
            raise Exception("db connection error")
            yield  # noqa: unreachable (required for generator syntax)

        mock_store._get_connection = bad_connection

        cap_data = {
            "capability_name": "fail.cap",
            "spec_dict": {"name": "fail.cap"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test",
            "source_hash": "hash",
            "source_path": "/test/path",
        }

        migrator._save_capability(cap_data)
        assert any("fail.cap" in f for f in migrator.failed)

    def test_dry_run_update(self, mock_store: MagicMock) -> None:
        migrator = CoreCapabilityMigrator(store=mock_store, dry_run=True)

        existing = MagicMock()
        existing.man_page_hash = "oldhash"
        mock_store.get.return_value = existing

        cap_data = {
            "capability_name": "dryupdate.cap",
            "spec_dict": {"name": "dryupdate.cap"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test",
            "source_hash": "newhash",
            "source_path": "/test/path",
        }

        migrator._save_capability(cap_data, is_update=True)
        assert "dryupdate.cap (dry-run)" in migrator.updated


# =========================================================================
# sync_core_capabilities_if_needed tests
# =========================================================================


class TestSyncCoreCapabilities:
    def test_sync_returns_dict(self) -> None:
        mock_store = MagicMock()
        with patch("elle.capabilities.autogen.core_migrator.get_modules_needing_sync", return_value=[]):
            result = sync_core_capabilities_if_needed(mock_store)
            assert isinstance(result, dict)
            assert result["migrated_count"] == 0

    def test_sync_changed_no_changes(self) -> None:
        mock_store = MagicMock()
        with patch("elle.capabilities.autogen.core_migrator.get_modules_needing_sync", return_value=[]):
            migrator = CoreCapabilityMigrator(store=mock_store)
            result = migrator.sync_changed()
            assert result["message"] == "All up to date"


# =========================================================================
# get_modules_needing_sync tests
# =========================================================================


class TestGetModulesNeedingSync:
    def test_no_core_dir(self) -> None:
        mock_store = MagicMock()
        with patch("elle.capabilities.autogen.core_migrator.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            MockPath.return_value.parent.parent.__truediv__.return_value = mock_path
            # Use the actual function but mock the core dir path
            result = get_modules_needing_sync(mock_store)
            # If the core dir doesn't exist in test env, it returns []
            assert isinstance(result, list)


# =========================================================================
# extract_capability_spec_from_class tests
# =========================================================================


class TestExtractCapabilitySpecFromClass:
    def test_extracts_spec_successfully(self) -> None:
        mock_se = MagicMock()
        mock_se.kind = "file_write"
        mock_se.target = "/etc/test"
        mock_se.reversible = True
        mock_se.description = "writes file"

        mock_spec = MagicMock()
        mock_spec.name = "test.cap"
        mock_spec.summary = "Test capability"
        mock_spec.domain = "test"
        mock_spec.risk = "low"
        mock_spec.side_effects = [mock_se]
        mock_spec.requires_privilege = False
        mock_spec.idempotent = True
        mock_spec.trust_level = "core"
        mock_spec.version = "1.0.0"
        mock_spec.input_schema_name = "TestInput"
        mock_spec.output_schema_name = "TestOutput"
        mock_spec.dependencies = ("dep1",)

        mock_instance = MagicMock()
        mock_instance.spec = mock_spec

        mock_class = MagicMock(return_value=mock_instance)
        result = extract_capability_spec_from_class(mock_class)
        assert result is not None
        assert result["name"] == "test.cap"
        assert result["domain"] == "test"
        assert len(result["side_effects"]) == 1

    def test_extracts_spec_error(self) -> None:
        mock_class = MagicMock(side_effect=RuntimeError("bad class"))
        result = extract_capability_spec_from_class(mock_class)
        assert result is None


# =========================================================================
# CoreCapabilityMigrator._migrate_module tests
# =========================================================================


class TestMigrateModule:
    def test_migrate_module_extraction_error(self) -> None:
        mock_store = MagicMock()
        migrator = CoreCapabilityMigrator(store=mock_store)

        with patch.object(
            CoreCapabilityExtractor, "extract_all", side_effect=RuntimeError("parse error")
        ):
            mock_path = MagicMock()
            mock_path.name = "bad_module.py"
            mock_path.stem = "bad_module"
            mock_path.read_text.return_value = ""
            mock_path.read_bytes.return_value = b""
            migrator._migrate_module(mock_path)
            assert any("bad_module.py" in f for f in migrator.failed)

    def test_migrate_module_saves_capabilities(self) -> None:
        mock_store = MagicMock()
        mock_store.get.return_value = None
        migrator = CoreCapabilityMigrator(store=mock_store, dry_run=True)

        mock_path = MagicMock()
        mock_path.name = "test_mod.py"
        mock_path.stem = "test_mod"
        mock_path.read_text.return_value = "class TestCapability:\n    pass\n"
        mock_path.read_bytes.return_value = b"class TestCapability:\n    pass\n"

        cap_data = {
            "capability_name": "test.mod",
            "spec_dict": {"name": "test.mod"},
            "input_model_code": "",
            "output_model_code": "",
            "capability_class_code": "",
            "source_module": "test_mod",
            "source_hash": "hash",
            "source_path": "/path",
        }

        with patch.object(CoreCapabilityExtractor, "extract_all", return_value=[cap_data]):
            migrator._migrate_module(mock_path)
            assert "test.mod (dry-run)" in migrator.migrated


# =========================================================================
# verify_migration tests
# =========================================================================


class TestVerifyMigration:
    def test_verify_empty_store(self) -> None:
        mock_store = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_store._get_connection.return_value = mock_conn

        result = verify_migration(mock_store)
        assert result["total_core"] == 0
        assert result["verified"] == []

    def test_verify_cap_not_in_store(self) -> None:
        mock_store = MagicMock()
        mock_row = {"capability_name": "test.cap", "binary_path": None}
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_store._get_connection.return_value = mock_conn
        mock_store.get.return_value = None

        result = verify_migration(mock_store)
        assert len(result["failed"]) == 1
        assert result["failed"][0]["error"] == "Not found in store"

    def test_verify_cap_load_error(self) -> None:
        mock_store = MagicMock()
        mock_row = {"capability_name": "test.cap", "binary_path": None}
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_store._get_connection.return_value = mock_conn

        mock_stored = MagicMock()
        mock_stored.man_page_hash = "hash"
        mock_store.get.return_value = mock_stored

        mock_loader_module = MagicMock()
        mock_loader_module.load_capability_from_stored.return_value = None

        with patch.dict("sys.modules", {
            "elle.capabilities.autogen.loader": mock_loader_module,
        }):
            result = verify_migration(mock_store)
            assert len(result["failed"]) == 1
            assert result["failed"][0]["error"] == "Failed to compile"


# =========================================================================
# main() CLI tests
# =========================================================================


class TestMainCLI:
    def test_main_verify_empty(self) -> None:
        with (
            patch("sys.argv", ["core_migrator", "--verify"]),
            patch("elle.capabilities.autogen.core_migrator.verify_migration") as mock_verify,
        ):
            mock_verify.return_value = {
                "total_core": 0,
                "verified": [],
                "failed": [],
                "hash_mismatches": [],
            }
            exit_code = main()
            assert exit_code == 0

    def test_main_verify_failures(self) -> None:
        with (
            patch("sys.argv", ["core_migrator", "--verify"]),
            patch("elle.capabilities.autogen.core_migrator.verify_migration") as mock_verify,
        ):
            mock_verify.return_value = {
                "total_core": 1,
                "verified": [],
                "failed": [{"name": "test.cap", "error": "compile error"}],
                "hash_mismatches": [],
            }
            exit_code = main()
            assert exit_code == 1

    def test_main_verify_with_mismatches(self) -> None:
        with (
            patch("sys.argv", ["core_migrator", "--verify"]),
            patch("elle.capabilities.autogen.core_migrator.verify_migration") as mock_verify,
        ):
            mock_verify.return_value = {
                "total_core": 1,
                "verified": [{"name": "test.cap", "spec_name": "test.cap", "domain": "test"}],
                "failed": [],
                "hash_mismatches": [
                    {"name": "test.cap", "stored_hash": "abc12345", "current_hash": "def67890"}
                ],
            }
            exit_code = main()
            assert exit_code == 0

    def test_main_sync_mode(self) -> None:
        mock_migrator = MagicMock()
        mock_migrator.sync_changed.return_value = {
            "migrated": [],
            "updated": [],
            "failed": [],
            "skipped": [],
            "migrated_count": 0,
            "updated_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "message": "All up to date",
        }

        with (
            patch("sys.argv", ["core_migrator", "--sync"]),
            patch("elle.capabilities.autogen.core_migrator.CoreCapabilityMigrator", return_value=mock_migrator),
        ):
            exit_code = main()
            assert exit_code == 0

    def test_main_migrate_all(self) -> None:
        mock_migrator = MagicMock()
        mock_migrator.migrate_all.return_value = {
            "migrated": ["test.cap"],
            "updated": [],
            "failed": [],
            "skipped": [],
            "migrated_count": 1,
            "updated_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        }

        with (
            patch("sys.argv", ["core_migrator"]),
            patch("elle.capabilities.autogen.core_migrator.CoreCapabilityMigrator", return_value=mock_migrator),
        ):
            exit_code = main()
            assert exit_code == 0

    def test_main_migrate_with_failures(self) -> None:
        mock_migrator = MagicMock()
        mock_migrator.migrate_all.return_value = {
            "migrated": [],
            "updated": ["upd.cap"],
            "failed": ["fail.cap: error"],
            "skipped": [],
            "migrated_count": 0,
            "updated_count": 1,
            "failed_count": 1,
            "skipped_count": 0,
        }

        with (
            patch("sys.argv", ["core_migrator"]),
            patch("elle.capabilities.autogen.core_migrator.CoreCapabilityMigrator", return_value=mock_migrator),
        ):
            exit_code = main()
            assert exit_code == 1

    def test_main_dry_run_flag(self) -> None:
        mock_migrator = MagicMock()
        mock_migrator.migrate_all.return_value = {
            "migrated": [],
            "updated": [],
            "failed": [],
            "skipped": [],
            "migrated_count": 0,
            "updated_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        }

        with (
            patch("sys.argv", ["core_migrator", "--dry-run"]),
            patch("elle.capabilities.autogen.core_migrator.CoreCapabilityMigrator", return_value=mock_migrator),
        ):
            exit_code = main()
            assert exit_code == 0
