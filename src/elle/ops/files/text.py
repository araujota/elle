"""Text and Markdown file handler.

Provides utilities for creating and manipulating plain text and
markdown files with a fluent builder API.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from elle.ops.files.handler import read_file, write_file
from elle.ops.files.models import ReadResult, WriteResult

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class TextOpResult(BaseModel):
    """Result of a text file operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the operation succeeded")
    path: str = Field(description="Path to the file")
    content: str | None = Field(
        default=None,
        description="File content after operation",
    )
    bytes_written: int = Field(
        ge=0,
        default=0,
        description="Bytes written (for write operations)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if operation failed",
    )


# =============================================================================
# TextHandler
# =============================================================================


class TextHandler:
    """Handler for plain text and markdown file operations.

    Provides higher-level operations on text files:
    - Create files with content
    - Read files
    - Append content to files
    - Replace sections within files (using markers)

    Usage:
        handler = TextHandler()

        # Create a new file
        result = handler.create(Path("/tmp/notes.txt"), "Hello, world!")

        # Append content
        result = handler.append(Path("/tmp/notes.txt"), "More content...")

        # Replace a marked section
        result = handler.replace_section(
            Path("/tmp/notes.txt"),
            marker="<!-- CONFIG -->",
            content="new config here",
        )
    """

    def __init__(self, encoding: str = "utf-8") -> None:
        """Initialize the text handler.

        Args:
            encoding: Default file encoding.
        """
        self._encoding = encoding

    def create(
        self,
        path: Path | str,
        content: str,
        file_type: str = "text",
        overwrite: bool = False,
    ) -> WriteResult:
        """Create a file with content.

        Args:
            path: Path to the file to create.
            content: Content to write.
            file_type: File type hint (text, markdown). Currently unused
                but available for future formatting.
            overwrite: Allow overwriting existing files.

        Returns:
            WriteResult with outcome.
        """
        return write_file(
            path=path,
            content=content,
            overwrite=overwrite,
            encoding=self._encoding,
        )

    def read(self, path: Path | str) -> ReadResult:
        """Read a text file.

        Args:
            path: Path to the file to read.

        Returns:
            ReadResult with file content or error.
        """
        return read_file(path=path, encoding=self._encoding)

    def append(
        self,
        path: Path | str,
        content: str,
        separator: str = "\n",
    ) -> TextOpResult:
        """Append content to an existing file.

        Args:
            path: Path to the file to append to.
            content: Content to append.
            separator: Separator between existing content and new content.

        Returns:
            TextOpResult with outcome.
        """
        path = Path(path)

        # Read existing content
        read_result = read_file(path, encoding=self._encoding)

        if not read_result.success:
            # If file doesn't exist, create it
            if "not found" in (read_result.error or "").lower():
                write_result = write_file(
                    path=path,
                    content=content,
                    overwrite=False,
                    encoding=self._encoding,
                )
                return TextOpResult(
                    success=write_result.success,
                    path=str(path),
                    content=content if write_result.success else None,
                    bytes_written=write_result.bytes_written,
                    error=write_result.error,
                )
            return TextOpResult(
                success=False,
                path=str(path),
                error=read_result.error,
            )

        # Append new content
        existing = read_result.content or ""
        new_content = existing + separator + content

        write_result = write_file(
            path=path,
            content=new_content,
            overwrite=True,
            encoding=self._encoding,
        )

        return TextOpResult(
            success=write_result.success,
            path=str(path),
            content=new_content if write_result.success else None,
            bytes_written=write_result.bytes_written,
            error=write_result.error,
        )

    def replace_section(
        self,
        path: Path | str,
        marker: str,
        content: str,
        end_marker: str | None = None,
    ) -> TextOpResult:
        """Replace a section of a file between markers.

        If end_marker is not provided, replaces content from marker to
        the next occurrence of the same marker.

        If markers are not found, appends the section at the end.

        Args:
            path: Path to the file.
            marker: Start marker (e.g., "<!-- BEGIN CONFIG -->").
            content: New content to insert between markers.
            end_marker: End marker. If None, uses marker as end marker too.

        Returns:
            TextOpResult with outcome.
        """
        path = Path(path)
        end_marker = end_marker or marker

        # Read existing content
        read_result = read_file(path, encoding=self._encoding)

        if not read_result.success:
            return TextOpResult(
                success=False,
                path=str(path),
                error=read_result.error,
            )

        existing = read_result.content or ""

        # Find markers
        start_idx = existing.find(marker)
        if start_idx == -1:
            # Marker not found, append section at end
            new_content = existing + f"\n{marker}\n{content}\n{end_marker}\n"
        else:
            # Find end marker (after start marker)
            end_search_start = start_idx + len(marker)
            end_idx = existing.find(end_marker, end_search_start)

            if end_idx == -1:
                # No end marker, append after start marker
                before = existing[: start_idx + len(marker)]
                new_content = f"{before}\n{content}\n{end_marker}\n"
            else:
                # Replace content between markers
                before = existing[: start_idx + len(marker)]
                after = existing[end_idx:]
                new_content = f"{before}\n{content}\n{after}"

        write_result = write_file(
            path=path,
            content=new_content,
            overwrite=True,
            encoding=self._encoding,
        )

        return TextOpResult(
            success=write_result.success,
            path=str(path),
            content=new_content if write_result.success else None,
            bytes_written=write_result.bytes_written,
            error=write_result.error,
        )

    def prepend(
        self,
        path: Path | str,
        content: str,
        separator: str = "\n",
    ) -> TextOpResult:
        """Prepend content to an existing file.

        Args:
            path: Path to the file.
            content: Content to prepend.
            separator: Separator between new content and existing content.

        Returns:
            TextOpResult with outcome.
        """
        path = Path(path)

        # Read existing content
        read_result = read_file(path, encoding=self._encoding)

        if not read_result.success:
            # If file doesn't exist, create it
            if "not found" in (read_result.error or "").lower():
                write_result = write_file(
                    path=path,
                    content=content,
                    overwrite=False,
                    encoding=self._encoding,
                )
                return TextOpResult(
                    success=write_result.success,
                    path=str(path),
                    content=content if write_result.success else None,
                    bytes_written=write_result.bytes_written,
                    error=write_result.error,
                )
            return TextOpResult(
                success=False,
                path=str(path),
                error=read_result.error,
            )

        # Prepend new content
        existing = read_result.content or ""
        new_content = content + separator + existing

        write_result = write_file(
            path=path,
            content=new_content,
            overwrite=True,
            encoding=self._encoding,
        )

        return TextOpResult(
            success=write_result.success,
            path=str(path),
            content=new_content if write_result.success else None,
            bytes_written=write_result.bytes_written,
            error=write_result.error,
        )


# =============================================================================
# MarkdownBuilder
# =============================================================================


class MarkdownBuilder:
    """Fluent builder for constructing markdown content.

    Usage:
        md = (
            MarkdownBuilder()
            .heading("Installation Guide", level=1)
            .paragraph("Follow these steps to install the package.")
            .heading("Prerequisites", level=2)
            .list_items(["Python 3.10+", "pip", "virtualenv"])
            .heading("Installation", level=2)
            .code_block("pip install elle", language="bash")
            .build()
        )
    """

    def __init__(self) -> None:
        """Initialize an empty markdown builder."""
        self._parts: list[str] = []

    def heading(self, text: str, level: int = 1) -> Self:
        """Add a heading.

        Args:
            text: Heading text.
            level: Heading level (1-6).

        Returns:
            Self for chaining.
        """
        level = max(1, min(6, level))
        prefix = "#" * level
        self._parts.append(f"{prefix} {text}\n")
        return self

    def paragraph(self, text: str) -> Self:
        """Add a paragraph.

        Args:
            text: Paragraph text.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"{text}\n")
        return self

    def text(self, text: str) -> Self:
        """Add raw text without additional formatting.

        Args:
            text: Raw text.

        Returns:
            Self for chaining.
        """
        self._parts.append(text)
        return self

    def newline(self, count: int = 1) -> Self:
        """Add newlines.

        Args:
            count: Number of newlines to add.

        Returns:
            Self for chaining.
        """
        self._parts.append("\n" * count)
        return self

    def horizontal_rule(self) -> Self:
        """Add a horizontal rule.

        Returns:
            Self for chaining.
        """
        self._parts.append("\n---\n")
        return self

    def code_block(self, code: str, language: str = "") -> Self:
        """Add a fenced code block.

        Args:
            code: Code content.
            language: Language identifier for syntax highlighting.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"```{language}\n{code}\n```\n")
        return self

    def inline_code(self, code: str) -> Self:
        """Add inline code.

        Args:
            code: Code text.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"`{code}`")
        return self

    def list_items(
        self,
        items: list[str],
        ordered: bool = False,
        indent: int = 0,
    ) -> Self:
        """Add a list.

        Args:
            items: List items.
            ordered: Use ordered (numbered) list.
            indent: Indentation level (for nested lists).

        Returns:
            Self for chaining.
        """
        prefix_spaces = "  " * indent
        lines = []

        for i, item in enumerate(items, start=1):
            if ordered:
                lines.append(f"{prefix_spaces}{i}. {item}")
            else:
                lines.append(f"{prefix_spaces}- {item}")

        self._parts.append("\n".join(lines) + "\n")
        return self

    def checkbox_list(
        self,
        items: list[tuple[str, bool]],
        indent: int = 0,
    ) -> Self:
        """Add a checkbox list.

        Args:
            items: List of (text, checked) tuples.
            indent: Indentation level.

        Returns:
            Self for chaining.
        """
        prefix_spaces = "  " * indent
        lines = []

        for text, checked in items:
            checkbox = "[x]" if checked else "[ ]"
            lines.append(f"{prefix_spaces}- {checkbox} {text}")

        self._parts.append("\n".join(lines) + "\n")
        return self

    def table(
        self,
        headers: list[str],
        rows: list[list[str]],
        alignment: list[str] | None = None,
    ) -> Self:
        """Add a table.

        Args:
            headers: Column headers.
            rows: Table rows (list of lists).
            alignment: Column alignments ("left", "center", "right").
                If None, all columns are left-aligned.

        Returns:
            Self for chaining.
        """
        if not headers:
            return self

        # Default alignment
        if alignment is None:
            alignment = ["left"] * len(headers)

        # Ensure alignment matches headers
        alignment = (alignment + ["left"] * len(headers))[: len(headers)]

        # Create separator
        separators = []
        for align in alignment:
            if align == "center":
                separators.append(":---:")
            elif align == "right":
                separators.append("---:")
            else:
                separators.append("---")

        # Build table
        lines = []
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(separators) + " |")

        for row in rows:
            # Pad row to match headers
            padded = (list(row) + [""] * len(headers))[: len(headers)]
            lines.append("| " + " | ".join(padded) + " |")

        self._parts.append("\n".join(lines) + "\n")
        return self

    def blockquote(self, text: str) -> Self:
        """Add a blockquote.

        Args:
            text: Quote text (can be multiline).

        Returns:
            Self for chaining.
        """
        lines = text.split("\n")
        quoted = "\n".join(f"> {line}" for line in lines)
        self._parts.append(quoted + "\n")
        return self

    def link(self, text: str, url: str, title: str | None = None) -> Self:
        """Add a link.

        Args:
            text: Link text.
            url: Link URL.
            title: Optional link title.

        Returns:
            Self for chaining.
        """
        if title:
            self._parts.append(f'[{text}]({url} "{title}")')
        else:
            self._parts.append(f"[{text}]({url})")
        return self

    def image(
        self,
        alt_text: str,
        url: str,
        title: str | None = None,
    ) -> Self:
        """Add an image.

        Args:
            alt_text: Alt text for the image.
            url: Image URL.
            title: Optional image title.

        Returns:
            Self for chaining.
        """
        if title:
            self._parts.append(f'![{alt_text}]({url} "{title}")\n')
        else:
            self._parts.append(f"![{alt_text}]({url})\n")
        return self

    def bold(self, text: str) -> Self:
        """Add bold text.

        Args:
            text: Text to make bold.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"**{text}**")
        return self

    def italic(self, text: str) -> Self:
        """Add italic text.

        Args:
            text: Text to make italic.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"*{text}*")
        return self

    def strikethrough(self, text: str) -> Self:
        """Add strikethrough text.

        Args:
            text: Text to strike through.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"~~{text}~~")
        return self

    def definition_list(self, items: list[tuple[str, str]]) -> Self:
        """Add a definition list.

        Args:
            items: List of (term, definition) tuples.

        Returns:
            Self for chaining.
        """
        lines = []
        for term, definition in items:
            lines.append(f"{term}")
            lines.append(f": {definition}")
            lines.append("")

        self._parts.append("\n".join(lines))
        return self

    def footnote(self, label: str, text: str) -> Self:
        """Add a footnote definition.

        Args:
            label: Footnote label.
            text: Footnote text.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"[^{label}]: {text}\n")
        return self

    def footnote_ref(self, label: str) -> Self:
        """Add a footnote reference.

        Args:
            label: Footnote label to reference.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"[^{label}]")
        return self

    def details(self, summary: str, content: str) -> Self:
        """Add a collapsible details section (HTML).

        Args:
            summary: Summary text (always visible).
            content: Content to show when expanded.

        Returns:
            Self for chaining.
        """
        self._parts.append(f"<details>\n<summary>{summary}</summary>\n\n{content}\n</details>\n")
        return self

    def clear(self) -> Self:
        """Clear all content.

        Returns:
            Self for chaining.
        """
        self._parts.clear()
        return self

    def build(self) -> str:
        """Build the final markdown string.

        Returns:
            Complete markdown content.
        """
        return "".join(self._parts)

    def save(
        self,
        path: Path | str,
        overwrite: bool = False,
    ) -> WriteResult:
        """Build and save the markdown to a file.

        Args:
            path: Path to save the file.
            overwrite: Allow overwriting existing files.

        Returns:
            WriteResult with outcome.
        """
        content = self.build()
        return write_file(path=path, content=content, overwrite=overwrite)


# =============================================================================
# Module-level Singleton
# =============================================================================

_handler: TextHandler | None = None


def get_text_handler(encoding: str = "utf-8") -> TextHandler:
    """Get the shared TextHandler instance.

    Args:
        encoding: File encoding (only used on first call).

    Returns:
        TextHandler singleton.
    """
    global _handler
    if _handler is None:
        _handler = TextHandler(encoding=encoding)
    return _handler


def reset_text_handler() -> None:
    """Reset the shared TextHandler instance."""
    global _handler
    _handler = None
