"""Ollama integration for local LLM inference.

All inference runs locally via Ollama.
"""


def query(prompt: str, system_prompt: str | None = None) -> str:
    """Send a query to the local Ollama instance.

    Args:
        prompt: The user prompt.
        system_prompt: Optional system prompt.

    Returns:
        The model's response.
    """
    raise NotImplementedError


def check_available() -> bool:
    """Check if Ollama is running and available.

    Returns:
        True if Ollama is ready for inference.
    """
    raise NotImplementedError


def list_models() -> list[str]:
    """List available Ollama models.

    Returns:
        List of model names.
    """
    raise NotImplementedError
