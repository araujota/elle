"""Migrate and sync core capabilities from Python files to autogen.db.

This module manages core capabilities stored in capabilities/core/*.py:
- Python files remain the SOURCE OF TRUTH (for versioning/updates)
- autogen.db serves as a compiled CACHE for faster loading
- On startup, checks if Python files changed and re-syncs if needed

This approach allows core capabilities to be updated when ELLE is upgraded
while still benefiting from unified storage in autogen.db.

Usage:
    # Full migration (for fresh install or major updates)
    python -m elle.capabilities.autogen.core_migrator

    # Check and sync only changed files (for startup)
    python -m elle.capabilities.autogen.core_migrator --sync

    # Verify stored capabilities match source
    python -m elle.capabilities.autogen.core_migrator --verify
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from elle.capabilities.autogen.store import AutogenStore, get_store

logger = logging.getLogger(__name__)


# =============================================================================
# Source Code Extraction
# =============================================================================


def extract_imports(source: str) -> str:
    """Extract import statements from source code."""
    tree = ast.parse(source)
    imports = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            segment = ast.get_source_segment(source, node)
            if segment:
                imports.append(segment)

    return "\n".join(imports)


def extract_class_source(source: str, class_name: str) -> str | None:
    """Extract the full source code of a class."""
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return ast.get_source_segment(source, node)

    return None


def extract_function_source(source: str, func_name: str) -> str | None:
    """Extract the full source code of a module-level function."""
    tree = ast.parse(source)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(source, node)

    return None


def find_helper_functions(source: str) -> list[str]:
    """Find all helper functions (those starting with _) in source."""
    tree = ast.parse(source)
    helpers = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_"):
            helpers.append(node.name)

    return helpers


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file's contents."""
    content = file_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def extract_capability_spec_from_class(cap_class: type) -> dict[str, Any] | None:
    """Extract CapabilitySpec data from a capability class instance."""
    try:
        instance = cap_class()
        spec = instance.spec

        return {
            "name": spec.name,
            "summary": spec.summary,
            "domain": spec.domain,
            "risk": spec.risk,
            "side_effects": [
                {
                    "kind": se.kind,
                    "target": se.target,
                    "reversible": se.reversible,
                    "description": se.description,
                }
                for se in spec.side_effects
            ],
            "requires_privilege": spec.requires_privilege,
            "idempotent": spec.idempotent,
            "trust_level": spec.trust_level,
            "version": spec.version,
            "input_schema_name": spec.input_schema_name,
            "output_schema_name": spec.output_schema_name,
            "dependencies": list(spec.dependencies),
        }
    except Exception as e:
        logger.error(f"Failed to extract spec from {cap_class}: {e}")
        return None


# =============================================================================
# Core Module Processing
# =============================================================================


class CoreCapabilityExtractor:
    """Extracts capabilities from a core module file."""

    def __init__(self, module_path: Path) -> None:
        self.module_path = module_path
        self.source = module_path.read_text()
        self.module_name = module_path.stem
        self.source_hash = compute_file_hash(module_path)

    def extract_all(self) -> list[dict[str, Any]]:
        """Extract all capabilities from the module."""
        results = []

        imports = extract_imports(self.source)
        helpers = find_helper_functions(self.source)
        helper_code = "\n\n".join(
            extract_function_source(self.source, h) or ""
            for h in helpers
        )

        tree = ast.parse(self.source)
        capability_classes = []
        model_classes = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                if node.name.endswith("Capability"):
                    capability_classes.append(node.name)
                elif node.name.endswith("Input") or node.name.endswith("Output"):
                    model_classes.append(node.name)

        input_models_code = ""
        output_models_code = ""

        for model_name in model_classes:
            model_source = extract_class_source(self.source, model_name)
            if model_source:
                if model_name.endswith("Input"):
                    input_models_code += model_source + "\n\n"
                else:
                    output_models_code += model_source + "\n\n"

        for class_name in capability_classes:
            cap_data = self._process_capability(
                class_name,
                imports,
                helper_code,
                input_models_code,
                output_models_code,
            )
            if cap_data:
                results.append(cap_data)

        return results

    def _process_capability(
        self,
        class_name: str,
        imports: str,
        helper_code: str,
        input_models_code: str,
        output_models_code: str,
    ) -> dict[str, Any] | None:
        """Process a single capability class."""
        class_source = extract_class_source(self.source, class_name)
        if not class_source:
            logger.warning(f"Could not extract source for {class_name}")
            return None

        capability_class_code = f'''{imports}

# Helper functions
{helper_code}

# Capability class
{class_source}
'''

        input_model_code = f'''from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

{input_models_code}
'''

        output_model_code = f'''from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

{output_models_code}
'''

        try:
            module = __import__(
                f"elle.capabilities.core.{self.module_name}",
                fromlist=[class_name],
            )
            cap_class = getattr(module, class_name)
            spec_dict = extract_capability_spec_from_class(cap_class)

            if not spec_dict:
                logger.warning(f"Could not extract spec for {class_name}")
                return None

            return {
                "capability_name": spec_dict["name"],
                "spec_dict": spec_dict,
                "input_model_code": input_model_code,
                "output_model_code": output_model_code,
                "capability_class_code": capability_class_code,
                "source_module": self.module_name,
                "source_hash": self.source_hash,
                "source_path": str(self.module_path),
            }

        except Exception as e:
            logger.error(f"Failed to process {class_name}: {e}")
            return None


# =============================================================================
# Version Tracking
# =============================================================================


def get_stored_hash_for_module(store: AutogenStore, module_name: str) -> str | None:
    """Get the stored source hash for a core module.

    Args:
        store: AutogenStore instance.
        module_name: Module name (e.g., 'service').

    Returns:
        Stored hash or None if not found.
    """
    with store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT man_page_hash FROM generated_capabilities
            WHERE trust_level = 'core' AND source_command = ?
            LIMIT 1
            """,
            (module_name,),
        )
        row = cursor.fetchone()
        return row["man_page_hash"] if row else None


def get_modules_needing_sync(store: AutogenStore) -> list[Path]:
    """Find core modules whose source has changed since last sync.

    Args:
        store: AutogenStore instance.

    Returns:
        List of module paths that need re-syncing.
    """
    core_dir = Path(__file__).parent.parent / "core"
    if not core_dir.exists():
        return []

    modules_to_sync = []

    for module_path in core_dir.glob("*.py"):
        if module_path.name == "__init__.py":
            continue

        module_name = module_path.stem
        stored_hash = get_stored_hash_for_module(store, module_name)
        current_hash = compute_file_hash(module_path)

        if stored_hash != current_hash:
            logger.debug(f"Module {module_name} needs sync (hash changed)")
            modules_to_sync.append(module_path)

    return modules_to_sync


# =============================================================================
# Migration & Sync
# =============================================================================


class CoreCapabilityMigrator:
    """Migrates/syncs core capabilities to autogen.db."""

    def __init__(
        self,
        store: AutogenStore | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> None:
        """Initialize migrator.

        Args:
            store: AutogenStore instance (uses default if None).
            dry_run: If True, don't actually save to database.
            force: If True, re-migrate even if hash matches.
        """
        self.store = store or get_store()
        self.dry_run = dry_run
        self.force = force
        self.migrated: list[str] = []
        self.updated: list[str] = []
        self.failed: list[str] = []
        self.skipped: list[str] = []

    def migrate_all(self) -> dict[str, Any]:
        """Migrate all core capability modules.

        Returns:
            Summary dict with counts and lists.
        """
        core_dir = Path(__file__).parent.parent / "core"

        if not core_dir.exists():
            logger.error(f"Core directory not found: {core_dir}")
            return {"error": f"Core directory not found: {core_dir}"}

        module_files = [
            f for f in core_dir.glob("*.py")
            if f.name != "__init__.py"
        ]

        logger.info(f"Found {len(module_files)} core capability modules")

        for module_path in module_files:
            self._migrate_module(module_path)

        return {
            "migrated": self.migrated,
            "updated": self.updated,
            "failed": self.failed,
            "skipped": self.skipped,
            "migrated_count": len(self.migrated),
            "updated_count": len(self.updated),
            "failed_count": len(self.failed),
            "skipped_count": len(self.skipped),
        }

    def sync_changed(self) -> dict[str, Any]:
        """Sync only modules whose source has changed.

        Returns:
            Summary dict.
        """
        modules_to_sync = get_modules_needing_sync(self.store)

        if not modules_to_sync:
            logger.info("All core capabilities are up to date")
            return {
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

        logger.info(f"Found {len(modules_to_sync)} modules needing sync")

        for module_path in modules_to_sync:
            self._migrate_module(module_path, is_update=True)

        return {
            "migrated": self.migrated,
            "updated": self.updated,
            "failed": self.failed,
            "skipped": self.skipped,
            "migrated_count": len(self.migrated),
            "updated_count": len(self.updated),
            "failed_count": len(self.failed),
            "skipped_count": len(self.skipped),
        }

    def _migrate_module(self, module_path: Path, is_update: bool = False) -> None:
        """Migrate a single module file."""
        logger.info(f"Processing {module_path.name}")

        extractor = CoreCapabilityExtractor(module_path)

        try:
            capabilities = extractor.extract_all()
        except Exception as e:
            logger.error(f"Failed to extract from {module_path.name}: {e}")
            self.failed.append(f"{module_path.name}: extraction failed - {e}")
            return

        for cap_data in capabilities:
            self._save_capability(cap_data, is_update=is_update)

    def _save_capability(
        self,
        cap_data: dict[str, Any],
        is_update: bool = False,
    ) -> None:
        """Save a single capability to the store."""
        cap_name = cap_data["capability_name"]

        # Check if already exists with same hash
        existing = self.store.get(cap_name)
        if existing and not self.force:
            stored_hash = existing.man_page_hash
            if stored_hash == cap_data["source_hash"]:
                logger.debug(f"Skipping {cap_name}: hash unchanged")
                self.skipped.append(cap_name)
                return

        if self.dry_run:
            action = "update" if existing else "migrate"
            logger.info(f"[DRY RUN] Would {action}: {cap_name}")
            if is_update:
                self.updated.append(f"{cap_name} (dry-run)")
            else:
                self.migrated.append(f"{cap_name} (dry-run)")
            return

        try:
            spec_json = json.dumps(cap_data["spec_dict"])
            cap_id = str(uuid.uuid4())

            with self.store._get_connection() as conn:
                # Delete existing
                conn.execute(
                    "DELETE FROM generated_capabilities WHERE capability_name = ?",
                    (cap_name,),
                )

                # Insert new/updated
                conn.execute(
                    """
                    INSERT INTO generated_capabilities (
                        id, capability_name, spec_json, input_model_code,
                        output_model_code, capability_class_code, source_command,
                        man_page_hash, generated_at, trust_level, approved, enabled,
                        binary_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cap_id,
                        cap_name,
                        spec_json,
                        cap_data["input_model_code"],
                        cap_data["output_model_code"],
                        cap_data["capability_class_code"],
                        cap_data["source_module"],
                        cap_data["source_hash"],
                        datetime.utcnow().isoformat(),
                        "core",
                        1,  # Approved
                        1,  # Enabled
                        cap_data["source_path"],  # Store source path for reference
                    ),
                )
                conn.commit()

            action = "Updated" if existing else "Migrated"
            logger.info(f"{action}: {cap_name}")

            if is_update or existing:
                self.updated.append(cap_name)
            else:
                self.migrated.append(cap_name)

        except Exception as e:
            logger.error(f"Failed to save {cap_name}: {e}")
            self.failed.append(f"{cap_name}: {e}")


# =============================================================================
# Startup Sync Function
# =============================================================================


def sync_core_capabilities_if_needed(store: AutogenStore | None = None) -> dict[str, Any]:
    """Sync core capabilities at startup if source files have changed.

    This function is designed to be called during daemon/CLI startup
    to ensure stored capabilities match the current Python source.

    Args:
        store: AutogenStore instance.

    Returns:
        Sync results dict.
    """
    store = store or get_store()
    migrator = CoreCapabilityMigrator(store=store)
    return migrator.sync_changed()


# =============================================================================
# Verification
# =============================================================================


def verify_migration(store: AutogenStore | None = None) -> dict[str, Any]:
    """Verify that stored capabilities can be loaded and match source."""
    from elle.capabilities.autogen.loader import load_capability_from_stored

    store = store or get_store()
    results = {
        "verified": [],
        "failed": [],
        "hash_mismatches": [],
        "total_core": 0,
    }

    with store._get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM generated_capabilities WHERE trust_level = 'core'"
        )
        rows = cursor.fetchall()

    results["total_core"] = len(rows)

    for row in rows:
        cap_name = row["capability_name"]
        try:
            stored = store.get(cap_name)
            if stored:
                # Check hash against current source
                source_path = row["binary_path"]
                if source_path:
                    current_hash = compute_file_hash(Path(source_path))
                    if current_hash != stored.man_page_hash:
                        results["hash_mismatches"].append({
                            "name": cap_name,
                            "stored_hash": stored.man_page_hash[:8],
                            "current_hash": current_hash[:8],
                        })

                # Try to load and instantiate
                cap_class = load_capability_from_stored(stored)
                if cap_class:
                    instance = cap_class()
                    spec = instance.spec
                    results["verified"].append({
                        "name": cap_name,
                        "spec_name": spec.name,
                        "domain": spec.domain,
                    })
                else:
                    results["failed"].append({
                        "name": cap_name,
                        "error": "Failed to compile",
                    })
            else:
                results["failed"].append({
                    "name": cap_name,
                    "error": "Not found in store",
                })
        except Exception as e:
            results["failed"].append({
                "name": cap_name,
                "error": str(e),
            })

    return results


# =============================================================================
# CLI
# =============================================================================


def main() -> int:
    """Run the migration."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate/sync core capabilities to autogen.db"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually save to database",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify stored capabilities match source",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Only sync modules whose source has changed",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-migration even if hash matches",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )

    if args.verify:
        print("Verifying stored core capabilities...")
        results = verify_migration()
        print(f"\nTotal core capabilities: {results['total_core']}")
        print(f"Successfully verified: {len(results['verified'])}")
        print(f"Failed to verify: {len(results['failed'])}")
        print(f"Hash mismatches: {len(results['hash_mismatches'])}")

        if results["hash_mismatches"]:
            print("\nCapabilities with outdated stored versions:")
            for item in results["hash_mismatches"]:
                print(f"  - {item['name']}: stored={item['stored_hash']}... current={item['current_hash']}...")

        if results["failed"]:
            print("\nFailed capabilities:")
            for item in results["failed"]:
                print(f"  - {item['name']}: {item['error']}")
            return 1

        print("\nAll core capabilities verified successfully!")
        return 0

    # Run migration or sync
    migrator = CoreCapabilityMigrator(dry_run=args.dry_run, force=args.force)

    if args.sync:
        print("Syncing changed core capabilities...")
        results = migrator.sync_changed()
    else:
        print("Migrating all core capabilities to autogen.db...")
        results = migrator.migrate_all()

    if args.dry_run:
        print("(DRY RUN - no changes made)")

    print("\nResults:")
    print(f"  New:     {results['migrated_count']}")
    print(f"  Updated: {results['updated_count']}")
    print(f"  Skipped: {results['skipped_count']}")
    print(f"  Failed:  {results['failed_count']}")

    if results.get("message"):
        print(f"\n{results['message']}")

    if results["migrated"]:
        print("\nNew capabilities:")
        for cap in results["migrated"]:
            print(f"  + {cap}")

    if results["updated"]:
        print("\nUpdated capabilities:")
        for cap in results["updated"]:
            print(f"  ~ {cap}")

    if results["failed"]:
        print("\nFailed capabilities:")
        for cap in results["failed"]:
            print(f"  ! {cap}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
