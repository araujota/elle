"""Model warmup service for ELLE.

Provides async methods to pre-load the LLM model into GPU memory
for reduced inference latency. The service handles:

- Model availability checking and pulling
- Keeping models warm via Ollama's keep_alive mechanism
- Periodic health checks to maintain model warmth

Usage:
    warmup = ModelWarmupService()
    await warmup.ensure_model_ready()
    await warmup.warm_llm()
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
    LLM_KEEP_ALIVE,
    LLM_MODEL,
    LLM_NUM_CTX,
    LLM_WARMUP_INTERVAL,
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
    """Service for warming up the LLM model.

    Pre-loads the model into GPU memory to minimize inference latency.
    Supports periodic warmup to keep the model loaded for local inference.

    Attributes:
        llm_model: LLM model name (default: qwen2.5 Q8).
        host: Ollama API host URL.
    """

    def __init__(
        self,
        llm_model: str = LLM_MODEL,
        host: str = "http://localhost:11434",
    ) -> None:
        """Initialize the warmup service.

        Args:
            llm_model: LLM model name for generation.
            host: Ollama API host URL.
        """
        self.llm_model = llm_model
        self.host = host
        self._client: httpx.AsyncClient | None = None
        self._warmup_task: asyncio.Task[None] | None = None
        self._shutdown_event: asyncio.Event | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.host,
                timeout=httpx.Timeout(WARMUP_TIMEOUT),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and stop periodic warmup."""
        # Stop periodic warmup if running
        await self.stop_periodic_warmup()

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

    async def ensure_model_ready(self) -> bool:
        """Ensure the LLM model is available.

        Returns:
            True if ready.
        """
        return await self.ensure_model_exists(self.llm_model)

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

    async def warm_llm(self) -> WarmupResult:
        """Warm the LLM for generation.

        Skips warmup when the primary provider is remote (e.g. OpenAI-compatible),
        since remote models don't need local GPU preloading. If fallback triggers
        later, the first request will load the model on demand.

        Returns:
            WarmupResult with status.
        """
        # Skip warmup for remote providers
        if self._is_remote_provider():
            logger.info("Skipping LLM warmup: primary provider is remote")
            return WarmupResult(
                success=True,
                model=self.llm_model,
                message="Warmup skipped: remote provider",
            )

        logger.info(f"Warming LLM: {self.llm_model}")
        return await self._warm_model(
            model=self.llm_model,
            keep_alive=LLM_KEEP_ALIVE,
            num_ctx=LLM_NUM_CTX,
        )

    def _is_remote_provider(self) -> bool:
        """Check if the primary LLM provider is remote (non-Ollama)."""
        try:
            from elle.rag.llm_config import LLMProviderType, load_elle_llm_config

            cfg = load_elle_llm_config()
            return cfg.provider.type == LLMProviderType.OPENAI
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Periodic Warmup (Keep Model Warm)
    # -------------------------------------------------------------------------

    async def start_periodic_warmup(
        self,
        interval: int = LLM_WARMUP_INTERVAL,
    ) -> None:
        """Start periodic warmup to keep the LLM loaded in memory.

        This runs a background task that periodically pings the model
        to ensure it stays loaded for fast local inference. Skips
        entirely when the primary provider is remote.

        Args:
            interval: Seconds between warmup pings (default: 5 minutes).
        """
        if self._is_remote_provider():
            logger.info("Skipping periodic warmup: primary provider is remote")
            return

        if self._warmup_task is not None:
            logger.warning("Periodic warmup already running")
            return

        self._shutdown_event = asyncio.Event()
        self._warmup_task = asyncio.create_task(
            self._periodic_warmup_loop(interval),
            name="llm-warmup",
        )
        logger.info(f"Started periodic LLM warmup (interval={interval}s)")

    async def stop_periodic_warmup(self) -> None:
        """Stop the periodic warmup task."""
        if self._warmup_task is None:
            return

        if self._shutdown_event:
            self._shutdown_event.set()

        self._warmup_task.cancel()
        try:
            await self._warmup_task
        except asyncio.CancelledError:
            pass

        self._warmup_task = None
        self._shutdown_event = None
        logger.info("Stopped periodic LLM warmup")

    async def _periodic_warmup_loop(self, interval: int) -> None:
        """Background loop that periodically warms the LLM.

        Args:
            interval: Seconds between warmup pings.
        """
        while True:
            try:
                # Wait for the interval or shutdown
                if self._shutdown_event:
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=interval,
                        )
                        # Event was set, time to shut down
                        break
                    except (TimeoutError, asyncio.TimeoutError):
                        # Timeout expired, time to warm up
                        pass
                else:
                    await asyncio.sleep(interval)

                # Check if model is still loaded
                if not await self.is_model_loaded(self.llm_model):
                    logger.info("LLM not loaded, warming up...")
                    result = await self.warm_llm()
                    if result.success:
                        logger.debug(f"LLM warmup ping successful ({result.duration_ms:.0f}ms)")
                    else:
                        logger.warning(f"LLM warmup ping failed: {result.error}")
                else:
                    logger.debug("LLM still loaded, skipping warmup ping")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic warmup: {e}")
                # Continue the loop, don't crash

    @property
    def is_periodic_warmup_running(self) -> bool:
        """Check if periodic warmup is running."""
        return self._warmup_task is not None and not self._warmup_task.done()

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
