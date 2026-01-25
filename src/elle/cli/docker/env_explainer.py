"""Docker Environment Variable Explainer.

Provides LLM-powered explanations for Docker environment variables
when profiles don't cover them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from elle.cli.docker.env_profiles import extract_image_family, get_profile

if TYPE_CHECKING:
    from elle.rag.llm import LLM


logger = logging.getLogger(__name__)


class EnvVarExplanation(BaseModel):
    """Explanation for an environment variable."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Environment variable name")
    description: str = Field(description="Human-readable explanation")
    is_sensitive: bool = Field(
        default=False,
        description="Whether this appears to be a sensitive value",
    )
    example: str | None = Field(
        default=None,
        description="Example value if available",
    )
    source: str = Field(
        default="llm",
        description="Where this explanation came from",
    )


# System prompt for environment variable explanations
ENV_EXPLAINER_SYSTEM = """You are a Docker expert. Explain Docker environment variables concisely.

For each variable, provide:
1. A brief description (1-2 sentences) of what it does
2. Whether it contains sensitive data (passwords, secrets, API keys)
3. An example value if helpful

Respond in JSON format:
{
    "description": "Brief explanation",
    "is_sensitive": true/false,
    "example": "example value or null"
}"""


class EnvVarExplainer:
    """LLM-powered explainer for Docker environment variables.

    Uses the local LLM to generate explanations for environment
    variables that aren't covered by built-in profiles.

    Example:
        from elle.rag.llm import get_llm

        explainer = EnvVarExplainer(llm=get_llm())
        explanation = explainer.explain("MY_CUSTOM_VAR", "myimage:latest")
        print(explanation)
    """

    def __init__(
        self,
        llm: "LLM | None" = None,
        *,
        timeout: float = 30.0,
        max_tokens: int = 256,
    ) -> None:
        """Initialize the explainer.

        Args:
            llm: LLM instance for explanations (lazy-loads if None).
            timeout: Timeout for LLM requests.
            max_tokens: Max tokens for explanations.
        """
        self._llm = llm
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._cache: dict[tuple[str, str], EnvVarExplanation] = {}

    @property
    def llm(self) -> "LLM":
        """Get or create the LLM instance."""
        if self._llm is None:
            from elle.rag.llm import get_llm

            self._llm = get_llm()
        return self._llm

    def explain(
        self,
        var_name: str,
        image: str,
        *,
        force_llm: bool = False,
    ) -> str:
        """Get a human-readable explanation for an environment variable.

        First checks built-in profiles, then falls back to LLM.

        Args:
            var_name: Environment variable name.
            image: Docker image name.
            force_llm: Force LLM even if profile exists.

        Returns:
            Human-readable explanation string.
        """
        explanation = self.get_explanation(var_name, image, force_llm=force_llm)
        if explanation:
            return explanation.description
        return ""

    def get_explanation(
        self,
        var_name: str,
        image: str,
        *,
        force_llm: bool = False,
    ) -> EnvVarExplanation | None:
        """Get a structured explanation for an environment variable.

        Args:
            var_name: Environment variable name.
            image: Docker image name.
            force_llm: Force LLM even if profile exists.

        Returns:
            EnvVarExplanation if available, None otherwise.
        """
        # Check cache first
        cache_key = (var_name, extract_image_family(image))
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Check built-in profiles
        if not force_llm:
            family = extract_image_family(image)
            profile = get_profile(family)
            if profile:
                for spec in profile.env_vars:
                    if spec.name == var_name:
                        explanation = EnvVarExplanation(
                            name=var_name,
                            description=spec.description,
                            is_sensitive=spec.sensitive,
                            example=spec.example,
                            source="profile",
                        )
                        self._cache[cache_key] = explanation
                        return explanation

        # Fall back to LLM
        try:
            explanation = self._explain_with_llm(var_name, image)
            if explanation:
                self._cache[cache_key] = explanation
                return explanation
        except Exception as e:
            logger.warning(f"LLM explanation failed for {var_name}: {e}")

        return None

    def _explain_with_llm(
        self,
        var_name: str,
        image: str,
    ) -> EnvVarExplanation | None:
        """Get explanation from LLM.

        Args:
            var_name: Environment variable name.
            image: Docker image name.

        Returns:
            EnvVarExplanation if successful, None otherwise.
        """
        if not self.llm.is_available():
            return None

        prompt = (
            f"Explain the Docker environment variable '{var_name}' "
            f"for the image '{image}'.\n\n"
            f"If you don't know this specific variable, make a reasonable guess "
            f"based on the naming convention."
        )

        try:
            result = self.llm.generate_json(
                prompt,
                system=ENV_EXPLAINER_SYSTEM,
                max_tokens=self._max_tokens,
                timeout=self._timeout,
                temperature=0.3,  # Lower for more factual responses
            )

            return EnvVarExplanation(
                name=var_name,
                description=result.get("description", ""),
                is_sensitive=result.get("is_sensitive", False),
                example=result.get("example"),
                source="llm",
            )

        except Exception as e:
            logger.debug(f"LLM explanation error: {e}")
            return None

    def explain_batch(
        self,
        var_names: list[str],
        image: str,
    ) -> dict[str, EnvVarExplanation]:
        """Get explanations for multiple variables.

        More efficient than calling explain() repeatedly as it
        batches the LLM calls.

        Args:
            var_names: List of environment variable names.
            image: Docker image name.

        Returns:
            Dict mapping variable names to explanations.
        """
        results: dict[str, EnvVarExplanation] = {}
        needs_llm: list[str] = []

        # Check profiles and cache first
        family = extract_image_family(image)
        profile = get_profile(family)

        for var_name in var_names:
            # Check cache
            cache_key = (var_name, family)
            if cache_key in self._cache:
                results[var_name] = self._cache[cache_key]
                continue

            # Check profile
            if profile:
                for spec in profile.env_vars:
                    if spec.name == var_name:
                        explanation = EnvVarExplanation(
                            name=var_name,
                            description=spec.description,
                            is_sensitive=spec.sensitive,
                            example=spec.example,
                            source="profile",
                        )
                        self._cache[cache_key] = explanation
                        results[var_name] = explanation
                        break

            # Need LLM for this one
            if var_name not in results:
                needs_llm.append(var_name)

        # Batch LLM call for remaining variables
        if needs_llm and self.llm.is_available():
            batch_results = self._explain_batch_with_llm(needs_llm, image)
            for var_name, explanation in batch_results.items():
                cache_key = (var_name, family)
                self._cache[cache_key] = explanation
                results[var_name] = explanation

        return results

    def _explain_batch_with_llm(
        self,
        var_names: list[str],
        image: str,
    ) -> dict[str, EnvVarExplanation]:
        """Get explanations for multiple variables from LLM.

        Args:
            var_names: List of variable names.
            image: Docker image name.

        Returns:
            Dict mapping variable names to explanations.
        """
        results: dict[str, EnvVarExplanation] = {}

        if not var_names:
            return results

        var_list = "\n".join(f"- {name}" for name in var_names)
        prompt = (
            f"Explain these Docker environment variables for the image '{image}':\n\n"
            f"{var_list}\n\n"
            f"For each variable, provide a brief description and whether it's sensitive.\n"
            f"Return a JSON object with variable names as keys."
        )

        try:
            result = self.llm.generate_json(
                prompt,
                system=ENV_EXPLAINER_SYSTEM.replace(
                    "Respond in JSON format:",
                    "Respond with a JSON object where keys are variable names:",
                ),
                max_tokens=self._max_tokens * len(var_names),
                timeout=self._timeout * 2,  # More time for batch
                temperature=0.3,
            )

            for var_name in var_names:
                if var_name in result:
                    data = result[var_name]
                    if isinstance(data, dict):
                        results[var_name] = EnvVarExplanation(
                            name=var_name,
                            description=data.get("description", ""),
                            is_sensitive=data.get("is_sensitive", False),
                            example=data.get("example"),
                            source="llm",
                        )

        except Exception as e:
            logger.debug(f"Batch LLM explanation error: {e}")
            # Fall back to individual calls
            for var_name in var_names:
                explanation = self._explain_with_llm(var_name, image)
                if explanation:
                    results[var_name] = explanation

        return results

    def clear_cache(self) -> None:
        """Clear the explanation cache."""
        self._cache.clear()


# =============================================================================
# Module-level Convenience Functions
# =============================================================================

_explainer: EnvVarExplainer | None = None


def get_explainer() -> EnvVarExplainer:
    """Get the global EnvVarExplainer instance.

    Returns:
        The global explainer instance.
    """
    global _explainer
    if _explainer is None:
        _explainer = EnvVarExplainer()
    return _explainer


def explain_env_var(var_name: str, image: str) -> str:
    """Get a human-readable explanation for an environment variable.

    Args:
        var_name: Environment variable name.
        image: Docker image name.

    Returns:
        Explanation string.
    """
    return get_explainer().explain(var_name, image)
