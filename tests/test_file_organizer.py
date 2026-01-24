"""Tests for the file organizer."""

from __future__ import annotations

import pytest

from elle.ops.files.organizer import (
    DEFAULT_CATEGORIES,
    FileOrganizer,
    analyze_directory,
    categorize_file,
    get_organizer,
    organize_directory,
)
from elle.ops.files.models import (
    FileCategory,
    OrganizeRequest,
    OrganizeResult,
)


class TestFileOrganizer:
    """Tests for FileOrganizer."""

    def test_get_organizer_singleton(self) -> None:
        """get_organizer should return consistent instance."""
        o1 = get_organizer()
        o2 = get_organizer()

        assert o1 is o2

    def test_organizer_initialization(self) -> None:
        """Organizer should initialize with default categories."""
        organizer = FileOrganizer()

        assert organizer._default_categories is not None
        assert len(organizer._default_categories) > 0


class TestDefaultCategories:
    """Tests for default categories."""

    def test_images_category_exists(self) -> None:
        """Images category should exist."""
        names = [c.name for c in DEFAULT_CATEGORIES]
        assert "images" in names

    def test_documents_category_exists(self) -> None:
        """Documents category should exist."""
        names = [c.name for c in DEFAULT_CATEGORIES]
        assert "documents" in names

    def test_videos_category_exists(self) -> None:
        """Videos category should exist."""
        names = [c.name for c in DEFAULT_CATEGORIES]
        assert "videos" in names

    def test_audio_category_exists(self) -> None:
        """Audio category should exist."""
        names = [c.name for c in DEFAULT_CATEGORIES]
        assert "audio" in names

    def test_archives_category_exists(self) -> None:
        """Archives category should exist."""
        names = [c.name for c in DEFAULT_CATEGORIES]
        assert "archives" in names

    def test_code_category_exists(self) -> None:
        """Code category should exist."""
        names = [c.name for c in DEFAULT_CATEGORIES]
        assert "code" in names

    def test_categories_have_patterns(self) -> None:
        """All categories should have patterns."""
        for category in DEFAULT_CATEGORIES:
            assert len(category.patterns) > 0

    def test_categories_have_destinations(self) -> None:
        """All categories should have destinations."""
        for category in DEFAULT_CATEGORIES:
            assert category.destination is not None


class TestCategorizeFile:
    """Tests for file categorization."""

    def test_categorize_image_jpg(self) -> None:
        """JPG should be categorized as image."""
        category = categorize_file("photo.jpg")
        assert category == "images"

    def test_categorize_image_png(self) -> None:
        """PNG should be categorized as image."""
        category = categorize_file("screenshot.png")
        assert category == "images"

    def test_categorize_document_pdf(self) -> None:
        """PDF should be categorized as document."""
        category = categorize_file("report.pdf")
        assert category == "documents"

    def test_categorize_document_docx(self) -> None:
        """DOCX should be categorized as document."""
        category = categorize_file("essay.docx")
        assert category == "documents"

    def test_categorize_video_mp4(self) -> None:
        """MP4 should be categorized as video."""
        category = categorize_file("movie.mp4")
        assert category == "videos"

    def test_categorize_audio_mp3(self) -> None:
        """MP3 should be categorized as audio."""
        category = categorize_file("song.mp3")
        assert category == "audio"

    def test_categorize_archive_zip(self) -> None:
        """ZIP should be categorized as archive."""
        category = categorize_file("files.zip")
        assert category == "archives"

    def test_categorize_code_py(self) -> None:
        """Python file should be categorized as code."""
        category = categorize_file("script.py")
        assert category == "code"

    def test_categorize_installer_deb(self) -> None:
        """DEB should be categorized as installer."""
        category = categorize_file("package.deb")
        assert category == "installers"

    def test_categorize_unknown(self) -> None:
        """Unknown extension should return None."""
        category = categorize_file("random.xyz")
        assert category is None

    def test_categorize_case_insensitive(self) -> None:
        """Categorization should be case insensitive."""
        assert categorize_file("PHOTO.JPG") == "images"
        assert categorize_file("Photo.Jpg") == "images"


class TestAnalyzeDirectory:
    """Tests for directory analysis."""

    def test_analyze_empty_directory(self, tmp_path) -> None:
        """Analyzing empty directory should return no operations."""
        organizer = FileOrganizer()
        result = organizer.analyze(tmp_path)

        assert result.success is True
        assert result.files_moved == 0
        assert len(result.operations) == 0

    def test_analyze_nonexistent_directory(self, tmp_path) -> None:
        """Analyzing nonexistent directory should fail."""
        organizer = FileOrganizer()
        result = organizer.analyze(tmp_path / "nonexistent")

        assert result.success is False
        assert result.error is not None

    def test_analyze_file_not_directory(self, tmp_path) -> None:
        """Analyzing a file should fail."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        organizer = FileOrganizer()
        result = organizer.analyze(test_file)

        assert result.success is False

    def test_analyze_mixed_files(self, tmp_path) -> None:
        """Analyzing mixed files should categorize correctly."""
        # Create test files
        (tmp_path / "photo.jpg").write_text("image data")
        (tmp_path / "doc.pdf").write_text("pdf data")
        (tmp_path / "song.mp3").write_text("audio data")
        (tmp_path / "unknown.xyz").write_text("unknown")

        organizer = FileOrganizer()
        result = organizer.analyze(tmp_path)

        assert result.success is True
        # 3 known files should be moved (unknown skipped)
        assert result.files_moved == 3
        assert result.files_skipped >= 1  # unknown.xyz

        # Check categories
        assert "images" in result.by_category
        assert "documents" in result.by_category
        assert "audio" in result.by_category

    def test_analyze_excludes_hidden(self, tmp_path) -> None:
        """Analysis should exclude hidden files by default."""
        (tmp_path / ".hidden.jpg").write_text("hidden")
        (tmp_path / "visible.jpg").write_text("visible")

        organizer = FileOrganizer()
        result = organizer.analyze(tmp_path, include_hidden=False)

        assert result.files_moved == 1
        assert result.files_skipped >= 1

    def test_analyze_includes_hidden(self, tmp_path) -> None:
        """Analysis should include hidden files when requested."""
        (tmp_path / ".hidden.jpg").write_text("hidden")
        (tmp_path / "visible.jpg").write_text("visible")

        organizer = FileOrganizer()
        result = organizer.analyze(tmp_path, include_hidden=True)

        assert result.files_moved == 2

    def test_analyze_skips_already_organized(self, tmp_path) -> None:
        """Files already in category folders should be skipped."""
        # Create images folder with file
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        (images_dir / "photo.jpg").write_text("image")

        # Create file in root
        (tmp_path / "new_photo.jpg").write_text("image")

        organizer = FileOrganizer()
        result = organizer.analyze(tmp_path)

        # Only root file should be moved
        assert result.files_moved == 1


class TestOrganizeDirectory:
    """Tests for directory organization."""

    def test_organize_moves_files(self, tmp_path) -> None:
        """Organize should move files to category folders."""
        (tmp_path / "photo.jpg").write_text("image")
        (tmp_path / "doc.pdf").write_text("pdf")

        organizer = FileOrganizer()
        result = organizer.organize(OrganizeRequest(
            source_dir=str(tmp_path),
            mode="by_type",
        ))

        assert result.success is True
        assert result.files_moved == 2

        # Check files are moved
        assert (tmp_path / "images" / "photo.jpg").exists()
        assert (tmp_path / "documents" / "doc.pdf").exists()

    def test_organize_dry_run(self, tmp_path) -> None:
        """Dry run should not move files."""
        (tmp_path / "photo.jpg").write_text("image")

        organizer = FileOrganizer()
        result = organizer.organize(OrganizeRequest(
            source_dir=str(tmp_path),
            mode="by_type",
            dry_run=True,
        ))

        assert result.success is True
        assert result.files_moved == 1

        # File should not actually be moved
        assert (tmp_path / "photo.jpg").exists()
        assert not (tmp_path / "images").exists()

    def test_organize_by_date(self, tmp_path) -> None:
        """Organize by date should create year/month folders."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        organizer = FileOrganizer()
        result = organizer.organize(OrganizeRequest(
            source_dir=str(tmp_path),
            mode="by_date",
        ))

        assert result.success is True
        # File should be in YYYY/MM folder
        assert not (tmp_path / "file.txt").exists()

    def test_organize_by_size(self, tmp_path) -> None:
        """Organize by size should create size folders."""
        # Small file (< 1MB)
        small_file = tmp_path / "small.txt"
        small_file.write_text("small content")

        organizer = FileOrganizer()
        result = organizer.organize(OrganizeRequest(
            source_dir=str(tmp_path),
            mode="by_size",
        ))

        assert result.success is True
        # Small file should be in "small" folder
        assert (tmp_path / "small" / "small.txt").exists()


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_analyze_directory_function(self, tmp_path) -> None:
        """analyze_directory should work."""
        (tmp_path / "file.jpg").write_text("content")

        result = analyze_directory(tmp_path)

        assert isinstance(result, OrganizeResult)
        assert result.files_moved == 1

    def test_organize_directory_function(self, tmp_path) -> None:
        """organize_directory should work."""
        (tmp_path / "file.jpg").write_text("content")

        result = organize_directory(tmp_path, dry_run=True)

        assert isinstance(result, OrganizeResult)
        assert result.success is True


class TestFileCategory:
    """Tests for FileCategory model."""

    def test_category_creation(self) -> None:
        """Category should be creatable."""
        category = FileCategory(
            name="test",
            patterns=("*.test", "*.tst"),
            destination="test_files",
        )

        assert category.name == "test"
        assert len(category.patterns) == 2
        assert category.destination == "test_files"

    def test_category_frozen(self) -> None:
        """Category should be immutable."""
        category = FileCategory(
            name="test",
            patterns=("*.test",),
        )

        with pytest.raises(Exception):
            category.name = "other"  # type: ignore[misc]


class TestOrganizeResult:
    """Tests for OrganizeResult model."""

    def test_success_result(self) -> None:
        """Success result should have correct values."""
        result = OrganizeResult(
            success=True,
            files_moved=5,
            files_skipped=2,
            bytes_organized=1024,
        )

        assert result.success is True
        assert result.files_moved == 5
        assert result.files_skipped == 2

    def test_failure_result(self) -> None:
        """Failure result should have error."""
        result = OrganizeResult(
            success=False,
            error="Directory not found",
        )

        assert result.success is False
        assert result.error is not None

    def test_by_category(self) -> None:
        """Result should include category counts."""
        result = OrganizeResult(
            success=True,
            by_category={"images": 3, "documents": 2},
        )

        assert result.by_category["images"] == 3
        assert result.by_category["documents"] == 2

    def test_frozen(self) -> None:
        """Result should be immutable."""
        result = OrganizeResult(success=True)

        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]
