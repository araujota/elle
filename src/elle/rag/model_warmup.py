"""Model warmup service for ELLE.

Provides async methods to pre-load SLM and LLM models into GPU memory
for reduced inference latency. The service handles:

- Model availability checking and pulling
- VRAM detection for intelligent warmup decisions
- Keeping models warm via Ollama's keep_alive mechanism
- Periodic health checks to maintain model warmth

Usage:
    warmup = ModelWarmupService()
    await warmup.ensure_models_ready()
    await warmup.warm_slm()  # Always warm SLM

    if warmup.has_sufficient_vram(threshold_gb=14):
        await warmup.warm_llm()  # Warm LLM if enough VRAM
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

from elle.rag.constants import (
    DUAL_MODEL_VRAM_THRESHOLD,
    LLM_KEEP_ALIVE,
    LLM_MODEL,
    LLM_NUM_CTX,
    SLM_KEEP_ALIVE,
    SLM_MODEL,
    SLM_NUM_CTX,
    WARMUP_PROMPT,
    WARMUP_TIMEOUT,
)

logger = logging.getLogger(__name__)


@dataclass
class ModelStatus:
    """Status of a model in Ollama."""

    name: str
    loaded: bool = False
    size_bytes: int | None = None
    vram_bytes: int | None = None
    expires_at: str | None = None


@dataclass
class WarmupResult:
    """Result of a warmup operation."""

    success: bool
    model: str
    message: str
    duration_ms: float | None = None
    error: str | None = None


class ModelWarmupService:
    """Service for warming up SLM and LLM models.

    Pre-loads models into GPU memory to minimize inference latency.
    The SLM is kept warm indefinitely for instant classification,
    while the LLM uses a configurable timeout.

    Attributes:
        slm_model: SLM model name (default: phi3.5 Q8).
        llm_model: LLM model name (default: qwen2.5 Q8).
        host: Ollama API host URL.
    """

    def __init__(
        self,
        slm_model: str = SLM_MODEL,
        llm_model: str = LLM_MODEL,
        host: str = "http://localhost:11434",
    ) -> None:
        """Initialize the warmup service.

        Args:
            slm_model: SLM model name for classification.
            llm_model: LLM model name for generation.
            host: Ollama API host URL.
        """
        self.slm_model = slm_model
        self.llm_model = llm_model
        self.host = host
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.host,
                timeout=httpx.Timeout(WARMUP_TIMEOUT),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> ModelWarmupService:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # -------------------------------------------------------------------------
    # Model Availability
    # -------------------------------------------------------------------------

    async def is_ollama_available(self) -> bool:
        """Check if Ollama service is running.

        Returns:
            True if Ollama is available.
        """
        try:
            client = await self._get_client()
            response = await client.get("/api/tags", timeout=5.0)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    async def list_models(self) -> list[str]:
        """List available models.

        Returns:
            List of model names.
        """
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except httpx.RequestError as e:
            logger.warning(f"Failed to list models: {e}")
            return []

    async def get_running_models(self) -> list[ModelStatus]:
        """Get currently loaded models.

        Returns:
            List of ModelStatus for loaded models.
        """
        try:
            client = await self._get_client()
            response = await client.get("/api/ps")
            response.raise_for_status()
            data = response.json()

            models = []
            for model in data.get("models", []):
                models.append(
                    ModelStatus(
                        name=model.get("name", ""),
                        loaded=True,
                        size_bytes=model.get("size"),
                        vram_bytes=model.get("size_vram"),
                        expires_at=model.get("expires_at"),
                    )
                )
            return models

        except httpx.RequestError as e:
            logger.warning(f"Failed to get running models: {e}")
            return []

    async def is_model_loaded(self, model: str) -> bool:
        """Check if a model is currently loaded.

        Args:
            model: Model name to check.

        Returns:
            True if the model is loaded.
        """
        running = await self.get_running_models()
        return any(m.name == model or model in m.name for m in running)

    # -------------------------------------------------------------------------
    # Model Management
    # -------------------------------------------------------------------------

    async def ensure_model_exists(self, model: str) -> bool:
        """Ensure a model exists, pulling if necessary.

        Args:
            model: Model name to check/pull.

        Returns:
            True if the model is available.
        """
        available = await self.list_models()

        # Check if model exists
        if any(model in m for m in available):
            logger.debug(f"Model {model} already available")
            return True

        # Try to pull the model
        logger.info(f"Pulling model {model}...")
        try:
            client = await self._get_client()
            response = await client.post(
                "/api/pull",
                json={"name": model, "stream": False},
                timeout=WARMUP_TIMEOUT,
            )
            response.raise_for_status()
            logger.info(f"Model {model} pulled successfully")
            return True

        except httpx.RequestError as e:
            logger.error(f"Failed to pull model {model}: {e}")
            return False

    async def ensure_models_ready(self) -> tuple[bool, bool]:
        """Ensure both SLM and LLM models are available.

        Returns:
            Tuple of (slm_ready, llm_ready).
        """
        slm_ready = await self.ensure_model_exists(self.slm_model)
        llm_ready = await self.ensure_model_exists(self.llm_model)
        return slm_ready, llm_ready

    # -------------------------------------------------------------------------
    # Model Warmup
    # -------------------------------------------------------------------------

    async def _warm_model(
        self,
        model: str,
        keep_alive: str,
        num_ctx: int,
    ) -> WarmupResult:
        """Warm a model by sending a minimal generation request.

        Args:
            model: Model name to warm.
            keep_alive: Keep-alive duration.
            num_ctx: Context window size.

        Returns:
            WarmupResult with status.
        """
        import time

        start = time.time()

        try:
            client = await self._get_client()
            response = await client.post(
                "/api/generate",
                json={
                    "model": model,
                    "prompt": WARMUP_PROMPT,
                    "stream": False,
                    "keep_alive": keep_alive,
                    "options": {
                        "num_predict": 1,  # Single token
                        "num_ctx": num_ctx,
                    },
                },
                timeout=WARMUP_TIMEOUT,
            )
            response.raise_for_status()

            duration_ms = (time.time() - start) * 1000
            logger.info(f"Model {model} warmed in {duration_ms:.0f}ms (keep_alive={keep_alive})")

            return WarmupResult(
                success=True,
                model=model,
                message="Model warmed successfully",
                duration_ms=duration_ms,
            )

        except httpx.TimeoutException:
            duration_ms = (time.time() - start) * 1000
            return WarmupResult(
                success=False,
                model=model,
                message="Warmup timed out",
                duration_ms=duration_ms,
                error="timeout",
            )

        except httpx.RequestError as e:
            return WarmupResult(
                success=False,
                model=model,
                message=f"Warmup failed: {e}",
                error=str(e),
            )

    async def warm_slm(self) -> WarmupResult:
        """Warm the SLM for instant classification.

        The SLM is kept warm indefinitely (keep_alive=-1).

        Returns:
            WarmupResult with status.
        """
        logger.info(f"Warming SLM: {self.slm_model}")
        return await self._warm_model(
            model=self.slm_model,
            keep_alive=SLM_KEEP_ALIVE,
            num_ctx=SLM_NUM_CTX,
        )

    async def warm_llm(self) -> WarmupResult:
        """Warm the LLM for generation.

        The LLM uses a configurable keep_alive duration.

        Returns:
            WarmupResult with status.
        """
        logger.info(f"Warming LLM: {self.llm_model}")
        return await self._warm_model(
            model=self.llm_model,
            keep_alive=LLM_KEEP_ALIVE,
            num_ctx=LLM_NUM_CTX,
        )

    async def warm_both(self) -> tuple[WarmupResult, WarmupResult]:
        """Warm both SLM and LLM models.

        Returns:
            Tuple of (slm_result, llm_result).
        """
        # Run warmups concurrently
        slm_task = asyncio.create_task(self.warm_slm())
        llm_task = asyncio.create_task(self.warm_llm())

        slm_result, llm_result = await asyncio.gather(slm_task, llm_task)
        return slm_result, llm_result

    # -------------------------------------------------------------------------
    # VRAM Detection
    # -------------------------------------------------------------------------

    def get_available_vram_gb(self) -> float | None:
        """Get available VRAM in GB.

        Attempts to detect NVIDIA GPU VRAM via nvidia-smi.
        Falls back to system memory if no GPU detected.

        Returns:
            Available VRAM in GB, or None if unable to detect.
        """
        # Try nvidia-smi first
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse free memory (in MiB)
                lines = result.stdout.strip().split("\n")
                if lines:
                    # Use first GPU, convert MiB to GB
                    free_mib = int(lines[0].strip())
                    return free_mib / 1024

        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

        # Try rocm-smi for AMD GPUs
        try:
            result = subprocess.run(
                ["rocm-smi", "--showmeminfo", "vram"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse VRAM info
                match = re.search(r"Total VRAM:\s*(\d+)", result.stdout)
                if match:
                    total_mb = int(match.group(1))
                    return total_mb / 1024

        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

        # Fallback to system memory
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        # Parse available memory in kB, convert to GB
                        kb = int(line.split()[1])
                        return kb / (1024 * 1024)

        except (FileNotFoundError, ValueError, IndexError):
            pass

        return None

    def has_sufficient_vram(
        self,
        threshold_gb: float = DUAL_MODEL_VRAM_THRESHOLD,
    ) -> bool:
        """Check if system has sufficient VRAM for both models.

        Args:
            threshold_gb: Minimum VRAM in GB.

        Returns:
            True if VRAM >= threshold.
        """
        available = self.get_available_vram_gb()
        if available is None:
            logger.warning("Unable to detect VRAM, assuming sufficient")
            return True

        sufficient = available >= threshold_gb
        logger.debug(f"VRAM: {available:.1f}GB available, threshold={threshold_gb}GB, sufficient={sufficient}")
        return sufficient


# Module-level singleton
_warmup_service: ModelWarmupService | None = None


def get_warmup_service() -> ModelWarmupService:
    """Get the shared warmup service instance.

    Returns:
        The ModelWarmupService singleton.
    """
    global _warmup_service
    if _warmup_service is None:
        _warmup_service = ModelWarmupService()
    return _warmup_service
