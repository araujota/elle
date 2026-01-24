"""Tests for the file handler."""

from __future__ import annotations

import pytest

from elle.ops.files.handler import (
    FileHandler,
    copy_file,
    create_directory,
    delete_file,
    execute_operations,
    get_handler,
    move_file,
    preview_operations,
)
from elle.ops.files.models import (
    FileOp,
    FileOpResult,
    FilePreview,
    FileRequest,
    FileResult,
)


class TestFileHandler:
    """Tests for FileHandler."""

    def test_get_handler_singleton(self) -> None:
        """get_handler should return consistent instance."""
        h1 = get_handler()
        h2 = get_handler()

        assert h1 is h2

    def test_handler_initialization(self) -> None:
        """Handler should initialize properly."""
        handler = FileHandler()
        assert handler is not None


class TestPreviewOperations:
    """Tests for preview functionality."""

    def test_preview_move_existing_file(self, tmp_path) -> None:
        """Preview should show move of existing file."""
        # Create source file
        source = tmp_path / "source.txt"
        source.write_text("content")

        dest = tmp_path / "dest.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(source), dest=str(dest)),
            ),
            description="Test move",
        )

        preview = handler.preview(request)

        assert isinstance(preview, FilePreview)
        assert len(preview.previews) == 1
        assert preview.previews[0].source_exists is True
        assert preview.previews[0].source_type == "file"

    def test_preview_nonexistent_source(self, tmp_path) -> None:
        """Preview should flag nonexistent source."""
        source = tmp_path / "nonexistent.txt"
        dest = tmp_path / "dest.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(source), dest=str(dest)),
            ),
            description="Test",
        )

        preview = handler.preview(request)

        assert preview.previews[0].source_exists is False
        assert preview.has_warnings is True
        assert preview.previews[0].warning is not None

    def test_preview_overwrite_warning(self, tmp_path) -> None:
        """Preview should warn about overwrites."""
        source = tmp_path / "source.txt"
        source.write_text("source content")

        dest = tmp_path / "dest.txt"
        dest.write_text("dest content")

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(source), dest=str(dest)),
            ),
            description="Test",
        )

        preview = handler.preview(request)

        assert preview.previews[0].would_overwrite is True
        assert preview.has_overwrites is True

    def test_preview_directory(self, tmp_path) -> None:
        """Preview should handle directories."""
        source_dir = tmp_path / "source_dir"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content")

        dest = tmp_path / "dest_dir"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(source_dir), dest=str(dest)),
            ),
            description="Test",
        )

        preview = handler.preview(request)

        assert preview.previews[0].source_type == "directory"
        assert preview.total_bytes > 0


class TestExecuteOperations:
    """Tests for execute functionality."""

    def test_execute_move(self, tmp_path) -> None:
        """Execute should move file."""
        source = tmp_path / "source.txt"
        source.write_text("content")

        dest = tmp_path / "dest.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(source), dest=str(dest)),
            ),
            description="Test move",
        )

        result = handler.execute(request)

        assert result.success is True
        assert not source.exists()
        assert dest.exists()
        assert dest.read_text() == "content"

    def test_execute_copy(self, tmp_path) -> None:
        """Execute should copy file."""
        source = tmp_path / "source.txt"
        source.write_text("content")

        dest = tmp_path / "dest.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="copy", source=str(source), dest=str(dest)),
            ),
            description="Test copy",
        )

        result = handler.execute(request)

        assert result.success is True
        assert source.exists()  # Source still exists
        assert dest.exists()
        assert dest.read_text() == "content"

    def test_execute_delete(self, tmp_path) -> None:
        """Execute should delete file."""
        source = tmp_path / "source.txt"
        source.write_text("content")

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="delete", source=str(source)),
            ),
            description="Test delete",
        )

        result = handler.execute(request)

        assert result.success is True
        assert not source.exists()

    def test_execute_mkdir(self, tmp_path) -> None:
        """Execute should create directory."""
        new_dir = tmp_path / "new_directory"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="mkdir", source=str(new_dir)),
            ),
            description="Test mkdir",
        )

        result = handler.execute(request)

        assert result.success is True
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_execute_rename(self, tmp_path) -> None:
        """Execute should rename file."""
        source = tmp_path / "old_name.txt"
        source.write_text("content")

        dest = tmp_path / "new_name.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="rename", source=str(source), dest=str(dest)),
            ),
            description="Test rename",
        )

        result = handler.execute(request)

        assert result.success is True
        assert not source.exists()
        assert dest.exists()

    def test_execute_dry_run(self, tmp_path) -> None:
        """Dry run should not modify files."""
        source = tmp_path / "source.txt"
        source.write_text("content")

        dest = tmp_path / "dest.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(source), dest=str(dest)),
            ),
            description="Test dry run",
            dry_run=True,
        )

        result = handler.execute(request)

        assert result.success is True
        assert source.exists()  # Not moved
        assert not dest.exists()  # Not created

    def test_execute_failure_rollback(self, tmp_path) -> None:
        """Failed operation should rollback previous ops."""
        source1 = tmp_path / "source1.txt"
        source1.write_text("content1")

        dest1 = tmp_path / "dest1.txt"

        source2 = tmp_path / "nonexistent.txt"  # Will fail
        dest2 = tmp_path / "dest2.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(source1), dest=str(dest1)),
                FileOp(kind="move", source=str(source2), dest=str(dest2)),
            ),
            description="Test rollback",
            atomic=True,
        )

        result = handler.execute(request)

        assert result.success is False
        assert result.rollback_applied is True
        # First move should be rolled back
        assert source1.exists()
        assert not dest1.exists()


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_move_file_function(self, tmp_path) -> None:
        """move_file should work."""
        source = tmp_path / "source.txt"
        source.write_text("content")

        dest = tmp_path / "dest.txt"

        result = move_file(source, dest)

        assert result.success is True
        assert dest.exists()

    def test_copy_file_function(self, tmp_path) -> None:
        """copy_file should work."""
        source = tmp_path / "source.txt"
        source.write_text("content")

        dest = tmp_path / "dest.txt"

        result = copy_file(source, dest)

        assert result.success is True
        assert source.exists()
        assert dest.exists()

    def test_delete_file_function(self, tmp_path) -> None:
        """delete_file should work."""
        source = tmp_path / "source.txt"
        source.write_text("content")

        result = delete_file(source)

        assert result.success is True
        assert not source.exists()

    def test_create_directory_function(self, tmp_path) -> None:
        """create_directory should work."""
        new_dir = tmp_path / "new_dir"

        result = create_directory(new_dir)

        assert result.success is True
        assert new_dir.exists()


class TestFileOp:
    """Tests for FileOp model."""

    def test_move_op(self) -> None:
        """Move operation should be valid."""
        op = FileOp(kind="move", source="/src", dest="/dst")

        assert op.kind == "move"
        assert op.source == "/src"
        assert op.dest == "/dst"

    def test_copy_op_recursive(self) -> None:
        """Copy operation can be recursive."""
        op = FileOp(kind="copy", source="/src", dest="/dst", recursive=True)

        assert op.recursive is True

    def test_delete_op(self) -> None:
        """Delete operation should not require dest."""
        op = FileOp(kind="delete", source="/file")

        assert op.dest is None

    def test_overwrite_flag(self) -> None:
        """Overwrite flag should work."""
        op = FileOp(kind="move", source="/src", dest="/dst", overwrite=True)

        assert op.overwrite is True

    def test_frozen(self) -> None:
        """FileOp should be immutable."""
        op = FileOp(kind="move", source="/src", dest="/dst")

        with pytest.raises(Exception):
            op.kind = "copy"  # type: ignore[misc]


class TestFileResult:
    """Tests for FileResult model."""

    def test_success_result(self) -> None:
        """Success result should have success=True."""
        result = FileResult(success=True)

        assert result.success is True
        assert result.error is None

    def test_failure_result(self) -> None:
        """Failure result should have error."""
        result = FileResult(
            success=False,
            error="File not found",
        )

        assert result.success is False
        assert result.error == "File not found"

    def test_rollback_result(self) -> None:
        """Rollback result should indicate rollback."""
        result = FileResult(
            success=False,
            rollback_applied=True,
        )

        assert result.rollback_applied is True

    def test_statistics(self) -> None:
        """Result should include statistics."""
        result = FileResult(
            success=True,
            total_bytes=1024,
            total_files=5,
        )

        assert result.total_bytes == 1024
        assert result.total_files == 5

    def test_frozen(self) -> None:
        """FileResult should be immutable."""
        result = FileResult(success=True)

        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]
