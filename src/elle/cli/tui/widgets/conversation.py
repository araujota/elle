"""Conversation view widget for the ELLE TUI.

A scrollable container that holds the message history. New messages
are appended at the bottom and the view auto-scrolls to keep the
latest message visible.
"""

from __future__ import annotations

from rich.console import RenderableType
from textual.containers import VerticalScroll

from elle.cli.tui.widgets.message import MessageBubble


class ConversationView(VerticalScroll):
    """Scrollable conversation history.

    Messages are mounted as child widgets. The view auto-scrolls
    to the bottom when new messages are added.
    """

    DEFAULT_CSS = """
    ConversationView {
        height: 1fr;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    """

    def add_user_message(self, text: str) -> MessageBubble:
        """Add a user message to the conversation.

        Args:
            text: The user's input text.

        Returns:
            The created MessageBubble widget.
        """
        bubble = MessageBubble(text, role="user")
        self.mount(bubble)
        self.scroll_end(animate=False)
        return bubble

    def add_elle_message(self, content: RenderableType | str = "") -> MessageBubble:
        """Add an ELLE response message to the conversation.

        Args:
            content: The response content (text or Rich renderable).

        Returns:
            The created MessageBubble widget.
        """
        bubble = MessageBubble(content, role="elle")
        self.mount(bubble)
        self.scroll_end(animate=False)
        return bubble

    def add_widget(self, widget: MessageBubble) -> None:
        """Add an arbitrary widget to the conversation.

        Args:
            widget: The widget to add.
        """
        self.mount(widget)
        self.scroll_end(animate=False)
