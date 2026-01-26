"""System Task Planner Service.

Orchestrates the complete planning pipeline:
1. Build context (Man Vault, prior art)
2. Generate plan via LLM
3. Verify plan safety
4. Execute with rollback support
5. Run validation checks
6. Record incident outcome
"""

# ruff: noqa: E402  # Imports after get_llm() wrapper for mocking

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# Import get_llm at module level for easier mocking
def get_llm():
    """Get the LLM instance (lazy import)."""
    from elle.rag.llm import get_llm as _get_llm

    return _get_llm()


import contextlib

from elle.cli.planner.models import (
    CheckResult,
    CommandPlan,
    DockerState,
    ManDocContext,
    NetworkState,
    PlanContext,
    PlanOutcome,
    PlanResult,
    PlanStep,
    PriorPlanContext,
    RollbackStep,
    StepResult,
    TaskRequest,
    ValidationCheck,
)
from elle.cli.planner.prompts import BASE_SYSTEM_PROMPT, build_planner_prompt, get_schema_hint
from elle.cli.planner.verifier import verify_plan
from elle.cli.subprocess_runner import RunMode, run_safe
from elle.common.session import Session

logger = logging.getLogger(__name__)


class PlannerService:
    """Orchestrates system task planning and execution.

    Handles the complete flow from natural language request
    to verified plan to safe execution with rollback.

    Supports both legacy command-based execution and capability-based
    execution for typed, policy-governed operations.
    """

    def __init__(
        self,
        use_man_vault: bool = True,
        use_incident_vault: bool = True,
        command_timeout: float = 60.0,
        use_capabilities: bool = True,
        use_preflight: bool = True,
    ) -> None:
        """Initialize the planner service.

        Args:
            use_man_vault: Whether to use Man Vault for documentation.
            use_incident_vault: Whether to use Incident Vault for prior art.
            command_timeout: Timeout for command execution in seconds.
            use_capabilities: Whether to use capability-based execution.
            use_preflight: Whether to use preflight validation for packages.
        """
        self.use_man_vault = use_man_vault
        self.use_incident_vault = use_incident_vault
        self.command_timeout = command_timeout
        self.use_capabilities = use_capabilities
        self.use_preflight = use_preflight
        self._capability_executor = None
        self._preflight_validator = None

    @property
    def capability_executor(self):
        """Get the capability executor (lazy initialization)."""
        if self._capability_executor is None and self.use_capabilities:
            try:
                from elle.capabilities import get_executor

                self._capability_executor = get_executor()
            except ImportError:
                logger.debug("Capabilities not available")
        return self._capability_executor

    @property
    def preflight_validator(self):
        """Get the preflight validator (lazy initialization)."""
        if self._preflight_validator is None and self.use_preflight:
            try:
                from elle.ops.preflight import get_validator

                self._preflight_validator = get_validator()
            except ImportError:
                logger.debug("Preflight module not available")
        return self._preflight_validator

    def build_context(
        self,
        request: str,
        session: Session,
        entities: tuple[str, ...] = (),
    ) -> PlanContext:
        """Build planning context from request and vaults.

        Args:
            request: The user's task request.
            session: Current session state.
            entities: Detected entities from classification.

        Returns:
            Complete PlanContext for LLM.
        """
        task_request = TaskRequest(
            request=request,
            cwd=session.cwd,
            entities=entities,
        )

        man_docs: tuple[ManDocContext, ...] = ()
        prior_plans: tuple[PriorPlanContext, ...] = ()

        # Search Man Vault for relevant documentation
        if self.use_man_vault:
            man_docs = self._search_man_vault(request, entities)

        # Search Incident Vault for similar past tasks
        if self.use_incident_vault:
            prior_plans = self._search_prior_plans(request, entities)

        # Build domain-specific context
        docker_state: DockerState | None = None
        network_state: NetworkState | None = None

        if self._is_docker_task(request, entities):
            docker_state = self._get_docker_state()

        if self._is_network_task(request, entities):
            network_state = self._get_network_state()

        return PlanContext(
            request=task_request,
            man_docs=man_docs,
            prior_plans=prior_plans,
            docker_state=docker_state,
            network_state=network_state,
        )

    def generate_plan(self, context: PlanContext) -> CommandPlan | None:
        """Generate a command plan using LLM.

        Args:
            context: Complete planning context.

        Returns:
            CommandPlan or None if LLM unavailable.
        """
        try:
            llm = get_llm()
            if not llm.is_available():
                logger.warning("LLM not available for plan generation")
                return None

            prompt = build_planner_prompt(context)
            schema_hint = get_schema_hint()

            response = llm.chat_json(
                messages=[
                    {"role": "system", "content": BASE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                schema=schema_hint,
                temperature=0.3,  # Lower temp for more deterministic plans
            )

            return self._parse_plan_response(response)

        except Exception as e:
            logger.error(f"Plan generation failed: {e}")
            return None

    def run_planning_pipeline(
        self,
        request: str,
        session: Session,
        entities: tuple[str, ...] = (),
    ) -> PlanResult:
        """Run the complete planning pipeline.

        Steps:
        1. Build context
        2. Generate plan
        3. Verify plan

        Does NOT execute - that requires approval.

        Args:
            request: The user's task request.
            session: Current session state.
            entities: Detected entities from classification.

        Returns:
            PlanResult ready for approval and execution.
        """
        # Build context
        context = self.build_context(request, session, entities)
        result = PlanResult(context=context)

        # Create incident draft
        if self.use_incident_vault:
            try:
                incident_id = self._create_incident(request, context)
                result = result.with_incident_id(incident_id)
            except Exception as e:
                logger.warning(f"Failed to create incident: {e}")

        # Generate plan
        plan = self.generate_plan(context)
        if plan is None:
            logger.warning("Plan generation failed, no fallback available")
            return result.with_outcome(PlanOutcome.ERROR)

        result = result.with_plan(plan)

        # Verify plan
        verification = verify_plan(plan)
        result = result.with_verification(verification)

        return result

    def execute_plan(
        self,
        result: PlanResult,
        session: Session,
        stop_on_failure: bool = True,
        skip_preflight: bool = False,
    ) -> PlanResult:
        """Execute an approved plan.

        Args:
            result: Approved PlanResult.
            session: Current session state.
            stop_on_failure: Whether to stop on first failure.
            skip_preflight: Whether to skip preflight validation.

        Returns:
            Updated PlanResult with execution results.
        """
        if not result.plan:
            return result.with_outcome(PlanOutcome.ERROR)

        result = result.with_approved()
        plan = result.plan
        all_succeeded = True

        # Run preflight validation for package operations
        if not skip_preflight and self.use_preflight:
            can_proceed, preflight_msg = self.run_preflight_for_plan(plan)
            if preflight_msg:
                logger.info(f"Preflight results:\n{preflight_msg}")
            if not can_proceed:
                logger.warning("Preflight validation failed, blocking execution")
                return result.with_outcome(PlanOutcome.ERROR)

        # Execute each step
        for i, step in enumerate(plan.steps):
            step_result = self._execute_step(step, i, session)
            result = result.with_step_result(step_result)

            # Record action in incident
            if result.incident_id:
                try:
                    self._record_action(
                        result.incident_id,
                        step.command,
                        step_result,
                    )
                except Exception as e:
                    logger.warning(f"Failed to record action: {e}")

            if not step_result.success:
                all_succeeded = False
                if stop_on_failure and not step.can_fail:
                    logger.warning(f"Step {i + 1} failed, stopping execution")
                    break

        # Determine outcome before checks
        if all_succeeded:
            outcome = PlanOutcome.SUCCESS
        elif result.steps_succeeded > 0:
            outcome = PlanOutcome.PARTIAL
        else:
            outcome = PlanOutcome.FAILED

        result = result.with_outcome(outcome)

        # Run validation checks if we succeeded or partially succeeded
        if outcome in (PlanOutcome.SUCCESS, PlanOutcome.PARTIAL) and plan.has_checks:
            check_results = self._run_checks(plan.checks, session)
            result = result.with_check_results(check_results)

            # Update outcome based on checks
            if result.checks_failed > 0:
                result = result.with_outcome(PlanOutcome.PARTIAL)

        return result

    def rollback_plan(
        self,
        result: PlanResult,
        session: Session,
    ) -> PlanResult:
        """Execute rollback for a plan.

        Args:
            result: PlanResult to rollback.
            session: Current session state.

        Returns:
            Updated PlanResult with rollback results.
        """
        if not result.plan or not result.plan.has_rollback:
            logger.warning("No rollback available")
            return result

        rollback_results: list[StepResult] = []

        for i, rb in enumerate(result.plan.rollback):
            rb_result = self._execute_rollback_step(rb, i, session)
            rollback_results.append(rb_result)

            # Record rollback action
            if result.incident_id:
                with contextlib.suppress(Exception):
                    self._record_action(
                        result.incident_id,
                        rb.command,
                        rb_result,
                        is_rollback=True,
                    )

        result = result.with_rollback_results(tuple(rollback_results))
        result = result.with_outcome(PlanOutcome.ROLLED_BACK)

        return result

    def finalize(self, result: PlanResult) -> PlanResult:
        """Finalize the plan execution and record outcome.

        Args:
            result: Completed PlanResult.

        Returns:
            Final PlanResult.
        """
        if result.incident_id and self.use_incident_vault:
            try:
                self._finalize_incident(result)
            except Exception as e:
                logger.warning(f"Failed to finalize incident: {e}")

        return result

    def run_preflight_for_plan(self, plan: CommandPlan) -> tuple[bool, str | None]:
        """Run preflight validation for package operations in a plan.

        Detects package operations (apt install, apt upgrade, apt remove)
        and runs appropriate validation before execution.

        Args:
            plan: The command plan to validate.

        Returns:
            Tuple of (can_proceed, validation_message).
            can_proceed is True if all validations pass.
            validation_message contains formatted results or None if no validation needed.
        """
        if not self.preflight_validator:
            return (True, None)

        # Extract package operations from plan
        package_ops = self._extract_package_operations(plan)

        if not package_ops:
            return (True, None)

        try:
            from elle.ops.preflight import format_result_for_display
            from elle.ops.preflight.models import PreflightStatus, PreflightTest

            all_results = []
            can_proceed = True

            for packages, operation in package_ops:
                test = PreflightTest(
                    packages=packages,
                    operation=operation,
                )
                result = self.preflight_validator.validate(test)
                all_results.append((packages, operation, result))

                if result.status == PreflightStatus.BLOCKED:
                    can_proceed = False

            # Format results
            if all_results:
                lines = ["", "━━━ Pre-flight Validation ━━━", ""]
                for packages, operation, result in all_results:
                    pkg_str = ", ".join(packages[:3])
                    if len(packages) > 3:
                        pkg_str += f" (+{len(packages) - 3} more)"
                    lines.append(f"Operation: {operation} {pkg_str}")
                    lines.append(format_result_for_display(result, use_colors=True))
                    lines.append("")

                return (can_proceed, "\n".join(lines))

            return (True, None)

        except Exception as e:
            logger.warning(f"Preflight validation failed: {e}")
            return (True, f"Preflight check skipped: {e}")

    def _extract_package_operations(
        self,
        plan: CommandPlan,
    ) -> list[tuple[tuple[str, ...], str]]:
        """Extract package operations from a plan.

        Parses plan steps to find apt/dpkg commands and extracts
        the packages being installed, upgraded, or removed.

        Args:
            plan: The command plan.

        Returns:
            List of (packages, operation) tuples.
        """
        operations = []

        # Patterns for package operations
        apt_install_pattern = re.compile(
            r"(?:apt(?:-get)?|dpkg)\s+(?:-\w+\s+)*install\s+(?:-\w+\s+)*(.+)",
            re.IGNORECASE,
        )
        apt_upgrade_pattern = re.compile(
            r"(?:apt(?:-get)?)\s+(?:-\w+\s+)*(?:upgrade|dist-upgrade|full-upgrade)",
            re.IGNORECASE,
        )
        apt_remove_pattern = re.compile(
            r"(?:apt(?:-get)?|dpkg)\s+(?:-\w+\s+)*(?:remove|purge)\s+(?:-\w+\s+)*(.+)",
            re.IGNORECASE,
        )

        for step in plan.steps:
            command = step.command or ""

            # Check for install
            match = apt_install_pattern.search(command)
            if match:
                packages = self._parse_package_list(match.group(1))
                if packages:
                    operations.append((packages, "install"))
                continue

            # Check for upgrade
            if apt_upgrade_pattern.search(command):
                # For full upgrade, we can't know packages in advance
                # but we can still trigger preflight for the operation
                operations.append((("upgrade",), "upgrade"))
                continue

            # Check for remove
            match = apt_remove_pattern.search(command)
            if match:
                packages = self._parse_package_list(match.group(1))
                if packages:
                    operations.append((packages, "remove"))
                continue

        return operations

    def _parse_package_list(self, package_string: str) -> tuple[str, ...]:
        """Parse a package list from a command string.

        Args:
            package_string: String containing package names.

        Returns:
            Tuple of package names.
        """
        # Split by whitespace and filter out flags
        tokens = package_string.split()
        packages = []

        for token in tokens:
            # Skip flags
            if token.startswith("-"):
                continue
            # Skip common apt options
            if token in ("--", "-y", "--yes", "--force-yes", "--no-install-recommends"):
                continue
            # Skip version specifiers that start with operators
            if token.startswith(("=", ">", "<")):
                continue
            # Valid package name
            packages.append(token)

        return tuple(packages)

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _search_man_vault(
        self,
        request: str,
        entities: tuple[str, ...],
    ) -> tuple[ManDocContext, ...]:
        """Search Man Vault for relevant documentation.

        Uses daemon API when available, falling back to direct store access.

        Args:
            request: The task request.
            entities: Detected entities.

        Returns:
            Tuple of ManDocContext.
        """
        # Build query from request and entities
        query = request
        if entities:
            query += " " + " ".join(entities)

        # Try daemon API first (daemon primacy)
        try:
            import asyncio

            from elle.cli.daemon_client import get_daemon_client

            client = get_daemon_client()

            async def search_via_daemon() -> list[dict]:
                if not await client.is_daemon_available():
                    return []
                return await client.search_manvault(query=query, limit=4)

            results = asyncio.get_event_loop().run_until_complete(search_via_daemon())

            if results:
                docs = []
                for r in results:
                    docs.append(
                        ManDocContext(
                            name=r.get("command", ""),
                            section=r.get("section", ""),
                            snippet=r.get("snippet", ""),
                            flags_used=(),
                        )
                    )
                logger.debug(f"Got {len(docs)} man docs from daemon API")
                return tuple(docs)
        except Exception as e:
            logger.debug(f"Daemon API man vault search failed: {e}")

        # Fallback to direct store access
        try:
            from elle.daemon.manvault import search

            results = search(query, k=4, search_type="hybrid")

            docs = []
            for r in results:
                docs.append(
                    ManDocContext(
                        name=r.name,
                        section=r.section,
                        snippet=r.snippet,
                        flags_used=(),  # Could extract flags from snippet
                    )
                )

            return tuple(docs)

        except ImportError:
            logger.debug("Man Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Man Vault search failed: {e}")
            return ()

    def _search_prior_plans(
        self,
        request: str,
        entities: tuple[str, ...],
    ) -> tuple[PriorPlanContext, ...]:
        """Search Incident Vault for similar past plans.

        Uses daemon API when available, falling back to direct store access.

        Args:
            request: The task request.
            entities: Detected entities.

        Returns:
            Tuple of PriorPlanContext.
        """
        # Build query
        query = request
        if entities:
            query += " " + " ".join(entities)

        # Try daemon API first (daemon primacy)
        try:
            import asyncio

            from elle.cli.daemon_client import get_daemon_client

            client = get_daemon_client()

            async def search_via_daemon() -> list[dict]:
                if not await client.is_daemon_available():
                    return []
                return await client.search_incidents(query=query, limit=3)

            results = asyncio.get_event_loop().run_until_complete(search_via_daemon())

            if results:
                contexts = []
                for art in results:
                    contexts.append(
                        PriorPlanContext(
                            incident_id=art.get("incident_id", ""),
                            title=art.get("title", ""),
                            outcome=art.get("outcome", "unknown"),
                            plan_summary=art.get("summary", ""),
                            commands_executed=(),  # Not available via simple API
                            rollback_used=False,
                            score=art.get("score", 0.0),
                            days_ago=0,
                        )
                    )
                logger.debug(f"Got {len(contexts)} prior plans from daemon API")
                return tuple(contexts)
        except Exception as e:
            logger.debug(f"Daemon API prior plan search failed: {e}")

        # Fallback to direct store access (for full-featured search)
        try:
            from elle.daemon.incidents import get_prior_art

            prior = get_prior_art(
                query=query,
                k=3,
                include_actions=True,
            )

            contexts = []
            for art in prior:
                commands = []
                for action in art.get("successful_actions", []):
                    if action.get("command"):
                        commands.append(action["command"])

                contexts.append(
                    PriorPlanContext(
                        incident_id=art["incident_id"],
                        title=art["title"],
                        outcome=art["outcome"],
                        plan_summary=art.get("summary", ""),
                        commands_executed=tuple(commands),
                        rollback_used=False,  # Could track this
                        score=art.get("score", 0.0),
                        days_ago=art.get("days_ago", 0),
                    )
                )

            return tuple(contexts)

        except ImportError:
            logger.debug("Incident Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Prior plan search failed: {e}")
            return ()

    def _is_docker_task(self, request: str, entities: tuple[str, ...]) -> bool:
        """Check if this is a Docker-related task.

        Args:
            request: The task request.
            entities: Detected entities.

        Returns:
            True if Docker-related.
        """
        keywords = {"docker", "container", "compose", "image", "volume"}
        request_lower = request.lower()

        if any(kw in request_lower for kw in keywords):
            return True

        return "docker" in entities

    def _is_network_task(self, request: str, entities: tuple[str, ...]) -> bool:
        """Check if this is a network-related task.

        Args:
            request: The task request.
            entities: Detected entities.

        Returns:
            True if network-related.
        """
        keywords = {
            "firewall",
            "ufw",
            "iptables",
            "port",
            "network",
            "wireguard",
            "vpn",
            "wg",
            "route",
            "dns",
            "connectivity",
        }
        request_lower = request.lower()

        if any(kw in request_lower for kw in keywords):
            return True

        return bool("network" in entities or "wireguard" in entities)

    def _get_docker_state(self) -> DockerState:
        """Get current Docker environment state from daemon.

        Queries the daemon's cached state instead of making direct
        subprocess calls. Falls back to subprocess if daemon unavailable.

        Returns:
            DockerState with current container/image info.
        """
        import asyncio

        from elle.cli.daemon_client import get_daemon_client

        try:
            client = get_daemon_client()
            daemon_state = asyncio.get_event_loop().run_until_complete(client.get_docker_state())

            # Convert daemon client's ContainerInfo to dict format expected by planner
            containers = [
                {
                    "ID": c.id,
                    "Names": c.names,
                    "Image": c.image,
                    "Status": c.status,
                    "State": c.state,
                    "Ports": c.ports,
                }
                for c in daemon_state.running_containers
            ]

            return DockerState(
                running_containers=tuple(containers),
                images=daemon_state.images,
                networks=daemon_state.networks,
                volumes=daemon_state.volumes,
                docker_available=daemon_state.docker_available,
            )

        except Exception as e:
            logger.warning(f"Failed to get Docker state from daemon: {e}")
            return DockerState(docker_available=False)

    def _get_network_state(self) -> NetworkState:
        """Get current network state from daemon.

        Queries the daemon's cached state instead of making direct
        subprocess calls. Falls back to subprocess if daemon unavailable.

        Returns:
            NetworkState with interface/firewall info.
        """
        import asyncio

        from elle.cli.daemon_client import get_daemon_client

        try:
            client = get_daemon_client()
            daemon_state = asyncio.get_event_loop().run_until_complete(client.get_network_state())

            # Convert daemon client's InterfaceInfo to dict format expected by planner
            interfaces = [
                {
                    "name": i.name,
                    "state": i.state,
                    "addresses": list(i.addresses),
                }
                for i in daemon_state.interfaces
            ]

            return NetworkState(
                interfaces=tuple(interfaces),
                firewall_active=daemon_state.firewall_active,
                firewall_type=daemon_state.firewall_backend,
                default_gateway=daemon_state.default_gateway,
                dns_servers=daemon_state.dns_servers,
                wireguard_interfaces=daemon_state.wireguard_interfaces,
            )

        except Exception as e:
            logger.warning(f"Failed to get network state from daemon: {e}")
            return NetworkState()

    def _parse_plan_response(self, response: dict) -> CommandPlan:
        """Parse LLM response into CommandPlan.

        Args:
            response: JSON response from LLM.

        Returns:
            Parsed CommandPlan.
        """
        # Parse steps
        steps = []
        for step_data in response.get("steps", []):
            steps.append(
                PlanStep(
                    command=step_data["command"],
                    explanation=step_data["explanation"],
                    risk_level=step_data.get("risk_level", "medium"),
                    requires_privilege=step_data.get("requires_privilege", False),
                    can_fail=step_data.get("can_fail", False),
                )
            )

        # Parse checks
        checks = []
        for check_data in response.get("checks", []):
            checks.append(
                ValidationCheck(
                    command=check_data["command"],
                    description=check_data["description"],
                    expected=check_data.get("expected"),
                )
            )

        # Parse rollback
        rollback = []
        for rb_data in response.get("rollback", []):
            rollback.append(
                RollbackStep(
                    command=rb_data["command"],
                    explanation=rb_data["explanation"],
                    requires_privilege=rb_data.get("requires_privilege", False),
                )
            )

        # Determine if privilege is required
        requires_privilege = any(s.requires_privilege for s in steps)

        return CommandPlan(
            title=response.get("title", "System Task"),
            explanation=response.get("explanation", ""),
            steps=tuple(steps),
            checks=tuple(checks),
            rollback=tuple(rollback),
            risks=tuple(response.get("risks", [])),
            requires_privilege=requires_privilege,
            grounded_in=tuple(response.get("grounded_in", [])),
        )

    def _execute_step(
        self,
        step: PlanStep,
        step_index: int,
        session: Session,
    ) -> StepResult:
        """Execute a single plan step.

        Prioritizes capability-based execution to ensure policy enforcement
        and audit trails. Falls back to raw commands only when no capability
        mapping exists.

        Args:
            step: The step to execute.
            step_index: Index of the step.
            session: Current session.

        Returns:
            StepResult with execution details.
        """
        from elle.cli.planner.command_mapper import map_command_to_capability

        # 1. If step already has a capability, use it
        if step.uses_capability and self.capability_executor:
            return self._execute_capability_step(step, step_index)

        # 2. Try to map command to capability
        if step.command and self.capability_executor:
            mapping = map_command_to_capability(step.command)
            if mapping.success and mapping.capability:
                # Create a new step with the mapped capability
                mapped_step = PlanStep(
                    command=step.command,
                    explanation=step.explanation,
                    risk_level=step.risk_level,
                    requires_privilege=step.requires_privilege,
                    can_fail=step.can_fail,
                    capability=mapping.capability,
                    capability_input=mapping.capability_input,
                )
                logger.debug(f"Mapped command '{step.command}' to capability '{mapping.capability}'")
                return self._execute_capability_step(mapped_step, step_index)
            else:
                # Log that we're falling back to raw command (audit trail)
                logger.warning(
                    f"No capability mapping for command: {step.command} "
                    f"(reason: {mapping.unmapped_reason}). "
                    "Falling back to raw execution."
                )

        # 3. Fall back to command-based execution (legacy path)
        return self._execute_command_step(step, step_index, session)

    def _execute_command_step(
        self,
        step: PlanStep,
        step_index: int,
        session: Session,
    ) -> StepResult:
        """Execute a command-based plan step.

        Args:
            step: The step to execute.
            step_index: Index of the step.
            session: Current session.

        Returns:
            StepResult with execution details.
        """
        start_time = time.time()

        command = step.command or ""
        result = run_safe(
            command,
            cwd=session.cwd,
            timeout=self.command_timeout,
            mode=RunMode.CAPTURE,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        return StepResult(
            step_index=step_index,
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            success=result.success,
            privileged=step.requires_privilege,
            duration_ms=duration_ms,
            executed_at=datetime.utcnow(),
        )

    def _execute_capability_step(
        self,
        step: PlanStep,
        step_index: int,
    ) -> StepResult:
        """Execute a capability-based plan step.

        Args:
            step: The step to execute.
            step_index: Index of the step.

        Returns:
            StepResult with execution details.
        """
        import asyncio

        start_time = time.time()

        try:
            from elle.capabilities import CapabilityNotFoundError

            # Build input model from capability_input dict
            cap_name = step.capability or ""
            cap_input = step.capability_input or {}

            # Get the capability to determine input type
            cap = self.capability_executor.registry.get(cap_name)
            if not cap:
                raise CapabilityNotFoundError(cap_name)

            # Try to construct input model
            # For now, use a generic BaseModel wrapper
            from pydantic import BaseModel

            class GenericInput(BaseModel):
                class Config:
                    extra = "allow"

            input_model = GenericInput(**cap_input)

            # Execute capability (async)
            async def run_cap():
                return await self.capability_executor.execute(
                    cap_name,
                    input_model,
                    require_confirmation=False,  # Already confirmed via plan approval
                )

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        cap_result = pool.submit(lambda: asyncio.run(run_cap())).result()
                else:
                    cap_result = loop.run_until_complete(run_cap())
            except RuntimeError:
                cap_result = asyncio.run(run_cap())

            duration_ms = int((time.time() - start_time) * 1000)

            return StepResult(
                step_index=step_index,
                command=cap_name,
                exit_code=0 if cap_result.success else 1,
                stdout=cap_result.evidence.verification_details or "",
                stderr=cap_result.error or "",
                success=cap_result.success,
                privileged=step.requires_privilege,
                duration_ms=duration_ms,
                executed_at=datetime.utcnow(),
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Capability execution failed: {e}")

            return StepResult(
                step_index=step_index,
                command=step.capability or "",
                exit_code=1,
                stdout="",
                stderr=str(e),
                success=False,
                privileged=step.requires_privilege,
                duration_ms=duration_ms,
                executed_at=datetime.utcnow(),
            )

    def _execute_rollback_step(
        self,
        step: RollbackStep,
        step_index: int,
        session: Session,
    ) -> StepResult:
        """Execute a rollback step.

        Args:
            step: The rollback step.
            step_index: Index of the step.
            session: Current session.

        Returns:
            StepResult with execution details.
        """
        start_time = time.time()

        result = run_safe(
            step.command,
            cwd=session.cwd,
            timeout=self.command_timeout,
            mode=RunMode.CAPTURE,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        return StepResult(
            step_index=step_index,
            command=step.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            success=result.success,
            privileged=step.requires_privilege,
            duration_ms=duration_ms,
            executed_at=datetime.utcnow(),
        )

    def _run_checks(
        self,
        checks: tuple[ValidationCheck, ...],
        session: Session,
    ) -> tuple[CheckResult, ...]:
        """Run validation checks.

        Args:
            checks: Validation checks to run.
            session: Current session.

        Returns:
            Tuple of CheckResult.
        """
        results = []

        for i, check in enumerate(checks):
            result = run_safe(
                check.command,
                cwd=session.cwd,
                timeout=30.0,
                mode=RunMode.CAPTURE,
            )

            passed = result.success
            actual = result.stdout.strip()

            # Check expected pattern if provided
            if passed and check.expected:
                if not re.search(check.expected, actual, re.IGNORECASE):
                    passed = False

            results.append(
                CheckResult(
                    check_index=i,
                    command=check.command,
                    passed=passed,
                    output=actual,
                    expected=check.expected,
                    actual=actual if check.expected else None,
                )
            )

        return tuple(results)

    def _create_incident(
        self,
        request: str,
        context: PlanContext,
    ) -> str:
        """Create an incident for this task.

        Args:
            request: The task request.
            context: Planning context.

        Returns:
            Incident ID.
        """
        from elle.daemon.incidents import create_incident_draft

        incident = create_incident_draft(
            title=f"Task: {request[:50]}",
            domain="task",
            severity="info",
            trigger_source="system_task",
            trigger_command=request,
        )

        return incident.incident_id

    def _record_action(
        self,
        incident_id: str,
        command: str,
        step_result: StepResult,
        is_rollback: bool = False,
    ) -> None:
        """Record an action in the incident.

        Args:
            incident_id: The incident ID.
            command: The command executed.
            step_result: Result of execution.
            is_rollback: Whether this is a rollback action.
        """
        from elle.daemon.incidents import append_action

        kind = "rollback" if is_rollback else "shell"

        append_action(
            incident_id,
            kind=kind,
            command=command,
            exit_code=step_result.exit_code,
            stdout=step_result.stdout[:500],
            stderr=step_result.stderr[:500],
            success=step_result.success,
        )

    def _finalize_incident(self, result: PlanResult) -> None:
        """Finalize the incident with outcome and provenance.

        Args:
            result: Final PlanResult.
        """
        from elle.daemon.incidents import finalize_outcome, update_incident

        if not result.incident_id:
            return

        # Map plan outcome to incident outcome
        outcome_map = {
            PlanOutcome.SUCCESS: "improved",
            PlanOutcome.PARTIAL: "partial",
            PlanOutcome.FAILED: "no_change",
            PlanOutcome.ROLLED_BACK: "no_change",
            PlanOutcome.CANCELLED: "no_change",
            PlanOutcome.ERROR: "no_change",
        }
        outcome = outcome_map.get(result.outcome, "unknown")

        # Build verification steps list
        verification_steps = []
        for check in result.check_results:
            status = "PASS" if check.passed else "FAIL"
            verification_steps.append(f"[{status}] {check.command}")

        # Update with summary (legacy)
        if result.plan:
            update_incident(
                result.incident_id,
                summary=result.plan.explanation,
                decision={
                    "plan_title": result.plan.title,
                    "steps": [s.command for s in result.plan.steps],
                    "risks": list(result.plan.risks),
                },
            )

            # Store structured decision record with provenance
            self._store_decision_record(result)

        finalize_outcome(
            result.incident_id,
            outcome=outcome,
            verification_steps=verification_steps,
            root_cause=None,  # Could be set by user
        )

    def _store_decision_record(self, result: PlanResult) -> None:
        """Store a structured decision record for this plan.

        Args:
            result: The plan result with context.
        """
        try:
            from elle.daemon.incidents.models import (
                ConfidenceBreakdown,
                DecisionRecord,
                IncidentCitation,
                ManPageCitation,
                Provenance,
            )
            from elle.daemon.incidents.store import store_decision_record

            if not result.incident_id or not result.plan:
                return

            # Build man page citations
            man_citations = []
            for doc in result.context.man_docs:
                man_citations.append(
                    ManPageCitation(
                        name=doc.name,
                        section=doc.section,
                        snippet=doc.snippet[:500],
                        relevance_score=0.5,  # Default score
                    )
                )

            # Build incident citations
            incident_citations = []
            for prior in result.context.prior_plans:
                incident_citations.append(
                    IncidentCitation(
                        incident_id=prior.incident_id,
                        title=prior.title,
                        outcome=prior.outcome,  # type: ignore
                        similarity_score=prior.score,
                        match_type="hybrid",
                        successful_actions=prior.commands_executed,
                    )
                )

            # Determine primary source
            if incident_citations and any(i.outcome in ("improved", "partial") for i in incident_citations):
                primary_source = "incident_vault"
            elif man_citations:
                primary_source = "man_vault"
            else:
                primary_source = "llm_only"

            provenance = Provenance(
                man_pages=tuple(man_citations),
                prior_incidents=tuple(incident_citations),
                primary_source=primary_source,  # type: ignore
            )

            # Compute confidence
            llm_conf = 0.7  # Default LLM confidence for plans
            man_conf = 0.2 if man_citations else 0.0
            incident_conf = 0.3 if incident_citations else 0.0

            confidence = ConfidenceBreakdown(
                overall=min(1.0, llm_conf + man_conf + incident_conf) / 2,
                from_man_vault=man_conf,
                from_incident_vault=incident_conf,
                from_llm=llm_conf / 2,
                dominant_tier="llm",
            )

            # Build decision record
            decision_record = DecisionRecord(
                chosen_approach=result.plan.title,
                rationale=result.plan.explanation,
                confidence=confidence,
                provenance=provenance,
                planned_commands=tuple(s.command for s in result.plan.steps),
            )

            store_decision_record(result.incident_id, decision_record)

        except ImportError:
            logger.debug("Incident store not available for decision record")
        except Exception as e:
            logger.warning(f"Failed to store decision record: {e}")


# =============================================================================
# Module-level Service Instance
# =============================================================================

_service: PlannerService | None = None


def get_planner_service() -> PlannerService:
    """Get the shared planner service instance.

    Returns:
        The PlannerService singleton.
    """
    global _service
    if _service is None:
        _service = PlannerService()
    return _service


def reset_planner_service() -> None:
    """Reset the shared service instance (for testing)."""
    global _service
    _service = None
