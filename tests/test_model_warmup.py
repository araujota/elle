"""Tests for ModelWarmupService."""

from elle.rag.constants import LLM_MODEL
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
        status = ModelStatus(name="qwen2.5:7b")
        assert status.name == "qwen2.5:7b"
        assert not status.loaded
        assert status.size_bytes is None
        assert status.vram_bytes is None

    def test_loaded_status(self) -> None:
        """Test creating a loaded model status."""
        status = ModelStatus(
            name="qwen2.5:7b",
            loaded=True,
            size_bytes=8_000_000_000,
            vram_bytes=7_500_000_000,
            expires_at="2024-01-01T12:00:00Z",
        )
        assert status.loaded
        assert status.size_bytes == 8_000_000_000
        assert status.vram_bytes == 7_500_000_000


class TestWarmupResult:
    """Tests for WarmupResult dataclass."""

    def test_success_result(self) -> None:
        """Test successful warmup result."""
        result = WarmupResult(
            success=True,
            model="qwen2.5:7b",
            message="Model warmed successfully",
            duration_ms=1500.0,
        )
        assert result.success
        assert result.model == "qwen2.5:7b"
        assert result.duration_ms == 1500.0
        assert result.error is None

    def test_failure_result(self) -> None:
        """Test failed warmup result."""
        result = WarmupResult(
            success=False,
            model="qwen2.5:7b",
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

        assert service.llm_model == LLM_MODEL
        assert service.host == "http://localhost:11434"

    def test_custom_initialization(self) -> None:
        """Test custom initialization."""
        service = ModelWarmupService(
            llm_model="custom:llm",
            host="http://localhost:11435",
        )

        assert service.llm_model == "custom:llm"
        assert service.host == "http://localhost:11435"


class TestVRAMDetection:
    """Tests for VRAM detection."""

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

    def test_model_name_is_q8(self) -> None:
        """Test that default model name uses Q8 quantization."""
        assert "q8" in LLM_MODEL.lower() or "q8_0" in LLM_MODEL.lower()


class TestPeriodicWarmup:
    """Tests for periodic warmup functionality."""

    def test_periodic_warmup_not_running_by_default(self) -> None:
        """Test that periodic warmup is not running by default."""
        service = ModelWarmupService()
        assert not service.is_periodic_warmup_running

    def test_warmup_service_has_periodic_methods(self) -> None:
        """Test that warmup service has periodic warmup methods."""
        service = ModelWarmupService()
        assert hasattr(service, "start_periodic_warmup")
        assert hasattr(service, "stop_periodic_warmup")
        assert hasattr(service, "is_periodic_warmup_running")


class TestKeepAliveConfiguration:
    """Tests for keep_alive configuration."""

    def test_keep_alive_is_permanent(self) -> None:
        """Test that LLM_KEEP_ALIVE is set to keep model loaded permanently."""
        from elle.rag.constants import LLM_KEEP_ALIVE

        # -1 means keep forever in Ollama
        assert LLM_KEEP_ALIVE == "-1"

    def test_warmup_interval_is_reasonable(self) -> None:
        """Test that warmup interval is a reasonable value."""
        from elle.rag.constants import LLM_WARMUP_INTERVAL

        # Should be between 1 and 30 minutes
        assert 60 <= LLM_WARMUP_INTERVAL <= 1800
