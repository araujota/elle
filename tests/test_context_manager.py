"""Tests for ContextWindowManager."""

from elle.rag.llm import ContextWindowManager


class TestContextWindowManager:
    """Tests for ContextWindowManager."""

    def test_initialization(self) -> None:
        """Test default initialization."""
        manager = ContextWindowManager()

        assert manager.max_tokens == 32768  # 32K context window
        assert manager.compact_threshold == 0.80
        assert manager.compact_target == 0.60
        assert manager.min_messages == 8
        assert manager.token_count == 0
        assert manager.message_count == 0

    def test_custom_initialization(self) -> None:
        """Test custom initialization."""
        manager = ContextWindowManager(
            max_tokens=4096,
            compact_threshold=0.8,
            compact_target=0.4,
            min_messages=3,
        )

        assert manager.max_tokens == 4096
        assert manager.compact_threshold == 0.8
        assert manager.compact_target == 0.4
        assert manager.min_messages == 3

    def test_add_message(self) -> None:
        """Test adding messages."""
        manager = ContextWindowManager()

        manager.add_message("system", "You are a helpful assistant.")
        assert manager.message_count == 1
        assert manager.token_count > 0

        manager.add_message("user", "Hello!")
        assert manager.message_count == 2

        manager.add_message("assistant", "Hi there!")
        assert manager.message_count == 3

    def test_add_messages_batch(self) -> None:
        """Test adding multiple messages at once."""
        manager = ContextWindowManager()

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        manager.add_messages(messages)
        assert manager.message_count == 3

    def test_get_messages(self) -> None:
        """Test retrieving messages."""
        manager = ContextWindowManager()

        manager.add_message("user", "Hello!")
        manager.add_message("assistant", "Hi!")

        messages = manager.get_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello!"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hi!"

    def test_token_estimation(self) -> None:
        """Test token count estimation."""
        manager = ContextWindowManager()

        # ~4 chars per token
        manager.add_message("user", "A" * 400)  # ~100 tokens
        assert 80 <= manager.token_count <= 120

    def test_utilization(self) -> None:
        """Test utilization calculation."""
        manager = ContextWindowManager(max_tokens=1000)

        # Empty
        assert manager.utilization == 0.0

        # Add content
        manager.add_message("user", "A" * 1000)  # ~250 tokens
        assert 0.2 <= manager.utilization <= 0.3

    def test_needs_compaction_false(self) -> None:
        """Test needs_compaction returns False when under threshold."""
        manager = ContextWindowManager(max_tokens=1000)

        manager.add_message("user", "Hello!")
        assert not manager.needs_compaction()

    def test_needs_compaction_true(self) -> None:
        """Test needs_compaction returns True when over threshold."""
        manager = ContextWindowManager(max_tokens=100, compact_threshold=0.5)

        # Add content exceeding 50% of 100 tokens
        manager.add_message("user", "A" * 400)  # ~100 tokens > 50
        assert manager.needs_compaction()

    def test_clear(self) -> None:
        """Test clearing all messages."""
        manager = ContextWindowManager()

        manager.add_message("user", "Hello!")
        manager.add_message("assistant", "Hi!")

        assert manager.message_count == 2
        assert manager.token_count > 0

        manager.clear()

        assert manager.message_count == 0
        assert manager.token_count == 0

    def test_compaction_count(self) -> None:
        """Test compaction count tracking."""
        manager = ContextWindowManager()

        assert manager.compaction_count == 0


class TestContextWindowManagerCompaction:
    """Tests for context compaction logic."""

    def test_compact_preserves_minimum_messages(self) -> None:
        """Test that compaction preserves minimum messages."""
        manager = ContextWindowManager(min_messages=3)

        # Add only 2 messages
        manager.add_message("user", "Hello!")
        manager.add_message("assistant", "Hi!")

        # Should not need compaction with few messages
        assert manager.message_count <= manager.min_messages

    def test_messages_returned_as_copy(self) -> None:
        """Test that get_messages returns a copy, not the internal list."""
        manager = ContextWindowManager()

        manager.add_message("user", "Hello!")
        messages = manager.get_messages()

        # Modifying returned list should not affect internal state
        messages.append({"role": "user", "content": "Sneaky!"})
        assert manager.message_count == 1


class TestContextWindowManagerEdgeCases:
    """Edge case tests for ContextWindowManager."""

    def test_empty_content(self) -> None:
        """Test handling of empty content."""
        manager = ContextWindowManager()

        manager.add_message("user", "")
        assert manager.message_count == 1
        assert manager.token_count >= 1  # Minimum 1 token

    def test_very_long_content(self) -> None:
        """Test handling of very long content."""
        manager = ContextWindowManager()

        # 100KB of content
        long_content = "A" * 100_000
        manager.add_message("user", long_content)

        # Should estimate ~25000 tokens
        assert 20000 <= manager.token_count <= 30000

    def test_zero_max_tokens(self) -> None:
        """Test with zero max_tokens doesn't crash."""
        manager = ContextWindowManager(max_tokens=0)

        manager.add_message("user", "Hello!")
        assert manager.utilization == 0.0  # Avoid division by zero
