"""ResponseSynthesizer - Converts gathered evidence into natural language.

Uses the LLM to synthesize evidence from capability executions into
a clear, concise answer to the user's question.
"""

from __future__ import annotations

import logging

from elle.cli.agentic.models import (
    AgenticResponse,
    ExecutionEvidence,
    ExecutionResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Follow-up Suggestion Rules
# =============================================================================

# Maps capability to potential follow-up suggestions
FOLLOW_UP_SUGGESTIONS: dict[str, list[str]] = {
    "service.status": [
        "Check service logs with 'show {target} logs'",
        "Restart the service with '/do restart {target}'",
    ],
    "service.logs": [
        "Check service status with 'is {target} running?'",
        "View more logs with 'journalctl -u {target}'",
    ],
    "file.read": [
        "Edit the file with '/do edit {target}'",
        "Check file permissions with 'ls -la {target}'",
    ],
    "package.info": [
        "Install the package with '/do install {target}'",
        "Check package dependencies",
    ],
    "docker.list": [
        "Inspect a specific container",
        "View container logs",
    ],
    "docker.inspect": [
        "View container logs with 'show {target} logs'",
        "Restart container with '/do restart container {target}'",
    ],
    "network.listeners": [
        "Check which process uses a specific port",
        "View network connections",
    ],
    "system.resources": [
        "Check which processes use most CPU/memory with 'top' or 'htop'",
        "Free up disk space",
    ],
}


# =============================================================================
# ResponseSynthesizer
# =============================================================================


class ResponseSynthesizer:
    """Synthesizes evidence into a natural language response.

    Uses the LLM to generate a clear, concise answer based on
    the gathered evidence. Falls back to rule-based formatting
    if the LLM is unavailable.
    """

    def __init__(self, use_llm: bool = True) -> None:
        """Initialize the synthesizer.

        Args:
            use_llm: Whether to use LLM for synthesis.
        """
        self.use_llm = use_llm

    async def synthesize(
        self,
        question: str,
        result: ExecutionResult,
    ) -> AgenticResponse:
        """Synthesize evidence into a response.

        Args:
            question: The original question.
            result: The gather result with evidence.

        Returns:
            AgenticResponse with the answer.
        """
        if not result.evidence:
            return AgenticResponse(
                answer="I couldn't gather any information to answer your question.",
                evidence=(),
                confidence=0.0,
                follow_up_suggestions=(),
            )

        # Filter to successful evidence
        successful_evidence = tuple(e for e in result.evidence if e.success)

        if not successful_evidence:
            # All capabilities failed - report errors
            errors = [e.error or f"{e.capability} failed" for e in result.evidence if not e.success]
            return AgenticResponse(
                answer=f"I couldn't retrieve the information: {'; '.join(errors)}",
                evidence=result.evidence,
                confidence=0.0,
                follow_up_suggestions=(),
            )

        # Try LLM synthesis first
        if self.use_llm:
            try:
                answer = await self._synthesize_with_llm(question, successful_evidence)
                if answer:
                    confidence = self._calculate_confidence(result)
                    suggestions = self._generate_suggestions(question, result)

                    return AgenticResponse(
                        answer=answer,
                        evidence=result.evidence,
                        confidence=confidence,
                        follow_up_suggestions=suggestions,
                    )
            except Exception as e:
                logger.warning(f"LLM synthesis failed, using fallback: {e}")

        # Fall back to rule-based synthesis
        answer = self._synthesize_with_rules(question, successful_evidence)
        confidence = self._calculate_confidence(result)
        suggestions = self._generate_suggestions(question, result)

        return AgenticResponse(
            answer=answer,
            evidence=result.evidence,
            confidence=confidence,
            follow_up_suggestions=suggestions,
        )

    async def _synthesize_with_llm(
        self,
        question: str,
        evidence: tuple[ExecutionEvidence, ...],
    ) -> str | None:
        """Synthesize using LLM.

        Args:
            question: The original question.
            evidence: Successful evidence to use.

        Returns:
            Synthesized answer or None if LLM unavailable.
        """
        from elle.rag.llm import get_llm

        llm = get_llm()
        if not llm.is_available():
            return None

        # Format evidence for the prompt
        evidence_text = self._format_evidence(evidence)

        prompt = f"""Based on the following evidence gathered from system commands, answer the user's question.

Question: {question}

Evidence:
{evidence_text}

Instructions:
- Be concise and direct - get to the point quickly
- Include specific values from the evidence (status, PIDs, versions, etc.)
- If the evidence is incomplete, mention what's missing
- Don't explain how you gathered the information
- Use plain language, not technical jargon unless necessary
- Keep your answer to 2-3 sentences unless more detail is needed"""

        try:
            response = llm.generate(
                prompt,
                max_tokens=300,
                temperature=0.3,
            )
            return response.content.strip() if response and response.content else None
        except Exception as e:
            logger.warning(f"LLM generation failed: {e}")
            return None

    def _synthesize_with_rules(
        self,
        question: str,
        evidence: tuple[ExecutionEvidence, ...],
    ) -> str:
        """Synthesize using rule-based formatting.

        Args:
            question: The original question.
            evidence: Successful evidence to use.

        Returns:
            Formatted answer string.
        """
        parts: list[str] = []

        for ev in evidence:
            formatted = self._format_evidence_item(ev)
            if formatted:
                parts.append(formatted)

        if not parts:
            return "No information could be retrieved."

        return "\n\n".join(parts)

    def _format_evidence(self, evidence: tuple[ExecutionEvidence, ...]) -> str:
        """Format evidence for LLM prompt.

        Args:
            evidence: Evidence to format.

        Returns:
            Formatted evidence string.
        """
        sections: list[str] = []

        for ev in evidence:
            section = f"[{ev.capability}]"
            if ev.args:
                args_str = ", ".join(f"{k}={v}" for k, v in ev.args.items())
                section += f" ({args_str})"
            section += f"\n{ev.output_text or ev.output or '(no output)'}"
            sections.append(section)

        return "\n\n".join(sections)

    def _format_evidence_item(self, evidence: ExecutionEvidence) -> str:
        """Format a single evidence item for display.

        Args:
            evidence: The evidence item.

        Returns:
            Formatted string.
        """
        output_str = evidence.output_text or (str(evidence.output) if evidence.output else None)
        if not output_str:
            return ""

        capability = evidence.capability
        output = output_str.strip()

        # Truncate very long outputs
        if len(output) > 1000:
            output = output[:997] + "..."

        # Format based on capability type
        if capability == "service.status":
            return self._format_service_status(evidence.args, output)
        elif capability == "package.info":
            return self._format_package_info(evidence.args, output)
        elif capability == "docker.list":
            return f"Docker containers:\n{output}"
        elif capability == "network.listeners":
            return f"Listening ports:\n{output}"
        elif capability == "system.resources":
            return f"System resources:\n{output}"
        else:
            return output

    def _format_service_status(self, args: dict[str, str], output: str) -> str:
        """Format service status output.

        Args:
            args: Capability arguments.
            output: Raw output.

        Returns:
            Formatted status string.
        """
        service = args.get("service", "unknown")

        # Parse systemctl show output
        props = {}
        for line in output.split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                props[key.strip()] = value.strip()

        active_state = props.get("ActiveState", "unknown")
        sub_state = props.get("SubState", "")
        pid = props.get("MainPID", "")
        description = props.get("Description", "")

        status_parts = [f"{service}"]

        if description:
            status_parts[0] += f" ({description})"

        status_parts.append(f"is {active_state}")

        if sub_state:
            status_parts[-1] += f" ({sub_state})"

        if pid and pid != "0":
            status_parts.append(f"PID: {pid}")

        return " ".join(status_parts) + "."

    def _format_package_info(self, args: dict[str, str], output: str) -> str:
        """Format package info output.

        Args:
            args: Capability arguments.
            output: Raw output.

        Returns:
            Formatted package info string.
        """
        package = args.get("package", "unknown")

        if "install ok installed" in output:
            # Extract version from dpkg output
            parts = output.split()
            version = parts[-1] if len(parts) > 3 else "unknown"
            return f"{package} is installed (version {version})."
        elif "deinstall" in output or "not-installed" in output:
            return f"{package} is not installed."
        else:
            # apt-cache show output
            lines = output.split("\n")
            version = ""
            desc = ""
            for line in lines:
                if line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                elif line.startswith("Description:"):
                    desc = line.split(":", 1)[1].strip()

            if version:
                return f"{package} version {version} is available." + (f" {desc}" if desc else "")

            return output[:200]

    def _calculate_confidence(self, result: ExecutionResult) -> float:
        """Calculate confidence in the answer.

        Args:
            result: The gather result.

        Returns:
            Confidence score between 0 and 1.
        """
        if not result.evidence:
            return 0.0

        successful = sum(1 for e in result.evidence if e.success)
        total = len(result.evidence)

        # Base confidence on success rate
        success_rate = successful / total if total > 0 else 0.0

        # Adjust based on success
        if result.all_succeeded:
            return min(1.0, success_rate + 0.1)
        else:
            return max(0.0, success_rate - 0.2)

    def _generate_suggestions(
        self,
        question: str,
        result: ExecutionResult,
    ) -> tuple[str, ...]:
        """Generate follow-up suggestions.

        Args:
            question: The original question.
            result: The gather result.

        Returns:
            Tuple of suggestion strings.
        """
        suggestions: list[str] = []

        for evidence in result.evidence:
            if not evidence.success:
                continue

            cap_suggestions = FOLLOW_UP_SUGGESTIONS.get(evidence.capability, [])

            # Get target from args
            target = ""
            for key in ("service", "name", "package", "path"):
                if key in evidence.args:
                    target = str(evidence.args[key])
                    break

            for suggestion in cap_suggestions[:1]:  # Take first suggestion only
                if target and "{target}" in suggestion:
                    suggestions.append(suggestion.replace("{target}", target))
                elif "{target}" not in suggestion:
                    suggestions.append(suggestion)

        return tuple(suggestions[:3])  # Limit to 3 suggestions


# =============================================================================
# Module-level singleton
# =============================================================================

_synthesizer: ResponseSynthesizer | None = None


def get_synthesizer() -> ResponseSynthesizer:
    """Get the shared synthesizer instance.

    Returns:
        The ResponseSynthesizer singleton.
    """
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = ResponseSynthesizer()
    return _synthesizer


def reset_synthesizer() -> None:
    """Reset the shared synthesizer instance."""
    global _synthesizer
    _synthesizer = None
