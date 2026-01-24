"""ELLE Engine - Core command processing.

This is the central processing unit for all ELLE interactions.
Both the REPL and one-shot commands route through this engine.

The engine is stateless - it takes input and session state,
and returns results with updated session state.

Flow:
    input + session -> classify -> policy check -> route -> execute -> result + new_session
"""

import logging
from enum import Enum, auto

from pydantic import BaseModel, ConfigDict

from elle.cli.subprocess_runner import RunMode, run_safe
from elle.cli.terminal.classifier import IntentClassifier, get_classifier
from elle.cli.terminal.incident_renderer import (
    render_incident_list,
    render_incident_detail,
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
                    justification = self._get_policy_justification(
                        policy_result.justification_prompt
                    )
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
                # Extract command from prefix if present
                command = self._extract_shell_command(user_input)
                return self._handle_shell_command(command, session, stream_output)
            case Intent.SYSTEM_QUESTION:
                return self._handle_system_question(user_input, intent_result, session)
            case Intent.SYSTEM_TASK:
                return self._handle_system_task(user_input, intent_result, session, stream_output)
            case Intent.FIXIT:
                return self._handle_fix(session, interactive=stream_output)
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
                output=f"{Colors.DIM}[Log viewer not yet implemented]{Colors.RESET}",
                session=session,
            )
        elif lower == "events":
            return EngineResult(
                output=f"{Colors.DIM}[Event viewer not yet implemented]{Colors.RESET}",
                session=session,
            )
        elif lower == "man" or lower.startswith("man "):
            return self._handle_man_command(user_input, session)
        elif lower == "search" or lower.startswith("search "):
            return EngineResult(
                output=f"{Colors.DIM}[Search not yet implemented]{Colors.RESET}",
                session=session,
            )
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
        parts = user_input.strip().split()

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
                get_incident,
                get_actions,
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
                list_incidents,
                get_actions,
                get_snapshots,
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
        """Handle system questions (RAG-powered).

        Args:
            user_input: The question.
            intent_result: Classification result with entities.
            session: Current session state.

        Returns:
            EngineResult with answer (placeholder for now).
        """
        # Extract the actual question (remove /ask prefix if present)
        question = user_input
        if question.startswith("/ask "):
            question = question[5:]

        lines = [
            f"{Colors.BOLD}Question:{Colors.RESET} {question}",
            "",
            f"{Colors.DIM}[RAG engine not yet implemented. "
            f"This will provide context-aware answers about your system.]{Colors.RESET}",
        ]

        if intent_result.entities:
            lines.extend([
                "",
                f"{Colors.DIM}Detected entities: {', '.join(intent_result.entities)}{Colors.RESET}",
            ])

        return EngineResult(
            output="\n".join(lines),
            session=session,
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
                return EngineResult(
                    output=f"{Colors.DIM}[Configuration not yet implemented]{Colors.RESET}",
                    session=session,
                )
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
            case _:
                return EngineResult(
                    output=f"Unknown meta command: {command}",
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
            output += (
                f"{Colors.DIM}Command failed (exit {result.exit_code}). "
                f"Type 'fix' for help.{Colors.RESET}"
            )

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

        lines = [
            f"  {i + 1}  {cmd}"
            for i, cmd in enumerate(session.history)
        ]
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
        return (
            f"{Colors.BOLD_RED}Command blocked{Colors.RESET}\n"
            f"{explanation}"
        )

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
  status    Show session status
  history   Show command history
  clear     Clear the screen
  exit      Exit ELLE terminal
  fix       Analyze last failed command

{Colors.BOLD}Prefix Commands:{Colors.RESET}
  /sh <cmd>   Execute shell command explicitly
  /ask <q>    Ask a system question
  /do <task>  Request a system task
  /fix        Fix last failed command
  !<cmd>      Shell command shortcut

{Colors.BOLD}Usage:{Colors.RESET}
  Shell commands are auto-detected and executed directly
  Questions (why, how, what) get context-aware answers
  Task requests are planned and executed safely
  Timeout protection: {self.DEFAULT_TIMEOUT:.0f}s default

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
Repository: https://github.com/elle-project/elle{Colors.RESET}"""

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
                lines.append(
                    f"{Colors.CYAN}{i}. {result.name}({result.section}){Colors.RESET}"
                    f" [{result.search_type}]"
                )

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
                f"{Colors.DIM}Tip: Use 'man <name> <section>' in your shell "
                f"for full documentation{Colors.RESET}"
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
        """Trigger Man Vault reindexing.

        Args:
            session: Current session state.

        Returns:
            EngineResult confirming reindex started.
        """
        try:
            from elle.daemon.manvault import get_service

            service = get_service()

            if service.is_indexing:
                return EngineResult(
                    output=f"{Colors.YELLOW}Indexing already in progress{Colors.RESET}",
                    session=session,
                )

            # Note: In a real daemon setup, this would trigger async reindex
            # For CLI usage, we run synchronously in background
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
