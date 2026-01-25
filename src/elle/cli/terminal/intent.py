"""Intent classification schema and types.

Defines the intent labels and result schema for the classifier.
All intents must be one of the fixed set of labels.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Fixed intent labels
IntentLabel = Literal[
    "shell_passthrough",
    "system_question",
    "system_task",
    "fixit",
    "navigation",
    "meta",
    "gui_task",
    "explain_command",
]


class Intent(str, Enum):
    """Classification of user input intent."""

    SHELL_PASSTHROUGH = "shell_passthrough"
    SYSTEM_QUESTION = "system_question"
    SYSTEM_TASK = "system_task"
    FIXIT = "fixit"
    NAVIGATION = "navigation"
    META = "meta"
    GUI_TASK = "gui_task"
    EXPLAIN_COMMAND = "explain_command"

    @classmethod
    def from_label(cls, label: str) -> Intent:
        """Convert a string label to Intent enum.

        Args:
            label: The intent label string.

        Returns:
            The corresponding Intent enum value.

        Raises:
            ValueError: If label is not a valid intent.
        """
        label_lower = label.lower().strip()
        for intent in cls:
            if intent.value == label_lower:
                return intent
        raise ValueError(f"Unknown intent label: {label}")


class IntentResult(BaseModel):
    """Result of intent classification.

    This schema is returned by both rule-based and SLM classification.
    The SLM is instructed to output JSON matching this exact schema.
    """

    model_config = ConfigDict(frozen=True)

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(description="One short sentence explaining the classification")
    entities: list[str] = Field(
        default_factory=list,
        description="Extracted entities: commands, files, services, ports",
    )
    suggested_followups: list[str] = Field(
        default_factory=list,
        description="Optional follow-up suggestions",
    )
    requires_clarification: bool = Field(
        default=False,
        description="True if confidence is too low to proceed",
    )
    classified_by: Literal["rule", "slm", "fallback"] = Field(
        default="slm",
        description="How the classification was determined",
    )

    @property
    def is_confident(self) -> bool:
        """Check if classification is confident enough to proceed."""
        return self.confidence >= 0.55 and not self.requires_clarification


# Confidence thresholds
CONFIDENCE_THRESHOLD = 0.55  # Below this, require clarification
HIGH_CONFIDENCE = 0.90  # For rule-based exact matches
MEDIUM_CONFIDENCE = 0.75  # For partial matches


def create_rule_result(
    intent: Intent,
    rationale: str,
    confidence: float = HIGH_CONFIDENCE,
    entities: list[str] | None = None,
) -> IntentResult:
    """Create an IntentResult from a rule-based classification.

    Args:
        intent: The classified intent.
        rationale: Explanation for the classification.
        confidence: Confidence level (default HIGH for rules).
        entities: Optional extracted entities.

    Returns:
        IntentResult marked as rule-based.
    """
    return IntentResult(
        intent=intent,
        confidence=confidence,
        rationale=rationale,
        entities=entities or [],
        requires_clarification=False,
        classified_by="rule",
    )


def create_clarification_result(
    rationale: str,
    suggested_followups: list[str] | None = None,
) -> IntentResult:
    """Create an IntentResult that requires clarification.

    Args:
        rationale: Why clarification is needed.
        suggested_followups: Suggested options for the user.

    Returns:
        IntentResult with requires_clarification=True.
    """
    return IntentResult(
        intent=Intent.META,  # Default to meta for clarification
        confidence=0.0,
        rationale=rationale,
        requires_clarification=True,
        suggested_followups=suggested_followups or [
            "Run a shell command",
            "Ask a system question",
            "Perform a system task",
        ],
        classified_by="fallback",
    )
