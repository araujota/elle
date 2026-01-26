"""Intelligence extractors for package capability generation.

Each extractor is responsible for a single data source:
- DpkgMetadataExtractor: Package metadata and dependencies
- FileManifestExtractor: Installed files categorized by type
- BashCompletionExtractor: Bash completion scripts (HIGH SIGNAL)
- ZshCompletionExtractor: Zsh completion scripts (HIGH SIGNAL)
- ManPageExtractor: Man page flag extraction
- SystemdUnitExtractor: Systemd service information
- HelpOutputExtractor: --help output fallback (LOW SIGNAL)

Extractors are run in priority order (lower = earlier).
Failed extractors don't block others.
"""

from __future__ import annotations

import logging
import re
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from elle.capabilities.autogen.intelligence.models import (
    ExtractedFlag,
    ExtractedSubcommand,
    ExtractionResult,
    FileManifest,
    PackageMetadata,
    ShellCompletions,
    SystemdUnitInfo,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Base Extractor
# =============================================================================


class BaseExtractor(ABC):
    """Base class for all extractors.

    Extractors are prioritized by their `priority` class variable.
    Lower priority = runs earlier.
    """

    name: ClassVar[str] = "base"
    priority: ClassVar[int] = 100

    @abstractmethod
    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract intelligence from this source.

        Args:
            package_name: Package name to extract for.
            manifest: File manifest (if already extracted).

        Returns:
            ExtractionResult with extracted data.
        """
        ...


# =============================================================================
# Dpkg Metadata Extractor
# =============================================================================


class DpkgMetadataExtractor(BaseExtractor):
    """Extract package metadata from dpkg-query."""

    name = "dpkg"
    priority = 10

    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract metadata from dpkg."""
        try:
            result = subprocess.run(
                [
                    "dpkg-query",
                    "-W",
                    "-f",
                    "${Package}\n${Version}\n${Description}\n${Section}\n${Depends}\n${Homepage}",
                    package_name,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return ExtractionResult(
                    source_name=self.name,
                    success=False,
                    error=f"Package not found: {package_name}",
                )

            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return ExtractionResult(
                    source_name=self.name,
                    success=False,
                    error="Invalid dpkg output",
                )

            # Parse output
            name = lines[0] if lines else package_name
            version = lines[1] if len(lines) > 1 else "unknown"
            description = lines[2] if len(lines) > 2 else ""
            section = lines[3] if len(lines) > 3 and lines[3] else None
            depends_str = lines[4] if len(lines) > 4 else ""
            homepage = lines[5] if len(lines) > 5 and lines[5] else None

            # Parse dependencies
            depends = tuple(
                dep.split()[0].strip()
                for dep in depends_str.split(",")
                if dep.strip()
            )

            metadata = PackageMetadata(
                name=name,
                version=version,
                description=description,
                section=section,
                depends=depends,
                homepage=homepage,
            )

            return ExtractionResult(
                source_name=self.name,
                success=True,
                metadata=metadata,
            )

        except subprocess.TimeoutExpired:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="dpkg-query timed out",
            )
        except Exception as e:
            logger.debug(f"DpkgMetadataExtractor failed: {e}")
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error=str(e),
            )


# =============================================================================
# File Manifest Extractor
# =============================================================================


class FileManifestExtractor(BaseExtractor):
    """Extract file manifest from dpkg -L."""

    name = "manifest"
    priority = 20

    # Patterns for categorization
    BINARY_DIRS = frozenset({
        "/usr/bin/",
        "/bin/",
        "/usr/sbin/",
        "/sbin/",
        "/usr/local/bin/",
    })
    MAN_PATTERN = re.compile(r"/man/man\d/.*\.\d")
    SYSTEMD_PATTERN = re.compile(r"/systemd/.*\.(service|socket|timer|target|path)$")
    POLKIT_PATTERN = re.compile(r"/polkit.*\.policy$")

    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract file manifest from dpkg -L."""
        try:
            result = subprocess.run(
                ["dpkg", "-L", package_name],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return ExtractionResult(
                    source_name=self.name,
                    success=False,
                    error=f"dpkg -L failed for {package_name}",
                )

            files = result.stdout.strip().split("\n")

            binaries: list[str] = []
            man_pages: list[str] = []
            completions: list[str] = []
            systemd_units: list[str] = []
            config_files: list[str] = []
            polkit_actions: list[str] = []

            for file_path in files:
                file_path = file_path.strip()
                if not file_path:
                    continue

                # Categorize by path patterns
                if any(file_path.startswith(d) for d in self.BINARY_DIRS):
                    if Path(file_path).is_file():
                        binaries.append(file_path)

                elif self.MAN_PATTERN.search(file_path):
                    man_pages.append(file_path)

                elif (
                    "bash-completion" in file_path
                    or "bash_completion" in file_path
                    or ("zsh" in file_path and "completion" in file_path)
                    or "/fish/completions/" in file_path
                ):
                    completions.append(file_path)

                elif self.SYSTEMD_PATTERN.search(file_path):
                    systemd_units.append(file_path)

                elif file_path.startswith("/etc/") and Path(file_path).is_file():
                    config_files.append(file_path)

                elif self.POLKIT_PATTERN.search(file_path):
                    polkit_actions.append(file_path)

            file_manifest = FileManifest(
                binaries=tuple(sorted(binaries)),
                man_pages=tuple(sorted(man_pages)),
                completions=tuple(sorted(completions)),
                systemd_units=tuple(sorted(systemd_units)),
                config_files=tuple(sorted(config_files)),
                polkit_actions=tuple(sorted(polkit_actions)),
            )

            return ExtractionResult(
                source_name=self.name,
                success=True,
                manifest=file_manifest,
            )

        except subprocess.TimeoutExpired:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="dpkg -L timed out",
            )
        except Exception as e:
            logger.debug(f"FileManifestExtractor failed: {e}")
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error=str(e),
            )


# =============================================================================
# Bash Completion Extractor
# =============================================================================


class BashCompletionExtractor(BaseExtractor):
    """Extract flags and subcommands from bash completion scripts.

    Bash completions are HIGH SIGNAL because they're maintained by
    package authors and contain structured command-line information.
    """

    name = "bash_completion"
    priority = 30

    # Patterns for parsing bash completions
    FLAG_PATTERN = re.compile(
        r"""
        ['"](-{1,2}[\w-]+)['"]  # Quoted flag
        |
        \b(-{1,2}[\w-]+)\b     # Unquoted flag
        """,
        re.VERBOSE,
    )

    # Common bash completion patterns
    COMPGEN_PATTERN = re.compile(r"COMPREPLY.*-W\s*['\"]([^'\"]+)['\"]")
    CASE_PATTERN = re.compile(r"\b(--?[\w-]+)\)")
    OPTS_VAR_PATTERN = re.compile(r"(?:opts|OPTS|options)=['\"]([^'\"]+)['\"]")

    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract from bash completion files."""
        if not manifest or not manifest.completions:
            # Try standard locations
            completion_files = self._find_completion_files(package_name)
        else:
            completion_files = [
                f for f in manifest.completions
                if "bash" in f.lower()
            ]

        if not completion_files:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="No bash completion files found",
            )

        flags: list[ExtractedFlag] = []
        subcommands: list[ExtractedSubcommand] = []
        source_file = None

        for comp_file in completion_files:
            try:
                content = Path(comp_file).read_text()
                source_file = comp_file

                # Extract flags from various patterns
                extracted_flags = self._extract_flags(content)
                flags.extend(extracted_flags)

                # Extract subcommands
                extracted_subs = self._extract_subcommands(content)
                subcommands.extend(extracted_subs)

            except Exception as e:
                logger.debug(f"Failed to parse {comp_file}: {e}")
                continue

        if not flags and not subcommands:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="No flags found in completion files",
            )

        completions = ShellCompletions(
            flags=tuple(flags),
            subcommands=tuple(subcommands),
            source_file=source_file,
            shell_type="bash",
        )

        return ExtractionResult(
            source_name=self.name,
            success=True,
            flags=tuple(flags),
            subcommands=tuple(subcommands),
            completions=completions,
        )

    def _find_completion_files(self, package_name: str) -> list[str]:
        """Find completion files in standard locations."""
        locations = [
            Path(f"/usr/share/bash-completion/completions/{package_name}"),
            Path(f"/etc/bash_completion.d/{package_name}"),
        ]

        return [str(p) for p in locations if p.exists()]

    def _extract_flags(self, content: str) -> list[ExtractedFlag]:
        """Extract flags from completion script content."""
        flags: list[ExtractedFlag] = []
        seen: set[str] = set()

        # Extract from COMPREPLY -W patterns
        for match in self.COMPREPLY_PATTERN.finditer(content):
            words = match.group(1).split()
            for word in words:
                if word.startswith("-") and word not in seen:
                    seen.add(word)
                    flags.append(ExtractedFlag(
                        flag=word,
                        long_form=None if word.startswith("--") else None,
                        description="",
                        takes_value=False,
                        source="bash_completion",
                        confidence=0.9,
                    ))

        # Extract from case patterns
        for match in self.CASE_PATTERN.finditer(content):
            flag = match.group(1)
            if flag not in seen:
                seen.add(flag)
                flags.append(ExtractedFlag(
                    flag=flag,
                    description="",
                    takes_value=False,
                    source="bash_completion",
                    confidence=0.85,
                ))

        # Extract from opts variable patterns
        for match in self.OPTS_VAR_PATTERN.finditer(content):
            words = match.group(1).split()
            for word in words:
                if word.startswith("-") and word not in seen:
                    seen.add(word)
                    flags.append(ExtractedFlag(
                        flag=word,
                        description="",
                        takes_value=False,
                        source="bash_completion",
                        confidence=0.85,
                    ))

        return flags

    def _extract_subcommands(self, content: str) -> list[ExtractedSubcommand]:
        """Extract subcommands from completion script."""
        subcommands: list[ExtractedSubcommand] = []

        # Look for common patterns like "cmds=" or subcommand arrays
        cmds_pattern = re.compile(r"(?:cmds|commands|CMDS|subcommands)=['\"]([^'\"]+)['\"]")
        for match in cmds_pattern.finditer(content):
            words = match.group(1).split()
            for word in words:
                if word and not word.startswith("-"):
                    subcommands.append(ExtractedSubcommand(
                        name=word,
                        description="",
                        source="bash_completion",
                    ))

        return subcommands

    # Alias for the pattern used in extraction
    COMPREPLY_PATTERN = COMPGEN_PATTERN


# =============================================================================
# Zsh Completion Extractor
# =============================================================================


class ZshCompletionExtractor(BaseExtractor):
    """Extract flags from zsh completion scripts.

    Zsh completions often have richer descriptions than bash.
    """

    name = "zsh_completion"
    priority = 31

    # Zsh completion patterns
    ZSH_FLAG_PATTERN = re.compile(
        r"""
        ['"](\*?)(-{1,2}[\w-]+)   # Optional * prefix, flag
        (?:\[([^\]]+)\])?         # Optional [description]
        (?::([^:'"]+))?           # Optional :description
        """,
        re.VERBOSE,
    )
    ZSH_ARGS_PATTERN = re.compile(r"_arguments.*'([^']+)'")

    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract from zsh completion files."""
        if not manifest or not manifest.completions:
            completion_files = self._find_completion_files(package_name)
        else:
            completion_files = [
                f for f in manifest.completions
                if "zsh" in f.lower() or f.startswith("_")
            ]

        if not completion_files:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="No zsh completion files found",
            )

        flags: list[ExtractedFlag] = []
        source_file = None

        for comp_file in completion_files:
            try:
                content = Path(comp_file).read_text()
                source_file = comp_file

                extracted = self._extract_flags(content)
                flags.extend(extracted)

            except Exception as e:
                logger.debug(f"Failed to parse {comp_file}: {e}")
                continue

        if not flags:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="No flags found in zsh completion",
            )

        completions = ShellCompletions(
            flags=tuple(flags),
            subcommands=(),
            source_file=source_file,
            shell_type="zsh",
        )

        return ExtractionResult(
            source_name=self.name,
            success=True,
            flags=tuple(flags),
            completions=completions,
        )

    def _find_completion_files(self, package_name: str) -> list[str]:
        """Find zsh completion files."""
        locations = [
            Path(f"/usr/share/zsh/vendor-completions/_{package_name}"),
            Path(f"/usr/share/zsh/functions/Completion/Unix/_{package_name}"),
        ]

        return [str(p) for p in locations if p.exists()]

    def _extract_flags(self, content: str) -> list[ExtractedFlag]:
        """Extract flags from zsh completion content."""
        flags: list[ExtractedFlag] = []
        seen: set[str] = set()

        # Parse _arguments style completions
        for match in self.ZSH_ARGS_PATTERN.finditer(content):
            arg_spec = match.group(1)

            # Parse the spec
            flag_match = re.match(r"'?(\*?)(-{1,2}[\w-]+)", arg_spec)
            if flag_match:
                flag = flag_match.group(2)
                if flag not in seen:
                    seen.add(flag)

                    # Try to extract description
                    desc_match = re.search(r"\[([^\]]+)\]", arg_spec)
                    description = desc_match.group(1) if desc_match else ""

                    # Check if takes value
                    takes_value = ":" in arg_spec and "::" not in arg_spec

                    flags.append(ExtractedFlag(
                        flag=flag,
                        description=description,
                        takes_value=takes_value,
                        source="zsh_completion",
                        confidence=0.92,  # Zsh has good descriptions
                    ))

        return flags


# =============================================================================
# Man Page Extractor
# =============================================================================


class ManPageExtractor(BaseExtractor):
    """Extract flags from man pages.

    Uses the existing parser from autogen module.
    """

    name = "man_page"
    priority = 40

    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract flags from man pages."""
        try:
            from elle.capabilities.autogen.parser import parse_man_page

            # Find the primary binary name
            binary_name = package_name
            if manifest and manifest.binaries:
                # Use the first binary that matches the package name
                for binary in manifest.binaries:
                    name = Path(binary).name
                    if name == package_name or name.startswith(package_name):
                        binary_name = name
                        break
                else:
                    binary_name = Path(manifest.binaries[0]).name

            # Parse the man page
            man_page = parse_man_page(binary_name)

            if not man_page:
                return ExtractionResult(
                    source_name=self.name,
                    success=False,
                    error=f"No man page found for {binary_name}",
                )

            # Convert ParsedFlag to ExtractedFlag
            flags: list[ExtractedFlag] = []
            for pf in man_page.flags:
                flag_str = pf.long or pf.short or pf.name
                if flag_str:
                    flags.append(ExtractedFlag(
                        flag=flag_str,
                        long_form=pf.long,
                        description=pf.description,
                        takes_value=pf.flag_type.value != "bool",
                        value_type=pf.metavar,
                        source="man_page",
                        confidence=0.8,
                    ))

            return ExtractionResult(
                source_name=self.name,
                success=True,
                flags=tuple(flags),
            )

        except ImportError:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="Man page parser not available",
            )
        except Exception as e:
            logger.debug(f"ManPageExtractor failed: {e}")
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error=str(e),
            )


# =============================================================================
# Systemd Unit Extractor
# =============================================================================


class SystemdUnitExtractor(BaseExtractor):
    """Extract information from systemd unit files."""

    name = "systemd_unit"
    priority = 50

    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract from systemd unit files."""
        if not manifest or not manifest.systemd_units:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="No systemd units in manifest",
            )

        units: list[SystemdUnitInfo] = []

        for unit_path in manifest.systemd_units:
            try:
                content = Path(unit_path).read_text()
                unit_name = Path(unit_path).name

                # Determine unit type
                unit_type = "service"
                for ext in [".service", ".socket", ".timer", ".target", ".path"]:
                    if unit_name.endswith(ext):
                        unit_type = ext[1:]  # Remove the dot
                        break

                # Parse unit file
                description = self._get_value(content, "Description")
                exec_start = self._get_value(content, "ExecStart")
                env_lines = self._get_all_values(content, "Environment")
                requires = self._get_all_values(content, "Requires")

                units.append(SystemdUnitInfo(
                    unit_name=unit_name,
                    unit_type=unit_type,
                    description=description,
                    exec_start=exec_start,
                    environment=tuple(env_lines),
                    requires=tuple(requires),
                ))

            except Exception as e:
                logger.debug(f"Failed to parse {unit_path}: {e}")
                continue

        if not units:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="Failed to parse any unit files",
            )

        return ExtractionResult(
            source_name=self.name,
            success=True,
            systemd_units=tuple(units),
        )

    def _get_value(self, content: str, key: str) -> str | None:
        """Get single value from unit file."""
        pattern = re.compile(rf"^{key}=(.+)$", re.MULTILINE)
        match = pattern.search(content)
        return match.group(1) if match else None

    def _get_all_values(self, content: str, key: str) -> list[str]:
        """Get all values for a key from unit file."""
        pattern = re.compile(rf"^{key}=(.+)$", re.MULTILINE)
        return [m.group(1) for m in pattern.finditer(content)]


# =============================================================================
# Help Output Extractor (Fallback)
# =============================================================================


class HelpOutputExtractor(BaseExtractor):
    """Extract flags from --help output.

    This is a LOW SIGNAL fallback when better sources aren't available.
    """

    name = "help_output"
    priority = 70  # Run late

    FLAG_PATTERN = re.compile(
        r"""
        ^\s*                      # Leading whitespace
        (-[\w])                   # Short flag
        (?:,\s*)?                 # Optional comma separator
        (--[\w-]+)?               # Optional long flag
        (?:\s+[\[<]?(\w+)[\]>]?)? # Optional argument placeholder
        \s{2,}                    # At least 2 spaces before description
        (.+?)                     # Description
        $
        """,
        re.VERBOSE | re.MULTILINE,
    )

    LONG_ONLY_PATTERN = re.compile(
        r"""
        ^\s*
        (--[\w-]+)                # Long flag only
        (?:[=\s][\[<]?(\w+)[\]>]?)? # Optional argument
        \s{2,}
        (.+?)
        $
        """,
        re.VERBOSE | re.MULTILINE,
    )

    def extract(
        self,
        package_name: str,
        manifest: FileManifest | None = None,
    ) -> ExtractionResult:
        """Extract from --help output."""
        # Find the binary to run
        binary = package_name
        if manifest and manifest.binaries:
            binary = manifest.binaries[0]

        try:
            result = subprocess.run(
                [binary, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            help_text = result.stdout or result.stderr

            if not help_text:
                return ExtractionResult(
                    source_name=self.name,
                    success=False,
                    error="No --help output",
                )

            flags = self._parse_help(help_text)

            if not flags:
                return ExtractionResult(
                    source_name=self.name,
                    success=False,
                    error="No flags found in --help",
                )

            return ExtractionResult(
                source_name=self.name,
                success=True,
                flags=tuple(flags),
            )

        except subprocess.TimeoutExpired:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error="--help timed out",
            )
        except FileNotFoundError:
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error=f"Binary not found: {binary}",
            )
        except Exception as e:
            logger.debug(f"HelpOutputExtractor failed: {e}")
            return ExtractionResult(
                source_name=self.name,
                success=False,
                error=str(e),
            )

    def _parse_help(self, help_text: str) -> list[ExtractedFlag]:
        """Parse --help output for flags."""
        flags: list[ExtractedFlag] = []
        seen: set[str] = set()

        # Parse short+long flag patterns
        for match in self.FLAG_PATTERN.finditer(help_text):
            short, long, arg, desc = match.groups()

            flag = long or short
            if flag and flag not in seen:
                seen.add(flag)
                flags.append(ExtractedFlag(
                    flag=flag,
                    long_form=long,
                    description=desc.strip(),
                    takes_value=bool(arg),
                    value_type=arg,
                    source="help_output",
                    confidence=0.7,  # Lower confidence for help parsing
                ))

        # Parse long-only patterns
        for match in self.LONG_ONLY_PATTERN.finditer(help_text):
            long, arg, desc = match.groups()

            if long and long not in seen:
                seen.add(long)
                flags.append(ExtractedFlag(
                    flag=long,
                    description=desc.strip(),
                    takes_value=bool(arg),
                    value_type=arg,
                    source="help_output",
                    confidence=0.7,
                ))

        return flags


# =============================================================================
# Extractor Registry
# =============================================================================


# All extractors in priority order
ALL_EXTRACTORS: tuple[type[BaseExtractor], ...] = (
    DpkgMetadataExtractor,
    FileManifestExtractor,
    BashCompletionExtractor,
    ZshCompletionExtractor,
    ManPageExtractor,
    SystemdUnitExtractor,
    HelpOutputExtractor,
)


def get_extractors() -> list[BaseExtractor]:
    """Get instances of all extractors sorted by priority.

    Returns:
        List of extractor instances in priority order.
    """
    extractors = [cls() for cls in ALL_EXTRACTORS]
    return sorted(extractors, key=lambda e: e.priority)
