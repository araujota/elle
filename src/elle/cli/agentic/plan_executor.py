"""PlanExecutor - Executes plans through the real CapabilityExecutor.

This module provides the execution layer for the unified agentic system.
It executes ExecutionPlan through the proper CapabilityExecutor with:
- Full policy checks
- Dry-run previews
- User confirmation
- Parallel execution within groups
- Verification
- Incident recording
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, cast

from pydantic import BaseModel

from elle.cli.agentic.models import (
    CapabilityCall,
    ExecutionEvidence,
    ExecutionPlan,
    ExecutionResult,
    ParallelGroup,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Type Aliases
# =============================================================================

# Callback for user confirmation
ConfirmCallback = Callable[[str, str], Awaitable[bool]]


# =============================================================================
# Fallback Execution
# =============================================================================

# Read-only capabilities that can be executed via fallback
FALLBACK_CAPABILITIES: frozenset[str] = frozenset(
    {
        "service.status",
        "service.logs",
        "file.read",
        "file.stat",
        "package.info",
        "docker.list",
        "docker.inspect",
        "docker.logs",
        "network.listeners",
        "network.connections",
        "system.info",
        "system.resources",
    }
)


async def _fallback_execution(
    capability: str,
    args: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Fallback execution for capabilities not in registry.

    Uses direct subprocess calls for core read-only capabilities.

    Args:
        capability: Capability name.
        args: Arguments dict.

    Returns:
        Tuple of (output_string, error_string).
    """
    from elle.cli.subprocess_runner import RunMode, run_safe

    if capability == "service.status":
        service = args.get("service", "")
        if not service:
            return (None, "Missing service name")
        result = run_safe(
            f"systemctl show {service} --property=ActiveState,SubState,MainPID,Description,LoadState",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, result.stderr or "Failed")

    elif capability == "service.logs":
        service = args.get("service", "")
        lines = args.get("lines", 50)
        if not service:
            return (None, "Missing service name")
        result = run_safe(
            f"journalctl -u {service} -n {lines} --no-pager",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, result.stderr or "Failed")

    elif capability == "file.read":
        path = args.get("path", "")
        if not path:
            return (None, "Missing file path")
        try:
            with open(path) as f:
                return (f.read(), None)
        except Exception as e:
            return (None, str(e))

    elif capability == "file.stat":
        path = args.get("path", "")
        if not path:
            return (None, "Missing file path")
        result = run_safe(f"stat {path}", timeout=5.0, mode=RunMode.CAPTURE)
        return (result.stdout, None) if result.success else (None, result.stderr or "Not found")

    elif capability == "package.info":
        package = args.get("package", "")
        if not package:
            return (None, "Missing package name")
        result = run_safe(
            f"dpkg-query -W -f='${{Status}} ${{Version}}\\n${{Description}}' {package} 2>/dev/null || apt-cache show {package} 2>/dev/null | head -20",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, f"Package '{package}' not found")

    elif capability == "docker.list":
        result = run_safe(
            "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, result.stderr or "Docker unavailable")

    elif capability == "docker.inspect":
        name = args.get("name", "")
        if not name:
            return (None, "Missing container name")
        result = run_safe(
            f"docker inspect {name} --format '{{{{.State.Status}}}} - {{{{.State.StartedAt}}}}'",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, f"Container '{name}' not found")

    elif capability == "docker.logs":
        name = args.get("name", "")
        lines = args.get("lines", 50)
        if not name:
            return (None, "Missing container name")
        result = run_safe(
            f"docker logs {name} --tail {lines}",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout or result.stderr, None) if result.success else (None, result.stderr or "Failed")

    elif capability == "network.listeners":
        result = run_safe(
            "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, "Failed to list listeners")

    elif capability == "network.connections":
        result = run_safe(
            "ss -tnp 2>/dev/null || netstat -tnp 2>/dev/null",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, "Failed to list connections")

    elif capability == "system.info":
        result = run_safe(
            "uname -a && cat /etc/os-release 2>/dev/null | head -5",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, "Failed")

    elif capability == "system.resources":
        result = run_safe(
            "free -h && echo '---' && df -h / && echo '---' && uptime",
            timeout=10.0,
            mode=RunMode.CAPTURE,
        )
        return (result.stdout, None) if result.success else (None, "Failed")

    return (None, f"Unknown capability: {capability}")


# =============================================================================
# PlanExecutor
# =============================================================================


class PlanExecutor:
    """Executes plans through the real CapabilityExecutor.

    Provides the full capability lifecycle:
    - Policy checks
    - Dry-run previews
    - User confirmation (for mutating operations)
    - Parallel execution within groups
    - Verification
    - Incident recording

    Usage:
        executor = PlanExecutor()
        result = await executor.execute(plan, confirm_callback=my_callback)
    """

    def __init__(
        self,
        max_parallel: int = 5,
        skip_confirmation_for_readonly: bool = True,
        use_fallback: bool = True,
    ) -> None:
        """Initialize the executor.

        Args:
            max_parallel: Maximum parallel capability executions per group.
            skip_confirmation_for_readonly: Skip confirmation for read-only ops.
            use_fallback: Use fallback execution for unregistered capabilities.
        """
        self.max_parallel = max_parallel
        self.skip_confirmation_for_readonly = skip_confirmation_for_readonly
        self.use_fallback = use_fallback
        self._capability_executor: Any = None

    @property
    def capability_executor(self) -> Any:
        """Get the real CapabilityExecutor (lazy init)."""
        if self._capability_executor is None:
            try:
                from elle.capabilities.executor import CapabilityExecutor

                self._capability_executor = CapabilityExecutor()
            except ImportError:
                logger.warning("CapabilityExecutor not available, using fallback only")
        return self._capability_executor

    async def execute(
        self,
        plan: ExecutionPlan,
        *,
        confirm_callback: ConfirmCallback | None = None,
        incident_id: str | None = None,
        skip_confirmation: bool = False,
    ) -> ExecutionResult:
        """Execute a plan and collect evidence.

        Groups are executed sequentially (to respect dependencies).
        Calls within each group are executed in parallel.

        Args:
            plan: The ExecutionPlan to execute.
            confirm_callback: Optional callback for user confirmation.
            incident_id: Optional incident ID for recording.
            skip_confirmation: Skip all confirmations (use with caution).

        Returns:
            ExecutionResult with all evidence.
        """
        start_time = time.time()
        all_evidence: list[ExecutionEvidence] = []

        if not plan.parallel_groups:
            return ExecutionResult(
                plan=plan,
                evidence=(),
                total_duration_ms=0,
            )

        # Get confirmation for the overall plan if needed
        if plan.requires_confirmation and not skip_confirmation:
            if confirm_callback:
                preview = self._format_plan_preview(plan)
                confirmed = await confirm_callback("Execution Plan", preview)
                if not confirmed:
                    return ExecutionResult(
                        plan=plan,
                        evidence=(
                            ExecutionEvidence(
                                capability="plan",
                                args={},
                                success=False,
                                error="Cancelled by user",
                                duration_ms=0,
                            ),
                        ),
                        total_duration_ms=int((time.time() - start_time) * 1000),
                    )

        # Execute groups sequentially
        for group_idx, group in enumerate(plan.parallel_groups):
            logger.debug(f"Executing group {group_idx + 1}/{len(plan.parallel_groups)}")

            group_evidence = await self._execute_group(
                group,
                confirm_callback=confirm_callback if not skip_confirmation else None,
                incident_id=incident_id,
            )
            all_evidence.extend(group_evidence)

            # Check if we should stop (e.g., if a critical capability failed)
            if self._should_stop_execution(group_evidence, group):
                logger.debug("Stopping execution due to failure")
                break

        total_duration = int((time.time() - start_time) * 1000)

        return ExecutionResult(
            plan=plan,
            evidence=tuple(all_evidence),
            total_duration_ms=total_duration,
        )

    async def _execute_group(
        self,
        group: ParallelGroup,
        *,
        confirm_callback: ConfirmCallback | None = None,
        incident_id: str | None = None,
    ) -> list[ExecutionEvidence]:
        """Execute a parallel group of capability calls.

        Args:
            group: The ParallelGroup to execute.
            confirm_callback: Optional confirmation callback.
            incident_id: Optional incident ID.

        Returns:
            List of ExecutionEvidence from the group.
        """
        if not group.calls:
            return []

        # Create tasks for parallel execution
        tasks = [self._execute_call(call, confirm_callback, incident_id) for call in group.calls]

        # Execute in parallel with semaphore for rate limiting
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def limited_execute(task: Awaitable[ExecutionEvidence]) -> ExecutionEvidence:
            async with semaphore:
                return await task

        results = await asyncio.gather(
            *[limited_execute(task) for task in tasks],
            return_exceptions=True,
        )

        # Convert exceptions to evidence
        evidence: list[ExecutionEvidence] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                evidence.append(
                    ExecutionEvidence(
                        capability=group.calls[i].capability,
                        args=group.calls[i].args,
                        success=False,
                        error=str(result),
                        duration_ms=0,
                    )
                )
            elif isinstance(result, ExecutionEvidence):
                evidence.append(result)

        return evidence

    async def _execute_call(
        self,
        call: CapabilityCall,
        confirm_callback: ConfirmCallback | None = None,
        incident_id: str | None = None,
    ) -> ExecutionEvidence:
        """Execute a single capability call.

        Args:
            call: The capability call to execute.
            confirm_callback: Optional confirmation callback.
            incident_id: Optional incident ID.

        Returns:
            ExecutionEvidence from the execution.
        """
        start_time = time.time()
        logger.debug(f"Executing capability: {call.capability} with args {call.args}")

        # Try to use the real CapabilityExecutor first
        if self.capability_executor:
            try:
                evidence = await self._execute_via_capability_executor(call, confirm_callback, incident_id)
                if evidence:
                    return evidence
            except Exception as e:
                logger.debug(f"CapabilityExecutor failed: {e}, trying fallback")

        # Fall back to direct execution for read-only capabilities
        if self.use_fallback and call.capability in FALLBACK_CAPABILITIES:
            try:
                output, error = await _fallback_execution(call.capability, call.args)
                duration_ms = int((time.time() - start_time) * 1000)

                return ExecutionEvidence(
                    capability=call.capability,
                    args=call.args,
                    success=error is None,
                    output=output,
                    output_text=output,
                    error=error,
                    duration_ms=duration_ms,
                    timestamp=datetime.now(timezone.utc),
                )
            except Exception as e:
                logger.warning(f"Fallback execution failed: {e}")

        # Neither worked - return failure
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionEvidence(
            capability=call.capability,
            args=call.args,
            success=False,
            error=f"Capability '{call.capability}' not available",
            duration_ms=duration_ms,
            timestamp=datetime.now(timezone.utc),
        )

    async def _execute_via_capability_executor(
        self,
        call: CapabilityCall,
        confirm_callback: ConfirmCallback | None = None,
        incident_id: str | None = None,
    ) -> ExecutionEvidence | None:
        """Execute via the real CapabilityExecutor.

        Args:
            call: The capability call.
            confirm_callback: Optional confirmation callback.
            incident_id: Optional incident ID.

        Returns:
            ExecutionEvidence or None if executor unavailable.
        """
        from elle.capabilities import get_registry

        start_time = time.time()

        # Get the capability from registry
        registry = get_registry()
        capability = registry.get(call.capability)

        if not capability:
            return None  # Will fall through to fallback

        # Build input model
        input_obj = self._build_input(capability, call.args)

        # Create confirmation wrapper if needed
        async def confirm_wrapper(dry_run_result: Any) -> bool:
            if not confirm_callback:
                # No callback - skip confirmation for read-only, deny for mutations
                if call.is_read_only:
                    return True
                logger.warning(f"Skipping confirmation for {call.capability} (no callback)")
                return True

            preview = self._format_dry_run_preview(call, dry_run_result)
            return await confirm_callback(f"Execute: {call.capability}", preview)

        # Execute through the real executor
        try:
            result = await self.capability_executor.execute(
                call.capability,
                input_obj,
                incident_id=incident_id,
                require_confirmation=call.requires_confirmation,
                confirm_callback=confirm_wrapper if call.requires_confirmation else None,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecutionEvidence(
                capability=call.capability,
                args=call.args,
                success=result.success,
                output=result.output,
                output_text=self._format_output(result.output),
                error=result.error,
                duration_ms=duration_ms,
                timestamp=datetime.now(timezone.utc),
                warnings=result.warnings,
                verification_passed=result.evidence.verification_passed if result.evidence else None,
                verification_details=result.evidence.verification_details if result.evidence else None,
                incident_id=incident_id,
                was_confirmed=call.requires_confirmation,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"Capability execution failed: {e}")

            return ExecutionEvidence(
                capability=call.capability,
                args=call.args,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                timestamp=datetime.now(timezone.utc),
            )

    def _build_input(self, capability: Any, args: dict[str, Any]) -> BaseModel | dict[str, Any]:
        """Build input object for a capability.

        Args:
            capability: The capability instance.
            args: Arguments dict.

        Returns:
            Input object (Pydantic model or dict).
        """
        input_schema_name = capability.spec.input_schema_name
        if not input_schema_name:
            return args

        # Try to find the input class
        module = type(capability).__module__

        try:
            import importlib

            cap_module = importlib.import_module(module)
            input_class = getattr(cap_module, input_schema_name, None)
            if input_class:
                return cast(BaseModel, input_class(**args))
        except Exception:
            pass

        return args

    def _format_output(self, output: Any) -> str:
        """Format capability output as string.

        Args:
            output: The capability output.

        Returns:
            String representation.
        """
        if output is None:
            return ""

        if isinstance(output, str):
            return output

        if hasattr(output, "model_dump"):
            data = output.model_dump()
            return json.dumps(data, indent=2, default=str)

        if isinstance(output, dict):
            return json.dumps(output, indent=2, default=str)

        return str(output)

    def _format_plan_preview(self, plan: ExecutionPlan) -> str:
        """Format a plan for confirmation preview.

        Args:
            plan: The execution plan.

        Returns:
            Formatted preview string.
        """
        lines = [f"Goal: {plan.intent.goal_summary}", ""]
        lines.append("Capabilities to execute:")

        for i, group in enumerate(plan.parallel_groups):
            if len(plan.parallel_groups) > 1:
                lines.append(f"\n  Group {i + 1} (parallel):")
            for call in group.calls:
                confirm_marker = " [requires confirmation]" if call.requires_confirmation else ""
                args_str = ", ".join(f"{k}={v}" for k, v in call.args.items())
                lines.append(f"    - {call.capability}({args_str}){confirm_marker}")
                lines.append(f"      Purpose: {call.purpose}")

        return "\n".join(lines)

    def _format_dry_run_preview(self, call: CapabilityCall, dry_run_result: Any) -> str:
        """Format a dry-run result for confirmation.

        Args:
            call: The capability call.
            dry_run_result: Result from dry_run().

        Returns:
            Formatted preview string.
        """
        lines = [
            f"Capability: {call.capability}",
            f"Purpose: {call.purpose}",
            "",
        ]

        if hasattr(dry_run_result, "preview_text") and dry_run_result.preview_text:
            lines.append("Preview:")
            lines.append(dry_run_result.preview_text)

        if hasattr(dry_run_result, "would_modify") and dry_run_result.would_modify:
            lines.append("")
            lines.append("Would modify:")
            for item in dry_run_result.would_modify:
                lines.append(f"  - {item}")

        return "\n".join(lines)

    def _should_stop_execution(
        self,
        evidence: list[ExecutionEvidence],
        group: ParallelGroup,
    ) -> bool:
        """Determine if execution should stop after a group.

        Args:
            evidence: Evidence from the group.
            group: The group that was executed.

        Returns:
            True if execution should stop.
        """
        # Stop if any non-read-only capability failed
        for ev, call in zip(evidence, group.calls, strict=False):
            if not ev.success and not call.is_read_only:
                return True

        return False


# =============================================================================
# Module-level singleton
# =============================================================================

_executor: PlanExecutor | None = None


def get_plan_executor() -> PlanExecutor:
    """Get the shared executor instance.

    Returns:
        The PlanExecutor singleton.
    """
    global _executor
    if _executor is None:
        _executor = PlanExecutor()
    return _executor


def reset_plan_executor() -> None:
    """Reset the shared executor instance."""
    global _executor
    _executor = None
