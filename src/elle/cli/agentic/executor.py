"""GatherExecutor - Executes read-only capabilities to gather information.

Safely executes capability calls to collect evidence for answering questions.
Only allows read-only capabilities (risk level 'none' or 'low').
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from elle.cli.agentic.models import (
    CapabilityCall,
    GatheredEvidence,
    GatherPlan,
    GatherResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Read-Only Capability Registry
# =============================================================================

# Capabilities that are safe for autonomous execution (no system mutations)
# These must have risk level 'none' or 'low' and no destructive side effects
READ_ONLY_CAPABILITIES: frozenset[str] = frozenset(
    {
        # Service domain
        "service.status",
        "service.logs",
        # File domain
        "file.read",
        "file.stat",
        "file.diff",
        "file.watch_snapshot",
        # Package domain
        "package.info",
        "package.detect-conflicts",
        # Docker domain
        "docker.list",
        "docker.inspect",
        "docker.logs",
        # Network domain
        "network.listeners",
        "network.connections",
        # Config domain
        "config.preview",
        # System domain
        "system.info",
        "system.resources",
    }
)


# =============================================================================
# GatherExecutor
# =============================================================================


class GatherExecutor:
    """Executes read-only capabilities to gather information.

    Enforces policy by only allowing execution of known read-only capabilities.
    Collects evidence from each execution for response synthesis.
    """

    def __init__(
        self,
        allowed_capabilities: frozenset[str] | None = None,
        max_parallel: int = 5,
    ) -> None:
        """Initialize the executor.

        Args:
            allowed_capabilities: Set of capabilities allowed for execution.
                Defaults to READ_ONLY_CAPABILITIES.
            max_parallel: Maximum parallel capability executions (unused for now).
        """
        self.allowed_capabilities = allowed_capabilities or READ_ONLY_CAPABILITIES
        self.max_parallel = max_parallel

    async def execute(self, plan: GatherPlan) -> GatherResult:
        """Execute a gather plan and collect evidence.

        Args:
            plan: The plan to execute.

        Returns:
            GatherResult with all collected evidence.
        """
        if not plan.calls:
            return GatherResult(
                plan=plan,
                evidence=(),
                sufficient=False,
                missing=("No capabilities to execute",),
            )

        evidence: list[GatheredEvidence] = []
        errors: list[str] = []

        for call in plan.calls:
            result = await self._execute_call(call)
            evidence.append(result)

            if not result.success and result.error:
                errors.append(f"{call.capability}: {result.error}")

        # Determine sufficiency based on successful evidence
        successful_evidence = [e for e in evidence if e.success]
        sufficient = len(successful_evidence) > 0

        return GatherResult(
            plan=plan,
            evidence=tuple(evidence),
            sufficient=sufficient,
            missing=tuple(errors) if errors else (),
        )

    async def _execute_call(self, call: CapabilityCall) -> GatheredEvidence:
        """Execute a single capability call.

        Args:
            call: The capability call to execute.

        Returns:
            GatheredEvidence from the execution.
        """
        start_time = time.time()

        # Check if capability is allowed
        if call.capability not in self.allowed_capabilities:
            return GatheredEvidence(
                capability=call.capability,
                args=call.args,
                success=False,
                output=None,
                error=f"Capability '{call.capability}' is not allowed for autonomous execution",
                duration_ms=0,
                timestamp=datetime.utcnow(),
            )

        try:
            # Execute the capability
            output, error = await self._run_capability(call.capability, call.args)
            duration_ms = int((time.time() - start_time) * 1000)

            return GatheredEvidence(
                capability=call.capability,
                args=call.args,
                success=error is None,
                output=output,
                error=error,
                duration_ms=duration_ms,
                timestamp=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning(f"Capability execution failed: {call.capability}: {e}")
            duration_ms = int((time.time() - start_time) * 1000)

            return GatheredEvidence(
                capability=call.capability,
                args=call.args,
                success=False,
                output=None,
                error=str(e),
                duration_ms=duration_ms,
                timestamp=datetime.utcnow(),
            )

    async def _run_capability(
        self,
        capability_name: str,
        args: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Run a capability and return output/error.

        Args:
            capability_name: Name of the capability to run.
            args: Arguments for the capability.

        Returns:
            Tuple of (output_string, error_string).
        """
        # Try to get the capability from registry
        try:
            from elle.capabilities import get_registry

            registry = get_registry()
            capability = registry.get(capability_name)

            if capability is None:
                # Capability not found - try fallback execution
                return await self._fallback_execution(capability_name, args)

            # Build input from args
            input_obj = self._build_input(capability, args)

            # Execute
            result = capability.run(input_obj)

            if result.success:
                output_str = self._format_output(result.output)
                return (output_str, None)
            else:
                return (None, result.error or "Capability failed")

        except ImportError:
            # Capability system not available - use fallback
            return await self._fallback_execution(capability_name, args)
        except Exception as e:
            return (None, str(e))

    def _build_input(self, capability: Any, args: dict[str, Any]) -> Any:
        """Build input object for a capability.

        Args:
            capability: The capability instance.
            args: Arguments dict.

        Returns:
            Input object for the capability.
        """
        # Get input schema from capability spec
        input_schema_name = capability.spec.input_schema_name
        if not input_schema_name:
            # No input schema - pass args directly if capability accepts them
            return args

        # Try to find the input class in the capability's module
        module = type(capability).__module__

        try:
            import importlib

            cap_module = importlib.import_module(module)
            input_class = getattr(cap_module, input_schema_name, None)

            if input_class:
                return input_class(**args)
        except Exception:
            pass

        # Fallback: return args dict
        return args

    def _format_output(self, output: Any) -> str:
        """Format capability output as string.

        Args:
            output: The capability output.

        Returns:
            String representation of the output.
        """
        if output is None:
            return ""

        if isinstance(output, str):
            return output

        # Handle Pydantic models
        if hasattr(output, "model_dump"):
            import json

            data = output.model_dump()
            return json.dumps(data, indent=2, default=str)

        # Handle dicts
        if isinstance(output, dict):
            import json

            return json.dumps(output, indent=2, default=str)

        # Default to string conversion
        return str(output)

    async def _fallback_execution(
        self,
        capability_name: str,
        args: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Fallback execution for capabilities not in registry.

        Uses direct subprocess calls for core capabilities.

        Args:
            capability_name: Name of the capability.
            args: Arguments dict.

        Returns:
            Tuple of (output_string, error_string).
        """
        from elle.cli.subprocess_runner import RunMode, run_safe

        # Map capabilities to commands
        if capability_name == "service.status":
            service = args.get("service", "")
            if not service:
                return (None, "Missing service name")

            result = run_safe(
                f"systemctl show {service} --property=ActiveState,SubState,MainPID,Description,LoadState",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, result.stderr or "Failed to get service status")

        elif capability_name == "service.logs":
            service = args.get("service", "")
            lines = args.get("lines", 50)
            if not service:
                return (None, "Missing service name")

            result = run_safe(
                f"journalctl -u {service} -n {lines} --no-pager",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, result.stderr or "Failed to get service logs")

        elif capability_name == "file.read":
            path = args.get("path", "")
            if not path:
                return (None, "Missing file path")

            try:
                with open(path) as f:
                    content = f.read()
                return (content, None)
            except Exception as e:
                return (None, str(e))

        elif capability_name == "package.info":
            package = args.get("package", "")
            if not package:
                return (None, "Missing package name")

            result = run_safe(
                f"dpkg-query -W -f='${{Status}} ${{Version}}\\n${{Description}}' {package} 2>/dev/null || apt-cache show {package} 2>/dev/null | head -20",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, f"Package '{package}' not found")

        elif capability_name == "docker.list":
            result = run_safe(
                "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, result.stderr or "Failed to list containers")

        elif capability_name == "docker.inspect":
            name = args.get("name", "")
            if not name:
                return (None, "Missing container name")

            result = run_safe(
                f"docker inspect {name} --format '{{{{.State.Status}}}} - {{{{.State.StartedAt}}}}'",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, result.stderr or f"Container '{name}' not found")

        elif capability_name == "docker.logs":
            name = args.get("name", "")
            lines = args.get("lines", 50)
            if not name:
                return (None, "Missing container name")

            result = run_safe(
                f"docker logs {name} --tail {lines}",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout or result.stderr, None)
            return (None, result.stderr or f"Failed to get logs for '{name}'")

        elif capability_name == "network.listeners":
            result = run_safe(
                "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, "Failed to list listeners")

        elif capability_name == "system.info":
            result = run_safe(
                "uname -a && cat /etc/os-release 2>/dev/null | head -5",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, "Failed to get system info")

        elif capability_name == "system.resources":
            result = run_safe(
                "free -h && echo '---' && df -h / && echo '---' && uptime",
                timeout=10.0,
                mode=RunMode.CAPTURE,
            )
            if result.success:
                return (result.stdout, None)
            return (None, "Failed to get system resources")

        return (None, f"Unknown capability: {capability_name}")


# =============================================================================
# Module-level singleton
# =============================================================================

_executor: GatherExecutor | None = None


def get_executor() -> GatherExecutor:
    """Get the shared executor instance.

    Returns:
        The GatherExecutor singleton.
    """
    global _executor
    if _executor is None:
        _executor = GatherExecutor()
    return _executor


def reset_executor() -> None:
    """Reset the shared executor instance."""
    global _executor
    _executor = None
