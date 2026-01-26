"""Capability versioning based on source package versions.

When packages are upgraded, this service:
1. Detects which capabilities are affected
2. Triggers LLM regeneration with updated man pages
3. Updates stored capabilities with new version info
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from elle.capabilities.autogen.discovery import discover_binary
from elle.capabilities.autogen.factory import create_capability_from_spec
from elle.capabilities.autogen.generator import generate_capability_spec
from elle.capabilities.autogen.models import StoredCapability
from elle.capabilities.autogen.parser import parse_man_page
from elle.capabilities.autogen.store import AutogenStore, get_store
from elle.capabilities.autogen.validator import validate_capability

if TYPE_CHECKING:
    from elle.capabilities.autogen.generator import CapabilityGenerator

logger = logging.getLogger(__name__)


class CapabilityVersioner:
    """Manages capability versioning based on source packages.

    Tracks which packages have generated capabilities and triggers
    regeneration when those packages are upgraded.
    """

    def __init__(
        self,
        store: AutogenStore | None = None,
        generator: CapabilityGenerator | None = None,
    ) -> None:
        """Initialize the versioner.

        Args:
            store: AutogenStore instance (or uses default).
            generator: CapabilityGenerator instance (optional, for testing).
        """
        self._store = store or get_store()
        self._generator = generator
        self._package_to_capabilities: dict[str, list[str]] = {}
        self._regeneration_stats: dict[str, Any] = {
            "total_regenerations": 0,
            "successful": 0,
            "failed": 0,
            "by_package": {},
        }

    def build_package_map(self) -> dict[str, list[str]]:
        """Scan store and build package -> capability_names mapping.

        Returns:
            Dict mapping package names to list of capability names.
        """
        self._package_to_capabilities.clear()

        for cap in self._store.list_all():
            if cap.source_package:
                if cap.source_package not in self._package_to_capabilities:
                    self._package_to_capabilities[cap.source_package] = []
                self._package_to_capabilities[cap.source_package].append(
                    cap.capability_name
                )

        logger.info(
            f"Built package map: {len(self._package_to_capabilities)} packages, "
            f"{sum(len(caps) for caps in self._package_to_capabilities.values())} capabilities"
        )

        return self._package_to_capabilities

    def get_watched_packages(self) -> set[str]:
        """Return set of packages that have capabilities.

        Returns:
            Set of package names to monitor.
        """
        return set(self._package_to_capabilities.keys())

    def get_capabilities_for_package(self, package_name: str) -> list[str]:
        """Get capability names associated with a package.

        Args:
            package_name: Package name.

        Returns:
            List of capability names.
        """
        return self._package_to_capabilities.get(package_name, [])

    def add_capability_mapping(
        self,
        package_name: str,
        capability_name: str,
    ) -> None:
        """Add a package->capability mapping.

        Called when a new capability is generated.

        Args:
            package_name: dpkg package name.
            capability_name: Capability name.
        """
        if package_name not in self._package_to_capabilities:
            self._package_to_capabilities[package_name] = []
        if capability_name not in self._package_to_capabilities[package_name]:
            self._package_to_capabilities[package_name].append(capability_name)

    async def on_package_upgraded(
        self,
        package_name: str,
        old_version: str | None,
        new_version: str,
    ) -> list[str]:
        """Handle package upgrade - regenerate affected capabilities.

        Args:
            package_name: Name of upgraded package.
            old_version: Previous version (may be None).
            new_version: New version.

        Returns:
            List of successfully regenerated capability names.
        """
        capabilities = self._package_to_capabilities.get(package_name, [])

        if not capabilities:
            logger.debug(f"No capabilities for package {package_name}")
            return []

        logger.info(
            f"Package {package_name} upgraded {old_version} -> {new_version}, "
            f"regenerating {len(capabilities)} capabilities"
        )

        regenerated: list[str] = []

        for cap_name in capabilities:
            stored = self._store.get(cap_name)
            if not stored:
                logger.warning(f"Capability {cap_name} not found in store")
                continue

            # Check if version actually differs
            if stored.package_version == new_version:
                logger.debug(
                    f"Capability {cap_name} already at version {new_version}"
                )
                continue

            # Attempt regeneration
            success = await self._regenerate_capability(stored, new_version)
            if success:
                regenerated.append(cap_name)
                self._regeneration_stats["successful"] += 1
            else:
                self._regeneration_stats["failed"] += 1

            self._regeneration_stats["total_regenerations"] += 1

        # Track per-package stats
        if package_name not in self._regeneration_stats["by_package"]:
            self._regeneration_stats["by_package"][package_name] = 0
        self._regeneration_stats["by_package"][package_name] += len(regenerated)

        return regenerated

    async def _regenerate_capability(
        self,
        stored: StoredCapability,
        new_version: str,
    ) -> bool:
        """Regenerate capability with updated package version.

        1. Re-discover binary (may have moved)
        2. Re-fetch man page (may have changed)
        3. Call LLM generator
        4. Update store with new spec + version

        Args:
            stored: Existing stored capability.
            new_version: New package version.

        Returns:
            True if regeneration succeeded.
        """
        cap_name = stored.capability_name
        source_command = stored.source_command

        try:
            # Re-discover binary
            binary = discover_binary(source_command)
            if not binary:
                logger.warning(
                    f"Could not find binary for {source_command} during regeneration"
                )
                return False

            # Re-parse man page
            man_page = parse_man_page(source_command)
            if not man_page:
                logger.warning(
                    f"Could not parse man page for {source_command} during regeneration"
                )
                return False

            # Check if man page actually changed
            if not self._store.check_needs_regeneration(cap_name, man_page.raw_text):
                # Man page unchanged, just update version
                logger.debug(
                    f"Man page unchanged for {cap_name}, updating version only"
                )
                return self._update_version_only(stored, binary, new_version)

            # Man page changed - regenerate with LLM
            logger.info(f"Man page changed for {cap_name}, regenerating with LLM")

            # Determine subcommand from capability name
            # e.g., "systemctl.restart" -> subcommand = "restart"
            subcommand = None
            if "." in cap_name:
                parts = cap_name.split(".", 1)
                if len(parts) == 2 and parts[1]:
                    subcommand = parts[1]

            # Generate new spec
            spec = await generate_capability_spec(
                man_page,
                subcommand=subcommand,
                use_llm=True,
            )

            if not spec:
                logger.error(f"Failed to generate spec for {cap_name}")
                return False

            # Validate new spec
            validation = validate_capability(spec, man_page)

            # Generate code
            input_code, output_code, cap_code = create_capability_from_spec(spec)

            # Save with updated package info
            self._store.save(
                spec=spec,
                input_model_code=input_code,
                output_model_code=output_code,
                capability_class_code=cap_code,
                man_page_text=man_page.raw_text,
                trust_level=validation.trust_level,
                validation_json=validation.model_dump_json(),
                source_package=binary.package,
                package_version=new_version,
                binary_path=binary.path,
            )

            # Preserve approval status
            if stored.approved:
                self._store.approve(cap_name)
            if not stored.enabled:
                self._store.disable(cap_name)

            logger.info(
                f"Successfully regenerated {cap_name} for version {new_version}"
            )
            return True

        except Exception as e:
            logger.error(f"Error regenerating {cap_name}: {e}")
            return False

    def _update_version_only(
        self,
        stored: StoredCapability,
        binary: Any,
        new_version: str,
    ) -> bool:
        """Update only the package version without regenerating.

        Used when man page hasn't changed but package version has.

        Args:
            stored: Existing stored capability.
            binary: Re-discovered binary info.
            new_version: New package version.

        Returns:
            True if update succeeded.
        """
        try:
            # We need to re-save with updated version info
            # Parse the existing spec
            from elle.capabilities.autogen.models import GeneratedCapabilitySpec

            spec = GeneratedCapabilitySpec.model_validate_json(stored.spec_json)

            # Re-save with updated package info
            self._store.save(
                spec=spec,
                input_model_code=stored.input_model_code,
                output_model_code=stored.output_model_code,
                capability_class_code=stored.capability_class_code,
                man_page_text="",  # Empty - we'll compute same hash
                trust_level=stored.trust_level,
                validation_json=None,
                source_package=binary.package,
                package_version=new_version,
                binary_path=binary.path,
            )

            # Preserve approval status
            if stored.approved:
                self._store.approve(stored.capability_name)
            if not stored.enabled:
                self._store.disable(stored.capability_name)

            return True

        except Exception as e:
            logger.error(f"Error updating version for {stored.capability_name}: {e}")
            return False

    def check_version_mismatch(
        self,
        capability_name: str,
        current_package_version: str,
    ) -> bool:
        """Check if a capability's version mismatches the installed package.

        Args:
            capability_name: Capability name.
            current_package_version: Currently installed version.

        Returns:
            True if versions mismatch.
        """
        stored = self._store.get(capability_name)
        if not stored or not stored.package_version:
            return False

        return stored.package_version != current_package_version

    def get_stats(self) -> dict[str, Any]:
        """Get versioner statistics.

        Returns:
            Dict with stats.
        """
        return {
            "packages_tracked": len(self._package_to_capabilities),
            "capabilities_tracked": sum(
                len(caps) for caps in self._package_to_capabilities.values()
            ),
            "regeneration_stats": self._regeneration_stats,
        }

    def get_package_capabilities_summary(self) -> list[dict[str, Any]]:
        """Get summary of packages and their capabilities.

        Returns:
            List of dicts with package info.
        """
        result = []
        for package, capabilities in sorted(self._package_to_capabilities.items()):
            # Get version from first capability
            version = None
            for cap_name in capabilities:
                stored = self._store.get(cap_name)
                if stored and stored.package_version:
                    version = stored.package_version
                    break

            result.append({
                "package": package,
                "version": version,
                "capabilities": capabilities,
                "count": len(capabilities),
            })

        return result


# Module-level instance
_versioner: CapabilityVersioner | None = None


def get_versioner(store: AutogenStore | None = None) -> CapabilityVersioner:
    """Get the shared versioner instance.

    Args:
        store: Optional store override.

    Returns:
        CapabilityVersioner instance.
    """
    global _versioner
    if _versioner is None:
        _versioner = CapabilityVersioner(store=store)
    return _versioner


def reset_versioner() -> None:
    """Reset the shared versioner (for testing)."""
    global _versioner
    _versioner = None
