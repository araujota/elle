"""Prompt segment system for ELLE.

Provides atomic, intent-specific prompt segments that augment the base system
prompt when the classifier detects specific user intents. This enables targeted
guidance for different task types without bloating every request.

Usage:
    from elle.rag.prompts import PromptComposer, PromptSegment

    # Create composer with base prompt
    composer = PromptComposer("You are a Linux system assistant.")

    # Register intent-specific segments
    composer.register(PromptSegment(
        id="docker_ops",
        intent="system_task",
        content="When working with Docker...",
    ))

    # Compose full prompt for a specific intent
    system_prompt = composer.compose("system_task")
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class PromptSegment(BaseModel):
    """Atomic prompt segment for a specific intent/competency.

    Attributes:
        id: Unique identifier for the segment.
        intent: Matching intent from classifier (e.g., "system_task").
        priority: Lower values are injected earlier (default: 100).
        content: The actual prompt text to inject.
        requires_context: Context keys needed for template formatting.
        enabled: Whether the segment is active (default: True).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    intent: str
    priority: int = 100
    content: str
    requires_context: tuple[str, ...] = ()
    enabled: bool = True


class PromptComposer:
    """Composes system prompts from base + intent-specific segments.

    Manages a registry of prompt segments and builds complete system
    prompts by combining the base prompt with relevant segments based
    on the detected intent.

    Attributes:
        base_prompt: The foundational system prompt.
    """

    def __init__(self, base_prompt: str) -> None:
        """Initialize the composer.

        Args:
            base_prompt: Base system prompt that's always included.
        """
        self.base_prompt = base_prompt
        self._segments: dict[str, PromptSegment] = {}

    def register(self, segment: PromptSegment) -> None:
        """Register a prompt segment.

        Args:
            segment: The segment to register.
        """
        if segment.id in self._segments:
            logger.debug(f"Replacing existing segment: {segment.id}")
        self._segments[segment.id] = segment
        logger.debug(f"Registered prompt segment: {segment.id} for intent={segment.intent}")

    def unregister(self, segment_id: str) -> bool:
        """Unregister a prompt segment.

        Args:
            segment_id: ID of the segment to remove.

        Returns:
            True if segment was removed, False if not found.
        """
        if segment_id in self._segments:
            del self._segments[segment_id]
            return True
        return False

    def get_segment(self, segment_id: str) -> PromptSegment | None:
        """Get a segment by ID.

        Args:
            segment_id: ID of the segment.

        Returns:
            The segment if found, None otherwise.
        """
        return self._segments.get(segment_id)

    def list_segments(self, intent: str | None = None) -> list[PromptSegment]:
        """List registered segments.

        Args:
            intent: If provided, filter by intent.

        Returns:
            List of matching segments sorted by priority.
        """
        segments = list(self._segments.values())

        if intent:
            segments = [s for s in segments if s.intent == intent]

        return sorted(segments, key=lambda s: s.priority)

    def compose(
        self,
        intent: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build full system prompt for a given intent.

        Combines the base prompt with all matching, enabled segments
        sorted by priority. Context variables are substituted into
        segments that require them.

        Args:
            intent: The detected intent to compose for.
            context: Optional context dict for template substitution.

        Returns:
            Complete system prompt string.
        """
        parts = [self.base_prompt]
        context = context or {}

        # Find matching, enabled segments
        matching = [
            s for s in self._segments.values()
            if s.intent == intent and s.enabled
        ]

        # Sort by priority (lower first)
        matching.sort(key=lambda s: s.priority)

        for segment in matching:
            content = segment.content

            # Apply context substitution if needed
            if segment.requires_context:
                try:
                    substitutions = {
                        k: context.get(k, "")
                        for k in segment.requires_context
                    }
                    content = content.format(**substitutions)
                except KeyError as e:
                    logger.warning(f"Missing context key for segment {segment.id}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to format segment {segment.id}: {e}")

            parts.append(content)

        return "\n\n".join(parts)

    def compose_with_segments(
        self,
        intent: str,
        segment_ids: list[str],
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build system prompt with specific segments.

        Unlike compose(), this includes only the specified segments
        regardless of intent matching.

        Args:
            intent: The intent (for logging).
            segment_ids: IDs of segments to include.
            context: Optional context dict for template substitution.

        Returns:
            Complete system prompt string.
        """
        parts = [self.base_prompt]
        context = context or {}

        for segment_id in segment_ids:
            segment = self._segments.get(segment_id)
            if not segment or not segment.enabled:
                continue

            content = segment.content

            if segment.requires_context:
                try:
                    substitutions = {
                        k: context.get(k, "")
                        for k in segment.requires_context
                    }
                    content = content.format(**substitutions)
                except Exception as e:
                    logger.warning(f"Failed to format segment {segment_id}: {e}")

            parts.append(content)

        return "\n\n".join(parts)


# Base system prompt for ELLE
BASE_SYSTEM_PROMPT = """You are ELLE, a local-first Linux system assistant.

You help users understand, configure, and maintain their Ubuntu system.
You prioritize safety, clarity, and user education.

Key principles:
- Explain before executing: Always describe what a command will do.
- No ambient sudo: Privileged operations are discrete and auditable.
- Local-first: All inference runs locally via Ollama.
- Learning focus: Help users understand, not just execute."""


# Module-level composer instance
_composer: PromptComposer | None = None


def get_composer() -> PromptComposer:
    """Get the shared PromptComposer instance.

    Returns:
        The PromptComposer singleton with default segments registered.
    """
    global _composer
    if _composer is None:
        _composer = PromptComposer(BASE_SYSTEM_PROMPT)
        _register_default_segments(_composer)
    return _composer


def _register_default_segments(composer: PromptComposer) -> None:
    """Register default prompt segments.

    Args:
        composer: The composer to register segments with.
    """
    from elle.rag.prompts.segments import DEFAULT_SEGMENTS

    for segment in DEFAULT_SEGMENTS:
        composer.register(segment)


__all__ = [
    "PromptSegment",
    "PromptComposer",
    "BASE_SYSTEM_PROMPT",
    "get_composer",
]
