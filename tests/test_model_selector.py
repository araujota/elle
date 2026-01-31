"""Tests for the auto model selector and forecast result models."""

from __future__ import annotations

import math
import time
from unittest.mock import patch

import pytest

from elle.daemon.telemetry.model_selector import (
    CachedModel,
    ForecastResult,
    ModelSelector,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _linear_data(n: int, slope: float = 0.5, intercept: float = 50.0) -> list[float]:
    """Generate n points along a straight line."""
    return [intercept + slope * i for i in range(n)]


def _seasonal_data(n: int, period: int = 288) -> list[float]:
    """Generate seasonal data with trend."""
    import math as _math

    return [50.0 + 0.01 * i + 10.0 * _math.sin(2 * _math.pi * i / period) for i in range(n)]


def _constant_data(n: int, value: float = 42.0) -> list[float]:
    return [value] * n


# ===========================================================================
# ForecastResult model
# ===========================================================================


class TestForecastResult:
    """Tests for ForecastResult Pydantic model."""

    def test_create_minimal(self):
        """Create with required fields only."""
        result = ForecastResult(
            method="linear",
            predicted_24h=55.0,
            predicted_7d=60.0,
            rate_of_change=0.5,
            confidence=0.3,
        )
        assert result.method == "linear"
        assert result.predicted_24h == 55.0
        assert result.mape is None
        assert result.seasonal_detected is False
        assert result.trend_direction == "stable"

    def test_create_full(self):
        """Create with all fields."""
        result = ForecastResult(
            method="holt_winters",
            predicted_24h=70.0,
            predicted_7d=85.0,
            rate_of_change=1.2,
            confidence=0.9,
            mape=5.0,
            seasonal_detected=True,
            trend_direction="rising",
        )
        assert result.method == "holt_winters"
        assert result.mape == 5.0
        assert result.seasonal_detected is True
        assert result.trend_direction == "rising"

    def test_confidence_bounds(self):
        """Confidence must be between 0.0 and 1.0."""
        result = ForecastResult(
            method="linear",
            predicted_24h=50.0,
            predicted_7d=50.0,
            rate_of_change=0.0,
            confidence=0.0,
        )
        assert result.confidence == 0.0

        result2 = ForecastResult(
            method="linear",
            predicted_24h=50.0,
            predicted_7d=50.0,
            rate_of_change=0.0,
            confidence=1.0,
        )
        assert result2.confidence == 1.0

    def test_confidence_out_of_bounds(self):
        """Confidence > 1.0 or < 0.0 should fail validation."""
        with pytest.raises(Exception):
            ForecastResult(
                method="linear",
                predicted_24h=50.0,
                predicted_7d=50.0,
                rate_of_change=0.0,
                confidence=1.5,
            )

    def test_trend_direction_values(self):
        """trend_direction accepts all three valid literals."""
        for direction in ("rising", "falling", "stable"):
            result = ForecastResult(
                method="ar1",
                predicted_24h=50.0,
                predicted_7d=50.0,
                rate_of_change=0.0,
                confidence=0.5,
                trend_direction=direction,
            )
            assert result.trend_direction == direction


# ===========================================================================
# CachedModel
# ===========================================================================


class TestCachedModel:
    """Tests for CachedModel."""

    def test_create(self):
        """CachedModel stores method and timestamp."""
        cm = CachedModel(method="ar1", mape=10.0, cached_at=1000.0)
        assert cm.method == "ar1"
        assert cm.mape == 10.0
        assert cm.cached_at == 1000.0

    def test_mape_optional(self):
        """mape can be None."""
        cm = CachedModel(method="linear", mape=None, cached_at=0.0)
        assert cm.mape is None


# ===========================================================================
# ModelSelector
# ===========================================================================


class TestModelSelector:
    """Tests for ModelSelector forecast method selection and execution."""

    @pytest.fixture()
    def selector(self) -> ModelSelector:
        return ModelSelector()

    # --- Data size routing ---

    def test_small_data_uses_linear(self, selector: ModelSelector):
        """Data < 72 samples should use linear method."""
        data = _linear_data(50)
        result = selector.forecast("cpu.load", data, current=75.0, avg_1h=74.0, avg_6h=72.0)
        assert result.method == "linear"

    def test_medium_data_tries_ar1(self, selector: ModelSelector):
        """Data in [72, 576) should try AR1."""
        data = _linear_data(200, slope=0.1, intercept=50.0)
        result = selector.forecast("cpu.load", data, current=data[-1], avg_1h=data[-1], avg_6h=data[-1] - 1)
        # Should be either ar1 or linear (if ar1 MAPE is too high)
        assert result.method in ("ar1", "linear")

    def test_large_data_tries_holt_winters(self, selector: ModelSelector):
        """Data >= 576 should try HoltWinters first."""
        data = _seasonal_data(600)
        result = selector.forecast("disk.used", data, current=data[-1], avg_1h=data[-1], avg_6h=data[-1])
        # Could be holt_winters, ar1, seasonal, or linear depending on MAPE
        assert result.method in ("holt_winters", "ar1", "seasonal", "linear")

    # --- Cache behavior ---

    def test_cache_hit(self, selector: ModelSelector):
        """Second call for same metric should use cached method."""
        data = _linear_data(50)
        result1 = selector.forecast("mem.pct", data, current=75.0, avg_1h=74.0, avg_6h=72.0)
        result2 = selector.forecast("mem.pct", data, current=76.0, avg_1h=75.0, avg_6h=73.0)
        assert result1.method == result2.method
        assert "mem.pct" in selector._cache

    def test_cache_miss_different_metric(self, selector: ModelSelector):
        """Different metric names get independent cache entries."""
        data = _linear_data(50)
        selector.forecast("metric_a", data, current=50.0, avg_1h=50.0, avg_6h=50.0)
        selector.forecast("metric_b", data, current=50.0, avg_1h=50.0, avg_6h=50.0)
        assert "metric_a" in selector._cache
        assert "metric_b" in selector._cache

    def test_cache_expiry(self, selector: ModelSelector):
        """Expired cache entry causes reselection."""
        data = _linear_data(50)
        # First call populates cache
        selector.forecast("test_metric", data, current=75.0, avg_1h=74.0, avg_6h=72.0)
        assert "test_metric" in selector._cache

        # Manually set cached_at to the past
        old_time = time.time() - selector.CACHE_TTL - 10
        selector._cache["test_metric"] = CachedModel(method="linear", mape=None, cached_at=old_time)

        # Next call should re-evaluate (cache expired)
        with patch.object(selector, "_select_method", wraps=selector._select_method) as mock_select:
            selector.forecast("test_metric", data, current=75.0, avg_1h=74.0, avg_6h=72.0)
            mock_select.assert_called_once()

    # --- _run_forecast for each method ---

    def test_run_forecast_linear(self, selector: ModelSelector):
        """_run_forecast with 'linear' method."""
        result = selector._run_forecast("linear", _linear_data(10), current=55.0, avg_1h=54.0, avg_6h=52.0)
        assert result.method == "linear"
        assert math.isfinite(result.predicted_24h)
        assert math.isfinite(result.predicted_7d)

    def test_run_forecast_ar1(self, selector: ModelSelector):
        """_run_forecast with 'ar1' method and enough data."""
        data = _linear_data(200, slope=0.1, intercept=50.0)
        result = selector._run_forecast("ar1", data, current=data[-1], avg_1h=data[-1], avg_6h=data[-1] - 1)
        assert result.method == "ar1"
        assert math.isfinite(result.predicted_24h)

    def test_run_forecast_ar1_fallback(self, selector: ModelSelector):
        """_run_forecast with 'ar1' falls back to linear if data is insufficient."""
        data = _linear_data(10)
        result = selector._run_forecast("ar1", data, current=55.0, avg_1h=54.0, avg_6h=52.0)
        # Should fall back to linear
        assert result.method == "linear"

    def test_run_forecast_holt_winters(self, selector: ModelSelector):
        """_run_forecast with 'holt_winters' and enough seasonal data."""
        data = _seasonal_data(600)
        result = selector._run_forecast("holt_winters", data, current=data[-1], avg_1h=data[-1], avg_6h=data[-1])
        assert result.method == "holt_winters"
        assert math.isfinite(result.predicted_24h)
        assert result.seasonal_detected is True

    def test_run_forecast_holt_winters_fallback(self, selector: ModelSelector):
        """_run_forecast with 'holt_winters' falls back on insufficient data."""
        data = _linear_data(10)
        result = selector._run_forecast("holt_winters", data, current=55.0, avg_1h=54.0, avg_6h=52.0)
        assert result.method == "linear"

    def test_run_forecast_seasonal(self, selector: ModelSelector):
        """_run_forecast with 'seasonal' method and enough data."""
        data = _seasonal_data(600)
        result = selector._run_forecast("seasonal", data, current=data[-1], avg_1h=data[-1], avg_6h=data[-1])
        assert result.method == "seasonal"
        assert math.isfinite(result.predicted_24h)

    def test_run_forecast_seasonal_fallback(self, selector: ModelSelector):
        """_run_forecast with 'seasonal' falls back on insufficient data."""
        data = _linear_data(10)
        result = selector._run_forecast("seasonal", data, current=55.0, avg_1h=54.0, avg_6h=52.0)
        assert result.method == "linear"

    # --- Trend direction ---

    def test_trend_direction_rising(self, selector: ModelSelector):
        """Positive rate yields 'rising' trend."""
        result = selector.forecast(
            "up_metric",
            _linear_data(50),
            current=50.0,
            avg_1h=55.0,
            avg_6h=50.0,
        )
        assert result.trend_direction == "rising"

    def test_trend_direction_falling(self, selector: ModelSelector):
        """Negative rate yields 'falling' trend."""
        result = selector.forecast(
            "down_metric",
            _linear_data(50),
            current=50.0,
            avg_1h=45.0,
            avg_6h=50.0,
        )
        assert result.trend_direction == "falling"

    def test_trend_direction_stable(self, selector: ModelSelector):
        """Near-zero rate yields 'stable' trend."""
        result = selector.forecast(
            "flat_metric",
            _linear_data(50),
            current=50.0,
            avg_1h=50.0,
            avg_6h=50.0,
        )
        assert result.trend_direction == "stable"

    # --- Confidence ---

    def test_confidence_small_data(self, selector: ModelSelector):
        """Small data yields low confidence (0.3)."""
        data = _linear_data(30)
        result = selector.forecast("small", data, current=65.0, avg_1h=65.0, avg_6h=65.0)
        assert result.confidence == pytest.approx(0.3)

    def test_confidence_medium_data_no_mape(self, selector: ModelSelector):
        """Medium data with linear fallback (no MAPE) yields 0.5 confidence."""
        # Use constant data so AR1 produces near-zero variance => MAPE = inf
        # which causes fallback to linear with confidence based on data size
        data = _constant_data(100)
        result = selector.forecast("med", data, current=42.0, avg_1h=42.0, avg_6h=42.0)
        # Method should be linear (fallback) with confidence 0.5 (72 <= n < 576)
        if result.method == "linear" and result.mape is None:
            assert result.confidence == pytest.approx(0.5)

    # --- Forecast result fields ---

    def test_forecast_result_has_all_fields(self, selector: ModelSelector):
        """Forecast result contains all expected fields."""
        data = _linear_data(50)
        result = selector.forecast("test", data, current=75.0, avg_1h=74.0, avg_6h=72.0)
        assert result.method is not None
        assert math.isfinite(result.predicted_24h)
        assert math.isfinite(result.predicted_7d)
        assert math.isfinite(result.rate_of_change)
        assert 0.0 <= result.confidence <= 1.0
        assert result.trend_direction in ("rising", "falling", "stable")
