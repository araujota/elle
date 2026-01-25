"""Tests for ModelWarmupService."""

import pytest

from elle.rag.constants import (
    DUAL_MODEL_VRAM_THRESHOLD,
    LLM_MODEL,
    SLM_MODEL,
)
from elle.rag.model_warmup import (
    ModelStatus,
    ModelWarmupService,
    WarmupResult,
    get_warmup_service,
)


class TestModelStatus:
    """Tests for ModelStatus dataclass."""

    def test_basic_status(self) -> None:
        """Test creating a basic model status."""
        status = ModelStatus(name="phi3.5:3.8b")
        assert status.name == "phi3.5:3.8b"
        assert not status.loaded
        assert status.size_bytes is None
        assert status.vram_bytes is None

    def test_loaded_status(self) -> None:
        """Test creating a loaded model status."""
        status = ModelStatus(
            name="phi3.5:3.8b",
            loaded=True,
            size_bytes=4_000_000_000,
            vram_bytes=3_500_000_000,
            expires_at="2024-01-01T12:00:00Z",
        )
        assert status.loaded
        assert status.size_bytes == 4_000_000_000
        assert status.vram_bytes == 3_500_000_000


class TestWarmupResult:
    """Tests for WarmupResult dataclass."""

    def test_success_result(self) -> None:
        """Test successful warmup result."""
        result = WarmupResult(
            success=True,
            model="phi3.5:3.8b",
            message="Model warmed successfully",
            duration_ms=1500.0,
        )
        assert result.success
        assert result.model == "phi3.5:3.8b"
        assert result.duration_ms == 1500.0
        assert result.error is None

    def test_failure_result(self) -> None:
        """Test failed warmup result."""
        result = WarmupResult(
            success=False,
            model="phi3.5:3.8b",
            message="Warmup failed",
            error="timeout",
        )
        assert not result.success
        assert result.error == "timeout"


class TestModelWarmupService:
    """Tests for ModelWarmupService."""

    def test_initialization(self) -> None:
        """Test default initialization."""
        service = ModelWarmupService()

        assert service.slm_model == SLM_MODEL
        assert service.llm_model == LLM_MODEL
        assert service.host == "http://localhost:11434"

    def test_custom_initialization(self) -> None:
        """Test custom initialization."""
        service = ModelWarmupService(
            slm_model="custom:slm",
            llm_model="custom:llm",
            host="http://localhost:11435",
        )

        assert service.slm_model == "custom:slm"
        assert service.llm_model == "custom:llm"
        assert service.host == "http://localhost:11435"


class TestVRAMDetection:
    """Tests for VRAM detection."""

    def test_has_sufficient_vram_default_threshold(self) -> None:
        """Test has_sufficient_vram with default threshold."""
        service = ModelWarmupService()

        # This will use actual system detection
        # Just verify it doesn't crash and returns a boolean
        result = service.has_sufficient_vram()
        assert isinstance(result, bool)

    def test_has_sufficient_vram_custom_threshold(self) -> None:
        """Test has_sufficient_vram with custom threshold."""
        service = ModelWarmupService()

        # Very low threshold should return True
        assert service.has_sufficient_vram(threshold_gb=0.001)

        # Very high threshold would likely return False (unless massive system)
        # But we can't guarantee this, so just verify it's a boolean
        result = service.has_sufficient_vram(threshold_gb=1000.0)
        assert isinstance(result, bool)

    def test_get_available_vram_gb(self) -> None:
        """Test VRAM detection returns None or a valid number."""
        service = ModelWarmupService()

        vram = service.get_available_vram_gb()

        # Should be None or a positive number
        if vram is not None:
            assert vram > 0


class TestModelWarmupServiceSingleton:
    """Tests for the singleton pattern."""

    def test_get_warmup_service_returns_same_instance(self) -> None:
        """Test that get_warmup_service returns the same instance."""
        service1 = get_warmup_service()
        service2 = get_warmup_service()

        assert service1 is service2

    def test_get_warmup_service_type(self) -> None:
        """Test that get_warmup_service returns correct type."""
        service = get_warmup_service()
        assert isinstance(service, ModelWarmupService)


class TestModelWarmupConstants:
    """Tests for warmup-related constants."""

    def test_dual_model_threshold(self) -> None:
        """Test DUAL_MODEL_VRAM_THRESHOLD is reasonable."""
        # Should be around 12-16GB for both models
        assert 10 <= DUAL_MODEL_VRAM_THRESHOLD <= 20

    def test_model_names_are_q8(self) -> None:
        """Test that default model names use Q8 quantization."""
        assert "q8" in SLM_MODEL.lower() or "q8_0" in SLM_MODEL.lower()
        assert "q8" in LLM_MODEL.lower() or "q8_0" in LLM_MODEL.lower()
