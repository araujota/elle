"""Intent classifier for ELLE Terminal.

Hybrid classifier that uses:
1. Hard keyword routing for meta/navigation commands (no SLM call)
2. SLM classification via Ollama for natural language inputs
3. Post-processing with safety overrides and confidence adjustments

The classifier runs before any action is taken, ensuring every input
is properly categorized and safety-checked.

SLM Configuration:
- Model: phi3.5:3.8b-mini-instruct-q8_0 (Q8 quantized for memory efficiency)
- Keep-alive: Indefinite (-1) for instant classification response
- Context: 2048 tokens (minimal for classification tasks)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from elle.cli.subprocess_runner import check_denylist
from elle.cli.terminal.intent import (
    CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE,
    Intent,
    IntentResult,
    create_clarification_result,
    create_rule_result,
)
from elle.common.session import Session
from elle.rag.constants import (
    SLM_FALLBACK_MODELS,
    SLM_KEEP_ALIVE,
    SLM_MAX_TOKENS,
    SLM_MODEL,
    SLM_NUM_CTX,
    SLM_TEMPERATURE,
    SLM_TIMEOUT,
)
from elle.rag.ollama_client import OllamaClient, OllamaConfig, OllamaError

logger = logging.getLogger(__name__)


# =============================================================================
# Hard routes - exact keyword matching (no SLM call needed)
# =============================================================================

# Meta commands - exact match (case insensitive)
META_COMMANDS = frozenset({
    "help", "exit", "quit", "clear", "config", "about", "version", "sponsor",
})

# Navigation commands - exact match
NAVIGATION_COMMANDS = frozenset({
    "status", "events", "logs", "man", "search", "history",
    "incidents", "incident",  # Incident vault navigation
})

# Fixit triggers - exact match or prefix
FIXIT_TRIGGERS = frozenset({
    "fix", "fix it", "fixit", "why did that fail", "what went wrong",
})

# Explain command triggers - exact match
EXPLAIN_TRIGGERS = frozenset({
    "explain", "what did that do", "what did it do",
    "what happened", "explain that", "explain command",
})

# Prefix commands - explicit intent markers
PREFIX_COMMANDS = {
    "/ask": Intent.SYSTEM_QUESTION,
    "/do": Intent.SYSTEM_TASK,
    "/fix": Intent.FIXIT,
    "/sh": Intent.SHELL_PASSTHROUGH,
    "/run": Intent.SHELL_PASSTHROUGH,
    "/map": Intent.NAVIGATION,  # GUI mapping/tree trace command (renamed from /learn)
    "/learn": Intent.LEARN_PACKAGE,  # Package capability learning
    "/trace": Intent.SHELL_PASSTHROUGH,  # Execute with syscall tracing
    "/explain": Intent.EXPLAIN_COMMAND,  # Explain last traced command
    "/preflight": Intent.NAVIGATION,  # Pre-flight package validation
    "!": Intent.SHELL_PASSTHROUGH,  # Bang prefix for shell
}


# =============================================================================
# Shell command detection patterns
# =============================================================================

# Common shell command prefixes (first word patterns)
SHELL_COMMAND_PATTERNS = [
    # File operations
    r"^(ls|cd|pwd|cat|head|tail|less|more|mkdir|rmdir|rm|cp|mv|touch|chmod|chown)\b",
    # Text processing
    r"^(grep|sed|awk|sort|uniq|wc|cut|tr|diff|patch)\b",
    # System info
    r"^(ps|top|htop|kill|pkill|pgrep|df|du|free|uname|uptime|who|whoami)\b",
    # Network
    r"^(ping|curl|wget|ssh|scp|rsync|netstat|ss|ip|ifconfig|dig|nslookup)\b",
    # Package management
    r"^(apt|apt-get|dpkg|snap|pip|npm|cargo|go)\b",
    # Services
    r"^(systemctl|service|journalctl)\b",
    # Docker/containers
    r"^(docker|podman|kubectl)\b",
    # Git
    r"^(git)\b",
    # Python/Node/etc
    r"^(python|python3|node|npm|npx|ruby|perl)\b",
    # Editors (usually for opening files)
    r"^(vim|vi|nano|emacs|code)\b",
    # Shell builtins and common utilities
    r"^(echo|printf|export|source|env|which|whereis|locate|find|xargs|exit|return|test)\b",
    # Archive/compression
    r"^(tar|zip|unzip|gzip|gunzip|bzip2|xz)\b",
    # Build tools
    r"^(make|cmake|gcc|g\+\+|clang|rustc)\b",
    # Testing
    r"^(pytest|jest|mocha|cargo test)\b",
    # Generic executable patterns
    r"^\./",  # ./script
    r"^~/.*/",  # ~/bin/something
    r"^/",  # Absolute path
]

_COMPILED_SHELL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SHELL_COMMAND_PATTERNS]


# Question indicators
QUESTION_INDICATORS = [
    r"^why\b",
    r"^how\b",
    r"^what\b",
    r"^when\b",
    r"^where\b",
    r"^can you explain\b",
    r"^tell me\b",
    r"^explain\b",
    r"\?$",  # Ends with question mark
]

_COMPILED_QUESTION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in QUESTION_INDICATORS]


# Task indicators (imperative requests)
TASK_INDICATORS = [
    r"^open port\b",
    r"^close port\b",
    r"^enable\b",
    r"^disable\b",
    r"^configure\b",
    r"^set up\b",
    r"^setup\b",
    r"^install\b",
    r"^uninstall\b",
    r"^create\b",
    r"^delete\b",
    r"^add\b",
    r"^remove\b",
    r"^update\b",
    r"^upgrade\b",
    r"^restart\b",
    r"^start\b",
    r"^stop\b",
    r"^schedule\b",
    r"^automate\b",
    r"^backup\b",
    r"^restore\b",
]

_COMPILED_TASK_PATTERNS = [re.compile(p, re.IGNORECASE) for p in TASK_INDICATORS]


# Incident navigation patterns (natural language for incident vault)
INCIDENT_PATTERNS = [
    r"^show\s+(me\s+)?(recent\s+)?incidents?",
    r"^list\s+(recent\s+)?incidents?",
    r"^(recent\s+)?incidents?$",
    r"^what\s+incidents?\s+(do\s+we\s+)?have",
    r"^any\s+(recent\s+)?incidents?",
    r"^incident\s+(history|log|reports?)",
    r"^export\s+(my\s+)?incident",
    r"^search\s+incidents?",
    r"^find\s+incidents?",
    r"show\s+(me\s+)?incident\s+[a-f0-9]+",
    r"^incident\s+[a-f0-9]+",  # Direct incident ID
]

_COMPILED_INCIDENT_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INCIDENT_PATTERNS]


# Docker domain patterns
DOCKER_PATTERNS = [
    # Container operations (system_task)
    (r"run\s+\w+\s+(?:container|locally|in\s+docker)", "system_task", 0.95),
    (r"(?:start|stop|restart|remove|kill)\s+(?:the\s+)?(?:container|docker)", "system_task", 0.90),
    (r"convert\s+docker\s+run\s+to\s+compose", "system_task", 0.95),
    (r"create\s+(?:a\s+)?docker[\s-]?compose", "system_task", 0.90),
    (r"run\s+(?:postgres|mysql|redis|nginx|mongo)\s+(?:locally|container)?", "system_task", 0.90),
    # Container questions (system_question)
    (r"what\s+containers?\s+(?:are|is)\s+(?:using|running)", "system_question", 0.90),
    (r"why\s+(?:did|does|is)\s+(?:the\s+)?container\s+(?:restart|crash|fail)", "system_question", 0.92),
    (r"which\s+container\s+(?:is\s+)?using\s+(?:most|the\s+most)", "system_question", 0.90),
    (r"explain\s+(?:the\s+)?docker\s+(?:error|issue|problem)", "system_question", 0.88),
    (r"show\s+(?:me\s+)?container\s+(?:logs?|stats?|status)", "navigation", 0.85),
]

_COMPILED_DOCKER_PATTERNS = [(re.compile(p, re.IGNORECASE), i, c) for p, i, c in DOCKER_PATTERNS]


# WireGuard/VPN domain patterns
WIREGUARD_PATTERNS = [
    # Setup and configuration (system_task)
    (r"set\s*up\s+(?:a\s+)?(?:wireguard|wg|vpn)", "system_task", 0.95),
    (r"configure\s+(?:a\s+)?(?:wireguard|wg|vpn)", "system_task", 0.92),
    (r"create\s+(?:a\s+)?(?:wireguard|wg|vpn)\s+(?:config|configuration|tunnel)", "system_task", 0.95),
    (r"add\s+(?:a\s+)?(?:wireguard|wg)\s+peer", "system_task", 0.92),
    # Key management (system_task)
    (r"rotate\s+(?:the\s+)?(?:wireguard|wg)\s+keys?", "system_task", 0.95),
    (r"generate\s+(?:new\s+)?(?:wireguard|wg)\s+keys?", "system_task", 0.92),
    # Monitoring (navigation)
    (r"show\s+(?:me\s+)?(?:traffic|bandwidth)\s+(?:through|on|for)\s+(?:wireguard|wg|vpn)", "navigation", 0.88),
    (r"(?:wireguard|wg|vpn)\s+status", "navigation", 0.90),
    (r"list\s+(?:wireguard|wg)\s+(?:peers?|interfaces?)", "navigation", 0.88),
    # Questions (system_question)
    (r"why\s+(?:is|isn't)\s+(?:the\s+)?(?:wireguard|wg|vpn)\s+(?:working|connecting)", "system_question", 0.90),
    (r"is\s+(?:my\s+)?(?:wireguard|wg|vpn)\s+(?:connected|up|active)", "system_question", 0.85),
]

_COMPILED_WIREGUARD_PATTERNS = [(re.compile(p, re.IGNORECASE), i, c) for p, i, c in WIREGUARD_PATTERNS]


# Network introspection patterns
NETWORK_PATTERNS = [
    # Connectivity diagnosis (system_question)
    (r"why\s+can'?t\s+I\s+(?:reach|connect\s+to|access)", "system_question", 0.92),
    (r"(?:can'?t|cannot|unable\s+to)\s+(?:reach|connect\s+to|access)", "system_question", 0.88),
    (r"is\s+\S+\s+(?:reachable|accessible|up)", "system_question", 0.85),
    (r"diagnose\s+(?:connectivity|connection|network)\s+(?:to|for|issue)?", "system_question", 0.90),
    # Firewall (system_question for explain, system_task for changes)
    (r"explain\s+(?:my\s+)?(?:firewall|ufw|iptables)\s*(?:rules?)?", "system_question", 0.92),
    (r"what\s+(?:is|are)\s+(?:my\s+)?(?:firewall|ufw)\s+rules?", "system_question", 0.90),
    (r"show\s+(?:me\s+)?(?:firewall|ufw)\s+(?:rules?|status)", "navigation", 0.88),
    # Service lockdown (system_task)
    (r"lock\s+(?:down\s+)?\w+\s+to\s+(?:localhost|local)", "system_task", 0.92),
    (r"restrict\s+\w+\s+to\s+(?:localhost|local|internal)", "system_task", 0.90),
    (r"(?:allow|block|deny)\s+(?:port\s+)?\d+\s+(?:from|to)", "system_task", 0.88),
    (r"open\s+port\s+\d+\s+(?:for|to|from)", "system_task", 0.90),
    (r"close\s+port\s+\d+", "system_task", 0.90),
    # Network info (navigation/question)
    (r"what\s+(?:is|are)\s+my\s+(?:ip|address|dns|gateway|route)", "system_question", 0.85),
    (r"show\s+(?:me\s+)?(?:my\s+)?(?:network|routing|dns)\s+(?:config|configuration|info)", "navigation", 0.85),
]

_COMPILED_NETWORK_PATTERNS = [(re.compile(p, re.IGNORECASE), i, c) for p, i, c in NETWORK_PATTERNS]


# GUI automation patterns (gui_task)
GUI_TASK_PATTERNS = [
    # Open app and do action
    (r"(?:open|launch|start)\s+(\w+)\s+and\s+(.+)", "gui_task", 0.92),
    # Click/toggle in app
    (r"(?:click|press|toggle|enable|disable)\s+(.+)\s+in\s+(\w+)", "gui_task", 0.90),
    (r"in\s+(\w+),?\s+(?:click|press|toggle|enable|disable)\s+(.+)", "gui_task", 0.90),
    # Settings/preferences patterns
    (r"(?:set|change|configure)\s+(.+)\s+in\s+(\w+)\s+to\s+(.+)", "gui_task", 0.88),
    (r"(?:in\s+)?settings?,?\s+(?:disable|enable|toggle|change)\s+(.+)", "gui_task", 0.88),
    # Common UI actions
    (r"turn\s+(?:off|on)\s+(.+)\s+in\s+(?:the\s+)?(?:settings?|preferences?|control)", "gui_task", 0.90),
    (r"(?:disable|enable)\s+(?:the\s+)?(.+)\s+(?:option|setting|toggle)", "gui_task", 0.85),
]

_COMPILED_GUI_PATTERNS = [(re.compile(p, re.IGNORECASE), i, c) for p, i, c in GUI_TASK_PATTERNS]


# Package learning patterns (learn_package)
PACKAGE_LEARN_PATTERNS = [
    # Explicit learning requests
    (r"^(?:learn|figure\s+out|understand)\s+(?:how\s+to\s+(?:use|work\s+with)\s+)?(\w[\w\-\.]+)$", "learn_package", 0.92),
    (r"^(?:what\s+can|how\s+do\s+I\s+use)\s+(\w[\w\-\.]+)", "learn_package", 0.88),
    (r"^teach\s+me\s+(?:about\s+)?(\w[\w\-\.]+)$", "learn_package", 0.90),
    # Discovery requests
    (r"^(?:discover|explore)\s+(?:the\s+)?(?:capabilities?\s+(?:of\s+)?)?(\w[\w\-\.]+)$", "learn_package", 0.88),
    (r"^(?:what\s+(?:does|can))\s+(\w[\w\-\.]+)\s+do\??$", "learn_package", 0.85),
    # Generate capabilities
    (r"^generate\s+(?:capabilities?\s+(?:for|from)\s+)?(\w[\w\-\.]+)$", "learn_package", 0.90),
]

_COMPILED_PACKAGE_LEARN_PATTERNS = [(re.compile(p, re.IGNORECASE), i, c) for p, i, c in PACKAGE_LEARN_PATTERNS]


# =============================================================================
# SLM Prompt Template
# =============================================================================

CLASSIFIER_SYSTEM_PROMPT = """You are an intent classifier for a Linux assistant.
Return ONLY valid JSON matching this exact schema - no other text:
{
  "intent": "...",
  "confidence": 0.0,
  "rationale": "...",
  "entities": [],
  "suggested_followups": [],
  "requires_clarification": false
}

INTENTS (use exactly these values):
- "meta": help, exit, config, about, clear, version
- "navigation": status, events, logs, man pages, searching docs, /map (GUI mapping)
- "fixit": user wants to fix a failed command or understand an error
- "system_question": user asks why/how/what about system state (not a command)
- "system_task": user requests a system change in natural language
- "shell_passthrough": user is entering a shell command to execute directly
- "gui_task": user wants to interact with a GUI application (click, toggle, enable/disable in settings)
- "explain_command": user wants to understand what a command did (what did that do, explain)
- "learn_package": user wants to learn about a package/binary, discover its capabilities, understand how to use it

RULES:
- Known keyword (help, exit, status) -> meta/navigation, confidence >= 0.9
- Shell command (starts with command name, has flags) -> shell_passthrough
- Question (starts with why/how/what, ends with ?) -> system_question
- Natural language action request -> system_task
- GUI interaction (click X in app, toggle Y in settings) -> gui_task
- Mentions previous failure or error -> fixit
- "what did that do", "explain", "what happened" -> explain_command
- "learn ffmpeg", "figure out how to use X", "teach me about X" -> learn_package
- Keep rationale to ONE short sentence
- entities: extract command names, file paths, service names, port numbers, app names, package names
- If confidence < 0.55, set requires_clarification to true"""


CLASSIFIER_PROMPT_TEMPLATE = """CONTEXT:
cwd: {cwd}
last_cmd: {last_cmd}
last_exit: {last_exit}

USER INPUT:
{text}"""


# =============================================================================
# Classifier Implementation
# =============================================================================

class ClassificationLog(BaseModel):
    """Log entry for a classification decision."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    input_text: str
    result: IntentResult
    session_cwd: str
    session_last_cmd: str | None
    session_last_exit: int | None
    model_used: str | None = None
    latency_ms: float | None = None
    parse_retried: bool = False


class IntentClassifier:
    """Hybrid intent classifier using rules + SLM.

    Classification precedence:
    1. Hard keyword routing (meta, navigation, fixit triggers)
    2. Prefix commands (/ask, /do, /sh, !)
    3. SLM classification via Ollama
    4. Post-processing (safety overrides, confidence floors)

    The SLM is kept warm indefinitely (keep_alive=-1) for instant
    classification response (<100ms when warm).

    Usage:
        classifier = IntentClassifier()
        result = classifier.classify("why is my disk full?", session)
    """

    # Default model for classification (Q8 quantized for memory efficiency)
    DEFAULT_MODEL = SLM_MODEL
    FALLBACK_MODELS = list(SLM_FALLBACK_MODELS)

    def __init__(
        self,
        client: OllamaClient | None = None,
        model: str | None = None,
        log_path: Path | None = None,
    ) -> None:
        """Initialize the classifier.

        Args:
            client: Ollama client. Uses shared instance if not provided.
            model: Model name to use. Auto-detects if not provided.
            log_path: Path for classification logs. Defaults to ~/.local/state/elle/
        """
        self._client = client
        self._model = model
        self._detected_model: str | None = None
        self._log_path = log_path or Path.home() / ".local" / "state" / "elle"
        self._log_file = self._log_path / "intent_log.jsonl"

        # SLM inference settings (from constants)
        self._keep_alive = SLM_KEEP_ALIVE
        self._num_ctx = SLM_NUM_CTX
        self._temperature = SLM_TEMPERATURE
        self._max_tokens = SLM_MAX_TOKENS

    @property
    def client(self) -> OllamaClient:
        """Get the Ollama client (lazy initialization).

        The client is configured with SLM-optimized settings.
        """
        if self._client is None:
            # Create client with SLM-optimized config
            config = OllamaConfig(
                timeout=SLM_TIMEOUT,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                keep_alive=self._keep_alive,
                num_ctx=self._num_ctx,
            )
            self._client = OllamaClient(config)
        return self._client

    @property
    def model(self) -> str:
        """Get the model to use (auto-detect if needed)."""
        if self._model:
            return self._model

        if self._detected_model:
            return self._detected_model

        # Auto-detect available model
        if self.client.is_available():
            available = self.client.list_models()
            for candidate in [self.DEFAULT_MODEL] + self.FALLBACK_MODELS:
                if any(candidate in m for m in available):
                    self._detected_model = candidate
                    logger.info(f"Using SLM model: {candidate}")
                    return candidate

        # Fallback
        self._detected_model = self.DEFAULT_MODEL
        return self._detected_model

    def classify(self, text: str, session: Session) -> IntentResult:
        """Classify user input into an intent.

        Args:
            text: Raw input string from user.
            session: Current session state for context.

        Returns:
            IntentResult with classification and confidence.
        """
        text = text.strip()

        # Empty input
        if not text:
            return create_rule_result(
                Intent.META,
                "Empty input",
                confidence=1.0,
            )

        # Step 1: Hard keyword routing
        result = self._check_hard_routes(text, session)
        if result:
            self._log_classification(text, result, session)
            return result

        # Step 2: Prefix commands
        result = self._check_prefix_commands(text)
        if result:
            self._log_classification(text, result, session)
            return result

        # Step 3: Pattern-based pre-classification (high confidence shortcuts)
        result = self._check_patterns(text, session)
        if result and result.confidence >= MEDIUM_CONFIDENCE:
            # Apply safety overrides to pattern results too
            result = self._apply_safety_overrides(text, result)
            self._log_classification(text, result, session)
            return result

        # Step 4: SLM classification
        result = self._classify_with_slm(text, session)

        # Step 5: Post-processing (safety overrides)
        result = self._apply_safety_overrides(text, result)

        self._log_classification(text, result, session)
        return result

    def _check_hard_routes(self, text: str, session: Session) -> IntentResult | None:
        """Check for exact keyword matches."""
        lower = text.lower()

        # Meta commands
        if lower in META_COMMANDS:
            return create_rule_result(
                Intent.META,
                f"Exact meta command: {lower}",
                entities=[lower],
            )

        # Navigation commands
        if lower in NAVIGATION_COMMANDS:
            return create_rule_result(
                Intent.NAVIGATION,
                f"Exact navigation command: {lower}",
                entities=[lower],
            )

        # Fixit triggers - always route to FIXIT, let handler deal with edge cases
        if lower in FIXIT_TRIGGERS:
            if session.last_cmd and session.last_failed:
                return create_rule_result(
                    Intent.FIXIT,
                    "User wants to fix the last failed command",
                    entities=[session.last_cmd],
                )
            elif session.last_cmd:
                return create_rule_result(
                    Intent.FIXIT,
                    "User requested fix (last command succeeded)",
                    confidence=MEDIUM_CONFIDENCE,
                )
            else:
                return create_rule_result(
                    Intent.FIXIT,
                    "User requested fix (no previous command)",
                    confidence=MEDIUM_CONFIDENCE,
                )

        # Explain command triggers - route to EXPLAIN_COMMAND
        if lower in EXPLAIN_TRIGGERS:
            if session.last_cmd:
                return create_rule_result(
                    Intent.EXPLAIN_COMMAND,
                    "User wants to understand what the last command did",
                    entities=[session.last_cmd],
                )
            else:
                return create_rule_result(
                    Intent.EXPLAIN_COMMAND,
                    "User requested explanation (no previous command)",
                    confidence=MEDIUM_CONFIDENCE,
                )

        return None

    def _check_prefix_commands(self, text: str) -> IntentResult | None:
        """Check for prefix command markers."""
        for prefix, intent in PREFIX_COMMANDS.items():
            if text.startswith(prefix):
                # Extract the actual content after prefix
                content = text[len(prefix):].strip()
                return create_rule_result(
                    intent,
                    f"Explicit prefix command: {prefix}",
                    entities=[content] if content else [],
                )
        return None

    def _check_patterns(self, text: str, session: Session) -> IntentResult | None:
        """Check pattern-based classifications."""
        # Incident patterns (high priority for incident vault queries)
        for pattern in _COMPILED_INCIDENT_PATTERNS:
            if pattern.search(text):
                return IntentResult(
                    intent=Intent.NAVIGATION,
                    confidence=0.90,
                    rationale="Incident vault navigation request",
                    entities=["incidents"],
                    classified_by="rule",
                )

        # Docker domain patterns (high confidence)
        for pattern, intent_str, confidence in _COMPILED_DOCKER_PATTERNS:
            if pattern.search(text):
                intent = Intent.from_label(intent_str)
                return IntentResult(
                    intent=intent,
                    confidence=confidence,
                    rationale="Docker/container operation detected",
                    entities=["docker"],
                    classified_by="rule",
                )

        # WireGuard/VPN domain patterns (high confidence)
        for pattern, intent_str, confidence in _COMPILED_WIREGUARD_PATTERNS:
            if pattern.search(text):
                intent = Intent.from_label(intent_str)
                return IntentResult(
                    intent=intent,
                    confidence=confidence,
                    rationale="WireGuard/VPN operation detected",
                    entities=["wireguard"],
                    classified_by="rule",
                )

        # Network introspection patterns (high confidence)
        for pattern, intent_str, confidence in _COMPILED_NETWORK_PATTERNS:
            if pattern.search(text):
                intent = Intent.from_label(intent_str)
                return IntentResult(
                    intent=intent,
                    confidence=confidence,
                    rationale="Network operation detected",
                    entities=["network"],
                    classified_by="rule",
                )

        # GUI automation patterns (high confidence)
        for pattern, intent_str, confidence in _COMPILED_GUI_PATTERNS:
            if pattern.search(text):
                intent = Intent.from_label(intent_str)
                return IntentResult(
                    intent=intent,
                    confidence=confidence,
                    rationale="GUI automation task detected",
                    entities=["gui"],
                    classified_by="rule",
                )

        # Package learning patterns (high confidence)
        for pattern, intent_str, confidence in _COMPILED_PACKAGE_LEARN_PATTERNS:
            match = pattern.search(text)
            if match:
                intent = Intent.from_label(intent_str)
                # Extract the package name from the match
                package_name = match.group(1) if match.groups() else None
                entities = [package_name] if package_name else []
                return IntentResult(
                    intent=intent,
                    confidence=confidence,
                    rationale="Package learning request detected",
                    entities=entities,
                    classified_by="rule",
                )

        # Shell command patterns (highest priority for obvious commands)
        for pattern in _COMPILED_SHELL_PATTERNS:
            if pattern.search(text):
                # Extract the command name
                first_word = text.split()[0] if text.split() else text
                return IntentResult(
                    intent=Intent.SHELL_PASSTHROUGH,
                    confidence=0.85,
                    rationale=f"Looks like a shell command starting with '{first_word}'",
                    entities=[first_word],
                    classified_by="rule",
                )

        # Question patterns
        for pattern in _COMPILED_QUESTION_PATTERNS:
            if pattern.search(text):
                return IntentResult(
                    intent=Intent.SYSTEM_QUESTION,
                    confidence=0.70,
                    rationale="Input appears to be a question",
                    classified_by="rule",
                )

        # Task patterns (only if not a shell command)
        for pattern in _COMPILED_TASK_PATTERNS:
            if pattern.search(text):
                return IntentResult(
                    intent=Intent.SYSTEM_TASK,
                    confidence=0.70,
                    rationale="Input appears to be a task request",
                    classified_by="rule",
                )

        return None

    def _classify_with_slm(self, text: str, session: Session) -> IntentResult:
        """Classify using the SLM via Ollama.

        The SLM is configured with keep_alive=-1 to stay warm indefinitely,
        enabling sub-100ms classification responses.
        """
        if not self.client.is_available():
            logger.warning("Ollama not available, using fallback classification")
            return self._fallback_classification(text, session)

        # Build prompt
        prompt = CLASSIFIER_PROMPT_TEMPLATE.format(
            cwd=session.cwd.name or str(session.cwd),
            last_cmd=session.last_cmd or "(none)",
            last_exit=session.last_exit if session.last_exit is not None else "(none)",
            text=text,
        )

        try:
            # Call SLM with JSON mode and optimized settings
            import time
            start = time.time()

            response_data = self.client.generate_json(
                self.model,
                prompt,
                system=CLASSIFIER_SYSTEM_PROMPT,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                keep_alive=self._keep_alive,
                num_ctx=self._num_ctx,
            )

            latency_ms = (time.time() - start) * 1000
            logger.debug(f"SLM classification took {latency_ms:.0f}ms")

            # Parse into IntentResult
            return self._parse_slm_response(response_data, latency_ms)

        except OllamaError as e:
            logger.warning(f"SLM classification failed: {e}")
            return self._fallback_classification(text, session)
        except Exception as e:
            logger.error(f"Unexpected error in SLM classification: {e}")
            return self._fallback_classification(text, session)

    def _parse_slm_response(
        self,
        data: dict,
        latency_ms: float | None = None,
    ) -> IntentResult:
        """Parse SLM JSON response into IntentResult."""
        try:
            # Normalize intent value
            intent_str = data.get("intent", "").lower().strip()
            intent = Intent.from_label(intent_str)

            confidence = float(data.get("confidence", 0.5))
            rationale = str(data.get("rationale", "SLM classification"))
            entities = data.get("entities", [])
            followups = data.get("suggested_followups", [])
            requires_clarification = data.get("requires_clarification", False)

            # Enforce confidence threshold
            if confidence < CONFIDENCE_THRESHOLD:
                requires_clarification = True

            return IntentResult(
                intent=intent,
                confidence=confidence,
                rationale=rationale,
                entities=entities if isinstance(entities, list) else [],
                suggested_followups=followups if isinstance(followups, list) else [],
                requires_clarification=requires_clarification,
                classified_by="slm",
            )

        except (ValueError, ValidationError) as e:
            logger.warning(f"Failed to parse SLM response: {e}")
            return create_clarification_result(
                "Could not understand the classification response",
            )

    def _fallback_classification(self, text: str, session: Session) -> IntentResult:
        """Fallback classification when SLM is unavailable."""
        # Try pattern matching again with lower confidence
        result = self._check_patterns(text, session)
        if result:
            # Lower confidence for fallback
            return IntentResult(
                intent=result.intent,
                confidence=result.confidence * 0.8,
                rationale=f"Fallback: {result.rationale}",
                entities=result.entities,
                requires_clarification=result.confidence * 0.8 < CONFIDENCE_THRESHOLD,
                classified_by="fallback",
            )

        # Ultimate fallback: ask for clarification
        return create_clarification_result(
            "Unable to determine intent (SLM unavailable)",
        )

    def _apply_safety_overrides(self, text: str, result: IntentResult) -> IntentResult:
        """Apply safety overrides to the classification."""
        # Only apply to shell_passthrough
        if result.intent != Intent.SHELL_PASSTHROUGH:
            return result

        # Check against denylist
        denied, reason, explanation = check_denylist(text)

        if denied:
            # Dangerous command detected - force clarification
            logger.warning(f"Dangerous command detected: {reason}")
            return IntentResult(
                intent=result.intent,
                confidence=result.confidence * 0.3,  # Drastically reduce confidence
                rationale=f"Safety check: {explanation}",
                entities=result.entities,
                requires_clarification=True,
                suggested_followups=[
                    "This command appears dangerous",
                    "Rephrase your request",
                    "Use a safer alternative",
                ],
                classified_by=result.classified_by,
            )

        return result

    def _log_classification(
        self,
        text: str,
        result: IntentResult,
        session: Session,
        latency_ms: float | None = None,
    ) -> None:
        """Log classification for future analysis."""
        try:
            # Ensure log directory exists
            self._log_path.mkdir(parents=True, exist_ok=True)

            log_entry = ClassificationLog(
                timestamp=datetime.now(),
                input_text=text,
                result=result,
                session_cwd=str(session.cwd),
                session_last_cmd=session.last_cmd,
                session_last_exit=session.last_exit,
                model_used=self._detected_model if result.classified_by == "slm" else None,
                latency_ms=latency_ms,
            )

            # Append to log file
            with open(self._log_file, "a") as f:
                f.write(log_entry.model_dump_json() + "\n")

        except Exception as e:
            # Don't fail classification due to logging errors
            logger.debug(f"Failed to log classification: {e}")


# Module-level classifier instance
_classifier: IntentClassifier | None = None


def get_classifier() -> IntentClassifier:
    """Get the shared classifier instance.

    Returns:
        The IntentClassifier singleton.
    """
    global _classifier
    if _classifier is None:
        _classifier = IntentClassifier()
    return _classifier


def classify_intent(text: str, session: Session) -> IntentResult:
    """Convenience function to classify intent.

    Args:
        text: User input to classify.
        session: Current session state.

    Returns:
        IntentResult with classification.
    """
    return get_classifier().classify(text, session)
