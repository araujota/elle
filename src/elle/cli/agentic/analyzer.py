"""InformationNeedAnalyzer - Determines what information is needed to answer a question.

Uses a combination of rule-based patterns and LLM fallback to extract
information needs from user questions.
"""

from __future__ import annotations

import logging
import re

from elle.cli.agentic.models import InformationCategory, InformationNeed

logger = logging.getLogger(__name__)


# =============================================================================
# Pattern Definitions
# =============================================================================

# Each pattern tuple: (regex_pattern, category, aspects, target_group_index)
# target_group_index indicates which regex group contains the target

PATTERNS: list[tuple[str, InformationCategory, tuple[str, ...], int]] = [
    # === Service Status Patterns ===
    (r"(?:what(?:'s| is) (?:the )?)?status of (\w[\w\-\.]*)(?: service)?", "service", ("status",), 1),
    (r"is (\w[\w\-\.]*)(?: service)? running\??", "service", ("status",), 1),
    (r"(\w[\w\-\.]*) service(?: status)?", "service", ("status",), 1),
    (r"check (\w[\w\-\.]*)(?:'s)? status", "service", ("status",), 1),
    (r"show(?: me)? (\w[\w\-\.]*) (?:service )?status", "service", ("status",), 1),
    (r"(?:show|get)(?: me)?(?: the)? (\w[\w\-\.]*) logs?", "service", ("logs",), 1),
    (r"(\w[\w\-\.]*) (?:service )?logs?", "service", ("logs",), 1),
    # === File Content Patterns ===
    (r"(?:show|read|cat|display)(?: me)?(?: the)?(?: contents? of)? ([\/\w\.\-_]+\.\w+)", "file", ("content",), 1),
    (r"what(?:'s|'s| is) in ([\/\w\.\-_]+)\??", "file", ("content",), 1),
    (r"(?:show|read)(?: me)? ([\/\w\.\-_]+)", "file", ("content",), 1),
    # === Package Patterns ===
    (r"(?:is )?([\w\-\.]+) installed\??", "package", ("info",), 1),
    (r"(?:what )?version of ([\w\-\.]+)", "package", ("info",), 1),
    (r"([\w\-\.]+) version\??", "package", ("info",), 1),
    (r"(?:show|get)(?: me)?(?: the)? ([\w\-\.]+) package(?: info(?:rmation)?)?", "package", ("info",), 1),
    # === Docker Patterns ===
    (r"(?:what )?docker (?:container(?:s)?|status)(?: of)? ?(\w+)?", "docker", ("list", "status"), 1),
    (r"(?:what )?containers? (?:are )?running\??", "docker", ("list",), 0),
    (r"status of(?: the)? (\w+) container", "docker", ("status", "logs"), 1),
    (r"(\w+) container(?: status)?", "docker", ("status",), 1),
    (r"(?:show|get)(?: me)?(?: the)? (\w+) container logs?", "docker", ("logs",), 1),
    # === Network Patterns ===
    (
        r"(?:what(?:'s| is)|show)(?: me)?(?: the)? (?:listening|open) (?:ports?|connections?)",
        "network",
        ("listeners",),
        0,
    ),
    (r"(?:what(?:'s| is)|show)(?: me)?(?: all)?(?: the)? listening(?: ports?)?", "network", ("listeners",), 0),
    (r"(?:what(?:'s| is))(?: listening)? on port (\d+)\??", "network", ("listeners",), 1),
    (r"port (\d+)(?: status)?", "network", ("listeners",), 1),
    (r"(?:who|what)(?:'s| is) (?:using|on) port (\d+)", "network", ("listeners",), 1),
    # === Config Patterns ===
    (r"(?:show|read)(?: me)?(?: the)? (\w+) config(?:uration)?", "config", ("content",), 1),
    (r"(\w+) config(?:uration)?(?: file)?", "config", ("content",), 1),
    # === System Patterns ===
    (
        r"(?:what(?:'s| is)|show)(?: me)?(?: the)? (?:system )?(?:info(?:rmation)?|resources?)",
        "system",
        ("info", "resources"),
        0,
    ),
    (r"(?:how much )?(?:memory|ram|cpu|disk)(?: usage)?", "system", ("resources",), 0),
    (r"(?:system )?(?:load|uptime)", "system", ("resources",), 0),
]


# =============================================================================
# InformationNeedAnalyzer
# =============================================================================


class InformationNeedAnalyzer:
    """Analyzes questions to determine what information is needed.

    Uses rule-based pattern matching first, with LLM fallback for
    complex or ambiguous questions.
    """

    def __init__(self, use_llm_fallback: bool = True) -> None:
        """Initialize the analyzer.

        Args:
            use_llm_fallback: Whether to use LLM for questions that
                don't match any pattern.
        """
        self.use_llm_fallback = use_llm_fallback
        self._compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), category, aspects, target_group)
            for pattern, category, aspects, target_group in PATTERNS
        ]

    def analyze(self, question: str) -> tuple[InformationNeed, ...]:
        """Analyze a question to extract information needs.

        Args:
            question: The user's question.

        Returns:
            Tuple of InformationNeed objects representing what's needed
            to answer the question.
        """
        question = question.strip()
        if not question:
            return ()

        # Try rule-based patterns first
        needs = self._analyze_with_patterns(question)
        if needs:
            logger.debug(f"Pattern match found {len(needs)} needs for: {question[:50]}")
            return needs

        # Try LLM fallback if enabled
        if self.use_llm_fallback:
            needs = self._analyze_with_llm(question)
            if needs:
                logger.debug(f"LLM analysis found {len(needs)} needs for: {question[:50]}")
                return needs

        logger.debug(f"No information needs identified for: {question[:50]}")
        return ()

    def _analyze_with_patterns(self, question: str) -> tuple[InformationNeed, ...]:
        """Analyze using rule-based patterns.

        Args:
            question: The user's question.

        Returns:
            Tuple of InformationNeed objects, empty if no patterns match.
        """
        needs: list[InformationNeed] = []

        for pattern, category, aspects, target_group in self._compiled_patterns:
            match = pattern.search(question)
            if match:
                # Extract target from the appropriate group
                if target_group > 0:
                    try:
                        target = match.group(target_group)
                        if target:
                            target = target.strip()
                    except IndexError:
                        target = ""
                else:
                    # No specific target (e.g., "what's listening")
                    target = ""

                # Create the need
                need = InformationNeed(
                    category=category,
                    target=target,
                    aspects=aspects,
                    confidence=0.9,  # High confidence for pattern matches
                )
                needs.append(need)

                # Usually one pattern match is enough
                if len(needs) >= 1:
                    break

        return tuple(needs)

    def _analyze_with_llm(self, question: str) -> tuple[InformationNeed, ...]:
        """Analyze using LLM for complex questions.

        Args:
            question: The user's question.

        Returns:
            Tuple of InformationNeed objects from LLM analysis.
        """
        try:
            from elle.rag.llm import get_llm

            llm = get_llm()
            if not llm.is_available():
                return ()

            # Build the analysis prompt
            prompt = f"""Analyze this system administration question and identify what information is needed.

Question: {question}

Respond with JSON containing an array of information needs:
{{
  "needs": [
    {{
      "category": "service|file|package|docker|network|config|system",
      "target": "name of service/file/package/container being asked about",
      "aspects": ["status", "logs", "content", "info", "list", "resources"]
    }}
  ]
}}

Categories:
- service: systemd services (nginx, ssh, postgresql)
- file: specific files (/etc/hosts, /etc/nginx/nginx.conf)
- package: installed packages (nginx, python3)
- docker: containers (check status, logs)
- network: ports, connections, listeners
- config: configuration files
- system: CPU, memory, disk, uptime

Only include needs that can be satisfied by reading system state (no mutations).
If the question isn't about system state, return {{"needs": []}}."""

            result = llm.generate_json(prompt, temperature=0.1)

            needs_data = result.get("needs", [])
            needs: list[InformationNeed] = []

            for need_dict in needs_data:
                if isinstance(need_dict, dict):
                    category = need_dict.get("category", "")
                    if category in ("service", "file", "package", "docker", "network", "config", "system"):
                        need = InformationNeed(
                            category=category,
                            target=need_dict.get("target", ""),
                            aspects=tuple(need_dict.get("aspects", [])),
                            confidence=0.75,  # Lower confidence for LLM
                        )
                        needs.append(need)

            return tuple(needs)

        except Exception as e:
            logger.warning(f"LLM analysis failed: {e}")
            return ()


# =============================================================================
# Module-level singleton
# =============================================================================

_analyzer: InformationNeedAnalyzer | None = None


def get_analyzer() -> InformationNeedAnalyzer:
    """Get the shared analyzer instance.

    Returns:
        The InformationNeedAnalyzer singleton.
    """
    global _analyzer
    if _analyzer is None:
        _analyzer = InformationNeedAnalyzer()
    return _analyzer


def reset_analyzer() -> None:
    """Reset the shared analyzer instance."""
    global _analyzer
    _analyzer = None
