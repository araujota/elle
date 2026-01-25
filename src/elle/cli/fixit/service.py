"""Fixit service orchestration.

The FixitService coordinates the entire fixit workflow:
1. Build context from session state
2. Search Man Vault for documentation
3. Search Incident Vault for prior art
4. Create incident draft for tracking
5. Send to LLM for analysis
6. Verify suggested commands
7. Return verified results

Supports graceful degradation when services are unavailable.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from elle.cli.fixit.models import (
    CommandFailure,
    FixCommand,
    FixitAction,
    FixitAnalysis,
    FixitContext,
    FixitDiagnosis,
    FixitOutcome,
    FixitResult,
    ManSnippetContext,
    PriorArtContext,
)
from elle.cli.fixit.prompts import BASE_SYSTEM_PROMPT, build_fixit_prompt, get_schema_hint
from elle.cli.fixit.verifier import verify_analysis

if TYPE_CHECKING:
    from elle.common.session import Session

logger = logging.getLogger(__name__)


class FixitService:
    """Orchestrates the fixit command workflow.

    Handles context building, LLM analysis, verification,
    and incident tracking with graceful degradation.
    """

    def __init__(
        self,
        use_man_vault: bool = True,
        use_incident_vault: bool = True,
        use_llm: bool = True,
    ) -> None:
        """Initialize the fixit service.

        Args:
            use_man_vault: Whether to search Man Vault for docs.
            use_incident_vault: Whether to search Incident Vault.
            use_llm: Whether to use LLM for analysis.
        """
        self._use_man_vault = use_man_vault
        self._use_incident_vault = use_incident_vault
        self._use_llm = use_llm

    # =========================================================================
    # Context Building
    # =========================================================================

    def build_context(self, session: Session) -> FixitContext:
        """Build fixit context from session state.

        Args:
            session: Current session with last command info.

        Returns:
            FixitContext with all available information.
        """
        # Build CommandFailure from session
        failure = CommandFailure(
            command=session.last_cmd or "",
            exit_code=session.last_exit or 1,
            stdout=session.last_stdout or "",
            stderr=session.last_stderr or "",
            cwd=session.cwd,
        )

        # Get recent history
        history = session.history[-10:] if session.history else ()

        # Search for documentation
        man_snippets = self._search_man_vault(failure) if self._use_man_vault else ()

        # Search for prior art
        prior_art = self._search_prior_art(failure) if self._use_incident_vault else ()

        return FixitContext(
            failure=failure,
            history=history,
            man_snippets=man_snippets,
            prior_art=prior_art,
        )

    def _search_man_vault(
        self,
        failure: CommandFailure,
    ) -> tuple[ManSnippetContext, ...]:
        """Search Man Vault for relevant documentation.

        Args:
            failure: The command failure.

        Returns:
            Tuple of man page snippets with relevance scores.
        """
        try:
            from elle.daemon.manvault.retriever import search

            # Build search query from command and error
            cmd_parts = failure.command.split()
            cmd_name = cmd_parts[0] if cmd_parts else ""

            # Search for the command itself
            results = search(
                query=f"{cmd_name} {failure.stderr[:200]}",
                k=3,
                name=cmd_name if cmd_name else None,
                search_type="hybrid",
            )

            snippets = []
            for result in results:
                # Extract relevance score if available
                score = getattr(result, "score", 0.0)
                if isinstance(score, (int, float)):
                    # Normalize to 0-1 range
                    relevance = min(1.0, max(0.0, float(score)))
                else:
                    relevance = 0.5  # Default if not available

                snippets.append(ManSnippetContext(
                    name=result.name,
                    section=result.section,
                    snippet=result.snippet,
                    match_section=result.match_section,
                    relevance_score=relevance,
                ))

            return tuple(snippets)

        except ImportError:
            logger.debug("Man Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Man Vault search failed: {e}")
            return ()

    def _search_prior_art(
        self,
        failure: CommandFailure,
    ) -> tuple[PriorArtContext, ...]:
        """Search Incident Vault for similar past incidents.

        Uses daemon API when available, falling back to direct store access.

        Three-tiered retrieval:
        1. Domain/entity filtering based on error patterns
        2. FTS search using stderr keywords and command names
        3. Semantic similarity (if embeddings available)

        Results are ranked by: similarity × outcome_quality × recency

        Args:
            failure: The command failure.

        Returns:
            Tuple of prior art contexts with successful actions.
        """
        # Try daemon API first (daemon primacy)
        try:
            import asyncio

            from elle.cli.daemon_client import get_daemon_client

            client = get_daemon_client()

            # Build search query
            cmd_parts = failure.command.split()
            cmd_name = cmd_parts[0] if cmd_parts else ""
            stderr_keywords = self._extract_error_keywords(failure.stderr)
            query = f"{cmd_name} {' '.join(stderr_keywords[:5])} exit {failure.exit_code}"

            async def search_via_daemon() -> list[dict]:
                if not await client.is_daemon_available():
                    return []
                return await client.search_incidents(query=query, limit=5)

            results = asyncio.get_event_loop().run_until_complete(search_via_daemon())

            if results:
                arts = []
                for result in results:
                    arts.append(PriorArtContext(
                        incident_id=result.get("incident_id", ""),
                        title=result.get("title", ""),
                        outcome=result.get("outcome", "unknown"),
                        summary=result.get("summary", ""),
                        decision={},
                        root_cause=result.get("root_cause"),
                        verification_steps=(),
                        successful_actions=(),
                        trigger_command=None,
                        score=result.get("score", 0.0),
                        outcome_weight=0.5,
                        precondition_match=0.0,
                        days_ago=0,
                        match_type="daemon_api",
                    ))
                logger.debug(f"Got {len(arts)} incidents from daemon API")
                return tuple(arts[:3])
        except Exception as e:
            logger.debug(f"Daemon API incident search failed, using fallback: {e}")

        # Fallback to direct store access (for full-featured search)
        try:
            from elle.daemon.incidents.correlator import IncidentCorrelator
            from elle.daemon.incidents.retriever import get_prior_art
            from elle.daemon.incidents.snapshot import collect_snapshot, extract_fingerprint

            # Collect current state for fingerprint matching
            # This enables pressure-based similarity (disk, memory, etc.)
            try:
                snapshot = collect_snapshot()
                fingerprint = extract_fingerprint(snapshot)
            except Exception:
                snapshot = None
                fingerprint = None

            # Detect domain from error for tier-1 filtering
            correlator = IncidentCorrelator()
            domain = correlator._detect_domain(failure.stderr)

            # Extract entities from error for better matching
            _entities = correlator._extract_entities(failure.stderr)  # TODO: Use for enhanced matching

            # Build search query from:
            # - Command name (most important)
            # - Key stderr keywords
            # - Exit code context
            cmd_parts = failure.command.split()
            cmd_name = cmd_parts[0] if cmd_parts else ""

            # Extract key error phrases for FTS
            stderr_keywords = self._extract_error_keywords(failure.stderr)
            query = f"{cmd_name} {' '.join(stderr_keywords[:5])} exit {failure.exit_code}"

            # Search with all context
            results = get_prior_art(
                query=query,
                domain=domain,
                fingerprint=fingerprint,
                snapshot=snapshot,
                k=5,  # Get more candidates, will filter
                include_actions=True,  # Include successful actions
            )

            arts = []
            for result in results:
                # Parse successful actions
                successful_actions = []
                for action in result.get("successful_actions", []):
                    from elle.cli.fixit.models import SuccessfulAction
                    successful_actions.append(SuccessfulAction(
                        command=action.get("command", ""),
                        kind=action.get("kind", "shell"),
                        exit_code=action.get("exit_code", 0),
                    ))

                arts.append(PriorArtContext(
                    incident_id=result.get("incident_id", ""),
                    title=result.get("title", ""),
                    outcome=result.get("outcome", "unknown"),
                    summary=result.get("summary", ""),
                    decision=result.get("decision", {}),
                    root_cause=result.get("root_cause"),
                    verification_steps=tuple(result.get("verification_steps", [])),
                    successful_actions=tuple(successful_actions),
                    trigger_command=result.get("trigger_command"),
                    score=result.get("score", 0.0),
                    outcome_weight=result.get("outcome_weight", 0.5),
                    precondition_match=result.get("precondition_match", 0.0),
                    days_ago=result.get("days_ago", 0),
                    match_type=result.get("match_type", "hybrid"),
                ))

            # Return top 3 most relevant
            return tuple(arts[:3])

        except ImportError:
            logger.debug("Incident Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Incident Vault search failed: {e}")
            return ()

    def _extract_error_keywords(self, stderr: str) -> list[str]:
        """Extract key error keywords from stderr for FTS search.

        Args:
            stderr: Error output text.

        Returns:
            List of relevant keywords.
        """
        import re

        # Common error patterns to extract
        keywords = []

        # Error codes/types
        for match in re.finditer(r"(E[A-Z]+|error|failed|denied|not found|cannot)", stderr, re.I):
            keywords.append(match.group(0).lower())

        # File paths (extract basename)
        for match in re.finditer(r"/[\w/.-]+", stderr):
            path = match.group(0)
            basename = path.split("/")[-1]
            if basename and len(basename) > 2:
                keywords.append(basename)

        # Service/package names
        for match in re.finditer(r"(\w+\.service|\w+-\w+)", stderr):
            keywords.append(match.group(0))

        # Deduplicate and limit
        seen = set()
        unique = []
        for kw in keywords:
            if kw not in seen and len(kw) > 2:
                seen.add(kw)
                unique.append(kw)

        return unique[:10]

    # =========================================================================
    # Incident Management
    # =========================================================================

    def _create_incident(self, failure: CommandFailure) -> str | None:
        """Create an incident draft for tracking.

        Args:
            failure: The command failure.

        Returns:
            Incident ID if created, None otherwise.
        """
        try:
            from elle.daemon.incidents.correlator import get_correlator

            correlator = get_correlator()
            incident_id = correlator.create_from_command_failure(
                command=failure.command,
                stderr=failure.stderr,
                exit_code=failure.exit_code,
            )
            return incident_id

        except ImportError:
            logger.debug("Incident Vault not available")
            return None
        except Exception as e:
            logger.warning(f"Failed to create incident: {e}")
            return None

    def record_action(
        self,
        incident_id: str,
        action: FixitAction,
    ) -> None:
        """Record an action to the incident.

        Args:
            incident_id: The incident ID.
            action: The action taken.
        """
        try:
            from elle.daemon.incidents.store import append_action

            append_action(
                incident_id=incident_id,
                kind="shell",
                command=action.command,
                exit_code=action.exit_code,
                stdout=action.stdout[:1000] if action.stdout else None,
                stderr=action.stderr[:1000] if action.stderr else None,
                success=action.success,
                privileged=action.privileged,
                duration_ms=action.duration_ms,
            )

        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Failed to record action: {e}")

    def finalize_incident(
        self,
        incident_id: str,
        result: FixitResult,
    ) -> None:
        """Finalize the incident with outcome and provenance.

        Args:
            incident_id: The incident ID.
            result: The fixit result.
        """
        try:
            from elle.daemon.incidents.models import (
                DecisionRecord,
                Provenance,
            )
            from elle.daemon.incidents.store import (
                finalize_outcome,
                store_decision_record,
                update_incident,
            )

            # Map FixitOutcome to incident outcome
            outcome_map = {
                FixitOutcome.IMPROVED: "improved",
                FixitOutcome.PARTIAL: "partial",
                FixitOutcome.NO_CHANGE: "no_change",
                FixitOutcome.WORSE: "worse",
                FixitOutcome.SKIPPED: "unknown",
                FixitOutcome.ERROR: "no_change",
            }
            outcome = outcome_map.get(result.outcome, "unknown")

            # Update legacy decision info (for backwards compatibility)
            if result.analysis:
                decision = {
                    "diagnosis": result.analysis.diagnosis.model_dump(),
                    "suggestions_count": len(result.analysis.suggestions),
                    "used_fallback": result.used_fallback,
                }
                update_incident(
                    incident_id,
                    decision=decision,
                    root_cause=result.analysis.diagnosis.root_cause,
                )

                # Build structured provenance
                man_citations = self._build_man_citations(result.context)
                incident_citations = self._build_incident_citations(result.context)
                provenance = Provenance(
                    man_pages=man_citations,
                    prior_incidents=incident_citations,
                    primary_source=result.context.primary_source,
                )

                # Compute confidence breakdown
                confidence = self._compute_confidence(result, provenance)

                # Build decision record
                planned_commands = tuple(
                    s.fix.command for s in result.verified_suggestions
                )
                decision_record = DecisionRecord(
                    chosen_approach=result.analysis.diagnosis.summary,
                    rationale=result.analysis.diagnosis.root_cause,
                    confidence=confidence,
                    provenance=provenance,
                    planned_commands=planned_commands,
                )

                # Store structured decision record
                store_decision_record(incident_id, decision_record)

            # Finalize outcome
            verification_steps = []
            for action in result.actions:
                step = f"{action.command}: {'success' if action.success else 'failed'}"
                verification_steps.append(step)

            finalize_outcome(
                incident_id=incident_id,
                outcome=outcome,
                verification_steps=verification_steps,
                root_cause=result.analysis.diagnosis.root_cause if result.analysis else None,
            )

        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Failed to finalize incident: {e}")

    def _build_man_citations(
        self,
        context: FixitContext,
    ) -> tuple:
        """Build ManPageCitation tuples from context.

        Args:
            context: The fixit context.

        Returns:
            Tuple of ManPageCitation.
        """
        from elle.daemon.incidents.models import ManPageCitation

        citations = []
        for snippet in context.man_snippets:
            citations.append(ManPageCitation(
                name=snippet.name,
                section=snippet.section,
                snippet=snippet.snippet[:500],  # Truncate for storage
                match_section=snippet.match_section,
                relevance_score=snippet.relevance_score,
            ))
        return tuple(citations)

    def _build_incident_citations(
        self,
        context: FixitContext,
    ) -> tuple:
        """Build IncidentCitation tuples from context.

        Args:
            context: The fixit context.

        Returns:
            Tuple of IncidentCitation.
        """
        from elle.daemon.incidents.models import IncidentCitation

        citations = []
        for art in context.prior_art:
            # Extract successful command strings
            successful_commands = tuple(
                a.command for a in art.successful_actions if a.command
            )
            citations.append(IncidentCitation(
                incident_id=art.incident_id,
                title=art.title,
                outcome=art.outcome,  # type: ignore
                similarity_score=art.score,
                match_type=art.match_type,
                successful_actions=successful_commands,
            ))
        return tuple(citations)

    def _compute_confidence(
        self,
        result: FixitResult,
        provenance: Any,
    ) -> Any:
        """Compute confidence breakdown from result and provenance.

        Args:
            result: The fixit result.
            provenance: The provenance record.

        Returns:
            ConfidenceBreakdown model.
        """
        from elle.daemon.incidents.models import ConfidenceBreakdown

        # Start with LLM confidence
        llm_conf = result.analysis.diagnosis.confidence if result.analysis else 0.0

        # Man vault contribution
        man_conf = 0.0
        if provenance.man_pages:
            man_scores = [m.relevance_score for m in provenance.man_pages]
            man_conf = max(man_scores) * 0.3  # Weight man pages at 30%

        # Incident vault contribution
        incident_conf = 0.0
        if provenance.prior_incidents:
            # Prioritize incidents with good outcomes
            good_incidents = [
                i for i in provenance.prior_incidents
                if i.outcome in ("improved", "partial")
            ]
            if good_incidents:
                incident_conf = max(i.similarity_score for i in good_incidents) * 0.4

        # Combine confidences
        overall = min(1.0, llm_conf * 0.5 + man_conf + incident_conf)

        # Determine dominant tier
        if incident_conf > man_conf and incident_conf > llm_conf * 0.3:
            dominant = "semantic" if provenance.prior_incidents else "llm"
        elif man_conf > llm_conf * 0.3:
            dominant = "lexical"
        else:
            dominant = "llm"

        return ConfidenceBreakdown(
            overall=overall,
            from_man_vault=man_conf,
            from_incident_vault=incident_conf,
            from_llm=llm_conf * 0.5,
            dominant_tier=dominant,
        )

    # =========================================================================
    # LLM Analysis
    # =========================================================================

    def _analyze_with_llm(self, context: FixitContext) -> FixitAnalysis | None:
        """Send context to LLM for analysis.

        Args:
            context: The fixit context.

        Returns:
            FixitAnalysis if successful, None if LLM unavailable.
        """
        try:
            from elle.rag.llm import LLMJSONError, LLMUnavailableError, get_llm

            llm = get_llm()
            if not llm.is_available():
                logger.info("LLM not available, will use fallback")
                return None

            prompt = build_fixit_prompt(context)

            try:
                response = llm.generate_json(
                    prompt=prompt,
                    system=BASE_SYSTEM_PROMPT,
                    schema=get_schema_hint(),
                    temperature=0.3,  # Lower temperature for more focused analysis
                    timeout=60.0,
                )
            except LLMJSONError as e:
                logger.warning(f"LLM returned invalid JSON: {e}")
                return None

            return self._parse_llm_response(response)

        except LLMUnavailableError:
            logger.info("LLM service unavailable")
            return None
        except ImportError:
            logger.debug("LLM module not available")
            return None
        except Exception as e:
            logger.warning(f"LLM analysis failed: {e}")
            return None

    def _parse_llm_response(self, response: dict[str, Any]) -> FixitAnalysis | None:
        """Parse LLM response into FixitAnalysis.

        Args:
            response: Raw JSON response from LLM.

        Returns:
            FixitAnalysis if valid, None if parsing fails.
        """
        try:
            # Parse diagnosis
            diag_data = response.get("diagnosis", {})
            diagnosis = FixitDiagnosis(
                error_category=diag_data.get("error_category", "other"),
                summary=diag_data.get("summary", "Unknown error"),
                root_cause=diag_data.get("root_cause", "Unknown cause"),
                confidence=float(diag_data.get("confidence", 0.5)),
            )

            # Parse suggestions
            suggestions = []
            for sugg in response.get("suggestions", []):
                fix = FixCommand(
                    command=sugg.get("command", ""),
                    explanation=sugg.get("explanation", ""),
                    risk_level=sugg.get("risk_level", "moderate"),
                    requires_privilege=sugg.get("requires_privilege", False),
                    verification=sugg.get("verification"),
                )
                if fix.command:  # Only add non-empty commands
                    suggestions.append(fix)

            # Parse optional fields
            alternative = response.get("alternative_approach")
            learn_more = tuple(response.get("learn_more", []))

            return FixitAnalysis(
                diagnosis=diagnosis,
                suggestions=tuple(suggestions),
                alternative_approach=alternative,
                learn_more=learn_more,
            )

        except Exception as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            return None

    # =========================================================================
    # Main Pipeline
    # =========================================================================

    def run_full_pipeline(self, session: Session) -> FixitResult:
        """Run the complete fixit pipeline.

        Steps:
        1. Build context from session
        2. Create incident for tracking
        3. Get LLM analysis (or use fallback)
        4. Verify suggested commands
        5. Return result ready for display/interaction

        Args:
            session: Current session state.

        Returns:
            FixitResult with analysis and verified suggestions.
        """
        # Step 1: Build context
        context = self.build_context(session)
        result = FixitResult(context=context)

        # Step 2: Create incident
        if self._use_incident_vault:
            incident_id = self._create_incident(context.failure)
            if incident_id:
                result = result.with_incident_id(incident_id)

        # Step 3: Get analysis
        analysis = None
        used_fallback = False

        if self._use_llm:
            analysis = self._analyze_with_llm(context)

        if analysis is None:
            # Use fallback
            from elle.cli.fixit.fallback import analyze_with_fallback
            analysis = analyze_with_fallback(context)
            used_fallback = True

        if analysis:
            result = FixitResult(
                context=result.context,
                analysis=analysis,
                verified_suggestions=result.verified_suggestions,
                verification_errors=result.verification_errors,
                actions=result.actions,
                outcome=result.outcome,
                incident_id=result.incident_id,
                used_fallback=used_fallback,
            )

        # Step 4: Verify suggestions
        if analysis and analysis.suggestions:
            verified, errors = verify_analysis(analysis)
            result = result.with_verified_suggestions(verified, errors)

        return result

    def execute_fix(
        self,
        result: FixitResult,
        command: str,
        session: Session,
    ) -> tuple[FixitResult, FixitAction]:
        """Execute a fix command and record the action.

        Prioritizes capability-based execution to ensure policy enforcement
        and audit trails. Falls back to raw commands only when no capability
        mapping exists.

        Args:
            result: Current fixit result.
            command: The command to execute.
            session: Current session state.

        Returns:
            Tuple of (updated result, action taken).
        """
        import asyncio

        from elle.cli.planner.command_mapper import map_command_to_capability
        from elle.cli.subprocess_runner import RunMode, run_safe

        start = time.time()

        # Try to map command to capability first
        mapping = map_command_to_capability(command)

        if mapping.success and mapping.capability:
            # Execute via capability system
            try:
                from elle.capabilities import get_executor

                executor = get_executor()
                cap = executor.registry.get(mapping.capability)

                if cap:
                    from pydantic import BaseModel

                    class GenericInput(BaseModel):
                        class Config:
                            extra = "allow"

                    input_model = GenericInput(**(mapping.capability_input or {}))

                    async def run_cap():
                        return await executor.execute(
                            mapping.capability,
                            input_model,
                            require_confirmation=False,
                        )

                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor() as pool:
                                cap_result = pool.submit(
                                    lambda: asyncio.run(run_cap())
                                ).result()
                        else:
                            cap_result = loop.run_until_complete(run_cap())
                    except RuntimeError:
                        cap_result = asyncio.run(run_cap())

                    duration_ms = int((time.time() - start) * 1000)

                    action = FixitAction(
                        command=f"[capability:{mapping.capability}] {command}",
                        exit_code=0 if cap_result.success else 1,
                        stdout=cap_result.evidence.verification_details or "",
                        stderr=cap_result.error or "",
                        success=cap_result.success,
                        privileged=False,
                        duration_ms=duration_ms,
                    )

                    new_result = result.with_action(action)
                    if result.incident_id:
                        self.record_action(result.incident_id, action)

                    return new_result, action

            except Exception as e:
                logger.warning(f"Capability execution failed, falling back: {e}")

        else:
            # Log that we're falling back to raw command (audit trail)
            logger.warning(
                f"No capability mapping for fix command: {command} "
                f"(reason: {mapping.unmapped_reason}). "
                "Falling back to raw execution."
            )

        # Fall back to raw command execution
        cmd_result = run_safe(
            command,
            cwd=session.cwd,
            timeout=60.0,
            mode=RunMode.CAPTURE,
        )

        duration_ms = int((time.time() - start) * 1000)

        # Create action record
        action = FixitAction(
            command=command,
            exit_code=cmd_result.exit_code,
            stdout=cmd_result.stdout,
            stderr=cmd_result.stderr,
            success=cmd_result.success,
            privileged=False,
            duration_ms=duration_ms,
        )

        # Update result
        new_result = result.with_action(action)

        # Record to incident
        if result.incident_id:
            self.record_action(result.incident_id, action)

        return new_result, action


# =============================================================================
# Module-level Service Instance
# =============================================================================

_service: FixitService | None = None


def get_fixit_service() -> FixitService:
    """Get the shared fixit service instance.

    Returns:
        The FixitService singleton.
    """
    global _service
    if _service is None:
        _service = FixitService()
    return _service


def reset_fixit_service() -> None:
    """Reset the fixit service (for testing)."""
    global _service
    _service = None
