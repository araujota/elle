"""Man page parser.

Parses man pages to extract:
- SYNOPSIS: Command signature, required/optional args
- OPTIONS: Flags with types, descriptions
- DESCRIPTION: Summary
- EXAMPLES: Usage patterns
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from elle.capabilities.autogen.models import (
    FlagType,
    ParsedExample,
    ParsedFlag,
    ParsedManPage,
    ParsedSynopsis,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Man Page Reading
# =============================================================================


def read_man_page(command: str) -> str | None:
    """Read raw man page text for a command.

    Args:
        command: Command name.

    Returns:
        Raw man page text or None.
    """
    try:
        # Use col -b to strip formatting codes
        result = subprocess.run(
            f"man {command} 2>/dev/null | col -b",
            shell=True,  # nosec B602 - man page retrieval requires pipe through col
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception as e:
        logger.debug(f"Failed to read man page for {command}: {e}")

    return None


def read_man_file(path: str) -> str | None:
    """Read man page from file path.

    Args:
        path: Path to man page file (may be .gz compressed).

    Returns:
        Raw man page text or None.
    """
    try:
        p = Path(path)
        if not p.exists():
            return None

        if path.endswith(".gz"):
            import gzip

            with gzip.open(path, "rt", errors="replace") as f:
                content = f.read()
        else:
            with open(path, errors="replace") as f:
                content = f.read()

        # If it's groff source, process it
        if content.startswith("."):
            result = subprocess.run(
                ["groff", "-Tutf8", "-man"],
                input=content,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Strip control characters
                result2 = subprocess.run(
                    ["col", "-b"],
                    input=result.stdout,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result2.returncode == 0:
                    return result2.stdout

        return content

    except Exception as e:
        logger.debug(f"Failed to read man file {path}: {e}")

    return None


# =============================================================================
# Section Extraction
# =============================================================================


def extract_sections(text: str) -> dict[str, str]:
    """Extract named sections from man page text.

    Args:
        text: Raw man page text.

    Returns:
        Dict mapping section names to content.
    """
    sections: dict[str, str] = {}

    # Pattern for section headers (all caps at start of line)
    section_pattern = re.compile(r"^([A-Z][A-Z ]+)$", re.MULTILINE)

    matches = list(section_pattern.finditer(text))

    for i, match in enumerate(matches):
        section_name = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        sections[section_name] = content

    return sections


# =============================================================================
# Synopsis Parsing
# =============================================================================


def parse_synopsis(text: str, command: str) -> ParsedSynopsis | None:
    """Parse SYNOPSIS section.

    Args:
        text: Synopsis section text.
        command: Command name.

    Returns:
        ParsedSynopsis or None.
    """
    if not text:
        return None

    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Extract subcommands (words after command name that aren't options)
    subcommands: list[str] = []
    positional: list[str] = []
    has_options = "[OPTIONS]" in text or "[OPTION" in text or "[-" in text

    # Common pattern: command [OPTIONS] subcommand [args...]
    # or: command subcommand [OPTIONS] [args...]

    # Try to extract subcommands from common patterns
    subcmd_pattern = re.compile(
        rf"{re.escape(command)}\s+(?:\[OPTIONS\]\s+)?(\w+)",
        re.IGNORECASE,
    )
    match = subcmd_pattern.search(text)
    if match:
        potential_subcmd = match.group(1)
        # Check if it looks like a subcommand (not an option or metavar)
        if not potential_subcmd.startswith("-") and potential_subcmd.isupper() is False:
            if potential_subcmd not in ("OPTIONS", "OPTION", "FILE", "DIR", "NAME"):
                subcommands.append(potential_subcmd)

    # Extract positional args (typically in angle brackets or CAPS)
    pos_pattern = re.compile(r"<([^>]+)>|(?:\s|^)([A-Z]{2,})(?:\s|$|\.\.\.)|\[([A-Z]+)\]")
    for match in pos_pattern.finditer(text):
        arg = match.group(1) or match.group(2) or match.group(3)
        if arg and arg not in ("OPTIONS", "OPTION"):
            positional.append(arg.lower())

    return ParsedSynopsis(
        raw=text,
        command=command,
        subcommands=tuple(subcommands),
        positional_args=tuple(positional),
        has_options=has_options,
    )


# =============================================================================
# Options Parsing
# =============================================================================


def infer_flag_type(description: str, metavar: str | None) -> FlagType:
    """Infer flag type from description and metavar.

    Args:
        description: Flag description.
        metavar: Argument placeholder.

    Returns:
        Inferred FlagType.
    """
    if not metavar:
        return FlagType.BOOL

    metavar_lower = metavar.lower()
    desc_lower = description.lower()

    # Path types
    if any(x in metavar_lower for x in ("file", "path", "dir", "directory")):
        return FlagType.PATH

    # Integer types
    if any(x in metavar_lower for x in ("num", "count", "n", "seconds", "size", "bytes")):
        return FlagType.INT
    if re.search(r"\b(number|integer|count)\b", desc_lower):
        return FlagType.INT

    # List types (can be specified multiple times)
    if "multiple" in desc_lower or "repeat" in desc_lower:
        return FlagType.LIST

    return FlagType.STRING


def parse_options(text: str) -> list[ParsedFlag]:
    """Parse OPTIONS section.

    Args:
        text: Options section text.

    Returns:
        List of ParsedFlag objects.
    """
    flags: list[ParsedFlag] = []

    # Split into option blocks (each starting with -)
    # Common formats:
    # -f, --foo          Description
    # -f, --foo=VALUE    Description
    # --foo              Description

    # Pattern to match option definitions
    option_pattern = re.compile(
        r"^\s*(-\w)(?:,\s*|\s+)?(?:(--[\w-]+))?(?:[=\s](\w+))?\s*(.*)$",
        re.MULTILINE,
    )

    # Also match long-only options
    long_only_pattern = re.compile(
        r"^\s*(--[\w-]+)(?:[=\s](\w+))?\s*(.*)$",
        re.MULTILINE,
    )

    # First try to find short+long pairs
    for match in option_pattern.finditer(text):
        short = match.group(1)
        long = match.group(2)
        metavar = match.group(3)
        description = match.group(4).strip()

        # Extract name from long form or short form
        name = long.lstrip("-").replace("-", "_") if long else short.lstrip("-")

        flag_type = infer_flag_type(description, metavar)

        flags.append(
            ParsedFlag(
                name=name,
                short=short,
                long=long,
                flag_type=flag_type,
                description=description[:200],  # Truncate
                required=False,
                metavar=metavar,
            )
        )

    # Then find long-only options not already found
    existing_longs = {f.long for f in flags if f.long}
    for match in long_only_pattern.finditer(text):
        long = match.group(1)
        if long in existing_longs:
            continue

        metavar = match.group(2)
        description = match.group(3).strip()
        name = long.lstrip("-").replace("-", "_")

        flag_type = infer_flag_type(description, metavar)

        flags.append(
            ParsedFlag(
                name=name,
                short=None,
                long=long,
                flag_type=flag_type,
                description=description[:200],
                required=False,
                metavar=metavar,
            )
        )

    return flags


# =============================================================================
# Examples Parsing
# =============================================================================


def parse_examples(text: str) -> list[ParsedExample]:
    """Parse EXAMPLES section.

    Args:
        text: Examples section text.

    Returns:
        List of ParsedExample objects.
    """
    examples: list[ParsedExample] = []

    # Examples typically look like:
    #   command arg1 arg2
    #       Description of what it does
    # or:
    #   $ command arg1 arg2
    #   Description

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines
        if not line:
            i += 1
            continue

        # Look for command lines (start with $ or common command prefixes)
        if line.startswith("$") or line.startswith("#"):
            command = line.lstrip("$#").strip()
            description = ""

            # Look for description on following lines
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("$", "#")):
                desc_line = lines[i].strip()
                if not desc_line.startswith("-"):
                    description += " " + desc_line
                    i += 1
                else:
                    break

            if command:
                examples.append(
                    ParsedExample(
                        command=command,
                        description=description.strip()[:200],
                    )
                )
        else:
            i += 1

    return examples


# =============================================================================
# Main Parser
# =============================================================================


def parse_man_page(command: str, man_path: str | None = None) -> ParsedManPage | None:
    """Parse a man page for a command.

    Args:
        command: Command name.
        man_path: Optional path to man page file.

    Returns:
        ParsedManPage or None if parsing fails.
    """
    # Read man page
    text = read_man_file(man_path) if man_path else read_man_page(command)

    if not text:
        return None

    # Extract sections
    sections = extract_sections(text)

    # Determine man section from NAME or header
    section = 1  # Default
    for sec_name in ("NAME", ""):
        sec_text = sections.get(sec_name, "")
        match = re.search(rf"{command}\((\d+)\)", sec_text, re.IGNORECASE)
        if match:
            section = int(match.group(1))
            break

    # Parse SYNOPSIS
    synopsis = None
    if "SYNOPSIS" in sections:
        synopsis = parse_synopsis(sections["SYNOPSIS"], command)

    # Parse DESCRIPTION (just take first paragraph)
    description = ""
    if "DESCRIPTION" in sections:
        desc_text = sections["DESCRIPTION"]
        # Take first paragraph (up to double newline or ~500 chars)
        para_end = desc_text.find("\n\n")
        description = desc_text[:para_end] if para_end > 0 else desc_text[:500]
        description = re.sub(r"\s+", " ", description).strip()

    # Parse OPTIONS
    flags: list[ParsedFlag] = []
    for sec_name in ("OPTIONS", "COMMAND OPTIONS", "FLAGS"):
        if sec_name in sections:
            flags.extend(parse_options(sections[sec_name]))

    # Parse EXAMPLES
    examples: list[ParsedExample] = []
    if "EXAMPLES" in sections:
        examples = parse_examples(sections["EXAMPLES"])

    # Parse SEE ALSO
    see_also: list[str] = []
    if "SEE ALSO" in sections:
        # Extract command names from "command(section)" references
        see_also_pattern = re.compile(r"(\w+)\(\d+\)")
        for match in see_also_pattern.finditer(sections["SEE ALSO"]):
            see_also.append(match.group(1))

    return ParsedManPage(
        name=command,
        section=section,
        synopsis=synopsis,
        description=description,
        flags=tuple(flags),
        examples=tuple(examples),
        see_also=tuple(see_also),
        raw_text=text,
    )


def flag_exists(man_page: ParsedManPage, flag_name: str) -> bool:
    """Check if a flag exists in a parsed man page.

    Args:
        man_page: Parsed man page.
        flag_name: Flag name to check (with or without dashes).

    Returns:
        True if flag exists.
    """
    # Normalize flag name
    normalized = flag_name.lstrip("-").replace("-", "_")

    for flag in man_page.flags:
        if flag.name == normalized:
            return True
        if flag.short and flag.short.lstrip("-") == flag_name.lstrip("-"):
            return True
        if flag.long and flag.long.lstrip("-").replace("-", "_") == normalized:
            return True

    return False
