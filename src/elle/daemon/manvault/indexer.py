"""Man Vault indexer (PostgreSQL).

Indexes all installed system documentation from /usr/share/man/**
into PostgreSQL + tsvector for fast retrieval.

Indexing pipeline:
1. Discover man pages in /usr/share/man/
2. Render via: env LANG=C man -P cat <section> <name> | col -bx
3. Normalize text and compute SHA256 hash
4. Chunk document by section headings
5. Upsert document and chunks to database
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from elle.daemon.manvault.models import ManChunk, ManDiscoveryItem, ManDoc
from elle.daemon.manvault.store import (
    delete_chunks_for_doc,
    get_all_hashes,
    get_doc,
    set_meta,
    upsert_chunks_batch,
    upsert_doc,
)
from elle.storage.engine import get_conn

logger = logging.getLogger(__name__)

PG_SCHEMA = "manvault"

# Default man page locations
MAN_PATHS = [Path("/usr/share/man")]

# Supported man sections
SUPPORTED_SECTIONS = ["1", "2", "3", "4", "5", "6", "7", "8"]

# Core commands that should always be seeded at launch
# These are essential for system administration and ELLE's functionality
CORE_COMMANDS = [
    # System services
    ("systemctl", "1"),
    ("journalctl", "1"),
    # Networking
    ("ufw", "8"),
    ("netplan", "5"),
    ("ip", "8"),
    ("ip-route", "8"),
    ("ip-address", "8"),
    ("ip-link", "8"),
    ("ss", "8"),
    ("ping", "8"),
    ("curl", "1"),
    ("wget", "1"),
    ("iptables", "8"),
    ("nft", "8"),
    ("dig", "1"),
    ("nslookup", "1"),
    ("netstat", "8"),
    ("traceroute", "1"),
    # Containers
    ("docker", "1"),
    ("docker-compose", "1"),
    ("docker-run", "1"),
    ("dockerfile", "5"),
    ("podman", "1"),
    # WireGuard VPN
    ("wg", "8"),
    ("wg-quick", "8"),
    # Package management
    ("apt", "8"),
    ("apt-get", "8"),
    ("dpkg", "1"),
    ("snap", "8"),
    # Scheduling
    ("crontab", "1"),
    ("crontab", "5"),
    # Hardware monitoring
    ("smartctl", "8"),
    ("sensors", "1"),
    # Disk and filesystem
    ("df", "1"),
    ("du", "1"),
    ("mount", "8"),
    ("umount", "8"),
    ("fdisk", "8"),
    ("lsblk", "8"),
    ("fstab", "5"),
    # Process and system
    ("ps", "1"),
    ("top", "1"),
    ("htop", "1"),
    ("kill", "1"),
    ("lsof", "8"),
    ("free", "1"),
    ("uptime", "1"),
    # Text processing
    ("grep", "1"),
    ("sed", "1"),
    ("awk", "1"),
    # File operations
    ("ls", "1"),
    ("cp", "1"),
    ("mv", "1"),
    ("rm", "1"),
    ("chmod", "1"),
    ("chown", "1"),
    ("find", "1"),
    ("tar", "1"),
    ("gzip", "1"),
    # User management
    ("useradd", "8"),
    ("usermod", "8"),
    ("passwd", "1"),
    ("sudo", "8"),
    # SSH
    ("ssh", "1"),
    ("scp", "1"),
    ("ssh-keygen", "1"),
    # Misc essentials
    ("man", "1"),
    ("cat", "1"),
    ("less", "1"),
    ("head", "1"),
    ("tail", "1"),
    ("wc", "1"),
    ("sort", "1"),
    ("uniq", "1"),
    ("cut", "1"),
    ("xargs", "1"),
]

# Man page file extensions
MAN_EXTENSIONS = {".gz", ".bz2", ".xz", ""}

# Common section headings in man pages
SECTION_HEADINGS = [
    "NAME",
    "SYNOPSIS",
    "DESCRIPTION",
    "OPTIONS",
    "ARGUMENTS",
    "EXAMPLES",
    "FILES",
    "ENVIRONMENT",
    "EXIT STATUS",
    "EXIT CODES",
    "RETURN VALUE",
    "RETURN VALUES",
    "ERRORS",
    "DIAGNOSTICS",
    "SEE ALSO",
    "STANDARDS",
    "HISTORY",
    "AUTHORS",
    "AUTHOR",
    "BUGS",
    "NOTES",
    "CAVEATS",
    "WARNINGS",
    "CONFORMING TO",
    "COLOPHON",
]

# Regex pattern for section headings (all caps at start of line)
HEADING_PATTERN = re.compile(r"^([A-Z][A-Z\s]+)$", re.MULTILINE)

# Default chunk size target (characters)
DEFAULT_CHUNK_SIZE = 800

# Timeouts for rendering
MAN_TIMEOUT = 10  # seconds
COL_TIMEOUT = 5  # seconds


def discover_man_pages(
    paths: list[Path] | None = None,
) -> Iterator[ManDiscoveryItem]:
    """Discover man pages in the specified paths.

    Walks /usr/share/man/ and yields discovered pages.
    Handles both English (manN/*.gz) and localized (fr/manN/*.gz) pages.

    Args:
        paths: List of paths to search. Defaults to MAN_PATHS.

    Yields:
        ManDiscoveryItem for each discovered page.
    """
    search_paths = paths or MAN_PATHS

    for base_path in search_paths:
        if not base_path.exists():
            logger.debug(f"Man path does not exist: {base_path}")
            continue

        # Walk the directory tree
        for man_dir in base_path.rglob("man[1-8]"):
            if not man_dir.is_dir():
                continue

            # Extract section from directory name (man1 -> 1)
            section = man_dir.name[3:]
            if section not in SUPPORTED_SECTIONS:
                continue

            # Determine language from path
            # /usr/share/man/man1 -> en
            # /usr/share/man/fr/man1 -> fr
            relative = man_dir.relative_to(base_path)
            parts = relative.parts
            lang = "en" if len(parts) == 1 else parts[0]

            # Find all man pages in this directory
            for man_file in man_dir.iterdir():
                if not man_file.is_file():
                    continue

                # Extract name from filename
                # ls.1.gz -> ls
                # systemd.service.5.gz -> systemd.service
                name = _extract_name(man_file.name, section)
                if not name:
                    continue

                try:
                    mtime = man_file.stat().st_mtime
                except OSError:
                    continue

                yield ManDiscoveryItem(
                    name=name,
                    section=section,
                    lang=lang,
                    source_path=str(man_file),
                    mtime=mtime,
                )


def _extract_name(filename: str, section: str) -> str | None:
    """Extract the command name from a man page filename.

    Args:
        filename: The filename (e.g., "ls.1.gz")
        section: The section number.

    Returns:
        The command name, or None if not a valid man page.
    """
    # Remove known compression extensions
    for ext in [".gz", ".bz2", ".xz"]:
        if filename.endswith(ext):
            filename = filename[: -len(ext)]
            break

    # Check for section suffix
    suffix = f".{section}"
    if not filename.endswith(suffix):
        return None

    # Extract name
    name = filename[: -len(suffix)]
    return name if name else None


def render_manpage(
    name: str,
    section: str,
    lang: str = "en",
) -> str | None:
    """Render a man page to plain text.

    Uses: env LANG=C LC_ALL=C man -P cat <section> <name> | col -bx

    Args:
        name: The command/topic name.
        section: The man section.
        lang: Language code (for localized pages).

    Returns:
        Plain text content, or None if not found.
    """
    # Build environment
    env = {
        "LANG": "C" if lang == "en" else f"{lang}_UTF-8",
        "LC_ALL": "C" if lang == "en" else f"{lang}_UTF-8",
        "MANWIDTH": "80",  # Consistent width
        "MAN_KEEP_FORMATTING": "1",
    }

    man_proc: subprocess.Popen[bytes] | None = None
    col_proc: subprocess.Popen[bytes] | None = None
    try:
        # Run man command
        man_proc = subprocess.Popen(
            ["man", "-P", "cat", section, name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # Pipe through col to remove formatting
        col_proc = subprocess.Popen(
            ["col", "-bx"],
            stdin=man_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Close man's stdout in parent
        if man_proc.stdout:
            man_proc.stdout.close()

        # Wait for col with timeout
        stdout, stderr = col_proc.communicate(timeout=COL_TIMEOUT)

        # Check man's return code
        man_proc.wait(timeout=MAN_TIMEOUT)
        if man_proc.returncode != 0:
            return None

        if col_proc.returncode != 0:
            logger.debug(f"col failed for {name}({section}): {stderr.decode()}")
            return None

        return stdout.decode("utf-8", errors="replace")

    except subprocess.TimeoutExpired:
        # Kill processes on timeout and reap them
        if man_proc:
            man_proc.kill()
            man_proc.wait()
        if col_proc:
            col_proc.kill()
            col_proc.wait()
        logger.warning(f"Timeout rendering {name}({section})")
        return None
    except FileNotFoundError:
        logger.error("man or col command not found")
        return None
    except Exception as e:
        logger.error(f"Error rendering {name}({section}): {e}")
        return None
    finally:
        # Ensure both processes are reaped if any unexpected path leaves them alive
        for proc in (man_proc, col_proc):
            if proc is not None and proc.returncode is None:
                proc.kill()
                proc.wait()


def normalize_text(text: str) -> str:
    """Normalize rendered man page text.

    Cleans up the text by:
    - Removing backspaces (bold/underline artifacts)
    - Collapsing multiple blank lines
    - Trimming whitespace

    Args:
        text: Raw rendered text.

    Returns:
        Normalized text.
    """
    # Remove backspace sequences (bold: X^HX, underline: _^HX)
    while "\b" in text:
        text = re.sub(r".\b", "", text)

    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Trim trailing whitespace from each line
    lines = [line.rstrip() for line in text.split("\n")]

    # Remove leading/trailing blank lines
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    return "\n".join(lines)


def compute_hash(text: str) -> str:
    """Compute SHA256 hash of normalized text.

    Used for change detection to avoid re-indexing unchanged pages.

    Args:
        text: Normalized text.

    Returns:
        SHA256 hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_document(
    text: str,
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[tuple[str, str | None]]:
    """Split document into chunks by section headings.

    Chunks are created by:
    1. Parsing the document into sections by heading
    2. Splitting large sections into ~max_chunk_size char chunks
    3. Keeping chunks aligned to paragraph boundaries

    Args:
        text: Document text.
        max_chunk_size: Target maximum chunk size in characters.

    Returns:
        List of (chunk_text, heading_name) tuples.
    """
    chunks: list[tuple[str, str | None]] = []

    # Split into sections by heading
    sections = _split_into_sections(text)

    for heading, content in sections:
        content = content.strip()
        if not content:
            continue

        # If section fits in one chunk, use it directly
        if len(content) <= max_chunk_size:
            chunks.append((content, heading))
            continue

        # Split large sections into smaller chunks
        section_chunks = _split_section(content, max_chunk_size)
        for chunk_text in section_chunks:
            chunks.append((chunk_text, heading))

    return chunks


def _split_into_sections(text: str) -> list[tuple[str | None, str]]:
    """Split document into sections by heading.

    Args:
        text: Document text.

    Returns:
        List of (heading, content) tuples.
    """
    sections: list[tuple[str | None, str]] = []

    # Find all section headings
    matches = list(HEADING_PATTERN.finditer(text))

    if not matches:
        # No headings found, return entire text
        return [(None, text)]

    # Handle content before first heading
    first_match = matches[0]
    if first_match.start() > 0:
        preamble = text[: first_match.start()].strip()
        if preamble:
            sections.append((None, preamble))

    # Process each heading and its content
    for i, match in enumerate(matches):
        heading = match.group(1).strip()

        # Content is from end of heading to start of next heading (or end of text)
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[content_start:content_end]

        sections.append((heading, content))

    return sections


def _split_section(text: str, max_size: int) -> list[str]:
    """Split a section into smaller chunks at paragraph boundaries.

    Args:
        text: Section text.
        max_size: Maximum chunk size.

    Returns:
        List of chunk texts.
    """
    chunks: list[str] = []

    # Split into paragraphs
    paragraphs = re.split(r"\n\n+", text)

    current_chunk: list[str] = []
    current_size = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_size = len(para)

        # If paragraph alone exceeds max, split it further
        if para_size > max_size:
            # Flush current chunk
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_size = 0

            # Split long paragraph by sentences
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                if current_size + len(sentence) > max_size and current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_size = 0
                current_chunk.append(sentence)
                current_size += len(sentence) + 2
            continue

        # If adding this paragraph would exceed max, start new chunk
        if current_size + para_size > max_size and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_size = 0

        current_chunk.append(para)
        current_size += para_size + 2  # +2 for \n\n separator

    # Flush remaining content
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def index_page(
    name: str,
    section: str,
    lang: str = "en",
    source_path: str | None = None,
    conn: psycopg.Connection | None = None,
) -> bool:
    """Index a single man page.

    Args:
        name: The command/topic name.
        section: The man section (1-8).
        lang: Language code.
        source_path: Path to source file (for tracking).
        conn: psycopg connection. Obtains from pool if not provided.

    Returns:
        True if successfully indexed.
    """
    if conn is not None:
        return _index_page_impl(conn, name, section, lang, source_path)

    with get_conn(schema=PG_SCHEMA) as c:
        return _index_page_impl(c, name, section, lang, source_path)


def _index_page_impl(
    conn: psycopg.Connection,
    name: str,
    section: str,
    lang: str,
    source_path: str | None,
) -> bool:
    # Render man page
    text = render_manpage(name, section, lang)
    if not text:
        return False

    # Normalize and hash
    text = normalize_text(text)
    sha256 = compute_hash(text)

    # Check if already indexed with same hash
    existing = get_doc(conn, name, section, lang)
    if existing and existing.sha256 == sha256:
        logger.debug(f"Skipping unchanged: {name}({section})")
        return True

    # Create document
    doc = ManDoc(
        name=name,
        section=section,
        lang=lang,
        source_path=source_path or f"/usr/share/man/man{section}/{name}.{section}.gz",
        sha256=sha256,
        text=text,
        updated_at=datetime.now(timezone.utc),
    )

    # Upsert document
    doc_id = upsert_doc(conn, doc)

    # Delete existing chunks (if updating)
    if existing:
        delete_chunks_for_doc(conn, doc_id)

    # Chunk and store
    chunks_data = chunk_document(text)
    chunks = [
        ManChunk(
            doc_id=doc_id,
            chunk_index=i,
            text=chunk_text,
            heading=heading,
        )
        for i, (chunk_text, heading) in enumerate(chunks_data)
    ]
    upsert_chunks_batch(conn, chunks)

    logger.debug(f"Indexed {name}({section}): {len(chunks)} chunks")
    return True


def index_all(
    paths: list[Path] | None = None,
    conn: psycopg.Connection | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    lang_filter: str = "en",
) -> int:
    """Index all man pages.

    Args:
        paths: Paths to search. Defaults to MAN_PATHS.
        conn: psycopg connection. Obtains from pool if not provided.
        progress_callback: Called with (current, total) for progress reporting.
        lang_filter: Only index pages in this language.

    Returns:
        Number of documents indexed.
    """
    if conn is not None:
        return _index_all_impl(conn, paths, progress_callback, lang_filter)

    with get_conn(schema=PG_SCHEMA) as c:
        return _index_all_impl(c, paths, progress_callback, lang_filter)


def _index_all_impl(
    conn: psycopg.Connection,
    paths: list[Path] | None,
    progress_callback: Callable[[int, int], None] | None,
    lang_filter: str,
) -> int:
    # Discover all pages
    pages = list(discover_man_pages(paths))

    # Filter by language
    if lang_filter:
        pages = [p for p in pages if p.lang == lang_filter]

    total = len(pages)
    logger.info(f"Discovered {total} man pages to index")

    count = 0
    for i, page in enumerate(pages):
        try:
            if _index_page_impl(
                conn,
                page.name,
                page.section,
                page.lang,
                page.source_path,
            ):
                count += 1
        except Exception as e:
            logger.error(f"Error indexing {page.name}({page.section}): {e}")

        if progress_callback and (i + 1) % 100 == 0:
            progress_callback(i + 1, total)

    # Update indexed_at timestamp
    set_meta(conn, "indexed_at", datetime.now(timezone.utc).isoformat())

    logger.info(f"Indexed {count}/{total} man pages")
    return count


def index_incremental(
    paths: list[Path] | None = None,
    conn: psycopg.Connection | None = None,
    lang_filter: str = "en",
) -> tuple[int, int]:
    """Incremental index: only changed/new pages.

    Compares discovered pages with existing hashes and only
    indexes pages that are new or have changed.

    Args:
        paths: Paths to search.
        conn: psycopg connection. Obtains from pool if not provided.
        lang_filter: Only index pages in this language.

    Returns:
        Tuple of (added, updated) counts.
    """
    if conn is not None:
        return _index_incremental_impl(conn, paths, lang_filter)

    with get_conn(schema=PG_SCHEMA) as c:
        return _index_incremental_impl(c, paths, lang_filter)


def _index_incremental_impl(
    conn: psycopg.Connection,
    paths: list[Path] | None,
    lang_filter: str,
) -> tuple[int, int]:
    # Get existing hashes
    existing_hashes = get_all_hashes(conn)

    # Discover pages
    pages = list(discover_man_pages(paths))
    if lang_filter:
        pages = [p for p in pages if p.lang == lang_filter]

    added = 0
    updated = 0

    for page in pages:
        key = (page.name, page.section, page.lang)

        # Render to check hash
        text = render_manpage(page.name, page.section, page.lang)
        if not text:
            continue

        text = normalize_text(text)
        new_hash = compute_hash(text)

        if key not in existing_hashes:
            # New page
            if _index_page_impl(
                conn,
                page.name,
                page.section,
                page.lang,
                page.source_path,
            ):
                added += 1
        elif existing_hashes[key] != new_hash:
            # Changed page
            if _index_page_impl(
                conn,
                page.name,
                page.section,
                page.lang,
                page.source_path,
            ):
                updated += 1

    logger.info(f"Incremental index: {added} added, {updated} updated")
    return added, updated


def seed_core_commands(
    conn: psycopg.Connection | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> int:
    """Seed the Man Vault with essential core commands.

    This ensures critical system administration documentation is always
    available immediately at launch, before full indexing completes.

    The core commands include: systemctl, journalctl, ufw, netplan, ip,
    docker, apt, snap, crontab, smartctl, sensors, df, du, lsof, ps,
    grep, sed, tar, curl, and other bare essentials.

    Args:
        conn: psycopg connection. Obtains from pool if not provided.
        progress_callback: Called with (current, total) for progress.

    Returns:
        Number of core commands successfully indexed.
    """
    if conn is not None:
        return _seed_core_commands_impl(conn, progress_callback)

    with get_conn(schema=PG_SCHEMA) as c:
        return _seed_core_commands_impl(c, progress_callback)


def _seed_core_commands_impl(
    conn: psycopg.Connection,
    progress_callback: Callable[[int, int], None] | None,
) -> int:
    total = len(CORE_COMMANDS)
    count = 0
    skipped = 0

    logger.info(f"Seeding {total} core commands...")

    for i, (name, section) in enumerate(CORE_COMMANDS):
        try:
            # Check if already indexed
            existing = get_doc(conn, name, section, "en")
            if existing:
                skipped += 1
                if progress_callback:
                    progress_callback(i + 1, total)
                continue

            # Try to index the command
            if _index_page_impl(conn, name, section, "en", None):
                count += 1
                logger.debug(f"Seeded: {name}({section})")
            else:
                logger.debug(f"Not available: {name}({section})")

        except Exception as e:
            logger.warning(f"Failed to seed {name}({section}): {e}")

        if progress_callback:
            progress_callback(i + 1, total)

    logger.info(f"Core seeding complete: {count} new, {skipped} already indexed")
    return count


def get_seeded_count(conn: psycopg.Connection | None = None) -> int:
    """Get the number of core commands currently indexed.

    Args:
        conn: psycopg connection. Obtains from pool if not provided.

    Returns:
        Number of core commands in the index.
    """
    if conn is not None:
        return _get_seeded_count_impl(conn)

    with get_conn(schema=PG_SCHEMA) as c:
        return _get_seeded_count_impl(c)


def _get_seeded_count_impl(conn: psycopg.Connection) -> int:
    count = 0
    for name, section in CORE_COMMANDS:
        if get_doc(conn, name, section, "en"):
            count += 1
    return count


def is_core_seeded(conn: psycopg.Connection | None = None, threshold: float = 0.5) -> bool:
    """Check if core commands have been seeded.

    Args:
        conn: psycopg connection. Obtains from pool if not provided.
        threshold: Minimum fraction of core commands that must be indexed.

    Returns:
        True if at least threshold fraction of core commands are indexed.
    """
    seeded = get_seeded_count(conn)
    return seeded >= len(CORE_COMMANDS) * threshold
