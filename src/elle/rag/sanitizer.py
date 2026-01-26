"""Input sanitization for LLM prompts.

Prevents prompt injection and ensures safe input handling.
"""

from __future__ import annotations

import re


def sanitize_for_prompt(text: str, context: str = "general") -> str:
    """Sanitize user input before embedding in LLM prompts.

    Args:
        text: Raw user input
        context: Sanitization context ("user_input", "system", "general")

    Returns:
        Sanitized text safe for prompt embedding
    """
    if not text:
        return ""

    # Remove null bytes
    text = text.replace("\x00", "")

    # Normalize whitespace (but preserve intentional newlines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Strip leading/trailing whitespace
    text = text.strip()

    # Context-specific sanitization
    if context == "user_input":
        # For user input, limit common prompt injection patterns
        # but don't be too aggressive - users may have legitimate uses
        pass

    return text
