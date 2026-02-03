"""Binary and man page discovery.

Discovers installed binaries and their man pages for capability generation.

Methods:
1. dpkg -l / apt list --installed - List installed packages
2. which <cmd> / PATH scanning - Find binaries
3. dpkg -S <binary> - Map binary to package
4. man -w <cmd> - Find man page path
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from elle.capabilities.autogen.models import (
    DiscoveryResult,
    InstalledBinary,
)

# Re-export models for convenience
__all__ = [
    "DiscoveryResult",
    "InstalledBinary",
    "discover_all_binaries",
    "discover_binary",
    "discover_specific_commands",
]

logger = logging.getLogger(__name__)

# Common binary directories
BINARY_DIRS = [
    "/usr/bin",
    "/usr/sbin",
    "/bin",
    "/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
]

# Skip these binaries (too dangerous or not useful)
SKIP_BINARIES = frozenset(
    {
        # Dangerous
        "rm",
        "dd",
        "mkfs",
        "fdisk",
        "parted",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "init",
        # Shell internals
        "sh",
        "bash",
        "zsh",
        "dash",
        "csh",
        "tcsh",
        "fish",
        # Too generic
        "test",
        "true",
        "false",
        "[",
        ":",
        # Package managers (complex)
        "apt-get",
        "dpkg",
        "aptitude",
    }
)


def find_binary(name: str) -> str | None:
    """Find the path to a binary.

    Args:
        name: Binary name.

    Returns:
        Full path or None if not found.
    """
    try:
        result = subprocess.run(
            ["which", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    # Fallback: check common directories
    for dir_path in BINARY_DIRS:
        path = Path(dir_path) / name
        if path.exists() and path.is_file() and os.access(path, os.X_OK):
            return str(path)

    return None


def get_package_for_binary(binary_path: str) -> tuple[str | None, str | None]:
    """Get the package that provides a binary.

    Args:
        binary_path: Full path to binary.

    Returns:
        Tuple of (package_name, version) or (None, None).
    """
    try:
        # Try dpkg -S
        result = subprocess.run(
            ["dpkg", "-S", binary_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output format: "package: /path/to/file"
            line = result.stdout.strip()
            if ":" in line:
                package = line.split(":")[0].strip()

                # Get version
                version_result = subprocess.run(
                    ["dpkg-query", "-W", "-f=${Version}", package],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                version = version_result.stdout.strip() if version_result.returncode == 0 else None

                return package, version
    except Exception as e:
        logger.debug(f"Failed to get package for {binary_path}: {e}")

    return None, None


def find_man_page(name: str) -> tuple[bool, int | None, str | None]:
    """Find man page for a command.

    Args:
        name: Command name.

    Returns:
        Tuple of (exists, section, path).
    """
    try:
        # man -w returns the path to the man page
        result = subprocess.run(
            ["man", "-w", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            path = result.stdout.strip()
            if path:
                # Extract section from path (e.g., /usr/share/man/man1/ls.1.gz)
                section = None
                parts = Path(path).name.split(".")
                for part in parts:
                    if part.isdigit():
                        section = int(part)
                        break
                return True, section, path
    except Exception as e:
        logger.debug(f"Failed to find man page for {name}: {e}")

    return False, None, None


def discover_binary(name: str) -> InstalledBinary | None:
    """Discover information about a single binary.

    Args:
        name: Binary name.

    Returns:
        InstalledBinary or None if not found.
    """
    if name in SKIP_BINARIES:
        return None

    path = find_binary(name)
    if not path:
        return None

    package, version = get_package_for_binary(path)
    man_available, man_section, man_path = find_man_page(name)

    return InstalledBinary(
        name=name,
        path=path,
        package=package,
        version=version,
        man_available=man_available,
        man_section=man_section,
        man_path=man_path,
    )


def scan_binary_directory(dir_path: str) -> list[str]:
    """Scan a directory for executable binaries.

    Args:
        dir_path: Directory path.

    Returns:
        List of binary names.
    """
    binaries: list[str] = []
    path = Path(dir_path)

    if not path.exists():
        return binaries

    try:
        for entry in path.iterdir():
            if entry.is_file() and os.access(entry, os.X_OK):
                name = entry.name
                # Skip hidden files and backups
                if name.startswith(".") or name.endswith("~"):
                    continue
                # Skip if in skip list
                if name in SKIP_BINARIES:
                    continue
                binaries.append(name)
    except PermissionError:
        logger.debug(f"Permission denied scanning {dir_path}")
    except Exception as e:
        logger.debug(f"Error scanning {dir_path}: {e}")

    return binaries


def discover_all_binaries(
    directories: list[str] | None = None,
    require_man: bool = True,
    limit: int | None = None,
) -> DiscoveryResult:
    """Discover all binaries with man pages.

    Args:
        directories: Directories to scan (defaults to BINARY_DIRS).
        require_man: Only return binaries with man pages.
        limit: Maximum number of binaries to return.

    Returns:
        DiscoveryResult with discovered binaries.
    """
    directories = directories or BINARY_DIRS
    seen_names: set[str] = set()
    binaries: list[InstalledBinary] = []
    total_scanned = 0
    with_man = 0

    for dir_path in directories:
        names = scan_binary_directory(dir_path)

        for name in names:
            if name in seen_names:
                continue
            seen_names.add(name)
            total_scanned += 1

            binary = discover_binary(name)
            if binary:
                if binary.man_available:
                    with_man += 1

                if not require_man or binary.man_available:
                    binaries.append(binary)

                    if limit and len(binaries) >= limit:
                        return DiscoveryResult(
                            binaries=tuple(binaries),
                            total_scanned=total_scanned,
                            with_man_pages=with_man,
                            timestamp=datetime.now(timezone.utc),
                        )

    return DiscoveryResult(
        binaries=tuple(binaries),
        total_scanned=total_scanned,
        with_man_pages=with_man,
        timestamp=datetime.now(timezone.utc),
    )


def discover_specific_commands(commands: list[str]) -> DiscoveryResult:
    """Discover specific commands.

    Args:
        commands: List of command names to discover.

    Returns:
        DiscoveryResult with discovered binaries.
    """
    binaries: list[InstalledBinary] = []
    total_scanned = len(commands)
    with_man = 0

    for name in commands:
        binary = discover_binary(name)
        if binary:
            if binary.man_available:
                with_man += 1
            binaries.append(binary)

    return DiscoveryResult(
        binaries=tuple(binaries),
        total_scanned=total_scanned,
        with_man_pages=with_man,
        timestamp=datetime.now(timezone.utc),
    )


def list_installed_packages() -> list[tuple[str, str]]:
    """List installed packages.

    Returns:
        List of (package_name, version) tuples.
    """
    packages: list[tuple[str, str]] = []

    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package} ${Version}\n"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    packages.append((parts[0], parts[1]))
    except Exception as e:
        logger.error(f"Failed to list packages: {e}")

    return packages
