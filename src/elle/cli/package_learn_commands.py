"""REPL commands for package capability learning.

Provides /learn command handlers for:
- Learning packages and generating capabilities
- Listing learned packages
- Showing generated capabilities
- Managing generated capabilities

This is the new /learn command that generates capabilities from
installed packages using multi-source intelligence extraction.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from elle.common.pydantic_compat import safe_model_dump_json

if TYPE_CHECKING:
    from elle.common.session import Session

logger = logging.getLogger(__name__)


# =============================================================================
# Response Models
# =============================================================================


class LearnResult(BaseModel, frozen=True):
    """Result of package learning."""

    package_name: str
    capabilities_generated: int
    capabilities_validated: int
    capabilities_saved: int
    extraction_sources: tuple[str, ...]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# =============================================================================
# Command Handlers
# =============================================================================


async def handle_learn_command(args: str, session: Session | None = None) -> str:
    """Handle /learn command for package capability generation.

    Usage:
        /learn                      - Show help
        /learn <package>            - Learn package and generate capabilities
        /learn list                 - List packages with generated capabilities
        /learn show <package>       - Show capabilities for a package
        /learn approve <capability> - Approve a capability for use
        /learn delete <capability>  - Delete a generated capability
        /learn refresh <package>    - Re-learn a package (regenerate)

    Args:
        args: Command arguments.
        session: Optional session for context.

    Returns:
        Response string.
    """
    args = args.strip()

    if not args:
        return _get_help()

    parts = args.split(None, 1)
    subcommand = parts[0].lower()
    subargs = parts[1] if len(parts) > 1 else ""

    async def _help_handler(_: str) -> str:
        return _get_help()

    handlers = {
        "list": _handle_list,
        "show": _handle_show,
        "approve": _handle_approve,
        "delete": _handle_delete,
        "refresh": _handle_refresh,
        "bootstrap": _handle_bootstrap,
        "status": _handle_bootstrap_status,
        "help": _help_handler,
    }

    if subcommand in handlers:
        return await handlers[subcommand](subargs)

    # Check for --all flag (learn all installed packages)
    if subcommand == "--all" or args.startswith("--all"):
        return await _handle_learn_all(args)

    # Default: learn package
    return await _handle_learn(args)


def _get_help() -> str:
    """Get help text for /learn command."""
    return """
/learn - Package capability learning

Generates typed capabilities from installed packages using multi-source
intelligence extraction (dpkg, shell completions, man pages, etc.)

Usage:
  /learn <package>           Learn a package and generate capabilities
  /learn list                List packages with generated capabilities
  /learn show <package>      Show generated capabilities for a package
  /learn approve <name>      Approve a capability for use
  /learn delete <name>       Delete a generated capability
  /learn refresh <package>   Re-learn a package (regenerate capabilities)

Batch learning:
  /learn --all               Learn ALL installed packages (may take a while)
  /learn --all --dry-run     Preview which packages would be learned
  /learn bootstrap           Learn core + optional packages
  /learn bootstrap --core    Only core system packages
  /learn bootstrap --deps    Only ELLE dependencies
  /learn status              Show bootstrap status

Examples:
  /learn ffmpeg              Learn ffmpeg and generate media capabilities
  /learn nginx               Learn nginx for web server capabilities
  /learn --all               Learn everything (full system capability coverage)
  /learn list                Show all learned packages
  /learn show ffmpeg         Show ffmpeg capabilities
  /learn approve ffmpeg.convert   Approve the convert capability

Natural language alternatives:
  "learn ffmpeg"             Same as /learn ffmpeg
  "figure out how to use nginx"
  "teach me about docker"

Note: Generated capabilities require approval before use in automated
workflows. Core commands (systemctl, docker, etc.) are auto-approved.
""".strip()


async def _handle_learn(args: str) -> str:
    """Learn a package and generate capabilities.

    Args:
        args: Package name and optional flags.

    Returns:
        Response string.
    """
    # Parse args
    parts = args.split()
    package_name = parts[0] if parts else ""

    if not package_name:
        return "Usage: /learn <package>"

    # Check for flags
    force_refresh = "--refresh" in parts or "--force" in parts
    dry_run = "--dry-run" in parts

    try:
        result = await _learn_package(package_name, force_refresh=force_refresh, dry_run=dry_run)
        return _format_learn_result(result, dry_run=dry_run)

    except Exception as e:
        logger.error(f"Error learning package: {e}")
        return f"Error learning package '{package_name}': {e}"


async def _learn_package(
    package_name: str,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> LearnResult:
    """Learn a package and generate capabilities.

    Args:
        package_name: Package to learn.
        force_refresh: Force regeneration even if already learned.
        dry_run: Don't save capabilities, just show what would be generated.

    Returns:
        LearnResult with statistics.
    """
    from elle.capabilities.autogen import (
        GeneratedCapabilitySpec,
        discover_binary,
        get_store,
        validate_capability,
    )
    from elle.capabilities.autogen.intelligence import (
        PackageIntelligence,
        get_aggregator,
    )
    from elle.rag.llm import generate_json
    from elle.rag.prompts import get_composer

    errors: list[str] = []
    warnings: list[str] = []

    # Step 1: Discover binary
    binary = discover_binary(package_name)
    if not binary:
        # Try using the package name directly for intelligence gathering
        logger.info(f"Binary not found, trying package name: {package_name}")

    # Step 2: Gather intelligence
    aggregator = get_aggregator()
    intel: PackageIntelligence = await aggregator.gather(package_name)

    if not intel.extraction_sources:
        errors.append("No intelligence sources available")
        return LearnResult(
            package_name=package_name,
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=(),
            errors=tuple(errors),
        )

    # Step 3: Generate capabilities via LLM
    context = aggregator.to_llm_context(intel)
    composer = get_composer()
    system_prompt = composer.compose("learn_package")

    user_prompt = f"""Generate capabilities for the following package:

{context}

Generate a comprehensive set of capabilities covering the most common and useful operations.
Focus on operations that are well-documented in the extracted intelligence.
Output valid JSON only."""

    try:
        capabilities_json = await generate_json(
            user_prompt,
            system_prompt=system_prompt,
        )
    except Exception as e:
        errors.append(f"LLM generation failed: {e}")
        return LearnResult(
            package_name=package_name,
            capabilities_generated=0,
            capabilities_validated=0,
            capabilities_saved=0,
            extraction_sources=intel.extraction_sources,
            errors=tuple(errors),
        )

    # Step 4: Parse and validate capabilities
    capabilities: list[GeneratedCapabilitySpec] = []

    if "capabilities" in capabilities_json:
        for cap_data in capabilities_json["capabilities"]:
            try:
                # Ensure required fields
                if "source_command" not in cap_data:
                    cap_data["source_command"] = intel.primary_binary or package_name

                # Convert lists to tuples for frozen model
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
                warnings.append(f"Invalid capability spec: {e}")
                continue

    generated_count = len(capabilities)

    # Step 5: Validate capabilities
    validated: list[tuple[GeneratedCapabilitySpec, any]] = []

    for spec in capabilities:
        try:
            validation = validate_capability(spec)
            if validation.overall_passed:
                validated.append((spec, validation))
            else:
                stage_errors = [err for stage in validation.stages for err in stage.errors]
                warnings.append(f"{spec.name}: Validation failed - {', '.join(stage_errors)}")
        except Exception as e:
            warnings.append(f"{spec.name}: Validation error - {e}")

    validated_count = len(validated)

    # Step 6: Save capabilities (unless dry run)
    saved_count = 0

    if not dry_run and validated:
        store = get_store()

        for spec, validation in validated:
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
                    man_page_text=context,  # Use context as content hash source
                    trust_level=validation.trust_level,
                    validation_json=safe_model_dump_json(validation),
                    source_package=intel.metadata.name,
                    package_version=intel.metadata.version,
                    binary_path=binary.path if binary else None,
                )
                saved_count += 1

            except Exception as e:
                warnings.append(f"Failed to save {spec.name}: {e}")

    return LearnResult(
        package_name=package_name,
        capabilities_generated=generated_count,
        capabilities_validated=validated_count,
        capabilities_saved=saved_count,
        extraction_sources=intel.extraction_sources,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _format_learn_result(result: LearnResult, dry_run: bool = False) -> str:
    """Format LearnResult for display."""
    from elle.cli.terminal.renderer import Colors

    lines = [
        f"{Colors.BOLD}Learned '{result.package_name}'{Colors.RESET}",
        "",
    ]

    # Statistics
    lines.append(f"  Sources: {', '.join(result.extraction_sources)}")
    lines.append(f"  Generated: {result.capabilities_generated} capabilities")
    lines.append(f"  Validated: {result.capabilities_validated} capabilities")

    if dry_run:
        lines.append(f"  {Colors.YELLOW}(dry-run: nothing saved){Colors.RESET}")
    else:
        lines.append(f"  Saved: {result.capabilities_saved} capabilities")

    # Errors
    if result.errors:
        lines.append("")
        lines.append(f"{Colors.RED}Errors:{Colors.RESET}")
        for error in result.errors:
            lines.append(f"  {Colors.RED}!{Colors.RESET} {error}")

    # Warnings
    if result.warnings:
        lines.append("")
        lines.append(f"{Colors.YELLOW}Warnings:{Colors.RESET}")
        for warning in result.warnings[:5]:
            lines.append(f"  {Colors.YELLOW}*{Colors.RESET} {warning}")
        if len(result.warnings) > 5:
            lines.append(f"  ... and {len(result.warnings) - 5} more warnings")

    # Next steps
    if result.capabilities_saved > 0:
        lines.append("")
        lines.append(f"{Colors.DIM}Next steps:{Colors.RESET}")
        lines.append(f"  /learn show {result.package_name}  - View generated capabilities")
        lines.append("  /learn approve <name>   - Approve for automated use")

    return "\n".join(lines)


async def _handle_list(args: str) -> str:
    """List packages with generated capabilities."""
    from elle.capabilities.autogen import get_store
    from elle.cli.terminal.renderer import Colors

    store = get_store()

    try:
        packages = store.list_packages_with_capabilities()

        if not packages:
            return "No packages have generated capabilities yet.\nUse '/learn <package>' to learn one."

        lines = [f"{Colors.BOLD}Packages with generated capabilities:{Colors.RESET}", ""]

        for pkg_name, pkg_version in packages:
            caps = store.list_by_package(pkg_name)
            approved = sum(1 for c in caps if c.approved)
            lines.append(f"  {pkg_name:30} v{pkg_version or 'unknown':12} ({len(caps)} caps, {approved} approved)")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error listing packages: {e}")
        return f"Error listing packages: {e}"


async def _handle_show(args: str) -> str:
    """Show capabilities for a package."""
    from elle.capabilities.autogen import GeneratedCapabilitySpec, get_store
    from elle.cli.terminal.renderer import Colors

    package_name = args.strip()
    if not package_name:
        return "Usage: /learn show <package>"

    store = get_store()

    try:
        caps = store.list_by_package(package_name)

        if not caps:
            # Try by command name
            caps = store.list_by_command(package_name)

        if not caps:
            return f"No capabilities found for '{package_name}'"

        lines = [
            f"{Colors.BOLD}Capabilities for '{package_name}':{Colors.RESET}",
            "",
        ]

        for stored in caps:
            try:
                spec = GeneratedCapabilitySpec.model_validate_json(stored.spec_json)

                # Status indicators
                status = []
                if stored.approved:
                    status.append(f"{Colors.GREEN}approved{Colors.RESET}")
                else:
                    status.append(f"{Colors.YELLOW}pending{Colors.RESET}")
                if not stored.enabled:
                    status.append(f"{Colors.RED}disabled{Colors.RESET}")

                status_str = ", ".join(status)

                lines.append(f"  {Colors.CYAN}{spec.name}{Colors.RESET}")
                lines.append(f"    {spec.description[:60]}...")
                lines.append(f"    Risk: {spec.risk_level}, Trust: {stored.trust_level.value}")
                lines.append(f"    Status: {status_str}")
                lines.append(f"    Template: {spec.command_template[:50]}...")
                lines.append("")

            except Exception as e:
                lines.append(f"  {stored.capability_name}: Error loading spec - {e}")
                lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error showing capabilities: {e}")
        return f"Error: {e}"


async def _handle_approve(args: str) -> str:
    """Approve a capability for use."""
    from elle.capabilities.autogen import get_store
    from elle.cli.terminal.renderer import Colors

    cap_name = args.strip()
    if not cap_name:
        return "Usage: /learn approve <capability_name>"

    store = get_store()

    try:
        if store.approve(cap_name):
            return f"{Colors.GREEN}Approved '{cap_name}' for use.{Colors.RESET}"
        else:
            return f"Capability '{cap_name}' not found."

    except Exception as e:
        logger.error(f"Error approving capability: {e}")
        return f"Error: {e}"


async def _handle_delete(args: str) -> str:
    """Delete a generated capability."""
    from elle.capabilities.autogen import get_store
    from elle.cli.terminal.renderer import Colors

    cap_name = args.strip()
    if not cap_name:
        return "Usage: /learn delete <capability_name>"

    store = get_store()

    try:
        if store.delete(cap_name):
            return f"{Colors.GREEN}Deleted capability '{cap_name}'.{Colors.RESET}"
        else:
            return f"Capability '{cap_name}' not found."

    except Exception as e:
        logger.error(f"Error deleting capability: {e}")
        return f"Error: {e}"


async def _handle_refresh(args: str) -> str:
    """Re-learn a package (regenerate capabilities)."""
    package_name = args.strip()
    if not package_name:
        return "Usage: /learn refresh <package>"

    return await _handle_learn(f"{package_name} --refresh")


async def _handle_bootstrap(args: str) -> str:
    """Run bootstrap capability generation for core packages.

    Args:
        args: Flags like --core, --deps, --optional

    Returns:
        Status message.
    """
    from elle.capabilities.autogen.bootstrap import (
        get_bootstrap_packages,
        run_bootstrap,
        set_bootstrap_complete,
    )
    from elle.cli.terminal.renderer import Colors

    # Parse flags
    parts = args.lower().split()
    include_core = "--core" in parts or not parts or "--all" in parts
    include_deps = "--deps" in parts or not parts or "--all" in parts
    include_optional = "--optional" in parts or not parts or "--all" in parts
    dry_run = "--dry-run" in parts

    if dry_run:
        # Just show what would be bootstrapped
        packages = get_bootstrap_packages()
        lines = [
            f"{Colors.BOLD}Bootstrap would learn {len(packages)} packages:{Colors.RESET}",
            "",
        ]

        # Group by category
        by_category: dict[str, list[str]] = {}
        for pkg_name, category in packages:
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(pkg_name)

        for category in sorted(by_category.keys()):
            lines.append(f"  {Colors.CYAN}{category}:{Colors.RESET}")
            for pkg in sorted(by_category[category]):
                lines.append(f"    - {pkg}")
            lines.append("")

        lines.append(f"{Colors.DIM}Run '/learn bootstrap' without --dry-run to proceed.{Colors.RESET}")
        return "\n".join(lines)

    # Run bootstrap with progress indicator
    lines = [
        f"{Colors.BOLD}Starting bootstrap capability generation...{Colors.RESET}",
        "",
    ]

    current_package = [""]

    def progress(current: int, total: int, pkg_name: str) -> None:
        current_package[0] = pkg_name
        # Progress is logged, not returned (async)
        logger.info(f"Bootstrap progress: {current}/{total} - {pkg_name}")

    try:
        result = await run_bootstrap(
            include_core=include_core,
            include_optional=include_optional,
            include_dependencies=include_deps,
            skip_existing=True,
            progress_callback=progress,
        )

        # Save state
        set_bootstrap_complete(result)

        # Format result
        lines.append(f"{Colors.GREEN}Bootstrap complete!{Colors.RESET}")
        lines.append("")
        lines.append(f"  Packages attempted: {result.packages_attempted}")
        lines.append(f"  Succeeded: {Colors.GREEN}{result.packages_succeeded}{Colors.RESET}")
        if result.packages_failed > 0:
            lines.append(f"  Failed: {Colors.RED}{result.packages_failed}{Colors.RESET}")
        if result.packages_skipped > 0:
            lines.append(f"  Skipped (existing): {Colors.DIM}{result.packages_skipped}{Colors.RESET}")
        lines.append("")
        lines.append(f"  Capabilities generated: {result.capabilities_generated}")
        lines.append(f"  Capabilities validated: {result.capabilities_validated}")
        lines.append(f"  Capabilities saved: {Colors.GREEN}{result.capabilities_saved}{Colors.RESET}")
        lines.append("")
        lines.append(f"  Duration: {result.duration_seconds:.1f}s")

        if result.failed_packages:
            lines.append("")
            lines.append(f"{Colors.YELLOW}Failed packages:{Colors.RESET}")
            for pkg_name, error in result.failed_packages[:10]:
                lines.append(f"  - {pkg_name}: {error[:50]}")
            if len(result.failed_packages) > 10:
                lines.append(f"  ... and {len(result.failed_packages) - 10} more")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Bootstrap failed: {e}")
        return f"{Colors.RED}Bootstrap failed: {e}{Colors.RESET}"


async def _handle_bootstrap_status(args: str) -> str:
    """Show bootstrap status."""
    from elle.capabilities.autogen.bootstrap import (
        get_bootstrap_packages,
        get_bootstrap_state,
        should_run_bootstrap,
    )
    from elle.cli.terminal.renderer import Colors

    state = get_bootstrap_state()
    packages = get_bootstrap_packages()

    lines = [
        f"{Colors.BOLD}Bootstrap Status{Colors.RESET}",
        "",
    ]

    if state.get("completed"):
        lines.append(f"  Status: {Colors.GREEN}Completed{Colors.RESET}")
        lines.append(f"  Last run: {state.get('last_run', 'unknown')}")
        lines.append(f"  ELLE version: {state.get('version', 'unknown')}")
        lines.append(f"  Packages learned: {state.get('packages_succeeded', 0)}")
        lines.append(f"  Capabilities saved: {state.get('capabilities_saved', 0)}")
    else:
        lines.append(f"  Status: {Colors.YELLOW}Not completed{Colors.RESET}")

    lines.append("")
    lines.append(f"  Available packages: {len(packages)}")

    if should_run_bootstrap():
        lines.append("")
        lines.append(f"{Colors.DIM}Run '/learn bootstrap' to generate capabilities for core packages.{Colors.RESET}")

    return "\n".join(lines)


async def _handle_learn_all(args: str) -> str:
    """Learn ALL installed packages.

    This discovers every installed package on the system and generates
    capabilities for each. This can take a significant amount of time.

    Args:
        args: Flags like --dry-run, --skip-existing, --max-concurrent

    Returns:
        Status message.
    """
    from elle.cli.terminal.renderer import Colors

    # Parse flags
    parts = args.lower().split()
    dry_run = "--dry-run" in parts
    skip_existing = "--skip-existing" not in parts or True  # Default to skip
    no_skip = "--no-skip" in parts

    # Parse max concurrent
    import contextlib

    max_concurrent = 3
    for part in parts:
        if part.startswith("--max-concurrent="):
            with contextlib.suppress(ValueError):
                max_concurrent = int(part.split("=")[1])

    if no_skip:
        skip_existing = False

    try:
        all_packages = await _get_all_installed_packages()
    except Exception as e:
        return f"{Colors.RED}Failed to list installed packages: {e}{Colors.RESET}"

    if not all_packages:
        return "No installed packages found."

    # Filter to packages with binaries/man pages
    learnable_packages = await _filter_learnable_packages(all_packages)

    if dry_run:
        lines = [
            f"{Colors.BOLD}Would learn {len(learnable_packages)} packages "
            f"(out of {len(all_packages)} total installed):{Colors.RESET}",
            "",
        ]

        # Show first 50 packages
        for pkg_name in sorted(learnable_packages)[:50]:
            lines.append(f"  - {pkg_name}")

        if len(learnable_packages) > 50:
            lines.append(f"  ... and {len(learnable_packages) - 50} more")

        lines.append("")
        lines.append(f"{Colors.DIM}Run '/learn --all' without --dry-run to proceed.{Colors.RESET}")
        lines.append(f"{Colors.DIM}Warning: This may take considerable time.{Colors.RESET}")
        return "\n".join(lines)

    # Run full system learning
    lines = [
        f"{Colors.BOLD}Learning all {len(learnable_packages)} packages...{Colors.RESET}",
        "",
        f"{Colors.DIM}This may take a while. Progress will be logged.{Colors.RESET}",
        "",
    ]

    try:
        result = await _run_full_system_learn(
            learnable_packages,
            skip_existing=skip_existing,
            max_concurrent=max_concurrent,
        )

        lines.append(f"{Colors.GREEN}Complete!{Colors.RESET}")
        lines.append("")
        lines.append(f"  Packages attempted: {result['attempted']}")
        lines.append(f"  Succeeded: {Colors.GREEN}{result['succeeded']}{Colors.RESET}")
        if result["failed"] > 0:
            lines.append(f"  Failed: {Colors.RED}{result['failed']}{Colors.RESET}")
        if result["skipped"] > 0:
            lines.append(f"  Skipped (existing): {Colors.DIM}{result['skipped']}{Colors.RESET}")
        lines.append("")
        lines.append(f"  Capabilities generated: {result['capabilities_generated']}")
        lines.append(f"  Duration: {result['duration']:.1f}s")

        if result["failures"]:
            lines.append("")
            lines.append(f"{Colors.YELLOW}Some failures (first 10):{Colors.RESET}")
            for pkg_name, error in result["failures"][:10]:
                lines.append(f"  - {pkg_name}: {error[:40]}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Full system learn failed: {e}")
        return f"{Colors.RED}Learn --all failed: {e}{Colors.RESET}"


async def _get_all_installed_packages() -> list[str]:
    """Get list of all installed packages from dpkg.

    Returns:
        List of package names.
    """
    import asyncio
    import subprocess

    result = await asyncio.to_thread(
        subprocess.run,
        ["dpkg-query", "-W", "-f=${Package}\n"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"dpkg-query failed: {result.stderr}")

    packages = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
    return packages


async def _filter_learnable_packages(packages: list[str]) -> list[str]:
    """Filter packages to those that have binaries or man pages.

    Args:
        packages: Full list of package names.

    Returns:
        Filtered list of packages that can be learned.
    """
    import asyncio
    import subprocess
    from pathlib import Path

    learnable: list[str] = []
    bin_dirs = ["/usr/bin", "/usr/sbin", "/bin", "/sbin"]
    man_dirs = ["/usr/share/man/man1", "/usr/share/man/man8"]

    # Check in batches for efficiency
    for pkg in packages:
        try:
            # Get files installed by package
            result = await asyncio.to_thread(
                subprocess.run,
                ["dpkg-query", "-L", pkg],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                continue

            files = result.stdout.strip().split("\n")

            # Check if package has binaries or man pages
            has_binary = any(
                any(f.startswith(d + "/") for d in bin_dirs)
                for f in files
                if Path(f).suffix == "" and not f.endswith("/")
            )

            has_manpage = any(any(f.startswith(d + "/") for d in man_dirs) for f in files)

            if has_binary or has_manpage:
                learnable.append(pkg)

        except Exception:
            continue

    return learnable


async def _run_full_system_learn(
    packages: list[str],
    skip_existing: bool = True,
    max_concurrent: int = 3,
) -> dict:
    """Run learning on all specified packages.

    Args:
        packages: List of package names to learn.
        skip_existing: Skip packages that already have capabilities.
        max_concurrent: Max concurrent learning operations.

    Returns:
        Dict with statistics.
    """
    import asyncio
    from datetime import datetime

    from elle.capabilities.autogen import get_store

    store = get_store()
    start_time = datetime.utcnow()

    # Get existing packages to skip
    existing_packages: set[str] = set()
    if skip_existing:
        for pkg_name, _ in store.list_packages_with_capabilities():
            existing_packages.add(pkg_name)

    # Filter out existing
    to_learn = [p for p in packages if p not in existing_packages]
    skipped = len(packages) - len(to_learn)

    # Track results
    attempted = 0
    succeeded = 0
    failed = 0
    capabilities_generated = 0
    failures: list[tuple[str, str]] = []

    # Learn in batches with semaphore
    semaphore = asyncio.Semaphore(max_concurrent)

    async def learn_one(pkg_name: str) -> tuple[bool, int, str | None]:
        async with semaphore:
            try:
                result = await _learn_package(pkg_name, force_refresh=False, dry_run=False)
                if result.errors:
                    return False, 0, result.errors[0]
                return True, result.capabilities_saved, None
            except Exception as e:
                return False, 0, str(e)

    # Process packages
    tasks = [learn_one(pkg) for pkg in to_learn]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for pkg_name, res in zip(to_learn, results, strict=True):
        attempted += 1
        if isinstance(res, Exception):
            failed += 1
            failures.append((pkg_name, str(res)))
        else:
            success, caps, error = res
            if success:
                succeeded += 1
                capabilities_generated += caps
            else:
                failed += 1
                if error:
                    failures.append((pkg_name, error))

        # Log progress periodically
        if attempted % 10 == 0:
            logger.info(f"Learn --all progress: {attempted}/{len(to_learn)} (+{succeeded} success, +{failed} fail)")

    end_time = datetime.utcnow()
    duration = (end_time - start_time).total_seconds()

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "capabilities_generated": capabilities_generated,
        "duration": duration,
        "failures": failures,
    }


# =============================================================================
# Synchronous wrapper
# =============================================================================


def handle_learn_command_sync(args: str, session: Session | None = None) -> str:
    """Synchronous version of handle_learn_command."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(handle_learn_command(args, session))
