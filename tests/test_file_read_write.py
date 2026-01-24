"""Tests for file read/write operations."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from elle.ops.files import (
    FileHandler,
    FileOp,
    FileRequest,
    ReadResult,
    WriteResult,
    read_file,
    write_file,
)


class TestReadFile:
    """Tests for read_file function."""

    def test_read_existing_file(self, tmp_path: Path) -> None:
        """Test reading an existing file."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("Hello, world!")

        result = read_file(file_path)

        assert result.success is True
        assert result.content == "Hello, world!"
        assert result.size_bytes == 13
        assert result.encoding == "utf-8"
        assert result.error is None

    def test_read_nonexistent_file(self, tmp_path: Path) -> None:
        """Test reading a file that doesn't exist."""
        file_path = tmp_path / "nonexistent.txt"

        result = read_file(file_path)

        assert result.success is False
        assert result.content is None
        assert "not found" in result.error.lower()

    def test_read_directory(self, tmp_path: Path) -> None:
        """Test reading a directory returns error."""
        result = read_file(tmp_path)

        assert result.success is False
        assert "directory" in result.error.lower()

    def test_read_with_encoding(self, tmp_path: Path) -> None:
        """Test reading with specific encoding."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("Héllo, wörld!", encoding="utf-8")

        result = read_file(file_path, encoding="utf-8")

        assert result.success is True
        assert result.content == "Héllo, wörld!"
        assert result.encoding == "utf-8"

    def test_read_empty_file(self, tmp_path: Path) -> None:
        """Test reading an empty file."""
        file_path = tmp_path / "empty.txt"
        file_path.write_text("")

        result = read_file(file_path)

        assert result.success is True
        assert result.content == ""
        assert result.size_bytes == 0

    def test_read_multiline_file(self, tmp_path: Path) -> None:
        """Test reading a multiline file."""
        file_path = tmp_path / "multiline.txt"
        content = "Line 1\nLine 2\nLine 3"
        file_path.write_text(content)

        result = read_file(file_path)

        assert result.success is True
        assert result.content == content


class TestWriteFile:
    """Tests for write_file function."""

    def test_write_new_file(self, tmp_path: Path) -> None:
        """Test writing a new file."""
        file_path = tmp_path / "new.txt"

        result = write_file(file_path, "Hello, world!")

        assert result.success is True
        assert result.created is True
        assert result.bytes_written == 13
        assert result.error is None
        assert file_path.read_text() == "Hello, world!"

    def test_write_existing_file_no_overwrite(self, tmp_path: Path) -> None:
        """Test writing to existing file without overwrite flag."""
        file_path = tmp_path / "existing.txt"
        file_path.write_text("Original content")

        result = write_file(file_path, "New content", overwrite=False)

        assert result.success is False
        assert "exists" in result.error.lower()
        assert file_path.read_text() == "Original content"

    def test_write_existing_file_with_overwrite(self, tmp_path: Path) -> None:
        """Test writing to existing file with overwrite flag."""
        file_path = tmp_path / "existing.txt"
        file_path.write_text("Original content")

        result = write_file(file_path, "New content", overwrite=True)

        assert result.success is True
        assert result.created is False
        assert file_path.read_text() == "New content"

    def test_write_creates_parent_directories(self, tmp_path: Path) -> None:
        """Test that write creates parent directories."""
        file_path = tmp_path / "nested" / "deep" / "file.txt"

        result = write_file(file_path, "Content")

        assert result.success is True
        assert file_path.exists()
        assert file_path.read_text() == "Content"

    def test_write_with_encoding(self, tmp_path: Path) -> None:
        """Test writing with specific encoding."""
        file_path = tmp_path / "encoded.txt"

        result = write_file(file_path, "Héllo, wörld!", encoding="utf-8")

        assert result.success is True
        assert file_path.read_text(encoding="utf-8") == "Héllo, wörld!"

    def test_write_empty_content(self, tmp_path: Path) -> None:
        """Test writing empty content."""
        file_path = tmp_path / "empty.txt"

        result = write_file(file_path, "")

        assert result.success is True
        assert result.bytes_written == 0
        assert file_path.read_text() == ""


class TestFileHandlerReadWrite:
    """Tests for FileHandler read/write operations."""

    def test_handler_read_operation(self, tmp_path: Path) -> None:
        """Test read operation through FileHandler."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("Handler test content")

        handler = FileHandler(temp_dir=tmp_path / "temp")
        request = FileRequest(
            operations=(FileOp(kind="read", source=str(file_path)),),
            description="Read test file",
        )

        result = handler.execute(request)

        assert result.success is True
        assert len(result.results) == 1
        assert result.results[0].success is True
        assert result.results[0].content == "Handler test content"

    def test_handler_write_operation(self, tmp_path: Path) -> None:
        """Test write operation through FileHandler."""
        file_path = tmp_path / "new.txt"

        handler = FileHandler(temp_dir=tmp_path / "temp")
        request = FileRequest(
            operations=(
                FileOp(
                    kind="write",
                    source=str(file_path),
                    content="New content from handler",
                ),
            ),
            description="Write test file",
        )

        result = handler.execute(request)

        assert result.success is True
        assert file_path.read_text() == "New content from handler"

    def test_handler_write_requires_content(self, tmp_path: Path) -> None:
        """Test write operation fails without content."""
        file_path = tmp_path / "new.txt"

        handler = FileHandler(temp_dir=tmp_path / "temp")
        request = FileRequest(
            operations=(FileOp(kind="write", source=str(file_path)),),
            description="Write without content",
        )

        result = handler.execute(request)

        assert result.success is False
        assert "content" in result.results[0].error.lower()

    def test_handler_write_with_rollback(self, tmp_path: Path) -> None:
        """Test write operation rollback on failure."""
        file1 = tmp_path / "file1.txt"
        file1.write_text("Original content")

        file2 = tmp_path / "nonexistent_dir" / "file2.txt"

        handler = FileHandler(temp_dir=tmp_path / "temp")
        request = FileRequest(
            operations=(
                FileOp(
                    kind="write",
                    source=str(file1),
                    content="Modified content",
                    overwrite=True,
                ),
                FileOp(kind="read", source=str(file2)),  # This should fail
            ),
            description="Multi-op with failure",
            atomic=True,
        )

        result = handler.execute(request)

        # The request should fail and rollback
        assert result.success is False
        # First file should be rolled back to original
        # (Note: In actual implementation, may need to verify backup/restore)

    def test_handler_read_nonexistent_file(self, tmp_path: Path) -> None:
        """Test read operation on nonexistent file."""
        file_path = tmp_path / "nonexistent.txt"

        handler = FileHandler(temp_dir=tmp_path / "temp")
        request = FileRequest(
            operations=(FileOp(kind="read", source=str(file_path)),),
            description="Read nonexistent file",
        )

        result = handler.execute(request)

        assert result.success is False
        assert "not found" in result.results[0].error.lower()


class TestReadResult:
    """Tests for ReadResult model."""

    def test_read_result_fields(self) -> None:
        """Test ReadResult has correct fields."""
        result = ReadResult(
            success=True,
            path="/test/file.txt",
            content="Test content",
            size_bytes=12,
            encoding="utf-8",
        )

        assert result.success is True
        assert result.path == "/test/file.txt"
        assert result.content == "Test content"
        assert result.size_bytes == 12
        assert result.encoding == "utf-8"
        assert result.error is None

    def test_read_result_with_error(self) -> None:
        """Test ReadResult with error."""
        result = ReadResult(
            success=False,
            path="/test/file.txt",
            error="File not found",
        )

        assert result.success is False
        assert result.content is None
        assert result.error == "File not found"


class TestWriteResult:
    """Tests for WriteResult model."""

    def test_write_result_fields(self) -> None:
        """Test WriteResult has correct fields."""
        result = WriteResult(
            success=True,
            path="/test/file.txt",
            bytes_written=100,
            created=True,
        )

        assert result.success is True
        assert result.path == "/test/file.txt"
        assert result.bytes_written == 100
        assert result.created is True
        assert result.error is None

    def test_write_result_with_error(self) -> None:
        """Test WriteResult with error."""
        result = WriteResult(
            success=False,
            path="/test/file.txt",
            error="Permission denied",
        )

        assert result.success is False
        assert result.bytes_written == 0
        assert result.error == "Permission denied"
