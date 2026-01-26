"""ELLE Engine - Core command processing.

This is the central processing unit for all ELLE interactions.
Both the REPL and one-shot commands route through this engine.

The engine is stateless - it takes input and session state,
and returns results with updated session state.

Flow:
    input + session -> classify -> policy check -> route -> execute -> result + new_session
"""

import contextlib
import logging
from enum import Enum, auto

from pydantic import BaseModel, ConfigDict

from elle.cli.subprocess_runner import RunMode, run_safe
from elle.cli.terminal.classifier import IntentClassifier, get_classifier
from elle.cli.terminal.incident_renderer import (
    render_incident_detail,
    render_incident_list,
    render_incident_markdown,
    render_search_results,
)
from elle.cli.terminal.intent import Intent, IntentResult
from elle.cli.terminal.renderer import Colors
from elle.common.session import Session

logger = logging.getLogger(__name__)


# =============================================================================
# Policy Integration
# =============================================================================

# Lazy import to avoid circular dependencies
_policy_module = None


def _get_policy_module():
    """Lazy load the policy module."""
    global _policy_module
    if _policy_module is None:
        try:
            from elle import policy

            _policy_module = policy
        except ImportError:
            logger.warning("Policy module not available")
            _policy_module = False
    return _policy_module if _policy_module else None


class EngineAction(Enum):
    """Actions the engine can signal to the caller."""

    CONTINUE = auto()  # Normal operation, continue REPL
    EXIT = auto()  # User requested exit
    CLEAR = auto()  # Clear the screen


class EngineResult(BaseModel):
    """Result of engine processing.

    Attributes:
        output: Text to display to the user (may be empty).
        session: Updated session state.
        action: Signal to the caller (continue, exit, etc.).
        success: Whether the operation succeeded.
    """

    model_config = ConfigDict(frozen=True)

    output: str
    session: Session
    action: EngineAction = EngineAction.CONTINUE
    success: bool = True


class Engine:
    """The ELLE command processing engine.

    Stateless processor that handles all user input. Designed to be
    used by both interactive REPL and one-shot CLI modes.

    Uses the IntentClassifier to route inputs to appropriate handlers:
    - META: help, exit, config, about, etc.
    - NAVIGATION: status, events, logs, man pages
    - SHELL_PASSTHROUGH: direct shell command execution
    - SYSTEM_QUESTION: questions about the system (RAG-powered)
    - SYSTEM_TASK: natural language task requests (RAG-powered)
    - FIXIT: fix last failed command

    Usage:
        engine = Engine()
        result = engine.process("help", session)
        # result.output contains response
        # result.session contains updated state
        # result.action signals what caller should do
    """

    # Default timeout for shell commands (seconds)
    DEFAULT_TIMEOUT = 30.0

    def __init__(self, classifier: IntentClassifier | None = None) -> None:
        """Initialize the engine.

        Args:
            classifier: Intent classifier. Uses shared instance if not provided.
        """
        self._classifier = classifier

    @property
    def classifier(self) -> IntentClassifier:
        """Get the intent classifier (lazy initialization)."""
        if self._classifier is None:
            self._classifier = get_classifier()
        return self._classifier

    def process(
        self,
        user_input: str,
        session: Session,
        *,
        stream_output: bool = True,
    ) -> EngineResult:
        """Process user input and return result with updated session.

        This is the main entry point. All input flows through here,
        whether from REPL or one-shot mode.

        Args:
            user_input: Raw input string from user.
            session: Current session state.
            stream_output: Whether to stream command output (for REPL).

        Returns:
            EngineResult with output, updated session, and action signal.
        """
        # Normalize input
        stripped = user_input.strip()

        # Empty input - just continue
        if not stripped:
            return EngineResult(output="", session=session)

        # Classify the input
        intent_result = self.classifier.classify(stripped, session)
        logger.debug(
            f"Classified '{stripped[:50]}...' as {intent_result.intent.value} "
            f"(confidence={intent_result.confidence:.2f})"
        )

        # Check if clarification is needed
        if intent_result.requires_clarification:
            return self._handle_clarification(stripped, intent_result, session)

        # Route based on intent
        return self._route_intent(stripped, intent_result, session, stream_output)

    def _route_intent(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
        stream_output: bool,
    ) -> EngineResult:
        """Route to appropriate handler based on classified intent.

        Policy evaluation happens here before routing to handlers.
        This ensures all operations are checked against the policy.

        Args:
            user_input: The user's input text.
            intent_result: Classification result.
            session: Current session state.
            stream_output: Whether to stream output.

        Returns:
            EngineResult from the appropriate handler.
        """
        # === POLICY EVALUATION ===
        # Skip policy check for meta commands (help, exit, etc.)
        if intent_result.intent != Intent.META:
            policy_result = self._evaluate_policy(user_input, intent_result, session)
            if policy_result is not None:
                # Policy blocked or requires interaction
                if not policy_result.should_proceed:
                    return EngineResult(
                        output=self._format_policy_blocked(policy_result.message),
                        session=session,
                        success=False,
                    )

                # Handle confirmation requirement
                if policy_result.requires_confirmation:
                    if not self._get_policy_confirmation(policy_result.message):
                        return EngineResult(
                            output=f"{Colors.DIM}Cancelled.{Colors.RESET}",
                            session=session,
                            success=False,
                        )

                # Handle justification requirement
                if policy_result.requires_justification:
                    justification = self._get_policy_justification(policy_result.justification_prompt)
                    if justification is None:
                        return EngineResult(
                            output=f"{Colors.DIM}Cancelled.{Colors.RESET}",
                            session=session,
                            success=False,
                        )
                    # Record the justification
                    self._record_policy_justification(policy_result, justification)

                # Handle preview requirement (for system tasks)
                if policy_result.requires_preview:
                    # Preview is handled by the task handler itself
                    logger.debug("Policy requires preview - will be handled by task handler")

        # Route based on intent
        match intent_result.intent:
            case Intent.META:
                return self._handle_meta(user_input.lower(), session)
            case Intent.NAVIGATION:
                return self._handle_navigation(user_input, session)
            case Intent.SHELL_PASSTHROUGH:
                # Check if this is a /trace command
                if user_input.startswith("/trace "):
                    command = user_input[7:]  # Remove "/trace " prefix
                    return self._handle_traced_command(command, session, stream_output)
                # Extract command from prefix if present
                command = self._extract_shell_command(user_input)
                return self._handle_shell_command(command, session, stream_output)
            case Intent.SYSTEM_QUESTION:
                return self._handle_system_question(user_input, intent_result, session)
            case Intent.SYSTEM_TASK:
                return self._handle_system_task(user_input, intent_result, session, stream_output)
            case Intent.FIXIT:
                return self._handle_fix(session, interactive=stream_output)
            case Intent.EXPLAIN_COMMAND:
                return self._handle_explain_command(user_input, session)
            case Intent.LEARN_PACKAGE:
                return self._handle_learn_package(user_input, intent_result, session)
            case _:
                # Fallback to shell passthrough
                return self._handle_shell_command(user_input, session, stream_output)

    def _evaluate_policy(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
    ):
        """Evaluate the current operation against the policy engine.

        Args:
            user_input: The user's input.
            intent_result: Classification result.
            session: Current session state.

        Returns:
            PolicyEvaluationResult or None if policy module unavailable.
        """
        policy = _get_policy_module()
        if policy is None:
            return None

        try:
            # Build the evaluation request based on intent
            operation_type = self._intent_to_operation_type(intent_result.intent)
            command = None
            path = None

            if intent_result.intent == Intent.SHELL_PASSTHROUGH:
                command = self._extract_shell_command(user_input)
            elif intent_result.intent == Intent.SYSTEM_TASK:
                # For tasks, we use the raw input
                command = user_input

            request = policy.PolicyEvaluationRequest(
                operation_type=operation_type,
                command=command,
                path=path,
                intent=intent_result.intent.value,
            )

            return policy.evaluate(request)

        except Exception as e:
            logger.warning(f"Policy evaluation failed: {e}")
            return None

    def _intent_to_operation_type(self, intent: Intent) -> str:
        """Map Intent to OperationType for policy evaluation.

        Args:
            intent: The classified intent.

        Returns:
            Operation type string.
        """
        mapping = {
            Intent.SHELL_PASSTHROUGH: "command",
            Intent.SYSTEM_TASK: "task",
            Intent.SYSTEM_QUESTION: "question",
            Intent.FIXIT: "command",
            Intent.NAVIGATION: "command",
            Intent.META: "command",
        }
        return mapping.get(intent, "command")

    def _format_policy_blocked(self, message: str | None) -> str:
        """Format a policy blocked message.

        Args:
            message: Optional policy message.

        Returns:
            Formatted message string.
        """
        if message:
            return f"{Colors.BOLD_RED}Policy blocked:{Colors.RESET} {message}"
        return f"{Colors.BOLD_RED}Operation blocked by policy.{Colors.RESET}"

    def _get_policy_confirmation(self, message: str | None) -> bool:
        """Prompt user for policy confirmation.

        Args:
            message: Optional message to show.

        Returns:
            True if user confirmed, False otherwise.
        """
        prompt = message or "This operation requires confirmation."
        print(f"\n{Colors.YELLOW}{prompt}{Colors.RESET}")
        try:
            response = input(f"{Colors.BOLD}Continue? [y/N]: {Colors.RESET}").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def _get_policy_justification(self, prompt: str | None) -> str | None:
        """Prompt user for policy justification.

        Args:
            prompt: Optional prompt to show.

        Returns:
            Justification text or None if cancelled.
        """
        prompt_text = prompt or "Please provide justification for this operation:"
        print(f"\n{Colors.YELLOW}{prompt_text}{Colors.RESET}")
        try:
            justification = input(f"{Colors.BOLD}Justification: {Colors.RESET}").strip()
            if not justification:
                return None
            return justification
        except (EOFError, KeyboardInterrupt):
            return None

    def _record_policy_justification(self, result, justification: str) -> None:
        """Record a policy justification.

        Args:
            result: Policy evaluation result.
            justification: User's justification.
        """
        policy = _get_policy_module()
        if policy:
            try:
                policy.get_policy_engine().record_justification(result, justification)
            except Exception as e:
                logger.warning(f"Failed to record justification: {e}")

    def _extract_shell_command(self, user_input: str) -> str:
        """Extract shell command from input, handling prefixes.

        Args:
            user_input: Raw input that may have a prefix.

        Returns:
            The actual command to execute.
        """
        # Handle prefix commands
        if user_input.startswith("/sh "):
            return user_input[4:]
        if user_input.startswith("/run "):
            return user_input[5:]
        if user_input.startswith("!"):
            return user_input[1:]
        return user_input

    def _handle_clarification(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
    ) -> EngineResult:
        """Handle inputs that require clarification.

        Args:
            user_input: The ambiguous input.
            intent_result: Classification with low confidence.
            session: Current session state.

        Returns:
            EngineResult prompting for clarification.
        """
        lines = [
            f"{Colors.YELLOW}I'm not sure how to interpret that.{Colors.RESET}",
            f"{Colors.DIM}({intent_result.rationale}){Colors.RESET}",
            "",
        ]

        if intent_result.suggested_followups:
            lines.append("Did you mean to:")
            for suggestion in intent_result.suggested_followups:
                lines.append(f"  - {suggestion}")
            lines.append("")
            lines.append(
                f"{Colors.DIM}Tip: Use prefixes for explicit intent: "
                f"/sh (shell), /ask (question), /do (task){Colors.RESET}"
            )

        return EngineResult(
            output="\n".join(lines),
            session=session,
            success=False,
        )

    def _handle_navigation(self, user_input: str, session: Session) -> EngineResult:
        """Handle navigation commands.

        Args:
            user_input: The navigation command.
            session: Current session state.

        Returns:
            EngineResult for the navigation action.
        """
        lower = user_input.lower().strip()

        if lower == "status":
            return self._handle_status(session)
        elif lower == "history":
            return self._handle_history(session)
        elif lower == "logs":
            return EngineResult(
                output=(
                    f"{Colors.DIM}Use 'journalctl' to view system logs "
                    f"or check /var/log/ for application logs{Colors.RESET}"
                ),
                session=session,
            )
        elif lower == "events":
            return self._handle_events(session)
        elif lower == "man" or lower.startswith("man "):
            return self._handle_man_command(user_input, session)
        elif lower == "search" or lower.startswith("search "):
            # Direct to man vault search
            query = user_input[6:].strip() if lower.startswith("search ") else ""
            if not query:
                return EngineResult(
                    output=f"{Colors.DIM}Usage: search <query> - searches man pages{Colors.RESET}",
                    session=session,
                )
            return self._handle_man_command(f"man -k {query}", session)
        elif lower.startswith("/preflight") or lower == "preflight":
            return self._handle_preflight_command(user_input, session)
        elif lower.startswith("/map"):
            return self._handle_map_command(user_input, session)
        elif lower.startswith("/mobile"):
            return self._handle_mobile_command(user_input, session)
        elif lower.startswith("/react"):
            return self._handle_reactive_command(user_input, session)
        elif self._is_incident_command(lower):
            return self._handle_incidents(user_input, session)
        elif self._is_reboot_command(lower):
            return self._handle_reboot(user_input, session)
        else:
            return EngineResult(
                output=f"Unknown navigation command: {user_input}",
                session=session,
                success=False,
            )

    def _is_reboot_command(self, text: str) -> bool:
        """Check if text is a reboot-related command."""
        import re

        reboot_patterns = [
            r"^reboot(\s|$)",
            r"^reboot\s+(status|history|cancel|confirm|rollback)",
            r"^reboot\s+[a-f0-9-]{8,36}",  # Intent ID
        ]
        return any(re.match(p, text, re.IGNORECASE) for p in reboot_patterns)

    def _handle_reboot(self, user_input: str, session: Session) -> EngineResult:
        """Handle reboot navigation commands.

        Args:
            user_input: The reboot command.
            session: Current session state.

        Returns:
            EngineResult for the reboot action.
        """
        try:
            from elle.cli.reboot.commands import handle_reboot_command

            output, success = handle_reboot_command(user_input, session)
            return EngineResult(
                output=output,
                session=session,
                success=success,
            )
        except Exception as e:
            logger.exception("Failed to handle reboot command")
            return EngineResult(
                output=f"{Colors.RED}Error handling reboot command: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_map_command(self, user_input: str, session: Session) -> EngineResult:
        """Handle /map command for GUI tree traces.

        Args:
            user_input: The map command.
            session: Current session state.

        Returns:
            EngineResult for the map action.
        """
        import asyncio

        try:
            from elle.cli.map_commands import handle_map_command

            # Extract args (remove /map prefix)
            args = user_input.strip()
            if args.startswith("/map"):
                args = args[4:].strip()

            # Run async handler
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            output = loop.run_until_complete(handle_map_command(args))
            return EngineResult(
                output=output,
                session=session,
                success=True,
            )

        except Exception as e:
            logger.exception("Failed to handle map command")
            return EngineResult(
                output=f"{Colors.RED}Error handling map command: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_mobile_command(self, user_input: str, session: Session) -> EngineResult:
        """Handle /mobile command for mobile gateway management.

        Args:
            user_input: The mobile command.
            session: Current session state.

        Returns:
            EngineResult for the mobile action.
        """
        try:
            from elle.cli.mobile_commands import handle_mobile_command

            output, success = handle_mobile_command(user_input, session)
            return EngineResult(
                output=output,
                session=session,
                success=success,
            )

        except Exception as e:
            logger.exception("Failed to handle mobile command")
            return EngineResult(
                output=f"{Colors.RED}Error handling mobile command: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_reactive_command(self, user_input: str, session: Session) -> EngineResult:
        """Handle /react command for reactive functions.

        Args:
            user_input: The reactive command.
            session: Current session state.

        Returns:
            EngineResult for the reactive action.
        """
        try:
            from elle.cli.reactive_commands import handle_reactive_command

            output, success = handle_reactive_command(user_input, session)
            return EngineResult(
                output=output,
                session=session,
                success=success,
            )

        except Exception as e:
            logger.exception("Failed to handle reactive command")
            return EngineResult(
                output=f"{Colors.RED}Error handling reactive command: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_learn_package(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
    ) -> EngineResult:
        """Handle package learning command.

        Gathers intelligence from multiple sources and generates capabilities.

        Args:
            user_input: The learn command or natural language request.
            intent_result: Classification result with extracted entities.
            session: Current session state.

        Returns:
            EngineResult with learning results.
        """
        import asyncio

        try:
            from elle.cli.package_learn_commands import handle_learn_command

            # Extract package name from input
            args = user_input.strip()

            # Handle /learn prefix
            if args.startswith("/learn"):
                args = args[6:].strip()
            # Handle natural language - extract package from entities
            elif intent_result.entities:
                args = intent_result.entities[0]
            else:
                # Try to extract from natural language patterns
                import re

                patterns = [
                    r"(?:learn|figure out|understand)\s+(?:how to (?:use|work with)\s+)?(\w[\w\-\.]+)",
                    r"(?:what can|how do I use)\s+(\w[\w\-\.]+)",
                    r"teach me (?:about\s+)?(\w[\w\-\.]+)",
                ]
                for pattern in patterns:
                    match = re.search(pattern, args, re.IGNORECASE)
                    if match:
                        args = match.group(1)
                        break

            # Run async handler
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            output = loop.run_until_complete(handle_learn_command(args, session))
            return EngineResult(
                output=output,
                session=session,
                success=True,
            )

        except Exception as e:
            logger.exception("Failed to handle learn package command")
            return EngineResult(
                output=f"{Colors.RED}Error learning package: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_preflight_command(self, user_input: str, session: Session) -> EngineResult:
        """Handle preflight validation commands.

        Supports:
        - /preflight <packages>: Validate package installation
        - /preflight --tier=N <packages>: Force specific tier
        - /preflight --upgrade <packages>: Validate upgrade
        - /preflight --remove <packages>: Validate removal

        Args:
            user_input: The preflight command.
            session: Current session state.

        Returns:
            EngineResult with validation results.
        """
        import re

        try:
            from elle.ops.preflight import (
                format_result_for_display,
                validate_packages,
            )
            from elle.ops.preflight.risk_classifier import (
                classify_risk,
                get_risk_summary,
            )
        except ImportError as e:
            return EngineResult(
                output=f"{Colors.RED}Preflight module not available: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

        # Parse the command
        # Remove /preflight prefix
        args = user_input.strip()
        if args.startswith("/preflight"):
            args = args[10:].strip()
        elif args.startswith("preflight"):
            args = args[9:].strip()

        # Show help if no arguments
        if not args or args in ("help", "--help", "-h"):
            return self._preflight_help(session)

        # Parse options
        tier: int | None = None
        operation = "install"

        # Check for --tier=N
        tier_match = re.search(r"--tier[=\s](\d+)", args)
        if tier_match:
            tier = int(tier_match.group(1))
            if tier not in (1, 2, 3):
                return EngineResult(
                    output=f"{Colors.RED}Invalid tier: {tier}. Valid tiers are 1, 2, or 3.{Colors.RESET}",
                    session=session,
                    success=False,
                )
            args = re.sub(r"--tier[=\s]\d+\s*", "", args)

        # Check for --upgrade
        if "--upgrade" in args:
            operation = "upgrade"
            args = args.replace("--upgrade", "").strip()

        # Check for --remove
        if "--remove" in args:
            operation = "remove"
            args = args.replace("--remove", "").strip()

        # Check for --risk (show risk assessment only)
        show_risk_only = "--risk" in args
        if show_risk_only:
            args = args.replace("--risk", "").strip()

        # Parse packages
        packages = tuple(p.strip() for p in args.split() if p.strip())

        if not packages:
            return EngineResult(
                output=f"{Colors.YELLOW}No packages specified.{Colors.RESET}\n\n"
                f"Usage: /preflight <package> [package2...]\n"
                f"       /preflight --upgrade <package>\n"
                f"       /preflight --remove <package>\n"
                f"       /preflight --tier=2 <package>",
                session=session,
                success=False,
            )

        # If --risk flag, just show risk assessment
        if show_risk_only:
            assessment = classify_risk(packages, operation)
            summary = get_risk_summary(assessment)
            return EngineResult(
                output=f"\n{Colors.BOLD}Risk Assessment{Colors.RESET}\n{'━' * 40}\n{summary}",
                session=session,
                success=True,
            )

        # Run validation
        try:
            result = validate_packages(packages, operation, force_tier=tier)
            output = format_result_for_display(result, use_colors=True)
            return EngineResult(
                output=output,
                session=session,
                success=result.can_proceed,
            )
        except Exception as e:
            logger.exception("Preflight validation failed")
            return EngineResult(
                output=f"{Colors.RED}Validation failed: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _preflight_help(self, session: Session) -> EngineResult:
        """Show preflight command help.

        Args:
            session: Current session state.

        Returns:
            EngineResult with help text.
        """
        help_text = f"""{Colors.BOLD}Pre-flight Package Validation{Colors.RESET}

Validates package operations before execution to detect potential issues.

{Colors.BOLD}Usage:{Colors.RESET}
  /preflight <package> [package2...]     Validate package installation
  /preflight --upgrade <package>         Validate package upgrade
  /preflight --remove <package>          Validate package removal
  /preflight --tier=N <package>          Force specific validation tier
  /preflight --risk <package>            Show risk assessment only

{Colors.BOLD}Validation Tiers:{Colors.RESET}
  Tier 1: apt --dry-run simulation (fast, ~1s)
  Tier 2: systemd-nspawn container test (thorough, ~30s)
  Tier 3: LXD snapshot validation (full, not yet implemented)

{Colors.BOLD}Examples:{Colors.RESET}
  /preflight nginx                       Validate nginx installation
  /preflight --upgrade postgresql        Validate PostgreSQL upgrade
  /preflight --tier=2 openssh-server     Force container validation
  /preflight --risk systemd              Show risk level only

{Colors.BOLD}Risk Levels:{Colors.RESET}
  NONE/LOW:    Safe packages, minimal validation
  MEDIUM:      Standard packages, Tier 1 validation
  HIGH:        Service packages, Tier 2 recommended
  CRITICAL:    System packages, Tier 3 recommended

{Colors.DIM}Tip: High-risk packages are automatically validated at Tier 2.{Colors.RESET}"""

        return EngineResult(output=help_text, session=session)

    def _is_incident_command(self, text: str) -> bool:
        """Check if text is an incident-related command."""
        import re

        incident_patterns = [
            r"^incidents?(\s|$)",
            r"^show\s+(me\s+)?(recent\s+)?incidents?",
            r"^list\s+(recent\s+)?incidents?",
            r"^(recent\s+)?incidents?$",
            r"^what\s+incidents?\s+(do\s+we\s+)?have",
            r"^any\s+(recent\s+)?incidents?",
            r"^incident\s+(history|log|reports?)",
            r"^export\s+(my\s+)?incident",
            r"^search\s+incidents?",
            r"^find\s+incidents?",
        ]
        return any(re.match(p, text, re.IGNORECASE) for p in incident_patterns)

    def _handle_incidents(self, user_input: str, session: Session) -> EngineResult:
        """Handle incident navigation commands.

        Supports:
        - incidents / incidents list - List recent incidents
        - incident <id> - Show incident details
        - incident <id> --markdown - Export as markdown
        - incidents search <term> - Search incidents
        """
        import re

        lower = user_input.lower().strip()

        # Parse the command
        # incident <id>
        id_match = re.match(r"incident\s+([a-f0-9-]+)", lower)
        if id_match:
            incident_id = id_match.group(1)
            export_md = "--markdown" in lower or "-m" in lower
            return self._show_incident_detail(incident_id, session, export_markdown=export_md)

        # incidents search <term>
        search_match = re.match(r"(incidents?\s+)?search\s+(.+)", lower)
        if search_match:
            query = search_match.group(2).strip()
            return self._search_incidents(query, session)

        # export incidents / export my incident reports
        if "export" in lower and "incident" in lower:
            return self._export_all_incidents(session)

        # Default: list incidents
        return self._list_incidents(session)

    def _list_incidents(
        self,
        session: Session,
        limit: int = 20,
        status: str | None = None,
    ) -> EngineResult:
        """List recent incidents."""
        try:
            from elle.daemon.incidents.store import list_incidents

            incidents = list_incidents(status=status, limit=limit)

            if not incidents:
                return EngineResult(
                    output=f"\n  {Colors.DIM}No incidents recorded yet.{Colors.RESET}\n\n"
                    f"  Incidents are created when ELLE helps diagnose or fix system issues.\n"
                    f"  Try running a task or fixing a failed command to generate incidents.\n",
                    session=session,
                )

            output = render_incident_list(incidents, title="Recent Incidents")
            return EngineResult(output=output, session=session)

        except Exception as e:
            logger.exception("Failed to list incidents")
            return EngineResult(
                output=f"{Colors.RED}Error listing incidents: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _show_incident_detail(
        self,
        incident_id: str,
        session: Session,
        export_markdown: bool = False,
    ) -> EngineResult:
        """Show detailed view of a single incident."""
        try:
            from elle.daemon.incidents.store import (
                get_actions,
                get_incident,
                get_snapshots,
            )

            # Support partial ID matching
            incident = get_incident(incident_id)

            # Try partial match if exact match fails
            if incident is None:
                from elle.daemon.incidents.store import list_incidents

                all_incidents = list_incidents(limit=1000)
                matches = [i for i in all_incidents if i.incident_id.startswith(incident_id)]
                if len(matches) == 1:
                    incident = matches[0]
                elif len(matches) > 1:
                    return EngineResult(
                        output=f"{Colors.YELLOW}Multiple incidents match '{incident_id}':{Colors.RESET}\n"
                        + "\n".join(f"  - {i.incident_id[:12]} {i.title}" for i in matches[:5]),
                        session=session,
                        success=False,
                    )

            if incident is None:
                return EngineResult(
                    output=f"{Colors.RED}Incident not found: {incident_id}{Colors.RESET}",
                    session=session,
                    success=False,
                )

            # Get related data
            actions = get_actions(incident.incident_id)
            snapshots = get_snapshots(incident.incident_id)

            if export_markdown:
                output = render_incident_markdown(incident, actions, snapshots)
                # Also save to file
                filename = f"incident_{incident.incident_id[:8]}.md"
                try:
                    with open(filename, "w") as f:
                        f.write(output)
                    return EngineResult(
                        output=f"{Colors.GREEN}Exported to {filename}{Colors.RESET}\n\n{output}",
                        session=session,
                    )
                except OSError:
                    return EngineResult(
                        output=output,
                        session=session,
                    )
            else:
                output = render_incident_detail(incident, actions, snapshots)
                return EngineResult(output=output, session=session)

        except Exception as e:
            logger.exception("Failed to show incident detail")
            return EngineResult(
                output=f"{Colors.RED}Error loading incident: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _search_incidents(self, query: str, session: Session) -> EngineResult:
        """Search incidents using FTS."""
        try:
            from elle.daemon.incidents.retriever import search

            results = search(query=query, k=10, search_type="lexical")

            if not results:
                return EngineResult(
                    output=f"\n  {Colors.DIM}No incidents found matching: {query}{Colors.RESET}\n",
                    session=session,
                )

            # Convert to (incident, score) tuples for renderer
            result_tuples = [(r.incident, r.score) for r in results]
            output = render_search_results(query, result_tuples)

            return EngineResult(output=output, session=session)

        except Exception as e:
            logger.exception("Failed to search incidents")
            return EngineResult(
                output=f"{Colors.RED}Error searching incidents: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _export_all_incidents(self, session: Session, limit: int = 50) -> EngineResult:
        """Export recent incidents to markdown files."""
        try:
            from elle.daemon.incidents.store import (
                get_actions,
                get_snapshots,
                list_incidents,
            )

            incidents = list_incidents(limit=limit)

            if not incidents:
                return EngineResult(
                    output=f"\n  {Colors.DIM}No incidents to export.{Colors.RESET}\n",
                    session=session,
                )

            exported = []
            for inc in incidents:
                actions = get_actions(inc.incident_id)
                snapshots = get_snapshots(inc.incident_id)
                md = render_incident_markdown(inc, actions, snapshots)

                filename = f"incident_{inc.incident_id[:8]}.md"
                try:
                    with open(filename, "w") as f:
                        f.write(md)
                    exported.append(filename)
                except OSError as e:
                    logger.warning(f"Failed to write {filename}: {e}")

            return EngineResult(
                output=f"\n  {Colors.GREEN}Exported {len(exported)} incident(s):{Colors.RESET}\n"
                + "\n".join(f"    - {f}" for f in exported)
                + "\n",
                session=session,
            )

        except Exception as e:
            logger.exception("Failed to export incidents")
            return EngineResult(
                output=f"{Colors.RED}Error exporting incidents: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_system_question(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
    ) -> EngineResult:
        """Handle system questions (LLM-powered).

        Uses the LLM to answer questions about the system, Linux administration,
        and general technical topics.

        Args:
            user_input: The question.
            intent_result: Classification result with entities.
            session: Current session state.

        Returns:
            EngineResult with LLM-generated answer.
        """
        # Extract the actual question (remove /ask prefix if present)
        question = user_input
        if question.startswith("/ask "):
            question = question[5:]

        # Try to use LLM to answer the question
        try:
            from elle.rag.llm import get_llm

            llm = get_llm()
            if not llm.is_available():
                return EngineResult(
                    output=(
                        f"{Colors.BOLD}Question:{Colors.RESET} {question}\n\n"
                        f"{Colors.YELLOW}LLM not available. "
                        f"Ensure Ollama is running with a suitable model.{Colors.RESET}"
                    ),
                    session=session,
                    success=False,
                )

            # Build system prompt for answering questions
            system_prompt = (
                "You are ELLE, an AI assistant for Linux system administration. "
                "Answer questions clearly and concisely about system administration, "
                "Linux commands, troubleshooting, and general technical topics. "
                "When relevant, provide example commands. "
                "If you don't know something, say so rather than guessing."
            )

            # Generate response
            response = llm.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ]
            )

            if response:
                lines = [
                    f"{Colors.BOLD}Question:{Colors.RESET} {question}",
                    "",
                    response,
                ]
                return EngineResult(
                    output="\n".join(lines),
                    session=session,
                    success=True,
                )
            else:
                return EngineResult(
                    output=(
                        f"{Colors.BOLD}Question:{Colors.RESET} {question}\n\n"
                        f"{Colors.YELLOW}No response from LLM.{Colors.RESET}"
                    ),
                    session=session,
                    success=False,
                )

        except Exception as e:
            logger.warning(f"Failed to generate response: {e}")
            return EngineResult(
                output=(
                    f"{Colors.BOLD}Question:{Colors.RESET} {question}\n\n"
                    f"{Colors.YELLOW}Failed to generate response: {e}{Colors.RESET}"
                ),
                session=session,
                success=False,
            )

    def _handle_system_task(
        self,
        user_input: str,
        intent_result: IntentResult,
        session: Session,
        interactive: bool = True,
    ) -> EngineResult:
        """Handle system task requests (planner-powered).

        Generates a verified command plan for the task request,
        presents it for approval, and executes with rollback support.

        Args:
            user_input: The task request.
            intent_result: Classification result with entities.
            session: Current session state.
            interactive: Whether to use interactive UI.

        Returns:
            EngineResult with plan execution results.
        """
        # Extract the actual task (remove /do prefix if present)
        task = user_input
        if task.startswith("/do "):
            task = task[4:]

        # Import planner
        from elle.cli.planner import (
            PlanOutcome,
            get_planner_service,
            render_plan_result,
            run_interactive_planner,
        )

        # Run planning pipeline
        service = get_planner_service()
        entities = tuple(intent_result.entities) if intent_result.entities else ()
        result = service.run_planning_pipeline(task, session, entities)

        # Check for planning failure
        if result.plan is None:
            return EngineResult(
                output=(
                    f"{Colors.RED}Failed to generate plan for:{Colors.RESET} {task}\n\n"
                    f"{Colors.DIM}The LLM may be unavailable. "
                    f"Try 'man <topic>' to search documentation manually.{Colors.RESET}"
                ),
                session=session,
                success=False,
            )

        # Check for verification failure
        if result.verification and not result.verification.is_valid:
            lines = [
                f"{Colors.BOLD}Plan:{Colors.RESET} {result.plan.title}",
                "",
                f"{Colors.RED}Plan failed verification:{Colors.RESET}",
            ]
            for error in result.verification.errors:
                lines.append(f"  {Colors.RED}!{Colors.RESET} {error}")

            return EngineResult(
                output="\n".join(lines),
                session=session,
                success=False,
            )

        # Interactive or non-interactive execution
        if interactive:
            try:
                final_result = run_interactive_planner(result, session)
                success = final_result.outcome in (
                    PlanOutcome.SUCCESS,
                    PlanOutcome.PARTIAL,
                )
                return EngineResult(
                    output="",  # Interactive mode handles output
                    session=session,
                    success=success,
                )
            except Exception as e:
                logger.warning(f"Interactive mode failed: {e}")
                # Fall through to text rendering

        # Non-interactive: just show the plan
        output = render_plan_result(result)
        return EngineResult(
            output=output,
            session=session,
            success=result.plan is not None,
        )

    def _handle_meta(self, command: str, session: Session) -> EngineResult:
        """Handle meta commands (help, exit, etc.).

        Args:
            command: The meta command (lowercase).
            session: Current session state.

        Returns:
            EngineResult for the meta command.
        """
        match command:
            case "exit" | "quit":
                return EngineResult(
                    output="Goodbye.",
                    session=session,
                    action=EngineAction.EXIT,
                )
            case "help":
                return EngineResult(
                    output=self._help_text(),
                    session=session,
                )
            case "clear":
                return EngineResult(
                    output="",
                    session=session,
                    action=EngineAction.CLEAR,
                )
            case "config":
                return self._handle_config(session)
            case "about":
                return EngineResult(
                    output=self._about_text(),
                    session=session,
                )
            case "version":
                return EngineResult(
                    output="ELLE v0.1.0-dev",
                    session=session,
                )
            case "sponsor":
                return EngineResult(
                    output=self._sponsor_text(),
                    session=session,
                )
            case _:
                return EngineResult(
                    output=f"Unknown meta command: {command}",
                    session=session,
                    success=False,
                )

    def _handle_traced_command(
        self,
        command: str,
        session: Session,
        stream: bool,
    ) -> EngineResult:
        """Execute a shell command with syscall tracing.

        Args:
            command: The shell command to execute.
            session: Current session state.
            stream: Whether to stream output.

        Returns:
            EngineResult with command output, trace, and updated session.
        """
        import subprocess

        # Try to initialize syscall tracing
        try:
            from elle.daemon.telemetry.ebpf.syscall_explainer import (
                format_trace_for_display,
            )
            from elle.daemon.telemetry.ebpf.syscall_manager import (
                get_syscall_manager,
                is_syscall_tracing_available,
            )

            if not is_syscall_tracing_available():
                # Fall back to regular execution with a note
                logger.info("Syscall tracing not available, running without trace")
                result = self._handle_shell_command(command, session, stream)
                output = result.output
                if output:
                    output += "\n"
                output += f"{Colors.DIM}(Syscall tracing not available - requires root/eBPF){Colors.RESET}"
                return EngineResult(
                    output=output,
                    session=result.session,
                    action=result.action,
                    success=result.success,
                )

            manager = get_syscall_manager()

        except ImportError:
            # BCC not available, fall back to regular execution
            result = self._handle_shell_command(command, session, stream)
            output = result.output
            if output:
                output += "\n"
            output += f"{Colors.DIM}(Syscall tracing not available){Colors.RESET}"
            return EngineResult(
                output=output,
                session=result.session,
                action=result.action,
                success=result.success,
            )

        # Start the process
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=session.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Start tracing the process
            if not manager.start_trace(command, process.pid, session.cwd):
                # Tracing failed, but still run the command
                stdout, stderr = process.communicate(timeout=self.DEFAULT_TIMEOUT)
                new_session = session.with_command_result(
                    cmd=command,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=process.returncode,
                )
                output = self._format_command_output(stdout, stderr, process.returncode)
                output += f"\n{Colors.YELLOW}(Syscall tracing failed to start){Colors.RESET}"
                return EngineResult(
                    output=output,
                    session=new_session,
                    success=process.returncode == 0,
                )

            # Wait for process to complete
            try:
                stdout, stderr = process.communicate(timeout=self.DEFAULT_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                trace = manager.stop_trace()
                new_session = session.with_command_result(
                    cmd=command,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=-1,
                    syscall_trace=trace,
                )
                return EngineResult(
                    output=self._format_timeout(command, self.DEFAULT_TIMEOUT),
                    session=new_session,
                    success=False,
                )

            # Stop tracing and get results
            trace = manager.stop_trace()

            # Update session with trace
            new_session = session.with_command_result(
                cmd=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=process.returncode,
                syscall_trace=trace,
            )

            # Format output with trace summary
            output_parts = []

            # Regular command output
            if stdout:
                output_parts.append(stdout.rstrip())
            if stderr:
                output_parts.append(f"{Colors.RED}{stderr.rstrip()}{Colors.RESET}")

            # Trace summary
            if trace.has_trace:
                output_parts.append("")
                output_parts.append(f"{Colors.CYAN}━━━ Syscall Trace ━━━{Colors.RESET}")
                output_parts.append(format_trace_for_display(trace, use_colors=True))
            elif trace.error:
                output_parts.append("")
                output_parts.append(f"{Colors.YELLOW}Trace: {trace.error}{Colors.RESET}")

            # Exit code if failed
            if process.returncode != 0:
                output_parts.append("")
                output_parts.append(
                    f"{Colors.DIM}Command failed (exit {process.returncode}). "
                    f"Type 'fix' for help, 'explain' for syscall details.{Colors.RESET}"
                )

            return EngineResult(
                output="\n".join(output_parts),
                session=new_session,
                success=process.returncode == 0,
            )

        except Exception as e:
            logger.error(f"Error executing traced command: {e}")
            # Try to stop any active trace
            with contextlib.suppress(Exception):
                manager.stop_trace()
            return EngineResult(
                output=f"{Colors.RED}Error executing command: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_explain_command(
        self,
        user_input: str,
        session: Session,
    ) -> EngineResult:
        """Handle the explain command intent.

        Shows the syscall trace explanation for the last traced command.

        Args:
            user_input: The user's input.
            session: Current session state.

        Returns:
            EngineResult with trace explanation.
        """
        # Check if we have a trace
        if not session.last_cmd:
            return EngineResult(
                output="No previous command to explain.",
                session=session,
                success=False,
            )

        if not session.has_trace:
            return EngineResult(
                output=(
                    f"No syscall trace available for: {session.last_cmd}\n\n"
                    f"{Colors.DIM}Tip: Use '/trace <command>' to execute with syscall tracing.{Colors.RESET}"
                ),
                session=session,
                success=False,
            )

        # Get the trace explanation
        try:
            from elle.daemon.telemetry.ebpf.syscall_explainer import (
                format_trace_for_display,
            )

            trace = session.last_syscall_trace
            explanation = format_trace_for_display(trace, use_colors=True)

            return EngineResult(
                output=explanation,
                session=session,
                success=True,
            )

        except ImportError:
            return EngineResult(
                output=f"{Colors.RED}Syscall tracing module not available.{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_shell_command(
        self,
        command: str,
        session: Session,
        stream: bool,
    ) -> EngineResult:
        """Execute a shell command safely.

        Args:
            command: The shell command to execute.
            session: Current session state.
            stream: Whether to stream output.

        Returns:
            EngineResult with command output and updated session.
        """
        # Execute through safe subprocess runner
        mode = RunMode.STREAM if stream else RunMode.CAPTURE
        result = run_safe(
            command,
            cwd=session.cwd,
            timeout=self.DEFAULT_TIMEOUT,
            mode=mode,
        )

        # Update session with results
        new_session = session.with_command_result(
            cmd=command,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
        )

        # Format output - but if streaming, stdout was already printed
        if stream and result.success:
            # Stdout was streamed; only include stderr if present
            output = ""
            if result.stderr:
                output = f"{Colors.RED}{result.stderr.rstrip()}{Colors.RESET}"
        else:
            output = self._format_command_output(result.stdout, result.stderr, result.exit_code)

        # Handle special cases
        if result.denied:
            output = self._format_denied(result.deny_explanation or "Command denied")
            return EngineResult(
                output=output,
                session=new_session,
                success=False,
            )

        if result.timed_out:
            output = self._format_timeout(command, self.DEFAULT_TIMEOUT)
            return EngineResult(
                output=output,
                session=new_session,
                success=False,
            )

        if result.failed:
            # Add hint about fix command
            if output:
                output += "\n"
            output += f"{Colors.DIM}Command failed (exit {result.exit_code}). Type 'fix' for help.{Colors.RESET}"

        return EngineResult(
            output=output,
            session=new_session,
            success=result.success,
        )

    def _handle_fix(
        self,
        session: Session,
        interactive: bool = True,
    ) -> EngineResult:
        """Handle the 'fix' command for failed commands.

        Analyzes the last failed command, searches for documentation
        and prior art, and presents fix suggestions.

        Args:
            session: Current session state.
            interactive: Whether to use interactive UI (REPL mode).

        Returns:
            EngineResult with fix analysis or interactive session result.
        """
        if not session.last_cmd:
            return EngineResult(
                output="No previous command to fix.",
                session=session,
                success=False,
            )

        if session.last_exit == 0:
            return EngineResult(
                output="Last command succeeded. Nothing to fix.",
                session=session,
            )

        # Import fixit service
        from elle.cli.fixit import (
            FixitOutcome,
            get_fixit_service,
            render_fixit_result,
            run_interactive_fixit,
        )

        # Run analysis pipeline
        service = get_fixit_service()
        result = service.run_full_pipeline(session)

        if interactive and result.analysis and result.has_suggestions:
            # REPL mode: Launch interactive navigation
            try:
                final_result = run_interactive_fixit(result, session)
                # Determine success based on outcome
                success = final_result.outcome in (
                    FixitOutcome.IMPROVED,
                    FixitOutcome.PARTIAL,
                )
                return EngineResult(
                    output="",  # Interactive mode handles its own output
                    session=session,
                    success=success,
                )
            except Exception as e:
                logger.warning(f"Interactive mode failed, falling back to text: {e}")
                # Fall through to text rendering

        # One-shot mode or interactive fallback: Render as text
        output = render_fixit_result(result)
        return EngineResult(
            output=output,
            session=session,
            success=result.analysis is not None,
        )

    def _handle_history(self, session: Session) -> EngineResult:
        """Display command history.

        Args:
            session: Current session state.

        Returns:
            EngineResult with formatted history.
        """
        if not session.has_history:
            return EngineResult(
                output="No commands in history.",
                session=session,
            )

        lines = [f"  {i + 1}  {cmd}" for i, cmd in enumerate(session.history)]
        return EngineResult(
            output="\n".join(lines),
            session=session,
        )

    def _handle_status(self, session: Session) -> EngineResult:
        """Display session status.

        Args:
            session: Current session state.

        Returns:
            EngineResult with status information.
        """
        lines = [
            f"cwd: {session.cwd}",
            f"history: {len(session.history)} commands",
        ]
        if session.last_cmd:
            lines.append(f"last command: {session.last_cmd}")
            lines.append(f"last exit code: {session.last_exit}")
            if session.last_failed:
                lines.append(f"{Colors.YELLOW}(failed - type 'fix' for help){Colors.RESET}")
        return EngineResult(
            output="\n".join(lines),
            session=session,
        )

    def _handle_events(self, session: Session) -> EngineResult:
        """Display recent telemetry events.

        Args:
            session: Current session state.

        Returns:
            EngineResult with recent events.
        """
        try:
            from elle.daemon.telemetry.store import list_events

            events = list_events(limit=10)
            if not events:
                return EngineResult(
                    output=f"{Colors.DIM}No recent events{Colors.RESET}",
                    session=session,
                )

            lines = [f"{Colors.BOLD}Recent Events:{Colors.RESET}", ""]
            for evt in events:
                ts = evt.get("ts", "")
                severity = evt.get("severity", "info")
                message = evt.get("message", "")[:80]
                color = {
                    "info": Colors.DIM,
                    "warning": Colors.YELLOW,
                    "error": Colors.RED,
                    "critical": Colors.BOLD_RED,
                }.get(severity, Colors.DIM)
                lines.append(f"  {color}{ts} [{severity}] {message}{Colors.RESET}")

            return EngineResult(
                output="\n".join(lines),
                session=session,
            )
        except ImportError:
            return EngineResult(
                output=f"{Colors.DIM}Telemetry store not available{Colors.RESET}",
                session=session,
            )
        except Exception as e:
            return EngineResult(
                output=f"{Colors.RED}Error loading events: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _handle_config(self, session: Session) -> EngineResult:
        """Display current configuration.

        Args:
            session: Current session state.

        Returns:
            EngineResult with configuration info.
        """
        try:
            from elle.common.config import get_config

            config = get_config()
            lines = [f"{Colors.BOLD}Current Configuration:{Colors.RESET}", ""]

            # Show key settings
            lines.append(f"  LLM model: {config.llm.model}")
            lines.append(f"  SLM model: {config.slm.model}")
            lines.append(f"  Ollama URL: {config.ollama.base_url}")
            lines.append("")
            lines.append(f"{Colors.DIM}Config file: ~/.config/elle/config.toml{Colors.RESET}")

            return EngineResult(
                output="\n".join(lines),
                session=session,
            )
        except ImportError:
            return EngineResult(
                output=f"{Colors.DIM}Configuration not loaded{Colors.RESET}",
                session=session,
            )
        except Exception as e:
            return EngineResult(
                output=f"{Colors.RED}Error loading config: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _format_command_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> str:
        """Format command output for display.

        Args:
            stdout: Command stdout.
            stderr: Command stderr.
            exit_code: Command exit code.

        Returns:
            Formatted output string.
        """
        parts: list[str] = []

        # Add stdout (already streamed, but include for non-streaming mode)
        if stdout:
            parts.append(stdout.rstrip())

        # Add stderr with visual distinction
        if stderr:
            # If we have both, add a separator
            if parts:
                parts.append("")
            parts.append(f"{Colors.RED}{stderr.rstrip()}{Colors.RESET}")

        return "\n".join(parts)

    def _format_denied(self, explanation: str) -> str:
        """Format a denied command message.

        Args:
            explanation: Why the command was denied.

        Returns:
            Formatted denial message.
        """
        return f"{Colors.BOLD_RED}Command blocked{Colors.RESET}\n{explanation}"

    def _format_timeout(self, command: str, timeout: float) -> str:
        """Format a timeout message.

        Args:
            command: The command that timed out.
            timeout: The timeout value in seconds.

        Returns:
            Formatted timeout message.
        """
        return (
            f"{Colors.BOLD_YELLOW}Command timed out{Colors.RESET}\n"
            f"'{command}' did not complete within {timeout:.0f} seconds."
        )

    def _indent(self, text: str, prefix: str = "  ") -> str:
        """Indent text for display.

        Args:
            text: Text to indent.
            prefix: Indentation prefix.

        Returns:
            Indented text.
        """
        lines = text.split("\n")
        return "\n".join(f"{prefix}{line}" for line in lines)

    def _help_text(self) -> str:
        """Generate help text.

        Returns:
            Formatted help string.
        """
        return f"""ELLE Terminal

{Colors.BOLD}Commands:{Colors.RESET}
  help      Show this help message
  about     About ELLE
  version   Show version
  sponsor   Support ELLE development
  status    Show session status
  history   Show command history
  clear     Clear the screen
  exit      Exit ELLE terminal
  fix       Analyze last failed command
  explain   Show what last traced command did

{Colors.BOLD}Prefix Commands:{Colors.RESET}
  /sh <cmd>     Execute shell command explicitly
  /trace <cmd>  Execute with syscall tracing
  /ask <q>      Ask a system question
  /do <task>    Request a system task
  /fix          Fix last failed command
  /explain      Explain last traced command
  /preflight    Validate packages before install
  !<cmd>        Shell command shortcut

{Colors.BOLD}Usage:{Colors.RESET}
  Shell commands are auto-detected and executed directly
  Questions (why, how, what) get context-aware answers
  Task requests are planned and executed safely
  Timeout protection: {self.DEFAULT_TIMEOUT:.0f}s default

{Colors.BOLD}Syscall Tracing:{Colors.RESET}
  Use '/trace <cmd>' to see exactly what a command does
  Then 'explain' or 'what did that do?' to see details

{Colors.BOLD}After a failed command:{Colors.RESET}
  Type 'fix' to see error details and get suggestions

{Colors.DIM}Note: ELLE never runs sudo. Privileged operations use Polkit.{Colors.RESET}"""

    def _about_text(self) -> str:
        """Generate about text.

        Returns:
            Formatted about string.
        """
        return f"""{Colors.BOLD}ELLE - Enabling Layer Learning Everything{Colors.RESET}

A local-first, agentic system layer for Ubuntu 24.04 LTS.

ELLE converts kernel-level telemetry into natural language insight
and safe system operations. All processing happens locally - no
cloud dependencies, no data leaves your machine.

{Colors.BOLD}Features:{Colors.RESET}
  - Natural language system interaction
  - Intent-aware command routing
  - Safe subprocess execution with denylist
  - Context-aware error diagnosis (fixit)
  - Local RAG-powered knowledge base

{Colors.DIM}License: GPL-3.0-or-later
Website: https://araujota.github.io/elle
Repository: https://github.com/araujota/elle

Questions/Feedback: araujota97@gmail.com
Sponsor: https://github.com/sponsors/araujota{Colors.RESET}"""

    def _sponsor_text(self) -> str:
        """Generate sponsor information text.

        Returns:
            Formatted sponsor information string.
        """
        sponsor_url = "https://github.com/sponsors/araujota"

        # Try to open the sponsor page in the default browser
        try:
            import subprocess

            subprocess.Popen(
                ["xdg-open", sponsor_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            opened_browser = True
        except Exception:
            opened_browser = False

        text = f"""{Colors.BOLD}Support ELLE Development{Colors.RESET}

ELLE is open source and free to use. If you find it useful,
consider sponsoring development to help keep the project sustainable.

{Colors.BOLD}Sponsor:{Colors.RESET} {sponsor_url}

{Colors.BOLD}Other ways to help:{Colors.RESET}
  - Report bugs and suggest features on GitHub
  - Contribute code or documentation
  - Share ELLE with others who might find it useful

{Colors.DIM}Questions or feedback? Email: araujota97@gmail.com{Colors.RESET}"""

        if opened_browser:
            text += f"\n\n{Colors.DIM}(Opening sponsor page in browser...){Colors.RESET}"

        return text

    def _handle_man_command(self, user_input: str, session: Session) -> EngineResult:
        """Handle man command and subcommands.

        Supports:
        - man <query>: Search documentation
        - man status: Show index status
        - man reindex: Trigger reindexing

        Args:
            user_input: The man command.
            session: Current session state.

        Returns:
            EngineResult for the man action.
        """
        parts = user_input.split(maxsplit=2)

        # Just "man" - show help
        if len(parts) == 1:
            return self._man_help(session)

        subcommand = parts[1].lower()

        if subcommand == "reindex":
            return self._man_reindex(session)
        elif subcommand == "status":
            return self._man_status(session)
        else:
            # Treat as search query
            query = " ".join(parts[1:])
            return self._man_search(query, session)

    def _man_help(self, session: Session) -> EngineResult:
        """Show man command help.

        Args:
            session: Current session state.

        Returns:
            EngineResult with help text.
        """
        help_text = f"""{Colors.BOLD}Man Vault - Local Documentation Index{Colors.RESET}

{Colors.BOLD}Commands:{Colors.RESET}
  man <query>    Search documentation (hybrid search)
  man status     Show index status
  man reindex    Trigger background reindexing

{Colors.BOLD}Examples:{Colors.RESET}
  man list files           Search for "list files"
  man how to check disk    Semantic search for disk-related docs
  man ls -l                Search for ls command with -l flag

{Colors.DIM}Man Vault indexes ~24,000 man pages from /usr/share/man/
for fast lexical and semantic search.{Colors.RESET}"""

        return EngineResult(output=help_text, session=session)

    def _man_search(self, query: str, session: Session) -> EngineResult:
        """Search the Man Vault.

        Args:
            query: Search query.
            session: Current session state.

        Returns:
            EngineResult with search results.
        """
        try:
            from elle.daemon.manvault import search

            results = search(query, k=5, search_type="hybrid")

            if not results:
                return EngineResult(
                    output=f"No results found for: {query}",
                    session=session,
                )

            lines = [f"{Colors.BOLD}Results for:{Colors.RESET} {query}", ""]

            for i, result in enumerate(results, 1):
                # Header with name and section
                lines.append(f"{Colors.CYAN}{i}. {result.name}({result.section}){Colors.RESET} [{result.search_type}]")

                # Match section if available
                if result.match_section:
                    lines.append(f"   {Colors.DIM}Section: {result.match_section}{Colors.RESET}")

                # Snippet (truncated for display)
                snippet = result.snippet[:300].replace("\n", " ")
                if len(result.snippet) > 300:
                    snippet += "..."
                lines.append(f"   {snippet}")
                lines.append("")

            lines.append(
                f"{Colors.DIM}Tip: Use 'man <name> <section>' in your shell for full documentation{Colors.RESET}"
            )

            return EngineResult(output="\n".join(lines), session=session)

        except Exception as e:
            logger.error(f"Man search failed: {e}")
            return EngineResult(
                output=f"{Colors.RED}Search failed: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _man_status(self, session: Session) -> EngineResult:
        """Show Man Vault status.

        Args:
            session: Current session state.

        Returns:
            EngineResult with status information.
        """
        try:
            from elle.daemon.manvault import get_status

            status = get_status()

            lines = [f"{Colors.BOLD}Man Vault Status{Colors.RESET}", ""]

            # Document counts
            lines.append(f"Documents:  {status.total_docs:,}")
            lines.append(f"Chunks:     {status.total_chunks:,}")
            lines.append(f"Embedded:   {status.embedded_chunks:,}")

            # Last indexed
            if status.indexed_at:
                lines.append(f"Indexed at: {status.indexed_at.strftime('%Y-%m-%d %H:%M')}")
            else:
                lines.append(f"Indexed at: {Colors.YELLOW}Never{Colors.RESET}")

            # Embedding model
            if status.embedding_model:
                lines.append(f"Embedding:  {status.embedding_model}")
            else:
                lines.append(f"Embedding:  {Colors.DIM}Not available{Colors.RESET}")

            # Database size
            size_mb = status.db_size_bytes / (1024 * 1024)
            lines.append(f"DB size:    {size_mb:.1f} MB")

            # Section breakdown
            if status.sections:
                lines.append("")
                lines.append(f"{Colors.BOLD}Sections:{Colors.RESET}")
                for section, count in sorted(status.sections.items()):
                    lines.append(f"  man{section}: {count:,}")

            # Status flags
            if status.is_indexing:
                lines.append("")
                lines.append(f"{Colors.YELLOW}Indexing in progress...{Colors.RESET}")
            if status.is_embedding:
                lines.append(f"{Colors.YELLOW}Embedding in progress...{Colors.RESET}")

            return EngineResult(output="\n".join(lines), session=session)

        except Exception as e:
            logger.error(f"Man status failed: {e}")
            return EngineResult(
                output=f"{Colors.RED}Failed to get status: {e}{Colors.RESET}",
                session=session,
                success=False,
            )

    def _man_reindex(self, session: Session) -> EngineResult:
        """Trigger Man Vault reindexing via daemon API.

        Args:
            session: Current session state.

        Returns:
            EngineResult confirming reindex started.
        """
        import asyncio

        from elle.cli.daemon_client import get_daemon_client

        try:
            client = get_daemon_client()

            # Try daemon API first (preferred)
            async def trigger_via_daemon() -> tuple[bool, str]:
                if await client.is_daemon_available():
                    return await client.trigger_manvault_reindex()
                return False, "Daemon not available"

            success, message = asyncio.get_event_loop().run_until_complete(trigger_via_daemon())

            if success:
                return EngineResult(
                    output=(
                        f"{Colors.GREEN}Reindexing started via daemon...{Colors.RESET}\n"
                        f"{Colors.DIM}Use 'man status' to check progress{Colors.RESET}"
                    ),
                    session=session,
                )

            # Fallback to direct indexing if daemon unavailable
            logger.debug(f"Daemon reindex failed: {message}, falling back to direct")
            from elle.daemon.manvault import get_service

            service = get_service()

            if service.is_indexing:
                return EngineResult(
                    output=f"{Colors.YELLOW}Indexing already in progress{Colors.RESET}",
                    session=session,
                )

            # Run synchronously in background thread (fallback only)
            import threading

            from elle.daemon.manvault.indexer import index_all

            def do_reindex():
                try:
                    count = index_all()
                    logger.info(f"Reindex complete: {count} documents")
                except Exception as e:
                    logger.error(f"Reindex failed: {e}")

            thread = threading.Thread(target=do_reindex, daemon=True)
            thread.start()

            return EngineResult(
                output=(
                    f"{Colors.GREEN}Reindexing started in background...{Colors.RESET}\n"
                    f"{Colors.DIM}Use 'man status' to check progress{Colors.RESET}"
                ),
                session=session,
            )

        except Exception as e:
            logger.error(f"Man reindex failed: {e}")
            return EngineResult(
                output=f"{Colors.RED}Failed to start reindex: {e}{Colors.RESET}",
                session=session,
                success=False,
            )


# Module-level engine instance for convenience
_engine: Engine | None = None


def get_engine() -> Engine:
    """Get the shared engine instance.

    Returns:
        The Engine singleton.
    """
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine
