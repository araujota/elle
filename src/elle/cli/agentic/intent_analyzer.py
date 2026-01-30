"""IntentAnalyzer - LLM-based unified intent extraction.

Extracts both information needs AND action requests from natural language.
Replaces the pattern-based analyzer with a more flexible LLM approach
while still supporting fast pattern matching for common cases.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, cast

from elle.cli.agentic.models import (
    ActionRequest,
    ActionType,
    AgenticIntent,
    InformationCategory,
    InformationNeed,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Pattern-Based Fast Path
# =============================================================================

# Patterns that map to information needs (fast path for common queries)
INFO_PATTERNS: list[tuple[re.Pattern[str], InformationCategory, str, tuple[str, ...]]] = [
    # Service patterns
    (re.compile(r"(?:is|what(?:'s| is))\s+(\w+)\s+(?:running|status|state)", re.I), "service", r"\1", ("status",)),
    (re.compile(r"(?:status|state)\s+(?:of\s+)?(\w+)\s*(?:service)?", re.I), "service", r"\1", ("status",)),
    (
        re.compile(r"show\s+(?:me\s+)?(\w+)\s+(?:service\s+)?(?:status|logs?)", re.I),
        "service",
        r"\1",
        ("status", "logs"),
    ),
    (re.compile(r"(\w+)\s+(?:service\s+)?logs?", re.I), "service", r"\1", ("logs",)),
    (
        re.compile(r"why\s+is\s+(\w+)\s+(?:failing|not\s+running|down|stopped)", re.I),
        "service",
        r"\1",
        ("status", "logs"),
    ),
    # File patterns
    (
        re.compile(r"(?:show|read|cat|view)\s+(?:me\s+)?(?:the\s+)?(.+?)\s*(?:file)?$", re.I),
        "file",
        r"\1",
        ("content",),
    ),
    (re.compile(r"what(?:'s| is)\s+in\s+(.+)", re.I), "file", r"\1", ("content",)),
    (re.compile(r"contents?\s+(?:of\s+)?(.+)", re.I), "file", r"\1", ("content",)),
    # Package patterns
    (re.compile(r"is\s+(\w+(?:-\w+)*)\s+installed", re.I), "package", r"\1", ("info",)),
    (re.compile(r"(?:package|version)\s+(?:of\s+)?(\w+(?:-\w+)*)", re.I), "package", r"\1", ("info",)),
    (re.compile(r"(\w+(?:-\w+)*)\s+version", re.I), "package", r"\1", ("info",)),
    # Docker patterns
    (re.compile(r"(?:what|which)\s+containers?\s+(?:are\s+)?running", re.I), "docker", "", ("list",)),
    (re.compile(r"(?:list|show)\s+(?:me\s+)?(?:docker\s+)?containers?", re.I), "docker", "", ("list",)),
    (re.compile(r"(?:docker|container)\s+(?:status|state)\s+(?:of\s+)?(\w+)", re.I), "docker", r"\1", ("status",)),
    (re.compile(r"(\w+)\s+container\s+(?:status|logs?)", re.I), "docker", r"\1", ("status", "logs")),
    # Network patterns
    (re.compile(r"what(?:'s| is)\s+(?:listening|running)\s+on\s+port\s+(\d+)", re.I), "network", r"\1", ("listeners",)),
    (re.compile(r"(?:listening|open)\s+ports?", re.I), "network", "", ("listeners",)),
    (re.compile(r"port\s+(\d+)\s+(?:status|in\s+use)", re.I), "network", r"\1", ("listeners",)),
    # System patterns
    (re.compile(r"(?:system|server)\s+(?:info|information|status)", re.I), "system", "", ("info",)),
    (re.compile(r"(?:memory|cpu|disk)\s+(?:usage|status)", re.I), "system", "", ("resources",)),
    (re.compile(r"(?:how\s+much\s+)?(?:free\s+)?(?:memory|disk|space)", re.I), "system", "", ("resources",)),
]

# Patterns that map to action requests (fast path for common tasks)
ACTION_PATTERNS: list[tuple[re.Pattern[str], ActionType, str, str]] = [
    # Service actions
    (re.compile(r"(?:restart|reboot)\s+(\w+)\s*(?:service)?", re.I), ActionType.RESTART, r"\1", "service"),
    (re.compile(r"start\s+(\w+)\s*(?:service)?", re.I), ActionType.START, r"\1", "service"),
    (re.compile(r"stop\s+(\w+)\s*(?:service)?", re.I), ActionType.STOP, r"\1", "service"),
    (re.compile(r"enable\s+(\w+)\s*(?:service)?", re.I), ActionType.ENABLE, r"\1", "service"),
    (re.compile(r"disable\s+(\w+)\s*(?:service)?", re.I), ActionType.DISABLE, r"\1", "service"),
    # Package actions
    (re.compile(r"install\s+(\w+(?:-\w+)*)", re.I), ActionType.INSTALL, r"\1", "package"),
    (re.compile(r"(?:remove|uninstall)\s+(\w+(?:-\w+)*)", re.I), ActionType.REMOVE, r"\1", "package"),
    (re.compile(r"(?:update|upgrade)\s+(\w+(?:-\w+)*)", re.I), ActionType.UPDATE, r"\1", "package"),
    # Docker actions
    (re.compile(r"restart\s+(?:container\s+)?(\w+)", re.I), ActionType.RESTART, r"\1", "docker"),
    (re.compile(r"stop\s+(?:container\s+)?(\w+)", re.I), ActionType.STOP, r"\1", "docker"),
    (re.compile(r"start\s+(?:container\s+)?(\w+)", re.I), ActionType.START, r"\1", "docker"),
    # Fix actions
    (re.compile(r"fix\s+(\w+)", re.I), ActionType.FIX, r"\1", "service"),
    (re.compile(r"(?:repair|resolve)\s+(\w+)", re.I), ActionType.FIX, r"\1", "service"),
]

# Conditional patterns (e.g., "if not running", "if it's down")
CONDITION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"if\s+(?:it(?:'s| is)\s+)?not\s+running", re.I), "if not running"),
    (re.compile(r"if\s+(?:it(?:'s| is)\s+)?(?:down|stopped|failed)", re.I), "if not running"),
    (re.compile(r"if\s+(?:it(?:'s| is)\s+)?(?:installed|present)", re.I), "if installed"),
    (re.compile(r"if\s+(?:it(?:'s| is)\s+)?not\s+(?:installed|present)", re.I), "if not installed"),
]


# =============================================================================
# LLM Prompt
# =============================================================================

INTENT_EXTRACTION_PROMPT = """Analyze this user request and extract what they need.

User request: "{user_input}"

Extract:
1. INFORMATION_NEEDS: What information does the user need? (read operations)
2. ACTION_REQUESTS: What actions does the user want performed? (write operations)

Return JSON with this exact structure:
{{
  "information_needs": [
    {{"category": "service|file|package|docker|network|config|system", "target": "name or path", "aspects": ["status", "logs", "content", etc.]}}
  ],
  "action_requests": [
    {{"action_type": "restart|start|stop|install|remove|update|configure|create|delete|fix|enable|disable|custom", "target": "name", "domain": "service|package|docker|file|network", "condition": "optional condition like 'if not running'"}}
  ],
  "goal_summary": "One sentence describing what user wants"
}}

Rules:
- Only include relevant needs/actions from the request
- If it's just a question, action_requests should be empty []
- If it's just an action, information_needs may be empty [] or include status checks
- For mixed requests like "why is X failing and can you restart it", include BOTH info needs AND actions
- Common categories: service (systemd), file (filesystem), package (apt), docker (containers), network (ports/connections), system (resources)
- Common action types: restart, start, stop, install, remove, update, fix, enable, disable

Return only valid JSON, no other text."""


# =============================================================================
# IntentAnalyzer
# =============================================================================


class IntentAnalyzer:
    """Analyzes user input to extract unified intent.

    Uses pattern matching for fast common cases, falls back to LLM
    for complex or ambiguous requests.
    """

    def __init__(
        self,
        use_llm_fallback: bool = True,
        pattern_confidence: float = 0.9,
        llm_confidence: float = 0.8,
    ) -> None:
        """Initialize the analyzer.

        Args:
            use_llm_fallback: Whether to use LLM for complex cases.
            pattern_confidence: Confidence for pattern-matched intents.
            llm_confidence: Confidence for LLM-extracted intents.
        """
        self.use_llm_fallback = use_llm_fallback
        self.pattern_confidence = pattern_confidence
        self.llm_confidence = llm_confidence

    async def analyze(self, user_input: str) -> AgenticIntent:
        """Analyze user input and extract unified intent.

        Args:
            user_input: Raw user input.

        Returns:
            AgenticIntent with information needs and action requests.
        """
        user_input = user_input.strip()
        if not user_input:
            return AgenticIntent(
                information_needs=(),
                action_requests=(),
                goal_summary="Empty input",
                confidence=0.0,
            )

        # Try pattern matching first (fast path)
        intent = self._analyze_with_patterns(user_input)
        if intent and (intent.information_needs or intent.action_requests):
            logger.debug(
                f"Pattern matched intent: {len(intent.information_needs)} needs, {len(intent.action_requests)} actions"
            )
            return intent

        # Fall back to LLM for complex cases
        if self.use_llm_fallback:
            try:
                llm_intent = await self._analyze_with_llm(user_input)
                if llm_intent:
                    logger.debug(
                        f"LLM extracted intent: {len(llm_intent.information_needs)} needs, {len(llm_intent.action_requests)} actions"
                    )
                    return llm_intent
            except Exception as e:
                logger.warning(f"LLM analysis failed: {e}")

        # Return empty intent if nothing worked
        return AgenticIntent(
            information_needs=(),
            action_requests=(),
            goal_summary=user_input[:100],
            confidence=0.0,
        )

    def _analyze_with_patterns(self, user_input: str) -> AgenticIntent | None:
        """Extract intent using pattern matching.

        Args:
            user_input: User input text.

        Returns:
            AgenticIntent if patterns matched, None otherwise.
        """
        info_needs: list[InformationNeed] = []
        actions: list[ActionRequest] = []
        condition: str | None = None

        # Check for conditional phrases
        for pattern, cond in CONDITION_PATTERNS:
            if pattern.search(user_input):
                condition = cond
                break

        # Extract information needs
        for pattern, category, target_group, aspects in INFO_PATTERNS:
            match = pattern.search(user_input)
            if match:
                target = match.expand(target_group) if target_group else ""
                # Clean up target
                target = target.strip().strip("'\"")
                if target or category in ("docker", "network", "system"):
                    info_needs.append(
                        InformationNeed(
                            category=category,
                            target=target,
                            aspects=aspects,
                            confidence=self.pattern_confidence,
                        )
                    )

        # Extract action requests
        for pattern, action_type, target_group, domain in ACTION_PATTERNS:
            match = pattern.search(user_input)
            if match:
                target = match.expand(target_group)
                target = target.strip().strip("'\"")
                if target:
                    actions.append(
                        ActionRequest(
                            action_type=action_type,
                            target=target,
                            domain=domain,
                            condition=condition,
                            confidence=self.pattern_confidence,
                        )
                    )

        # If we found anything, return the intent
        if info_needs or actions:
            # Deduplicate
            info_needs = list({(n.category, n.target): n for n in info_needs}.values())
            actions = list({(a.action_type, a.target, a.domain): a for a in actions}.values())

            return AgenticIntent(
                information_needs=tuple(info_needs),
                action_requests=tuple(actions),
                goal_summary=self._summarize_goal(info_needs, actions),
                confidence=self.pattern_confidence,
            )

        return None

    async def _analyze_with_llm(self, user_input: str) -> AgenticIntent | None:
        """Extract intent using LLM.

        Args:
            user_input: User input text.

        Returns:
            AgenticIntent if LLM extraction succeeded, None otherwise.
        """
        from elle.rag.llm import get_llm

        llm = get_llm()
        if not llm.is_available():
            return None

        prompt = INTENT_EXTRACTION_PROMPT.format(user_input=user_input)

        try:
            response = llm.generate(
                prompt,
                max_tokens=500,
                temperature=0.1,
            )

            if not response or not response.content:
                return None

            # Parse JSON response
            data = self._parse_llm_response(response.content)
            if not data:
                return None

            return self._build_intent_from_llm(data, user_input)

        except Exception as e:
            logger.warning(f"LLM intent extraction failed: {e}")
            return None

    def _parse_llm_response(self, content: str) -> dict[str, Any] | None:
        """Parse JSON from LLM response.

        Args:
            content: Raw LLM response.

        Returns:
            Parsed dict or None if parsing failed.
        """
        # Try to extract JSON from response
        content = content.strip()

        # Handle markdown code blocks
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()

        try:
            return cast(dict[str, Any], json.loads(content))
        except json.JSONDecodeError:
            # Try to find JSON object in the response
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                try:
                    return cast(dict[str, Any], json.loads(match.group()))
                except json.JSONDecodeError:
                    pass
            return None

    def _build_intent_from_llm(
        self,
        data: dict[str, Any],
        user_input: str,
    ) -> AgenticIntent:
        """Build AgenticIntent from parsed LLM response.

        Args:
            data: Parsed JSON data.
            user_input: Original user input.

        Returns:
            AgenticIntent built from the data.
        """
        info_needs: list[InformationNeed] = []
        actions: list[ActionRequest] = []

        # Parse information needs
        for need_data in data.get("information_needs", []):
            try:
                category = need_data.get("category", "system")
                if category not in ("service", "file", "package", "docker", "network", "config", "system"):
                    category = "system"

                info_needs.append(
                    InformationNeed(
                        category=category,
                        target=need_data.get("target", ""),
                        aspects=tuple(need_data.get("aspects", ["info"])),
                        confidence=self.llm_confidence,
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to parse info need: {e}")

        # Parse action requests
        for action_data in data.get("action_requests", []):
            try:
                action_type_str = action_data.get("action_type", "custom")
                try:
                    action_type = ActionType(action_type_str.lower())
                except ValueError:
                    action_type = ActionType.CUSTOM

                actions.append(
                    ActionRequest(
                        action_type=action_type,
                        target=action_data.get("target", ""),
                        domain=action_data.get("domain", "service"),
                        parameters=action_data.get("parameters", {}),
                        condition=action_data.get("condition"),
                        confidence=self.llm_confidence,
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to parse action: {e}")

        goal_summary = data.get("goal_summary", user_input[:100])

        return AgenticIntent(
            information_needs=tuple(info_needs),
            action_requests=tuple(actions),
            goal_summary=goal_summary,
            confidence=self.llm_confidence,
        )

    def _summarize_goal(
        self,
        info_needs: list[InformationNeed],
        actions: list[ActionRequest],
    ) -> str:
        """Generate a goal summary from extracted needs and actions.

        Args:
            info_needs: Extracted information needs.
            actions: Extracted action requests.

        Returns:
            Human-readable goal summary.
        """
        parts: list[str] = []

        if info_needs:
            targets = [n.target for n in info_needs if n.target]
            if targets:
                parts.append(f"Check {', '.join(targets[:3])}")
            else:
                categories = list({n.category for n in info_needs})
                parts.append(f"Get {', '.join(categories[:3])} info")

        if actions:
            action_descs = [f"{a.action_type.value} {a.target}" for a in actions]
            parts.append(", ".join(action_descs[:3]))

        return "; ".join(parts) if parts else "Process request"

    def can_handle(self, user_input: str) -> bool:
        """Quick check if input might be handleable.

        Args:
            user_input: User input text.

        Returns:
            True if the input might be handleable.
        """
        # Check if any pattern matches
        for pattern, *_ in INFO_PATTERNS:
            if pattern.search(user_input):
                return True

        for pattern, *_ in ACTION_PATTERNS:
            if pattern.search(user_input):
                return True

        # Check for common keywords
        keywords = (
            "status",
            "running",
            "logs",
            "show",
            "what",
            "why",
            "is",
            "restart",
            "start",
            "stop",
            "install",
            "remove",
            "fix",
            "container",
            "service",
            "package",
            "port",
            "file",
        )
        lower = user_input.lower()
        return any(kw in lower for kw in keywords)


# =============================================================================
# Module-level singleton
# =============================================================================

_analyzer: IntentAnalyzer | None = None


def get_intent_analyzer() -> IntentAnalyzer:
    """Get the shared analyzer instance.

    Returns:
        The IntentAnalyzer singleton.
    """
    global _analyzer
    if _analyzer is None:
        _analyzer = IntentAnalyzer()
    return _analyzer


def reset_intent_analyzer() -> None:
    """Reset the shared analyzer instance."""
    global _analyzer
    _analyzer = None
