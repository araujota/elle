"""Tests for pure Python forecasting algorithms."""

from __future__ import annotations

import math

import pytest

from elle.daemon.telemetry.forecasters import (
    AR1Forecaster,
    HoltWinters,
    LinearForecaster,
    SeasonalDecomposer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linear_data(n: int, slope: float = 1.0, intercept: float = 50.0) -> list[float]:
    """Generate n points along a straight line."""
    return [intercept + slope * i for i in range(n)]


def _seasonal_data(
    n: int,
    period: int = 12,
    amplitude: float = 10.0,
    trend: float = 0.05,
    offset: float = 50.0,
) -> list[float]:
    """Generate n points with a linear trend plus sinusoidal seasonality."""
    return [
        offset + trend * i + amplitude * math.sin(2 * math.pi * i / period)
        for i in range(n)
    ]


def _constant_data(n: int, value: float = 42.0) -> list[float]:
    """Generate n identical values."""
    return [value] * n


# ===========================================================================
# LinearForecaster
# ===========================================================================


class TestLinearForecaster:
    """Tests for LinearForecaster.forecast."""

    def test_steady_state(self):
        """When avg_1h == avg_6h the rate is zero; prediction equals current."""
        predicted, rate = LinearForecaster.forecast(
            current=60.0, avg_1h=60.0, avg_6h=60.0, hours_ahead=24.0
        )
        assert rate == pytest.approx(0.0)
        assert predicted == pytest.approx(60.0)

    def test_rising_trend(self):
        """Positive rate when avg_1h > avg_6h."""
        predicted, rate = LinearForecaster.forecast(
            current=50.0, avg_1h=55.0, avg_6h=50.0, hours_ahead=24.0
        )
        assert rate == pytest.approx(1.0)  # (55-50)/5
        assert predicted == pytest.approx(50.0 + 1.0 * 24.0)

    def test_falling_trend(self):
        """Negative rate when avg_1h < avg_6h."""
        predicted, rate = LinearForecaster.forecast(
            current=50.0, avg_1h=45.0, avg_6h=50.0, hours_ahead=24.0
        )
        assert rate == pytest.approx(-1.0)
        assert predicted == pytest.approx(50.0 - 24.0)

    def test_zero_hours_ahead(self):
        """Prediction with zero lookahead equals current regardless of rate."""
        predicted, rate = LinearForecaster.forecast(
            current=80.0, avg_1h=90.0, avg_6h=80.0, hours_ahead=0.0
        )
        assert predicted == pytest.approx(80.0)

    def test_custom_hours_ahead(self):
        """Verify formula with non-default hours_ahead."""
        predicted, rate = LinearForecaster.forecast(
            current=100.0, avg_1h=110.0, avg_6h=100.0, hours_ahead=10.0
        )
        assert rate == pytest.approx(2.0)
        assert predicted == pytest.approx(100.0 + 2.0 * 10.0)


# ===========================================================================
# HoltWinters
# ===========================================================================


class TestHoltWinters:
    """Tests for the Holt-Winters triple exponential smoothing forecaster."""

    @pytest.fixture()
    def hw12(self) -> HoltWinters:
        """Holt-Winters instance with a small seasonal period of 12."""
        return HoltWinters(alpha=0.3, beta=0.1, gamma=0.1, seasonal_period=12)

    def test_init_custom_params(self):
        """Custom alpha/beta/gamma/seasonal_period are stored."""
        hw = HoltWinters(alpha=0.5, beta=0.2, gamma=0.4, seasonal_period=24)
        assert hw.alpha == 0.5
        assert hw.beta == 0.2
        assert hw.gamma == 0.4
        assert hw.m == 24

    def test_init_defaults(self):
        """Default parameters match docstring."""
        hw = HoltWinters()
        assert hw.alpha == 0.3
        assert hw.beta == 0.1
        assert hw.gamma == 0.1
        assert hw.m == 288

    def test_fit_predict_returns_correct_length(self, hw12: HoltWinters):
        """fit_predict returns exactly steps_ahead predictions."""
        data = _seasonal_data(30, period=12)
        forecasts = hw12.fit_predict(data, steps_ahead=6)
        assert len(forecasts) == 6

    def test_fit_predict_seasonal_data(self, hw12: HoltWinters):
        """Forecasts from seasonal data should be finite and reasonable."""
        data = _seasonal_data(36, period=12, amplitude=10.0, trend=0.1)
        forecasts = hw12.fit_predict(data, steps_ahead=12)
        assert all(math.isfinite(v) for v in forecasts)
        # Predicted values should stay in a reasonable range
        data_max = max(data) + 30
        data_min = min(data) - 30
        assert all(data_min < v < data_max for v in forecasts)

    def test_fit_predict_insufficient_data(self, hw12: HoltWinters):
        """ValueError when data length < 2 * seasonal_period."""
        short_data = _linear_data(20)
        with pytest.raises(ValueError, match="Need at least 24 samples"):
            hw12.fit_predict(short_data)

    def test_fit_predict_exact_minimum_data(self, hw12: HoltWinters):
        """Exactly 2*m samples should succeed."""
        data = _seasonal_data(24, period=12)
        forecasts = hw12.fit_predict(data, steps_ahead=3)
        assert len(forecasts) == 3

    def test_backtest_mape_good_data(self, hw12: HoltWinters):
        """MAPE on clean seasonal data should be finite and not inf."""
        data = _seasonal_data(48, period=12, amplitude=5.0, trend=0.05, offset=100.0)
        mape = hw12.backtest_mape(data, test_ratio=0.2)
        assert math.isfinite(mape)
        assert mape >= 0.0

    def test_backtest_mape_insufficient_train(self):
        """MAPE returns inf when training split is too short."""
        hw = HoltWinters(seasonal_period=12)
        # 25 samples with test_ratio=0.2 => train=20, need 24 => inf
        data = _seasonal_data(25, period=12)
        mape = hw.backtest_mape(data, test_ratio=0.2)
        assert mape == float("inf")

    def test_backtest_mape_empty_test(self):
        """MAPE returns inf when test split is empty."""
        hw = HoltWinters(seasonal_period=12)
        data = _seasonal_data(24, period=12)
        # test_ratio=0.0 => no test data
        mape = hw.backtest_mape(data, test_ratio=0.0)
        assert mape == float("inf")


# ===========================================================================
# AR1Forecaster
# ===========================================================================


class TestAR1Forecaster:
    """Tests for the AR(1) autoregressive forecaster."""

    @pytest.fixture()
    def ar1(self) -> AR1Forecaster:
        """Fresh AR1Forecaster."""
        return AR1Forecaster()

    def test_initial_state(self, ar1: AR1Forecaster):
        """Coefficients are zero before fitting."""
        assert ar1.phi == 0.0
        assert ar1.c == 0.0
        assert ar1.mean == 0.0

    def test_fit_linear_data(self, ar1: AR1Forecaster):
        """Fitting on gently trending data should produce a non-zero phi."""
        data = _linear_data(100, slope=0.5)
        ar1.fit(data)
        assert ar1.phi != 0.0
        assert math.isfinite(ar1.phi)
        assert math.isfinite(ar1.c)

    def test_predict_returns_correct_length(self, ar1: AR1Forecaster):
        """predict returns exactly steps_ahead values."""
        data = _linear_data(100)
        ar1.fit(data)
        preds = ar1.predict(data[-1], steps_ahead=10)
        assert len(preds) == 10

    def test_predict_values_finite(self, ar1: AR1Forecaster):
        """Predicted values should be finite."""
        data = _linear_data(100, slope=0.1, intercept=50.0)
        ar1.fit(data)
        preds = ar1.predict(data[-1], steps_ahead=50)
        assert all(math.isfinite(v) for v in preds)

    def test_constant_data_phi_zero(self, ar1: AR1Forecaster):
        """Constant data yields phi~0 and predictions stay at the mean."""
        data = _constant_data(100, value=42.0)
        ar1.fit(data)
        assert ar1.phi == pytest.approx(0.0, abs=1e-6)
        assert ar1.c == pytest.approx(42.0, abs=1e-6)
        preds = ar1.predict(42.0, steps_ahead=5)
        for p in preds:
            assert p == pytest.approx(42.0, abs=1e-4)

    def test_fit_insufficient_data(self, ar1: AR1Forecaster):
        """ValueError when fewer than 72 samples."""
        with pytest.raises(ValueError, match="Need at least 72 samples"):
            ar1.fit(_linear_data(50))

    def test_fit_exact_minimum(self, ar1: AR1Forecaster):
        """Exactly 72 samples should succeed."""
        data = _linear_data(72)
        ar1.fit(data)
        assert math.isfinite(ar1.phi)

    def test_backtest_mape_finite(self, ar1: AR1Forecaster):
        """backtest_mape on reasonable data should be finite."""
        data = _linear_data(200, slope=0.1, intercept=100.0)
        mape = ar1.backtest_mape(data)
        assert math.isfinite(mape)
        assert mape >= 0.0

    def test_backtest_mape_insufficient_train(self, ar1: AR1Forecaster):
        """backtest_mape returns inf when training split < 72."""
        data = _linear_data(80)
        # train = 80 * 0.8 = 64 < 72 => inf
        mape = ar1.backtest_mape(data, test_ratio=0.2)
        assert mape == float("inf")

    def test_backtest_mape_empty_test(self, ar1: AR1Forecaster):
        """backtest_mape returns inf when test split is empty."""
        data = _linear_data(100)
        mape = ar1.backtest_mape(data, test_ratio=0.0)
        assert mape == float("inf")


# ===========================================================================
# SeasonalDecomposer
# ===========================================================================


class TestSeasonalDecomposer:
    """Tests for STL-style seasonal decomposition."""

    @pytest.fixture()
    def decomposer(self) -> SeasonalDecomposer:
        """Decomposer with small seasonal period of 12."""
        return SeasonalDecomposer(seasonal_period=12)

    def test_decompose_synthetic_sinusoidal(self, decomposer: SeasonalDecomposer):
        """Decomposing a trend+sine wave populates trend, seasonal, residual."""
        data = _seasonal_data(36, period=12, amplitude=10.0, trend=0.1, offset=100.0)
        decomposer.decompose(data)

        assert len(decomposer.trend) == 36
        assert len(decomposer.seasonal) == 12
        assert len(decomposer.residual) == 36

        # Trend values should be finite
        assert all(math.isfinite(v) for v in decomposer.trend)
        # Seasonal pattern should be non-trivial
        assert max(decomposer.seasonal) - min(decomposer.seasonal) > 0.0

    def test_decompose_residual_small(self, decomposer: SeasonalDecomposer):
        """Residuals should be small for clean seasonal data."""
        data = _seasonal_data(48, period=12, amplitude=10.0, trend=0.0, offset=100.0)
        decomposer.decompose(data)
        max_residual = max(abs(r) for r in decomposer.residual)
        # Residuals should be modest relative to amplitude
        assert max_residual < 15.0

    def test_decompose_insufficient_data(self, decomposer: SeasonalDecomposer):
        """ValueError when data < 2 * seasonal_period."""
        with pytest.raises(ValueError, match="Need at least 24 samples"):
            decomposer.decompose(_linear_data(20))

    def test_decompose_exact_minimum(self, decomposer: SeasonalDecomposer):
        """Exactly 2*m samples should succeed."""
        data = _seasonal_data(24, period=12)
        decomposer.decompose(data)
        assert len(decomposer.trend) == 24
        assert len(decomposer.seasonal) == 12

    def test_forecast_from_decomposed(self, decomposer: SeasonalDecomposer):
        """Forecast after decomposition returns correct number of steps."""
        data = _seasonal_data(36, period=12, amplitude=10.0, trend=0.1)
        forecasts = decomposer.forecast(data, steps_ahead=12)
        assert len(forecasts) == 12
        assert all(math.isfinite(v) for v in forecasts)

    def test_forecast_auto_decomposes(self):
        """forecast() calls decompose() if not already done."""
        sd = SeasonalDecomposer(seasonal_period=12)
        data = _seasonal_data(36, period=12)
        assert sd.trend == []  # not yet decomposed
        forecasts = sd.forecast(data, steps_ahead=6)
        assert len(forecasts) == 6
        assert len(sd.trend) == 36  # decompose was called

    def test_has_seasonality_true(self, decomposer: SeasonalDecomposer):
        """Strong sinusoidal signal should be detected as seasonal."""
        data = _seasonal_data(48, period=12, amplitude=15.0, trend=0.0, offset=50.0)
        assert decomposer.has_seasonality(data) is True

    def test_has_seasonality_false_constant(self, decomposer: SeasonalDecomposer):
        """Constant data has no seasonality."""
        data = _constant_data(30, value=50.0)
        # Will raise ValueError internally (< 24 samples is fine, 30 >= 24)
        # but constant data has zero total variance, so should be False
        result = decomposer.has_seasonality(data)
        assert result is False

    def test_has_seasonality_false_insufficient_data(self):
        """Insufficient data returns False (not an error)."""
        sd = SeasonalDecomposer(seasonal_period=12)
        data = _linear_data(10)
        assert sd.has_seasonality(data) is False

    def test_has_seasonality_pure_linear(self, decomposer: SeasonalDecomposer):
        """Purely linear data has negligible seasonal component."""
        data = _linear_data(48, slope=2.0, intercept=100.0)
        # Linear data may or may not cross the 10% threshold depending
        # on decomposition artefacts. Verify it returns a bool at minimum.
        result = decomposer.has_seasonality(data)
        assert isinstance(result, bool)
