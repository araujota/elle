"""LLM-based capability specification generator.

Uses Man Vault grounding and LLM to generate capability specifications
from parsed man pages.
"""

from __future__ import annotations

import json
import logging

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    InputFieldSpec,
    OutputFieldSpec,
    ParsedManPage,
)
from elle.capabilities.autogen.prompts import (
    CAPABILITY_GEN_SYSTEM_PROMPT,
    build_generation_prompt,
    build_subcommand_prompt,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Default Output Fields
# =============================================================================

DEFAULT_OUTPUT_FIELDS = (
    OutputFieldSpec(
        name="success",
        field_type="bool",
        description="Whether the operation succeeded",
    ),
    OutputFieldSpec(
        name="output",
        field_type="str",
        description="Command output or result",
    ),
    OutputFieldSpec(
        name="return_code",
        field_type="int",
        description="Command return code",
    ),
)


# =============================================================================
# Generator
# =============================================================================


class CapabilityGenerator:
    """Generates capability specifications from man pages using LLM.

    Uses Ollama for local inference with Man Vault context for grounding.
    """

    def __init__(
        self,
        model: str = "llama3.2",
        temperature: float = 0.3,
    ) -> None:
        """Initialize the generator.

        Args:
            model: Ollama model to use.
            temperature: Generation temperature (lower = more deterministic).
        """
        self.model = model
        self.temperature = temperature

    async def generate(
        self,
        man_page: ParsedManPage,
        subcommand: str | None = None,
    ) -> GeneratedCapabilitySpec | None:
        """Generate a capability spec from a man page.

        Args:
            man_page: Parsed man page.
            subcommand: Optional specific subcommand to focus on.

        Returns:
            GeneratedCapabilitySpec or None if generation fails.
        """
        # Build prompt
        if subcommand:
            user_prompt = build_subcommand_prompt(man_page, subcommand)
        else:
            user_prompt = build_generation_prompt(man_page)

        # Call LLM
        try:
            response = await self._call_llm(
                system=CAPABILITY_GEN_SYSTEM_PROMPT,
                user=user_prompt,
            )

            if not response:
                logger.warning(f"Empty LLM response for {man_page.name}")
                return None

            # Parse JSON response
            spec = self._parse_response(response, man_page, subcommand)
            return spec

        except Exception as e:
            logger.error(f"Generation failed for {man_page.name}: {e}")
            return None

    async def _call_llm(
        self,
        system: str,
        user: str,
    ) -> str | None:
        """Call the LLM with given prompts.

        Args:
            system: System prompt.
            user: User prompt.

        Returns:
            LLM response or None.
        """
        try:
            from elle.rag.llm import generate_json

            result = await generate_json(
                prompt=user,
                system=system,
                model=self.model,
                temperature=self.temperature,
            )

            if result:
                return json.dumps(result)
            return None

        except ImportError:
            # Fallback to direct Ollama call
            return await self._call_ollama_direct(system, user)

    async def _call_ollama_direct(
        self,
        system: str,
        user: str,
    ) -> str | None:
        """Direct Ollama API call fallback.

        Args:
            system: System prompt.
            user: User prompt.

        Returns:
            LLM response or None.
        """
        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": self.model,
                        "prompt": user,
                        "system": system,
                        "format": "json",
                        "options": {
                            "temperature": self.temperature,
                        },
                        "stream": False,
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("response")

        except Exception as e:
            logger.debug(f"Ollama call failed: {e}")

        return None

    def _parse_response(
        self,
        response: str,
        man_page: ParsedManPage,
        subcommand: str | None,
    ) -> GeneratedCapabilitySpec | None:
        """Parse LLM response into a spec.

        Args:
            response: JSON string from LLM.
            man_page: Original man page for defaults.
            subcommand: Subcommand if applicable.

        Returns:
            GeneratedCapabilitySpec or None.
        """
        try:
            # Extract JSON from response (may have markdown code blocks)
            json_str = response
            if "```" in response:
                # Extract JSON from code block
                start = response.find("```")
                end = response.rfind("```")
                if start != end:
                    json_str = response[start:end]
                    # Remove ```json prefix
                    json_str = json_str.lstrip("`").lstrip("json").strip()

            data = json.loads(json_str)

            # Build input fields
            input_fields = []
            for field_data in data.get("input_fields", []):
                input_fields.append(
                    InputFieldSpec(
                        name=field_data["name"],
                        field_type=field_data.get("field_type", "str"),
                        description=field_data.get("description", ""),
                        required=field_data.get("required", True),
                        default=field_data.get("default"),
                        constraints=field_data.get("constraints", {}),
                    )
                )

            # Build output fields (use defaults if not provided)
            output_fields = []
            for field_data in data.get("output_fields", []):
                output_fields.append(
                    OutputFieldSpec(
                        name=field_data["name"],
                        field_type=field_data.get("field_type", "str"),
                        description=field_data.get("description", ""),
                    )
                )
            if not output_fields:
                output_fields = list(DEFAULT_OUTPUT_FIELDS)

            # Build capability name
            name = data.get("name", "")
            if not name:
                if subcommand:
                    name = f"{man_page.name}.{subcommand.replace('-', '_')}"
                else:
                    name = f"{man_page.name}.default"

            return GeneratedCapabilitySpec(
                name=name,
                display_name=data.get("display_name", name.replace(".", " ").title()),
                description=data.get("description", man_page.description[:200]),
                domain=data.get("domain", "file"),
                risk_level=data.get("risk_level", "medium"),
                side_effects=tuple(data.get("side_effects", [])),
                input_fields=tuple(input_fields),
                output_fields=tuple(output_fields),
                command_template=data.get("command_template", man_page.name),
                verify_command=data.get("verify_command"),
                rollback_command=data.get("rollback_command"),
                source_command=man_page.name,
                source_man_section=man_page.section,
                confidence_score=float(data.get("confidence_score", 0.5)),
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Response was: {response[:500]}")
        except Exception as e:
            logger.warning(f"Failed to build spec from response: {e}")

        return None


# =============================================================================
# Fallback Rule-Based Generator
# =============================================================================


def generate_basic_spec(man_page: ParsedManPage) -> GeneratedCapabilitySpec:
    """Generate a basic spec without LLM (fallback).

    Args:
        man_page: Parsed man page.

    Returns:
        Basic GeneratedCapabilitySpec.
    """
    # Infer domain from command name
    domain = "file"
    if man_page.name in ("systemctl", "service", "journalctl"):
        domain = "service"
    elif man_page.name in ("docker", "podman", "crictl"):
        domain = "docker"
    elif man_page.name in ("ip", "ss", "netplan", "ufw", "wg"):
        domain = "network"
    elif man_page.name in ("apt", "apt-get", "dpkg", "snap"):
        domain = "package"
    elif man_page.name in ("useradd", "usermod", "passwd", "chmod", "chown"):
        domain = "auth"

    # Infer risk level
    risk = "medium"
    if man_page.name in ("cat", "ls", "ps", "df", "free", "uname"):
        risk = "none"
    elif man_page.name in ("rm", "dd", "mkfs", "fdisk"):
        risk = "critical"

    # Build basic input fields from positional args
    input_fields = []
    if man_page.synopsis and man_page.synopsis.positional_args:
        for arg in man_page.synopsis.positional_args[:3]:
            input_fields.append(
                InputFieldSpec(
                    name=arg.replace("-", "_"),
                    field_type="str",
                    description=f"{arg} argument",
                    required=True,
                )
            )

    return GeneratedCapabilitySpec(
        name=f"{man_page.name}.default",
        display_name=man_page.name.title(),
        description=man_page.description[:200] or f"Run {man_page.name} command",
        domain=domain,
        risk_level=risk,
        side_effects=(),
        input_fields=tuple(input_fields),
        output_fields=DEFAULT_OUTPUT_FIELDS,
        command_template=man_page.name + " " + " ".join(f"{{{f.name}}}" for f in input_fields),
        source_command=man_page.name,
        source_man_section=man_page.section,
        confidence_score=0.3,
    )


# =============================================================================
# Module-level generator
# =============================================================================

_generator: CapabilityGenerator | None = None


def get_generator() -> CapabilityGenerator:
    """Get the shared generator instance."""
    global _generator
    if _generator is None:
        _generator = CapabilityGenerator()
    return _generator


async def generate_capability_spec(
    man_page: ParsedManPage,
    subcommand: str | None = None,
    use_llm: bool = True,
) -> GeneratedCapabilitySpec | None:
    """Generate a capability spec from a man page.

    Convenience function for single-shot generation.

    Args:
        man_page: Parsed man page.
        subcommand: Optional subcommand to focus on.
        use_llm: Whether to use LLM (falls back to rules if False).

    Returns:
        GeneratedCapabilitySpec or None.
    """
    if use_llm:
        generator = get_generator()
        spec = await generator.generate(man_page, subcommand)
        if spec:
            return spec

    # Fallback to rule-based generation
    return generate_basic_spec(man_page)
