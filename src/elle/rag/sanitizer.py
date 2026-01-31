"""Input sanitization for LLM prompts.

Provides defense-in-depth against prompt injection attacks by sanitizing
user-derived data before embedding in LLM prompts.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Patterns that may indicate prompt injection attempts
INJECTION_PATTERNS = (
    # Role/instruction override attempts
    r"\[SYSTEM\]",
    r"\[ASSISTANT\]",
    r"\[USER\]",
    r"<<<?PROMPT",
    r"PROMPT>>>?",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    # Instruction override phrases
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|rules?|prompts?)",
    r"disregard\s+(all\s+)?(previous|above|prior)",
    r"forget\s+(everything|all|previous)",
    r"new\s+instructions?:",
    r"override\s+instructions?",
    r"system\s*:\s*you\s+are",
)

# Compiled patterns for efficiency
_COMPILED_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS)


def sanitize_for_prompt(text: str, context: str = "input") -> str:
    """Sanitize text before embedding in LLM prompt.

    Args:
        text: User-derived text to sanitize.
        context: Description of where this text comes from (for logging).

    Returns:
        Sanitized text safe for prompt embedding.
    """
    if not text:
        return text

    original = text

    # Check for injection patterns
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(text):
            logger.warning(f"Potential prompt injection detected in {context}: matched pattern {pattern.pattern!r}")
            # Replace the match with [FILTERED]
            text = pattern.sub("[FILTERED]", text)

    # Remove control characters (except newline, tab)
    text = "".join(c if c in "\n\t" or (32 <= ord(c) < 127) or ord(c) > 127 else "" for c in text)

    # Truncate extremely long inputs
    max_len = 10000
    if len(text) > max_len:
        text = text[:max_len] + "... [TRUNCATED]"
        logger.warning(f"Truncated {context} from {len(original)} to {max_len} chars")

    return text


def sanitize_command(cmd: str) -> str:
    """Sanitize a command string for prompt embedding.

    More restrictive than general sanitization - removes
    shell metacharacters that could confuse the LLM.
    """
    # Apply general sanitization first
    cmd = sanitize_for_prompt(cmd, context="command")

    # Escape backticks that could be misinterpreted
    cmd = cmd.replace("`", "'")

    return cmd
