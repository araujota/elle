"""Auto-selects the best forecasting method with fallback chain.

Evaluates forecaster performance via backtesting and selects the
most accurate method for each metric.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from elle.daemon.telemetry.forecasters import (
    AR1Forecaster,
    HoltWinters,
    LinearForecaster,
    SeasonalDecomposer,
)

ForecastMethod = Literal["holt_winters", "ar1", "seasonal", "linear"]


class ForecastResult(BaseModel):
    """Result of a forecasting operation."""

    method: ForecastMethod
    predicted_24h: float
    predicted_7d: float
    rate_of_change: float  # per hour
    confidence: float = Field(ge=0.0, le=1.0)
    mape: float | None = None
    seasonal_detected: bool = False
    trend_direction: Literal["rising", "falling", "stable"] = "stable"


class CachedModel(BaseModel):
    """Cached model selection for a metric."""

    method: ForecastMethod
    mape: float | None = None
    cached_at: float  # time.time()


class ModelSelector:
    """Auto-selects best forecasting method per metric.

    Fallback chain:
        samples >= 576 → try HoltWinters, fall back to AR1
        samples >= 72  → try AR1, fall back to Linear
        else           → Linear

    Caches selected model per metric for 1 hour.
    """

    # MAPE threshold: if above this, fall back to simpler method
    MAX_ACCEPTABLE_MAPE = 30.0

    # Cache duration in seconds
    CACHE_TTL = 3600

    def __init__(self) -> None:
        self._cache: dict[str, CachedModel] = {}

    def forecast(
        self,
        metric: str,
        data: list[float],
        current: float,
        avg_1h: float,
        avg_6h: float,
    ) -> ForecastResult:
        """Forecast a metric using the best available method.

        Args:
            metric: Metric identifier
            data: Historical data points (5-minute intervals)
            current: Current value
            avg_1h: 1-hour average
            avg_6h: 6-hour average

        Returns:
            ForecastResult with predictions and metadata
        """
        n = len(data)

        # Check cache
        cached = self._cache.get(metric)
        if cached and (time.time() - cached.cached_at) < self.CACHE_TTL:
            method = cached.method
        else:
            method = self._select_method(metric, data, n)

        # Run the selected method
        return self._run_forecast(method, data, current, avg_1h, avg_6h)

    def _select_method(self, metric: str, data: list[float], n: int) -> ForecastMethod:
        """Select the best method for this metric's data."""

        if n >= 576:
            # Try Holt-Winters first
            hw = HoltWinters()
            mape = hw.backtest_mape(data)
            if mape <= self.MAX_ACCEPTABLE_MAPE:
                self._cache_result(metric, "holt_winters", mape)
                return "holt_winters"

            # Fall back to AR(1)
            ar = AR1Forecaster()
            mape = ar.backtest_mape(data)
            if mape <= self.MAX_ACCEPTABLE_MAPE:
                self._cache_result(metric, "ar1", mape)
                return "ar1"

            # Check if seasonal decomposition helps
            sd = SeasonalDecomposer()
            if sd.has_seasonality(data):
                self._cache_result(metric, "seasonal", None)
                return "seasonal"

        elif n >= 72:
            # Try AR(1)
            ar = AR1Forecaster()
            mape = ar.backtest_mape(data)
            if mape <= self.MAX_ACCEPTABLE_MAPE:
                self._cache_result(metric, "ar1", mape)
                return "ar1"

        # Fallback to linear
        self._cache_result(metric, "linear", None)
        return "linear"

    def _cache_result(self, metric: str, method: ForecastMethod, mape: float | None) -> None:
        """Cache model selection result."""
        self._cache[metric] = CachedModel(
            method=method,
            mape=mape,
            cached_at=time.time(),
        )

    def _run_forecast(
        self,
        method: ForecastMethod,
        data: list[float],
        current: float,
        avg_1h: float,
        avg_6h: float,
    ) -> ForecastResult:
        """Run the selected forecasting method."""

        predicted_24h: float
        predicted_7d: float
        rate: float
        mape: float | None = None
        seasonal_detected = False

        if method == "holt_winters":
            hw = HoltWinters()
            try:
                forecasts = hw.fit_predict(data, steps_ahead=2016)  # 7 days
                predicted_24h = forecasts[287] if len(forecasts) > 287 else forecasts[-1]
                predicted_7d = forecasts[-1]
                mape = hw.backtest_mape(data)
                # Rate from trend
                rate = (forecasts[11] - current) / 1.0  # ~1 hour ahead
                seasonal_detected = True
            except (ValueError, IndexError):
                # Fall back to linear
                return self._run_forecast("linear", data, current, avg_1h, avg_6h)

        elif method == "ar1":
            ar = AR1Forecaster()
            try:
                ar.fit(data)
                preds_24h = ar.predict(current, steps_ahead=288)
                preds_7d = ar.predict(current, steps_ahead=2016)
                predicted_24h = preds_24h[-1]
                predicted_7d = preds_7d[-1]
                mape = ar.backtest_mape(data)
                rate = (preds_24h[11] - current) / 1.0 if len(preds_24h) > 11 else 0.0
            except (ValueError, IndexError):
                return self._run_forecast("linear", data, current, avg_1h, avg_6h)

        elif method == "seasonal":
            sd = SeasonalDecomposer()
            try:
                forecasts = sd.forecast(data, steps_ahead=2016)
                predicted_24h = forecasts[287] if len(forecasts) > 287 else forecasts[-1]
                predicted_7d = forecasts[-1]
                seasonal_detected = sd.has_seasonality(data)
                rate = (forecasts[11] - current) / 1.0 if len(forecasts) > 11 else 0.0
            except (ValueError, IndexError):
                return self._run_forecast("linear", data, current, avg_1h, avg_6h)

        else:  # linear
            predicted_24h, rate = LinearForecaster.forecast(current, avg_1h, avg_6h, 24.0)
            predicted_7d, _ = LinearForecaster.forecast(current, avg_1h, avg_6h, 168.0)

        # Compute confidence from MAPE
        if mape is not None and mape < 100:
            confidence = max(0.0, min(1.0, 1.0 - mape / 100.0))
        elif len(data) >= 576:
            confidence = 0.7
        elif len(data) >= 72:
            confidence = 0.5
        else:
            confidence = 0.3

        # Determine trend direction
        trend_direction: Literal["rising", "falling", "stable"]
        if abs(rate) < 0.01:
            trend_direction = "stable"
        elif rate > 0:
            trend_direction = "rising"
        else:
            trend_direction = "falling"

        return ForecastResult(
            method=method,
            predicted_24h=predicted_24h,
            predicted_7d=predicted_7d,
            rate_of_change=rate,
            confidence=confidence,
            mape=mape,
            seasonal_detected=seasonal_detected,
            trend_direction=trend_direction,
        )
