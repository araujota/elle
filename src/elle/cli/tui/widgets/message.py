"""Message bubble widget for the ELLE TUI.

Renders a single message in the conversation view, styled
differently for user input vs ELLE responses.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual.widgets import Static

from elle.cli.ui.theme import Colors, Icons


class MessageBubble(Static):
    """A single message in the conversation view.

    Renders with different styling based on role (user vs elle).
    Can display both plain text and Rich renderables.
    """

    DEFAULT_CSS = """
    MessageBubble {
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    MessageBubble.user-message {
        padding: 0 1 0 2;
    }

    MessageBubble.elle-message {
        padding: 0 1 0 2;
    }
    """

    def __init__(
        self,
        content: RenderableType | str = "",
        *,
        role: str = "elle",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a message bubble.

        Args:
            content: The message content (text or Rich renderable).
            role: "user" or "elle".
            name: Optional Textual widget name.
            id: Optional Textual widget ID.
            classes: Optional additional CSS classes.
        """
        self.role = role
        self._content = content

        css_class = f"{role}-message"
        if classes:
            css_class = f"{css_class} {classes}"

        super().__init__(name=name, id=id, classes=css_class)

    def render(self) -> RenderableType:
        """Render the message with role prefix."""
        prefix = self._make_prefix()

        if isinstance(self._content, str):
            text = Text()
            text.append_text(prefix)
            text.append(self._content)
            return text

        # For Rich renderables, show prefix then the renderable
        # Static.update() handles renderables natively
        return self._content

    def _make_prefix(self) -> Text:
        """Create the role prefix label."""
        text = Text()
        if self.role == "user":
            text.append(f"{Icons.CHEVRON} you", style=f"bold {Colors.ACCENT}")
            text.append("  ", style="")
        else:
            text.append(f"{Icons.CHEVRON} elle", style=f"bold {Colors.SUCCESS}")
            text.append("  ", style="")
        return text

    def set_content(self, content: RenderableType | str) -> None:
        """Update the message content.

        Args:
            content: New content to display.
        """
        self._content = content
        self.update(self.render())

    def append_text(self, text: str) -> None:
        """Append text to the current content (for streaming).

        Args:
            text: Text to append.
        """
        if isinstance(self._content, str):
            self._content += text
        else:
            # If current content is a renderable, convert to string
            self._content = str(self._content) + text
        self.update(self.render())
