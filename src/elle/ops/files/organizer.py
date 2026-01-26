"""Smart file organization.

Provides intelligent file organization by type, date, size, or custom
categories for directories like Downloads, Desktop, etc.
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from elle.ops.files.handler import FileHandler, get_handler
from elle.ops.files.models import (
    FileCategory,
    FileOp,
    FileRequest,
    OrganizeRequest,
    OrganizeResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Default Categories
# =============================================================================

DEFAULT_CATEGORIES: tuple[FileCategory, ...] = (
    FileCategory(
        name="images",
        patterns=(
            "*.jpg",
            "*.jpeg",
            "*.png",
            "*.gif",
            "*.webp",
            "*.svg",
            "*.bmp",
            "*.ico",
            "*.tiff",
            "*.tif",
            "*.raw",
            "*.heic",
            "*.heif",
        ),
        destination="images",
    ),
    FileCategory(
        name="documents",
        patterns=(
            "*.pdf",
            "*.doc",
            "*.docx",
            "*.txt",
            "*.md",
            "*.odt",
            "*.rtf",
            "*.xls",
            "*.xlsx",
            "*.ods",
            "*.ppt",
            "*.pptx",
            "*.odp",
            "*.csv",
            "*.epub",
            "*.mobi",
        ),
        destination="documents",
    ),
    FileCategory(
        name="videos",
        patterns=("*.mp4", "*.mkv", "*.avi", "*.mov", "*.wmv", "*.webm", "*.flv", "*.m4v", "*.mpeg", "*.mpg", "*.3gp"),
        destination="videos",
    ),
    FileCategory(
        name="audio",
        patterns=("*.mp3", "*.wav", "*.flac", "*.ogg", "*.m4a", "*.aac", "*.wma", "*.opus", "*.aiff"),
        destination="audio",
    ),
    FileCategory(
        name="archives",
        patterns=(
            "*.zip",
            "*.tar",
            "*.gz",
            "*.bz2",
            "*.xz",
            "*.7z",
            "*.rar",
            "*.tar.gz",
            "*.tar.bz2",
            "*.tar.xz",
            "*.tgz",
        ),
        destination="archives",
    ),
    FileCategory(
        name="code",
        patterns=(
            "*.py",
            "*.js",
            "*.ts",
            "*.go",
            "*.rs",
            "*.java",
            "*.c",
            "*.cpp",
            "*.h",
            "*.hpp",
            "*.cs",
            "*.rb",
            "*.php",
            "*.swift",
            "*.kt",
            "*.scala",
            "*.sh",
            "*.bash",
            "*.zsh",
            "*.html",
            "*.css",
            "*.json",
            "*.yaml",
            "*.yml",
            "*.xml",
            "*.sql",
        ),
        destination="code",
    ),
    FileCategory(
        name="installers",
        patterns=("*.deb", "*.rpm", "*.exe", "*.msi", "*.dmg", "*.pkg", "*.appimage", "*.flatpak", "*.snap"),
        destination="installers",
    ),
    FileCategory(
        name="fonts",
        patterns=("*.ttf", "*.otf", "*.woff", "*.woff2", "*.eot"),
        destination="fonts",
    ),
    FileCategory(
        name="torrents",
        patterns=("*.torrent",),
        destination="torrents",
    ),
    FileCategory(
        name="data",
        patterns=("*.db", "*.sqlite", "*.sqlite3", "*.mdb", "*.accdb", "*.bak"),
        destination="data",
    ),
)


# =============================================================================
# File Organizer
# =============================================================================


class FileOrganizer:
    """Smart file organization for user directories.

    Organizes files by type, date, size, or custom categories.

    Usage:
        organizer = FileOrganizer()

        # Analyze directory
        result = organizer.analyze("/home/user/Downloads")
        for op in result.operations:
            print(f"Would move {op.source} to {op.dest}")

        # Organize by type
        result = organizer.organize(OrganizeRequest(
            source_dir="/home/user/Downloads",
            mode="by_type",
            dry_run=False,
        ))
    """

    def __init__(
        self,
        handler: FileHandler | None = None,
        categories: tuple[FileCategory, ...] | None = None,
    ) -> None:
        """Initialize the organizer.

        Args:
            handler: FileHandler to use for operations.
            categories: Default categories to use.
        """
        self._handler = handler or get_handler()
        self._default_categories = categories or DEFAULT_CATEGORIES

    def analyze(
        self,
        directory: str | Path,
        categories: tuple[FileCategory, ...] | None = None,
        include_hidden: bool = False,
        recursive: bool = False,
    ) -> OrganizeResult:
        """Analyze a directory and propose organization.

        Does not modify any files - only analyzes and proposes moves.

        Args:
            directory: Directory to analyze.
            categories: Categories to use (defaults to default categories).
            include_hidden: Include hidden files.
            recursive: Analyze subdirectories.

        Returns:
            OrganizeResult with proposed operations.
        """
        directory = Path(directory)
        if not directory.exists():
            return OrganizeResult(
                success=False,
                error=f"Directory does not exist: {directory}",
            )

        if not directory.is_dir():
            return OrganizeResult(
                success=False,
                error=f"Not a directory: {directory}",
            )

        categories = categories or self._default_categories
        operations: list[FileOp] = []
        by_category: dict[str, int] = {}
        total_bytes = 0
        skipped = 0

        # Get files to process
        files = directory.rglob("*") if recursive else directory.iterdir()

        for path in files:
            # Skip directories
            if path.is_dir():
                continue

            # Skip hidden files if not requested
            if not include_hidden and path.name.startswith("."):
                skipped += 1
                continue

            # Skip files already in category subdirectories
            if path.parent != directory and not recursive:
                continue

            # Categorize the file
            category = self._categorize(path, categories)
            if category is None:
                skipped += 1
                continue

            # Determine destination
            dest_dir = directory / (category.destination or category.name)
            dest = dest_dir / path.name

            # Skip if already in correct location
            if path.parent == dest_dir:
                skipped += 1
                continue

            # Create move operation
            operations.append(
                FileOp(
                    kind="move",
                    source=str(path),
                    dest=str(dest),
                )
            )

            # Track statistics
            by_category[category.name] = by_category.get(category.name, 0) + 1
            total_bytes += path.stat().st_size

        return OrganizeResult(
            success=True,
            files_moved=len(operations),
            files_skipped=skipped,
            bytes_organized=total_bytes,
            by_category=by_category,
            operations=tuple(operations),
        )

    def organize(self, request: OrganizeRequest) -> OrganizeResult:
        """Organize files in a directory.

        Args:
            request: Organization request.

        Returns:
            OrganizeResult with outcome.
        """
        directory = Path(request.source_dir)
        categories = request.categories or self._default_categories

        # Select organization mode
        if request.mode == "by_type":
            result = self.analyze(
                directory,
                categories,
                request.include_hidden,
                request.recursive,
            )
        elif request.mode == "by_date":
            result = self._analyze_by_date(
                directory,
                request.include_hidden,
                request.recursive,
            )
        elif request.mode == "by_size":
            result = self._analyze_by_size(
                directory,
                request.include_hidden,
                request.recursive,
            )
        elif request.mode == "custom":
            result = self.analyze(
                directory,
                categories,
                request.include_hidden,
                request.recursive,
            )
        else:
            return OrganizeResult(
                success=False,
                error=f"Unknown organization mode: {request.mode}",
            )

        if not result.success or request.dry_run:
            return result

        # Execute the operations
        if result.operations:
            file_request = FileRequest(
                operations=result.operations,
                description=f"Organize {directory} by {request.mode}",
                atomic=True,
            )
            file_result = self._handler.execute(file_request)

            if not file_result.success:
                return OrganizeResult(
                    success=False,
                    files_moved=0,
                    files_skipped=result.files_skipped,
                    operations=result.operations,
                    error=file_result.error,
                )

        logger.info(
            f"Organized {result.files_moved} files in {directory} ({result.bytes_organized / 1024 / 1024:.1f} MB)"
        )

        return result

    def organize_by_type(
        self,
        directory: str | Path,
        dry_run: bool = False,
    ) -> OrganizeResult:
        """Organize files by type (convenience method).

        Args:
            directory: Directory to organize.
            dry_run: If True, only analyze.

        Returns:
            OrganizeResult with outcome.
        """
        return self.organize(
            OrganizeRequest(
                source_dir=str(directory),
                mode="by_type",
                dry_run=dry_run,
            )
        )

    def organize_by_date(
        self,
        directory: str | Path,
        dry_run: bool = False,
    ) -> OrganizeResult:
        """Organize files by modification date (convenience method).

        Args:
            directory: Directory to organize.
            dry_run: If True, only analyze.

        Returns:
            OrganizeResult with outcome.
        """
        return self.organize(
            OrganizeRequest(
                source_dir=str(directory),
                mode="by_date",
                dry_run=dry_run,
            )
        )

    # =========================================================================
    # Organization Modes
    # =========================================================================

    def _analyze_by_date(
        self,
        directory: Path,
        include_hidden: bool = False,
        recursive: bool = False,
    ) -> OrganizeResult:
        """Organize files by modification date into YYYY/MM folders."""
        operations: list[FileOp] = []
        by_category: dict[str, int] = {}
        total_bytes = 0
        skipped = 0

        files = directory.rglob("*") if recursive else directory.iterdir()

        for path in files:
            if path.is_dir():
                continue

            if not include_hidden and path.name.startswith("."):
                skipped += 1
                continue

            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                year_month = mtime.strftime("%Y/%m")
                dest_dir = directory / year_month
                dest = dest_dir / path.name

                # Skip if already in correct location
                if path.parent == dest_dir:
                    skipped += 1
                    continue

                operations.append(FileOp(kind="move", source=str(path), dest=str(dest)))

                by_category[year_month] = by_category.get(year_month, 0) + 1
                total_bytes += path.stat().st_size

            except Exception as e:
                logger.warning(f"Failed to process {path}: {e}")
                skipped += 1

        return OrganizeResult(
            success=True,
            files_moved=len(operations),
            files_skipped=skipped,
            bytes_organized=total_bytes,
            by_category=by_category,
            operations=tuple(operations),
        )

    def _analyze_by_size(
        self,
        directory: Path,
        include_hidden: bool = False,
        recursive: bool = False,
    ) -> OrganizeResult:
        """Organize files by size into small/medium/large folders."""
        SIZE_CATEGORIES = {
            "small": (0, 1024 * 1024),  # < 1MB
            "medium": (1024 * 1024, 100 * 1024 * 1024),  # 1MB - 100MB
            "large": (100 * 1024 * 1024, float("inf")),  # > 100MB
        }

        operations: list[FileOp] = []
        by_category: dict[str, int] = {}
        total_bytes = 0
        skipped = 0

        files = directory.rglob("*") if recursive else directory.iterdir()

        for path in files:
            if path.is_dir():
                continue

            if not include_hidden and path.name.startswith("."):
                skipped += 1
                continue

            try:
                size = path.stat().st_size
                category_name = None

                for cat_name, (min_size, max_size) in SIZE_CATEGORIES.items():
                    if min_size <= size < max_size:
                        category_name = cat_name
                        break

                if category_name is None:
                    skipped += 1
                    continue

                dest_dir = directory / category_name
                dest = dest_dir / path.name

                if path.parent == dest_dir:
                    skipped += 1
                    continue

                operations.append(FileOp(kind="move", source=str(path), dest=str(dest)))

                by_category[category_name] = by_category.get(category_name, 0) + 1
                total_bytes += size

            except Exception as e:
                logger.warning(f"Failed to process {path}: {e}")
                skipped += 1

        return OrganizeResult(
            success=True,
            files_moved=len(operations),
            files_skipped=skipped,
            bytes_organized=total_bytes,
            by_category=by_category,
            operations=tuple(operations),
        )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _categorize(
        self,
        path: Path,
        categories: tuple[FileCategory, ...],
    ) -> FileCategory | None:
        """Determine category for a file.

        Args:
            path: File path.
            categories: Available categories.

        Returns:
            Matching category or None.
        """
        filename = path.name.lower()

        for category in categories:
            for pattern in category.patterns:
                if fnmatch.fnmatch(filename, pattern.lower()):
                    return category

        return None

    def get_category(
        self,
        path: str | Path,
        categories: tuple[FileCategory, ...] | None = None,
    ) -> str | None:
        """Get the category name for a file.

        Args:
            path: File path.
            categories: Categories to check.

        Returns:
            Category name or None.
        """
        categories = categories or self._default_categories
        category = self._categorize(Path(path), categories)
        return category.name if category else None


# =============================================================================
# Module-level convenience functions
# =============================================================================

_organizer: FileOrganizer | None = None


def get_organizer() -> FileOrganizer:
    """Get the global FileOrganizer instance.

    Returns:
        FileOrganizer instance.
    """
    global _organizer
    if _organizer is None:
        _organizer = FileOrganizer()
    return _organizer


def analyze_directory(
    directory: str | Path,
    mode: Literal["by_type", "by_date", "by_size"] = "by_type",
) -> OrganizeResult:
    """Analyze a directory for organization (convenience function).

    Args:
        directory: Directory to analyze.
        mode: Organization mode.

    Returns:
        OrganizeResult with proposed operations.
    """
    organizer = get_organizer()
    if mode == "by_type":
        return organizer.analyze(directory)
    elif mode == "by_date":
        return organizer._analyze_by_date(Path(directory))
    else:
        return organizer._analyze_by_size(Path(directory))


def organize_directory(
    directory: str | Path,
    mode: Literal["by_type", "by_date", "by_size"] = "by_type",
    dry_run: bool = False,
) -> OrganizeResult:
    """Organize a directory (convenience function).

    Args:
        directory: Directory to organize.
        mode: Organization mode.
        dry_run: If True, only analyze.

    Returns:
        OrganizeResult with outcome.
    """
    return get_organizer().organize(
        OrganizeRequest(
            source_dir=str(directory),
            mode=mode,
            dry_run=dry_run,
        )
    )


def categorize_file(path: str | Path) -> str | None:
    """Get the category for a file (convenience function).

    Args:
        path: File path.

    Returns:
        Category name or None.
    """
    return get_organizer().get_category(path)
