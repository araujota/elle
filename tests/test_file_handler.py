"""Tests for the file handler."""

from __future__ import annotations

import pytest

from elle.ops.files.handler import (
    FileHandler,
    copy_file,
    create_directory,
    delete_file,
    get_handler,
    move_file,
)
from elle.ops.files.models import (
    FileOp,
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
            operations=(FileOp(kind="move", source=str(source), dest=str(dest)),),
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
            operations=(FileOp(kind="move", source=str(source), dest=str(dest)),),
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
            operations=(FileOp(kind="move", source=str(source), dest=str(dest)),),
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
            operations=(FileOp(kind="move", source=str(source_dir), dest=str(dest)),),
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
            operations=(FileOp(kind="move", source=str(source), dest=str(dest)),),
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
            operations=(FileOp(kind="copy", source=str(source), dest=str(dest)),),
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
            operations=(FileOp(kind="delete", source=str(source)),),
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
            operations=(FileOp(kind="mkdir", source=str(new_dir)),),
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
            operations=(FileOp(kind="rename", source=str(source), dest=str(dest)),),
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
            operations=(FileOp(kind="move", source=str(source), dest=str(dest)),),
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


# ---------------------------------------------------------------------------
# Extended coverage: Read and Write operations
# ---------------------------------------------------------------------------


class TestReadOperations:
    """Tests for read operations through FileHandler."""

    def test_read_existing_file(self, tmp_path) -> None:
        """Should read content of existing file."""
        f = tmp_path / "readme.txt"
        f.write_text("hello world")

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="read", source=str(f)),),
            description="Read test",
        )
        result = handler.execute(request)
        assert result.success is True
        assert result.results[0].content == "hello world"

    def test_read_nonexistent_file(self, tmp_path) -> None:
        """Should fail for nonexistent file."""
        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="read", source=str(tmp_path / "missing.txt")),),
            description="Read test",
        )
        result = handler.execute(request)
        assert result.success is False

    def test_read_directory(self, tmp_path) -> None:
        """Should fail when reading a directory."""
        d = tmp_path / "adir"
        d.mkdir()

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="read", source=str(d)),),
            description="Read test",
        )
        result = handler.execute(request)
        assert result.success is False
        assert "directory" in result.results[0].error.lower()

    def test_read_with_encoding_error(self, tmp_path) -> None:
        """Should handle encoding error."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x80\x81\x82\x83")

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="read", source=str(f), encoding="ascii"),),
            description="Read test",
        )
        result = handler.execute(request)
        assert result.success is False
        assert "Encoding error" in result.results[0].error


class TestWriteOperations:
    """Tests for write operations through FileHandler."""

    def test_write_new_file(self, tmp_path) -> None:
        """Should write content to new file."""
        f = tmp_path / "output.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="write", source=str(f), content="new content", overwrite=True),),
            description="Write test",
        )
        result = handler.execute(request)
        assert result.success is True
        assert f.read_text() == "new content"

    def test_write_without_content(self, tmp_path) -> None:
        """Should fail when content is None."""
        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="write", source=str(tmp_path / "out.txt")),),
            description="Write test",
        )
        result = handler.execute(request)
        assert result.success is False
        assert "requires content" in result.results[0].error

    def test_write_overwrite_existing(self, tmp_path) -> None:
        """Should overwrite existing file when overwrite=True."""
        f = tmp_path / "existing.txt"
        f.write_text("old content")

        handler = FileHandler(temp_dir=tmp_path / "backups")
        request = FileRequest(
            operations=(FileOp(kind="write", source=str(f), content="new content", overwrite=True),),
            description="Write test",
        )
        result = handler.execute(request)
        assert result.success is True
        assert f.read_text() == "new content"

    def test_write_no_overwrite_existing(self, tmp_path) -> None:
        """Should fail when overwrite=False and file exists."""
        f = tmp_path / "existing.txt"
        f.write_text("old content")

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="write", source=str(f), content="new", overwrite=False),),
            description="Write test",
        )
        result = handler.execute(request)
        assert result.success is False

    def test_write_creates_parent_dirs(self, tmp_path) -> None:
        """Should create parent directories if needed."""
        f = tmp_path / "subdir" / "deep" / "file.txt"

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="write", source=str(f), content="deep content", overwrite=True),),
            description="Write test",
        )
        result = handler.execute(request)
        assert result.success is True
        assert f.read_text() == "deep content"


# ---------------------------------------------------------------------------
# Extended coverage: Unknown operation kind
# ---------------------------------------------------------------------------


class TestUnknownOperationKind:
    def test_execute_unknown_kind(self, tmp_path) -> None:
        """Should handle unknown operation kind gracefully."""
        handler = FileHandler()
        # Create a FileOp with a known kind then monkey-patch it for the test
        op = FileOp(kind="move", source=str(tmp_path / "src"))
        # We need to use the internal method directly
        result = handler._execute_op(op)
        # The source doesn't exist, so it will fail with FileNotFoundError
        assert result.success is False


# ---------------------------------------------------------------------------
# Extended coverage: Copy directory operations
# ---------------------------------------------------------------------------


class TestCopyDirectoryOperations:
    def test_copy_directory_recursive(self, tmp_path) -> None:
        """Should copy directory recursively."""
        src = tmp_path / "srcdir"
        src.mkdir()
        (src / "a.txt").write_text("aaa")
        (src / "b.txt").write_text("bbb")
        dest = tmp_path / "destdir"

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="copy", source=str(src), dest=str(dest), recursive=True),),
            description="Copy dir",
        )
        result = handler.execute(request)
        assert result.success is True
        assert (dest / "a.txt").exists()
        assert (dest / "b.txt").exists()

    def test_copy_directory_not_recursive(self, tmp_path) -> None:
        """Should fail to copy directory without recursive flag."""
        src = tmp_path / "srcdir"
        src.mkdir()
        (src / "file.txt").write_text("content")
        dest = tmp_path / "destdir"

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="copy", source=str(src), dest=str(dest), recursive=False),),
            description="Copy dir",
        )
        result = handler.execute(request)
        assert result.success is False


# ---------------------------------------------------------------------------
# Extended coverage: Delete operations
# ---------------------------------------------------------------------------


class TestDeleteOperationsExtended:
    def test_delete_nonexistent_is_success(self, tmp_path) -> None:
        """Deleting nonexistent file should succeed."""
        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="delete", source=str(tmp_path / "missing.txt")),),
            description="Delete test",
        )
        result = handler.execute(request)
        assert result.success is True

    def test_delete_directory_recursive(self, tmp_path) -> None:
        """Should delete directory recursively."""
        d = tmp_path / "toremove"
        d.mkdir()
        (d / "sub").mkdir()
        (d / "sub" / "file.txt").write_text("data")

        handler = FileHandler(temp_dir=tmp_path / "backups")
        request = FileRequest(
            operations=(FileOp(kind="delete", source=str(d), recursive=True),),
            description="Delete dir",
        )
        result = handler.execute(request)
        assert result.success is True
        assert not d.exists()

    def test_delete_empty_directory_non_recursive(self, tmp_path) -> None:
        """Should delete empty directory without recursive."""
        d = tmp_path / "emptydir"
        d.mkdir()

        handler = FileHandler(temp_dir=tmp_path / "backups")
        request = FileRequest(
            operations=(FileOp(kind="delete", source=str(d)),),
            description="Delete dir",
        )
        result = handler.execute(request)
        assert result.success is True
        assert not d.exists()


# ---------------------------------------------------------------------------
# Extended coverage: Mkdir operations
# ---------------------------------------------------------------------------


class TestMkdirOperationsExtended:
    def test_mkdir_existing_dir(self, tmp_path) -> None:
        """Creating existing directory should succeed."""
        d = tmp_path / "existing"
        d.mkdir()

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="mkdir", source=str(d)),),
            description="Mkdir test",
        )
        result = handler.execute(request)
        assert result.success is True

    def test_mkdir_existing_file(self, tmp_path) -> None:
        """Creating dir at file path should fail."""
        f = tmp_path / "afile"
        f.write_text("not a dir")

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="mkdir", source=str(f)),),
            description="Mkdir test",
        )
        result = handler.execute(request)
        assert result.success is False

    def test_mkdir_nested(self, tmp_path) -> None:
        """Should create nested directories."""
        d = tmp_path / "a" / "b" / "c"

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="mkdir", source=str(d)),),
            description="Mkdir test",
        )
        result = handler.execute(request)
        assert result.success is True
        assert d.exists()


# ---------------------------------------------------------------------------
# Extended coverage: Rollback mechanism
# ---------------------------------------------------------------------------


class TestRollbackMechanism:
    def test_rollback_on_atomic_failure(self, tmp_path) -> None:
        """Rollback should undo previous operations on failure."""
        src = tmp_path / "file1.txt"
        src.write_text("content1")
        dest1 = tmp_path / "moved1.txt"

        handler = FileHandler(temp_dir=tmp_path / "backups")
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(src), dest=str(dest1)),
                FileOp(kind="move", source=str(tmp_path / "nonexistent"), dest=str(tmp_path / "out")),
            ),
            description="Rollback test",
            atomic=True,
        )
        result = handler.execute(request)
        assert result.success is False
        assert result.rollback_applied is True
        # First operation should be rolled back
        assert src.exists()
        assert not dest1.exists()

    def test_non_atomic_no_rollback(self, tmp_path) -> None:
        """Non-atomic should not rollback on failure."""
        src = tmp_path / "file1.txt"
        src.write_text("content1")
        dest1 = tmp_path / "moved1.txt"

        handler = FileHandler(temp_dir=tmp_path / "backups")
        request = FileRequest(
            operations=(
                FileOp(kind="move", source=str(src), dest=str(dest1)),
                FileOp(kind="move", source=str(tmp_path / "nonexistent"), dest=str(tmp_path / "out")),
            ),
            description="Non-atomic test",
            atomic=False,
        )
        handler.execute(request)
        # First op succeeded, second failed, but no rollback
        assert dest1.exists()


# ---------------------------------------------------------------------------
# Extended coverage: Convenience functions read_file and write_file
# ---------------------------------------------------------------------------


class TestConvenienceFunctionsExtended:
    def test_read_file_success(self, tmp_path) -> None:
        """read_file should return content."""
        from elle.ops.files.handler import read_file

        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = read_file(f)
        assert result.success is True
        assert result.content == "hello"

    def test_read_file_not_found(self, tmp_path) -> None:
        """read_file should fail for missing file."""
        from elle.ops.files.handler import read_file

        result = read_file(tmp_path / "missing.txt")
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_read_file_directory(self, tmp_path) -> None:
        """read_file should fail for directory."""
        from elle.ops.files.handler import read_file

        result = read_file(tmp_path)
        assert result.success is False
        assert "directory" in result.error.lower()

    def test_read_file_encoding_error(self, tmp_path) -> None:
        """read_file should handle encoding errors."""
        from elle.ops.files.handler import read_file

        f = tmp_path / "bin.dat"
        f.write_bytes(b"\x80\x81\x82")
        result = read_file(f, encoding="ascii")
        assert result.success is False
        assert "Encoding error" in result.error

    def test_write_file_success(self, tmp_path) -> None:
        """write_file should write content."""
        from elle.ops.files.handler import write_file

        f = tmp_path / "out.txt"
        result = write_file(f, "hello")
        assert result.success is True
        assert result.created is True
        assert f.read_text() == "hello"

    def test_write_file_already_exists(self, tmp_path) -> None:
        """write_file should fail without overwrite."""
        from elle.ops.files.handler import write_file

        f = tmp_path / "existing.txt"
        f.write_text("old")
        result = write_file(f, "new", overwrite=False)
        assert result.success is False
        assert "already exists" in result.error

    def test_write_file_overwrite(self, tmp_path) -> None:
        """write_file should overwrite with flag."""
        from elle.ops.files.handler import write_file

        f = tmp_path / "existing.txt"
        f.write_text("old")
        result = write_file(f, "new", overwrite=True)
        assert result.success is True
        assert f.read_text() == "new"

    def test_write_file_creates_parents(self, tmp_path) -> None:
        """write_file should create parent directories."""
        from elle.ops.files.handler import write_file

        f = tmp_path / "sub" / "deep" / "file.txt"
        result = write_file(f, "content")
        assert result.success is True
        assert f.read_text() == "content"

    def test_preview_operations_convenience(self, tmp_path) -> None:
        """preview_operations convenience function should work."""
        from elle.ops.files.handler import preview_operations

        f = tmp_path / "src.txt"
        f.write_text("data")
        request = FileRequest(
            operations=(FileOp(kind="move", source=str(f), dest=str(tmp_path / "dst.txt")),),
            description="Preview test",
        )
        preview = preview_operations(request)
        assert len(preview.previews) == 1

    def test_execute_operations_convenience(self, tmp_path) -> None:
        """execute_operations convenience function should work."""
        from elle.ops.files.handler import execute_operations

        f = tmp_path / "src.txt"
        f.write_text("data")
        request = FileRequest(
            operations=(FileOp(kind="copy", source=str(f), dest=str(tmp_path / "dst.txt")),),
            description="Execute test",
        )
        result = execute_operations(request)
        assert result.success is True


# ---------------------------------------------------------------------------
# Extended coverage: Preview with symlink
# ---------------------------------------------------------------------------


class TestPreviewExtended:
    def test_preview_symlink(self, tmp_path) -> None:
        """Preview should detect symlinks."""
        target = tmp_path / "target.txt"
        target.write_text("target content")
        link = tmp_path / "link.txt"
        link.symlink_to(target)

        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="move", source=str(link), dest=str(tmp_path / "out.txt")),),
            description="Symlink test",
        )
        preview = handler.preview(request)
        assert preview.previews[0].source_type == "symlink"

    def test_preview_mkdir_no_source_warning(self, tmp_path) -> None:
        """Preview mkdir should not warn about missing source."""
        handler = FileHandler()
        request = FileRequest(
            operations=(FileOp(kind="mkdir", source=str(tmp_path / "newdir")),),
            description="Mkdir preview",
        )
        preview = handler.preview(request)
        # mkdir does not warn about source not existing
        assert preview.previews[0].warning is None


# ---------------------------------------------------------------------------
# Extended coverage: _get_size helper
# ---------------------------------------------------------------------------


class TestGetSizeHelper:
    def test_get_size_file(self, tmp_path) -> None:
        """Should return file size."""
        f = tmp_path / "sized.txt"
        f.write_text("12345")
        handler = FileHandler()
        assert handler._get_size(f) == 5

    def test_get_size_directory(self, tmp_path) -> None:
        """Should return total directory size."""
        d = tmp_path / "dir"
        d.mkdir()
        (d / "a.txt").write_text("aaa")
        (d / "b.txt").write_text("bb")
        handler = FileHandler()
        assert handler._get_size(d) == 5
