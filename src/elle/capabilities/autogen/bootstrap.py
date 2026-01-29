"""Bootstrap capability generation for core packages.

Automatically generates capabilities for:
1. ELLE's own dependencies
2. Core Ubuntu packages that users commonly have installed
3. Detected installed services (nginx, docker, postgresql, etc.)

This runs after ELLE installation to provide out-of-box capability coverage.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from elle.common.pydantic_compat import safe_model_dump_json

logger = logging.getLogger(__name__)


# =============================================================================
# Core Package Lists
# =============================================================================


class PackageCategory(str, Enum):
    """Categories for core packages."""

    SYSTEM = "system"  # Essential system utilities
    ESSENTIAL = "essential"  # Debian Essential packages (unremovable)
    FILE = "file"  # File management
    NETWORK = "network"  # Network tools
    TEXT = "text"  # Text processing
    PACKAGE = "package"  # Package management
    SERVICE = "service"  # Common services
    CONTAINER = "container"  # Container tools
    MEDIA = "media"  # Media processing
    ARCHIVE = "archive"  # Archive tools
    SECURITY = "security"  # Security tools


# Core packages that are almost always installed on Ubuntu
# These are safe to generate capabilities for without checking
CORE_SYSTEM_PACKAGES: tuple[tuple[str, PackageCategory], ...] = (
    # System utilities
    ("coreutils", PackageCategory.SYSTEM),  # ls, cp, mv, rm, etc.
    ("util-linux", PackageCategory.SYSTEM),  # mount, fdisk, etc.
    ("procps", PackageCategory.SYSTEM),  # ps, top, free, etc.
    ("systemd", PackageCategory.SYSTEM),  # systemctl, journalctl
    ("findutils", PackageCategory.FILE),  # find, xargs
    ("grep", PackageCategory.TEXT),  # grep
    ("sed", PackageCategory.TEXT),  # sed
    ("gawk", PackageCategory.TEXT),  # awk
    ("diffutils", PackageCategory.TEXT),  # diff, cmp
    # Network essentials
    ("iproute2", PackageCategory.NETWORK),  # ip, ss
    ("iputils-ping", PackageCategory.NETWORK),  # ping
    ("net-tools", PackageCategory.NETWORK),  # ifconfig, netstat (legacy)
    ("curl", PackageCategory.NETWORK),  # curl
    ("wget", PackageCategory.NETWORK),  # wget
    ("openssh-client", PackageCategory.NETWORK),  # ssh, scp, sftp
    ("dnsutils", PackageCategory.NETWORK),  # dig, nslookup
    # Package management
    ("apt", PackageCategory.PACKAGE),  # apt, apt-get, apt-cache
    ("dpkg", PackageCategory.PACKAGE),  # dpkg
    # Archive tools
    ("tar", PackageCategory.ARCHIVE),  # tar
    ("gzip", PackageCategory.ARCHIVE),  # gzip, gunzip
    ("bzip2", PackageCategory.ARCHIVE),  # bzip2
    ("xz-utils", PackageCategory.ARCHIVE),  # xz
    ("zip", PackageCategory.ARCHIVE),  # zip, unzip
    # Text/file utilities
    ("less", PackageCategory.TEXT),  # less
    ("file", PackageCategory.FILE),  # file (type detection)
    ("tree", PackageCategory.FILE),  # tree
    # Security
    ("gnupg", PackageCategory.SECURITY),  # gpg
    ("openssl", PackageCategory.SECURITY),  # openssl
)


# Packages to check if installed and generate capabilities for
# These are common but not universal
OPTIONAL_PACKAGES: tuple[tuple[str, PackageCategory], ...] = (
    # Services
    ("nginx", PackageCategory.SERVICE),
    ("apache2", PackageCategory.SERVICE),
    ("postgresql", PackageCategory.SERVICE),
    ("mysql-server", PackageCategory.SERVICE),
    ("redis-server", PackageCategory.SERVICE),
    ("mongodb-org", PackageCategory.SERVICE),
    # Container tools
    ("docker.io", PackageCategory.CONTAINER),
    ("docker-ce", PackageCategory.CONTAINER),
    ("podman", PackageCategory.CONTAINER),
    ("containerd", PackageCategory.CONTAINER),
    # Development
    ("git", PackageCategory.SYSTEM),
    ("make", PackageCategory.SYSTEM),
    ("gcc", PackageCategory.SYSTEM),
    ("python3", PackageCategory.SYSTEM),
    ("nodejs", PackageCategory.SYSTEM),
    # Media
    ("ffmpeg", PackageCategory.MEDIA),
    ("imagemagick", PackageCategory.MEDIA),
    # Network services
    ("ufw", PackageCategory.SECURITY),
    ("fail2ban", PackageCategory.SECURITY),
    ("wireguard-tools", PackageCategory.NETWORK),
    # Monitoring
    ("htop", PackageCategory.SYSTEM),
    ("iotop", PackageCategory.SYSTEM),
    ("iftop", PackageCategory.NETWORK),
    # Snap (if installed)
    ("snapd", PackageCategory.PACKAGE),
)


# =============================================================================
# Bootstrap Result
# =============================================================================


@dataclass
class BootstrapResult:
    """Result of bootstrap capability generation."""

    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

    # Package counts
    packages_attempted: int = 0
    packages_succeeded: int = 0
    packages_failed: int = 0
    packages_skipped: int = 0

    # Capability counts
    capabilities_generated: int = 0
    capabilities_validated: int = 0
    capabilities_saved: int = 0

    # Details
    succeeded_packages: list[str] = field(default_factory=list)
    failed_packages: list[tuple[str, str]] = field(default_factory=list)  # (name, error)
    skipped_packages: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """Get duration in seconds."""
        if self.completed_at is None:
            return (datetime.utcnow() - self.started_at).total_seconds()
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def success_rate(self) -> float:
        """Get success rate as percentage."""
        if self.packages_attempted == 0:
            return 0.0
        return (self.packages_succeeded / self.packages_attempted) * 100


# =============================================================================
# Package Detection
# =============================================================================


def is_package_installed(package_name: str) -> bool:
    """Check if a package is installed.

    Args:
        package_name: Name of the package to check.

    Returns:
        True if installed, False otherwise.
    """
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f", "${Status}", package_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "install ok installed" in result.stdout
    except Exception:
        return False


def get_elle_dependencies() -> list[str]:
    """Get ELLE's package dependencies AND the elle package itself.

    Extracts system package dependencies from ELLE's installed state,
    and includes the elle package if it's installed.

    Returns:
        List of package names ELLE depends on, plus 'elle' itself.
    """
    dependencies: list[str] = []

    # Include ELLE itself if installed
    if is_package_installed("elle"):
        dependencies.append("elle")

    # Try to get dependencies from elle package
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f", "${Depends}", "elle"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0 and result.stdout:
            # Parse dependency string
            for dep in result.stdout.split(","):
                dep = dep.strip()
                if dep:
                    # Remove version constraints
                    pkg_name = dep.split()[0].split("(")[0].strip()
                    if pkg_name:
                        dependencies.append(pkg_name)

    except Exception as e:
        logger.debug(f"Could not get ELLE package dependencies: {e}")

    # Also check common Python package system dependencies
    python_pkg_deps = [
        "python3",
        "python3-gi",  # PyGObject
        "python3-dbus",  # D-Bus bindings
    ]

    for dep in python_pkg_deps:
        if is_package_installed(dep) and dep not in dependencies:
            dependencies.append(dep)

    return dependencies


def get_installed_optional_packages() -> list[tuple[str, PackageCategory]]:
    """Get list of optional packages that are installed.

    Returns:
        List of (package_name, category) tuples for installed optional packages.
    """
    installed = []

    for pkg_name, category in OPTIONAL_PACKAGES:
        if is_package_installed(pkg_name):
            installed.append((pkg_name, category))

    return installed


def get_essential_packages() -> list[tuple[str, PackageCategory]]:
    """Get Debian Essential packages via dpkg-query.

    Essential packages are those marked as Essential=yes in dpkg database.
    These packages cannot be removed without special flags.

    Returns:
        List of (package_name, ESSENTIAL) tuples.
    """
    essential: list[tuple[str, PackageCategory]] = []
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package} ${Essential}\n"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.rsplit(" ", 1)
                if len(parts) == 2:
                    pkg_name, is_essential = parts
                    if is_essential.strip().lower() == "yes":
                        essential.append((pkg_name.strip(), PackageCategory.ESSENTIAL))
    except Exception as e:
        logger.warning(f"Error querying Essential packages: {e}")
    return essential


# =============================================================================
# Bootstrap Runner
# =============================================================================


class BootstrapRunner:
    """Runs bootstrap capability generation.

    Generates capabilities for core and optional packages in batches,
    with progress tracking and error handling.
    """

    def __init__(
        self,
        *,
        include_core: bool = True,
        include_essential: bool = True,
        include_optional: bool = True,
        include_dependencies: bool = True,
        max_concurrent: int = 1,
        skip_existing: bool = True,
    ) -> None:
        """Initialize the bootstrap runner.

        Args:
            include_core: Include core system packages.
            include_essential: Include Debian Essential packages.
            include_optional: Include detected optional packages.
            include_dependencies: Include ELLE dependencies.
            max_concurrent: Max concurrent package learning (default 1 for sequential).
            skip_existing: Skip packages that already have capabilities.
        """
        self.include_core = include_core
        self.include_essential = include_essential
        self.include_optional = include_optional
        self.include_dependencies = include_dependencies
        self.max_concurrent = max_concurrent
        self.skip_existing = skip_existing

    def get_packages_to_bootstrap(self) -> list[tuple[str, PackageCategory]]:
        """Get the full list of packages to bootstrap.

        Tier order:
        1. Core system packages (hardcoded in CORE_SYSTEM_PACKAGES)
        2. Debian Essential packages (dpkg Essential=yes)
        3. ELLE dependencies (elle package + its runtime deps)
        4. Optional packages (common but not universal)

        Returns:
            List of (package_name, category) tuples.
        """
        packages: list[tuple[str, PackageCategory]] = []
        seen: set[str] = set()

        # Tier 1: Core packages
        if self.include_core:
            for pkg_name, category in CORE_SYSTEM_PACKAGES:
                if pkg_name not in seen and is_package_installed(pkg_name):
                    packages.append((pkg_name, category))
                    seen.add(pkg_name)

        # Tier 2: Essential packages (not already in core)
        if self.include_essential:
            for pkg_name, category in get_essential_packages():
                if pkg_name not in seen and is_package_installed(pkg_name):
                    packages.append((pkg_name, category))
                    seen.add(pkg_name)

        # Tier 3: ELLE dependencies
        if self.include_dependencies:
            for pkg_name in get_elle_dependencies():
                if pkg_name not in seen and is_package_installed(pkg_name):
                    packages.append((pkg_name, PackageCategory.SYSTEM))
                    seen.add(pkg_name)

        # Tier 4: Optional packages
        if self.include_optional:
            for pkg_name, category in get_installed_optional_packages():
                if pkg_name not in seen:
                    packages.append((pkg_name, category))
                    seen.add(pkg_name)

        return packages

    async def run(self, progress_callback: Callable[[int, int, str], None] | None = None) -> BootstrapResult:
        """Run the bootstrap process.

        Args:
            progress_callback: Optional callback(current, total, package_name)

        Returns:
            BootstrapResult with statistics.
        """
        result = BootstrapResult()

        packages = self.get_packages_to_bootstrap()
        total = len(packages)
        result.packages_attempted = total

        logger.info(f"Bootstrap: {total} packages to process")

        # Process in batches
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def process_package(
            pkg_name: str,
            category: PackageCategory,
            index: int,
        ) -> tuple[str, bool, str | None, int, int, int]:
            """Process a single package."""
            async with semaphore:
                if progress_callback:
                    progress_callback(index + 1, total, pkg_name)

                try:
                    # Check if already has capabilities
                    if self.skip_existing and self._has_capabilities(pkg_name):
                        return (pkg_name, False, "skipped", 0, 0, 0)

                    # Learn the package
                    gen, val, saved = await self._learn_package(pkg_name)
                    return (pkg_name, True, None, gen, val, saved)

                except Exception as e:
                    logger.warning(f"Bootstrap failed for {pkg_name}: {e}")
                    return (pkg_name, False, str(e), 0, 0, 0)

        # Run all packages
        tasks = [process_package(pkg_name, category, i) for i, (pkg_name, category) in enumerate(packages)]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        for res in results:
            if isinstance(res, BaseException):
                result.packages_failed += 1
                continue

            pkg_name, success, error, gen, val, saved = res

            if error == "skipped":
                result.packages_skipped += 1
                result.skipped_packages.append(pkg_name)
            elif success:
                result.packages_succeeded += 1
                result.succeeded_packages.append(pkg_name)
                result.capabilities_generated += gen
                result.capabilities_validated += val
                result.capabilities_saved += saved
            else:
                result.packages_failed += 1
                result.failed_packages.append((pkg_name, error or "Unknown error"))

        result.completed_at = datetime.utcnow()
        logger.info(
            f"Bootstrap complete: {result.packages_succeeded}/{total} succeeded, "
            f"{result.capabilities_saved} capabilities saved"
        )

        return result

    def _has_capabilities(self, package_name: str) -> bool:
        """Check if package already has generated capabilities."""
        try:
            from elle.capabilities.autogen import get_store

            store = get_store()
            caps = store.list_by_package(package_name)
            return len(caps) > 0
        except Exception:
            return False

    async def _learn_package(
        self,
        package_name: str,
    ) -> tuple[int, int, int]:
        """Learn a single package and return counts.

        Returns:
            Tuple of (generated, validated, saved) counts.
        """
        from elle.capabilities.autogen import (
            GeneratedCapabilitySpec,
            get_store,
            validate_capability,
        )
        from elle.capabilities.autogen.intelligence import get_aggregator
        from elle.rag.llm import LLM
        from elle.rag.prompts import get_composer

        # Gather intelligence
        aggregator = get_aggregator()
        intel = await aggregator.gather(package_name)

        if not intel.extraction_sources:
            raise ValueError(f"No intelligence sources for {package_name}")

        # Generate capabilities via LLM
        context = aggregator.to_llm_context(intel)
        composer = get_composer()
        system_prompt = composer.compose("learn_package")

        user_prompt = f"""Generate capabilities for the following package:

{context}

Focus on the most common and useful operations.
Output valid JSON only."""

        import asyncio

        llm = LLM()
        capabilities_json = await asyncio.to_thread(
            llm.generate_json,
            user_prompt,
            system=system_prompt,
        )

        # Parse capabilities
        capabilities: list[GeneratedCapabilitySpec] = []

        if "capabilities" in capabilities_json:
            for cap_data in capabilities_json["capabilities"]:
                try:
                    if "source_command" not in cap_data:
                        cap_data["source_command"] = intel.primary_binary or package_name

                    if "side_effects" in cap_data:
                        cap_data["side_effects"] = tuple(cap_data["side_effects"])
                    if "input_fields" in cap_data:
                        for field in cap_data["input_fields"]:
                            if "constraints" not in field:
                                field["constraints"] = {}
                        cap_data["input_fields"] = tuple(cap_data["input_fields"])
                    if "output_fields" in cap_data:
                        cap_data["output_fields"] = tuple(cap_data["output_fields"])

                    spec = GeneratedCapabilitySpec(**cap_data)
                    capabilities.append(spec)
                except Exception as e:
                    logger.debug(f"Invalid capability spec: {e}")
                    continue

        generated = len(capabilities)

        # Validate
        validated_specs = []
        for spec in capabilities:
            try:
                validation = validate_capability(spec, intel=intel)
                if validation.overall_passed:
                    validated_specs.append((spec, validation))
            except Exception:
                continue

        validated = len(validated_specs)

        # Save
        saved = 0
        store = get_store()

        for spec, validation in validated_specs:
            try:
                from elle.capabilities.autogen.factory import (
                    generate_capability_class_code,
                    generate_input_model_code,
                    generate_output_model_code,
                )

                input_code = generate_input_model_code(spec)
                output_code = generate_output_model_code(spec)
                cap_code = generate_capability_class_code(spec)

                store.save(
                    spec=spec,
                    input_model_code=input_code,
                    output_model_code=output_code,
                    capability_class_code=cap_code,
                    man_page_text=context,
                    trust_level=validation.trust_level,
                    validation_json=safe_model_dump_json(validation),
                    source_package=intel.metadata.name,
                    package_version=intel.metadata.version,
                    binary_path=None,
                )
                saved += 1

            except Exception as e:
                logger.debug(f"Failed to save capability: {e}")

        return generated, validated, saved


# =============================================================================
# Convenience Functions
# =============================================================================


async def run_bootstrap(
    *,
    include_core: bool = True,
    include_essential: bool = True,
    include_optional: bool = True,
    include_dependencies: bool = True,
    max_concurrent: int = 1,
    skip_existing: bool = True,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> BootstrapResult:
    """Run bootstrap capability generation.

    Args:
        include_core: Include core system packages.
        include_essential: Include Debian Essential packages.
        include_optional: Include detected optional packages.
        include_dependencies: Include ELLE dependencies.
        max_concurrent: Max concurrent package learning (default 1 for sequential).
        skip_existing: Skip packages with existing capabilities.
        progress_callback: Optional progress callback.

    Returns:
        BootstrapResult with statistics.
    """
    runner = BootstrapRunner(
        include_core=include_core,
        include_essential=include_essential,
        include_optional=include_optional,
        include_dependencies=include_dependencies,
        max_concurrent=max_concurrent,
        skip_existing=skip_existing,
    )
    return await runner.run(progress_callback)


def get_bootstrap_packages() -> list[tuple[str, str]]:
    """Get list of packages that would be bootstrapped.

    Returns:
        List of (package_name, category) tuples.
    """
    runner = BootstrapRunner()
    return [(pkg, cat.value) for pkg, cat in runner.get_packages_to_bootstrap()]


# =============================================================================
# Bootstrap State Tracking
# =============================================================================


_BOOTSTRAP_STATE_FILE = Path("/var/lib/elle/bootstrap_state.json")


def get_bootstrap_state() -> dict[str, Any]:
    """Get the current bootstrap state.

    Returns:
        Dict with bootstrap state info.
    """
    import json

    if not _BOOTSTRAP_STATE_FILE.exists():
        return {"completed": False, "last_run": None, "version": None}

    try:
        data = json.loads(_BOOTSTRAP_STATE_FILE.read_text())
        return dict(data) if isinstance(data, dict) else {"completed": False, "last_run": None, "version": None}
    except Exception:
        return {"completed": False, "last_run": None, "version": None}


def set_bootstrap_complete(result: BootstrapResult) -> None:
    """Mark bootstrap as complete.

    Args:
        result: The bootstrap result to record.
    """
    import json

    from elle import __version__

    state = {
        "completed": True,
        "last_run": result.completed_at.isoformat() if result.completed_at else None,
        "version": __version__,
        "packages_succeeded": result.packages_succeeded,
        "capabilities_saved": result.capabilities_saved,
        "duration_seconds": result.duration_seconds,
    }

    try:
        _BOOTSTRAP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BOOTSTRAP_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save bootstrap state: {e}")


def should_run_bootstrap() -> bool:
    """Check if bootstrap should run.

    Returns True if:
    - Never completed before
    - ELLE version changed since last run

    Returns:
        True if bootstrap should run.
    """
    from elle import __version__

    state = get_bootstrap_state()

    if not state.get("completed"):
        return True

    return state.get("version") != __version__
