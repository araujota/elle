"""Tests for TextHandler and MarkdownBuilder."""

from __future__ import annotations

from pathlib import Path

from elle.ops.files import (
    MarkdownBuilder,
    TextHandler,
    TextOpResult,
    get_text_handler,
    reset_text_handler,
)


class TestTextHandler:
    """Tests for TextHandler."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_text_handler()

    def test_create_file(self, tmp_path: Path) -> None:
        """Test creating a new file."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"

        result = handler.create(file_path, "Hello, world!")

        assert result.success is True
        assert file_path.read_text() == "Hello, world!"

    def test_create_file_no_overwrite(self, tmp_path: Path) -> None:
        """Test create fails without overwrite flag."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Original")

        result = handler.create(file_path, "New content", overwrite=False)

        assert result.success is False
        assert file_path.read_text() == "Original"

    def test_create_file_with_overwrite(self, tmp_path: Path) -> None:
        """Test create succeeds with overwrite flag."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Original")

        result = handler.create(file_path, "New content", overwrite=True)

        assert result.success is True
        assert file_path.read_text() == "New content"

    def test_read_file(self, tmp_path: Path) -> None:
        """Test reading a file."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Test content")

        result = handler.read(file_path)

        assert result.success is True
        assert result.content == "Test content"

    def test_append_to_file(self, tmp_path: Path) -> None:
        """Test appending to a file."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Line 1")

        result = handler.append(file_path, "Line 2")

        assert result.success is True
        assert result.content == "Line 1\nLine 2"
        assert file_path.read_text() == "Line 1\nLine 2"

    def test_append_to_nonexistent_file(self, tmp_path: Path) -> None:
        """Test appending to nonexistent file creates it."""
        handler = TextHandler()
        file_path = tmp_path / "new.txt"

        result = handler.append(file_path, "First line")

        assert result.success is True
        assert file_path.read_text() == "First line"

    def test_append_with_custom_separator(self, tmp_path: Path) -> None:
        """Test appending with custom separator."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Item1")

        result = handler.append(file_path, "Item2", separator=", ")

        assert result.success is True
        assert result.content == "Item1, Item2"

    def test_prepend_to_file(self, tmp_path: Path) -> None:
        """Test prepending to a file."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Line 2")

        result = handler.prepend(file_path, "Line 1")

        assert result.success is True
        assert result.content == "Line 1\nLine 2"

    def test_prepend_to_nonexistent_file(self, tmp_path: Path) -> None:
        """Test prepending to nonexistent file creates it."""
        handler = TextHandler()
        file_path = tmp_path / "new.txt"

        result = handler.prepend(file_path, "First line")

        assert result.success is True
        assert file_path.read_text() == "First line"

    def test_replace_section(self, tmp_path: Path) -> None:
        """Test replacing a section between markers."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Header\n<!-- BEGIN -->\nold content\n<!-- BEGIN -->\nFooter")

        result = handler.replace_section(
            file_path,
            marker="<!-- BEGIN -->",
            content="new content",
        )

        assert result.success is True
        assert "new content" in result.content
        assert "old content" not in result.content

    def test_replace_section_with_end_marker(self, tmp_path: Path) -> None:
        """Test replacing section with different start/end markers."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Before\n<!-- START -->\nold\n<!-- END -->\nAfter")

        result = handler.replace_section(
            file_path,
            marker="<!-- START -->",
            end_marker="<!-- END -->",
            content="new",
        )

        assert result.success is True
        assert "new" in result.content

    def test_replace_section_no_marker_appends(self, tmp_path: Path) -> None:
        """Test replace_section appends if marker not found."""
        handler = TextHandler()
        file_path = tmp_path / "test.txt"
        file_path.write_text("Existing content")

        result = handler.replace_section(
            file_path,
            marker="<!-- CONFIG -->",
            content="new config",
        )

        assert result.success is True
        assert "Existing content" in result.content
        assert "<!-- CONFIG -->" in result.content
        assert "new config" in result.content

    def test_get_text_handler_singleton(self) -> None:
        """Test get_text_handler returns singleton."""
        handler1 = get_text_handler()
        handler2 = get_text_handler()

        assert handler1 is handler2


class TestMarkdownBuilder:
    """Tests for MarkdownBuilder."""

    def test_heading(self) -> None:
        """Test heading generation."""
        md = MarkdownBuilder().heading("Title", level=1).build()
        assert md == "# Title\n"

        md = MarkdownBuilder().heading("Subtitle", level=2).build()
        assert md == "## Subtitle\n"

    def test_heading_level_bounds(self) -> None:
        """Test heading level is bounded 1-6."""
        md = MarkdownBuilder().heading("Test", level=0).build()
        assert md.startswith("# ")

        md = MarkdownBuilder().heading("Test", level=10).build()
        assert md.startswith("###### ")

    def test_paragraph(self) -> None:
        """Test paragraph generation."""
        md = MarkdownBuilder().paragraph("Some text.").build()
        assert md == "Some text.\n"

    def test_text(self) -> None:
        """Test raw text."""
        md = MarkdownBuilder().text("raw text").build()
        assert md == "raw text"

    def test_newline(self) -> None:
        """Test newline generation."""
        md = MarkdownBuilder().newline(2).build()
        assert md == "\n\n"

    def test_horizontal_rule(self) -> None:
        """Test horizontal rule."""
        md = MarkdownBuilder().horizontal_rule().build()
        assert "---" in md

    def test_code_block(self) -> None:
        """Test code block generation."""
        md = MarkdownBuilder().code_block("print('hello')", language="python").build()
        assert "```python" in md
        assert "print('hello')" in md
        assert "```" in md

    def test_inline_code(self) -> None:
        """Test inline code."""
        md = MarkdownBuilder().inline_code("code").build()
        assert md == "`code`"

    def test_unordered_list(self) -> None:
        """Test unordered list generation."""
        md = MarkdownBuilder().list_items(["Item 1", "Item 2", "Item 3"]).build()
        assert "- Item 1" in md
        assert "- Item 2" in md
        assert "- Item 3" in md

    def test_ordered_list(self) -> None:
        """Test ordered list generation."""
        md = MarkdownBuilder().list_items(["First", "Second"], ordered=True).build()
        assert "1. First" in md
        assert "2. Second" in md

    def test_nested_list(self) -> None:
        """Test nested list generation."""
        md = MarkdownBuilder().list_items(["Nested"], indent=1).build()
        assert "  - Nested" in md

    def test_checkbox_list(self) -> None:
        """Test checkbox list generation."""
        md = (
            MarkdownBuilder()
            .checkbox_list(
                [
                    ("Done task", True),
                    ("Pending task", False),
                ]
            )
            .build()
        )
        assert "- [x] Done task" in md
        assert "- [ ] Pending task" in md

    def test_table(self) -> None:
        """Test table generation."""
        md = (
            MarkdownBuilder()
            .table(
                headers=["Name", "Age"],
                rows=[["Alice", "30"], ["Bob", "25"]],
            )
            .build()
        )
        assert "| Name | Age |" in md
        assert "| --- | --- |" in md
        assert "| Alice | 30 |" in md
        assert "| Bob | 25 |" in md

    def test_table_with_alignment(self) -> None:
        """Test table with alignment."""
        md = (
            MarkdownBuilder()
            .table(
                headers=["Left", "Center", "Right"],
                rows=[["a", "b", "c"]],
                alignment=["left", "center", "right"],
            )
            .build()
        )
        assert "| --- |" in md
        assert ":---:" in md
        assert "---:" in md

    def test_blockquote(self) -> None:
        """Test blockquote generation."""
        md = MarkdownBuilder().blockquote("Quoted text").build()
        assert "> Quoted text" in md

    def test_multiline_blockquote(self) -> None:
        """Test multiline blockquote."""
        md = MarkdownBuilder().blockquote("Line 1\nLine 2").build()
        assert "> Line 1" in md
        assert "> Line 2" in md

    def test_link(self) -> None:
        """Test link generation."""
        md = MarkdownBuilder().link("Click here", "https://example.com").build()
        assert md == "[Click here](https://example.com)"

    def test_link_with_title(self) -> None:
        """Test link with title."""
        md = (
            MarkdownBuilder()
            .link(
                "Click here",
                "https://example.com",
                title="Example Site",
            )
            .build()
        )
        assert '[Click here](https://example.com "Example Site")' in md

    def test_image(self) -> None:
        """Test image generation."""
        md = MarkdownBuilder().image("Alt text", "image.png").build()
        assert "![Alt text](image.png)" in md

    def test_bold(self) -> None:
        """Test bold text."""
        md = MarkdownBuilder().bold("important").build()
        assert md == "**important**"

    def test_italic(self) -> None:
        """Test italic text."""
        md = MarkdownBuilder().italic("emphasized").build()
        assert md == "*emphasized*"

    def test_strikethrough(self) -> None:
        """Test strikethrough text."""
        md = MarkdownBuilder().strikethrough("deleted").build()
        assert md == "~~deleted~~"

    def test_definition_list(self) -> None:
        """Test definition list."""
        md = (
            MarkdownBuilder()
            .definition_list(
                [
                    ("Term 1", "Definition 1"),
                    ("Term 2", "Definition 2"),
                ]
            )
            .build()
        )
        assert "Term 1" in md
        assert ": Definition 1" in md

    def test_footnote(self) -> None:
        """Test footnote definition."""
        md = MarkdownBuilder().footnote("1", "This is a footnote.").build()
        assert "[^1]: This is a footnote." in md

    def test_footnote_ref(self) -> None:
        """Test footnote reference."""
        md = MarkdownBuilder().footnote_ref("1").build()
        assert md == "[^1]"

    def test_details(self) -> None:
        """Test collapsible details."""
        md = MarkdownBuilder().details("Click to expand", "Hidden content").build()
        assert "<details>" in md
        assert "<summary>Click to expand</summary>" in md
        assert "Hidden content" in md
        assert "</details>" in md

    def test_clear(self) -> None:
        """Test clearing content."""
        builder = MarkdownBuilder()
        builder.heading("Title")
        builder.clear()
        md = builder.build()
        assert md == ""

    def test_chaining(self) -> None:
        """Test method chaining."""
        md = (
            MarkdownBuilder()
            .heading("Installation", level=1)
            .paragraph("Follow these steps:")
            .list_items(["Step 1", "Step 2"])
            .code_block("pip install package", language="bash")
            .build()
        )

        assert "# Installation" in md
        assert "Follow these steps:" in md
        assert "- Step 1" in md
        assert "```bash" in md
        assert "pip install package" in md

    def test_save(self, tmp_path: Path) -> None:
        """Test saving markdown to file."""
        file_path = tmp_path / "doc.md"

        result = MarkdownBuilder().heading("Document").paragraph("Content").save(file_path)

        assert result.success is True
        assert file_path.exists()
        content = file_path.read_text()
        assert "# Document" in content
        assert "Content" in content

    def test_save_no_overwrite(self, tmp_path: Path) -> None:
        """Test save fails without overwrite."""
        file_path = tmp_path / "existing.md"
        file_path.write_text("Original")

        result = MarkdownBuilder().heading("New").save(file_path, overwrite=False)

        assert result.success is False


class TestTextOpResult:
    """Tests for TextOpResult model."""

    def test_text_op_result_fields(self) -> None:
        """Test TextOpResult has correct fields."""
        result = TextOpResult(
            success=True,
            path="/test/file.txt",
            content="Content",
            bytes_written=7,
        )

        assert result.success is True
        assert result.path == "/test/file.txt"
        assert result.content == "Content"
        assert result.bytes_written == 7
        assert result.error is None

    def test_text_op_result_with_error(self) -> None:
        """Test TextOpResult with error."""
        result = TextOpResult(
            success=False,
            path="/test/file.txt",
            error="Operation failed",
        )

        assert result.success is False
        assert result.error == "Operation failed"
