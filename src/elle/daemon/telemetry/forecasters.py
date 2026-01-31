"""Pure Python forecasting algorithms for telemetry trend analysis.

Provides HoltWinters, AR(1), SeasonalDecomposer, and LinearForecaster
implementations without any numpy dependency.
"""

from __future__ import annotations


class LinearForecaster:
    """Simple linear extrapolation forecaster.

    Uses the rate of change between 1h and 6h averages to project future values.
    This is the fallback forecaster when insufficient data exists for more
    sophisticated methods.
    """

    @staticmethod
    def forecast(
        current: float,
        avg_1h: float,
        avg_6h: float,
        hours_ahead: float = 24.0,
    ) -> tuple[float, float]:
        """Forecast using linear extrapolation.

        Args:
            current: Current metric value
            avg_1h: Average over the last hour
            avg_6h: Average over the last 6 hours
            hours_ahead: Hours to forecast ahead

        Returns:
            Tuple of (predicted_value, rate_per_hour)
        """
        rate = (avg_1h - avg_6h) / 5.0
        predicted = current + rate * hours_ahead
        return predicted, rate


class HoltWinters:
    """Triple exponential smoothing with additive seasonality.

    Implements the Holt-Winters method for time series with trend
    and seasonal components. Requires at least 2 full seasonal
    periods (576 samples at 5-minute intervals = 48h).

    Parameters:
        alpha: Level smoothing (default 0.3)
        beta: Trend smoothing (default 0.1)
        gamma: Seasonal smoothing (default 0.1)
        seasonal_period: Number of samples per season (default 288 = 24h at 5min)
    """

    def __init__(
        self,
        alpha: float = 0.3,
        beta: float = 0.1,
        gamma: float = 0.1,
        seasonal_period: int = 288,
    ):
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.m = seasonal_period

    def fit_predict(
        self,
        data: list[float],
        steps_ahead: int = 288,
    ) -> list[float]:
        """Fit model on data and forecast steps_ahead values.

        Args:
            data: Historical time series values (must have >= 2*m samples)
            steps_ahead: Number of future steps to predict

        Returns:
            List of predicted values

        Raises:
            ValueError: If insufficient data for the seasonal period
        """
        m = self.m
        n = len(data)

        if n < 2 * m:
            raise ValueError(f"Need at least {2 * m} samples for seasonal period {m}, got {n}")

        # Initialize level: average of first season
        level = sum(data[:m]) / m

        # Initialize trend: average difference between first two seasons
        trend = 0.0
        for i in range(m):
            trend += (data[m + i] - data[i]) / m
        trend /= m

        # Initialize seasonal components: deviation from first-season average
        seasonal = [0.0] * m
        for i in range(m):
            seasonal[i] = data[i] - level

        # Run through data
        for t in range(n):
            val = data[t]
            s_idx = t % m

            prev_level = level
            # Level update
            level = self.alpha * (val - seasonal[s_idx]) + (1 - self.alpha) * (level + trend)
            # Trend update
            trend = self.beta * (level - prev_level) + (1 - self.beta) * trend
            # Seasonal update
            seasonal[s_idx] = self.gamma * (val - level) + (1 - self.gamma) * seasonal[s_idx]

        # Forecast
        forecasts = []
        for h in range(1, steps_ahead + 1):
            s_idx = (n + h - 1) % m
            forecasts.append(level + h * trend + seasonal[s_idx])

        return forecasts

    def backtest_mape(self, data: list[float], test_ratio: float = 0.2) -> float:
        """Compute MAPE on a train/test split.

        Args:
            data: Full time series
            test_ratio: Fraction of data to use as test set

        Returns:
            Mean Absolute Percentage Error (0-100+)
        """
        split = int(len(data) * (1 - test_ratio))
        if split < 2 * self.m:
            return float("inf")

        train = data[:split]
        test = data[split:]

        if not test:
            return float("inf")

        try:
            predictions = self.fit_predict(train, len(test))
        except ValueError:
            return float("inf")

        errors = []
        for actual, predicted in zip(test, predictions, strict=False):
            if abs(actual) > 1e-10:
                errors.append(abs((actual - predicted) / actual) * 100.0)

        if not errors:
            return float("inf")

        return sum(errors) / len(errors)


class AR1Forecaster:
    """Single-coefficient autoregressive AR(1) model.

    Y_t = c + phi * Y_{t-1} + epsilon

    Requires at least 72 samples (6h of data at 5-minute intervals).
    """

    def __init__(self) -> None:
        self.phi: float = 0.0
        self.c: float = 0.0
        self.mean: float = 0.0

    def fit(self, data: list[float]) -> None:
        """Fit AR(1) model to data.

        Args:
            data: Historical time series (must have >= 72 samples)

        Raises:
            ValueError: If insufficient data
        """
        n = len(data)
        if n < 72:
            raise ValueError(f"Need at least 72 samples for AR(1), got {n}")

        # Compute mean
        self.mean = sum(data) / n

        # Compute variance and autocovariance in single pass
        var_sum = 0.0
        cov_sum = 0.0
        for i in range(1, n):
            d_curr = data[i] - self.mean
            d_prev = data[i - 1] - self.mean
            var_sum += d_prev * d_prev
            cov_sum += d_curr * d_prev

        if var_sum < 1e-10:
            self.phi = 0.0
            self.c = self.mean
            return

        self.phi = cov_sum / var_sum
        self.c = self.mean * (1 - self.phi)

    def predict(self, last_value: float, steps_ahead: int = 1) -> list[float]:
        """Predict future values.

        Args:
            last_value: Most recent observation
            steps_ahead: Number of steps to forecast

        Returns:
            List of predicted values
        """
        predictions = []
        current = last_value
        for _ in range(steps_ahead):
            current = self.c + self.phi * current
            predictions.append(current)
        return predictions

    def backtest_mape(self, data: list[float], test_ratio: float = 0.2) -> float:
        """Compute MAPE on train/test split."""
        split = int(len(data) * (1 - test_ratio))
        if split < 72:
            return float("inf")

        train = data[:split]
        test = data[split:]

        if not test:
            return float("inf")

        self.fit(train)
        predictions = self.predict(train[-1], len(test))

        errors = []
        for actual, predicted in zip(test, predictions, strict=False):
            if abs(actual) > 1e-10:
                errors.append(abs((actual - predicted) / actual) * 100.0)

        if not errors:
            return float("inf")

        return sum(errors) / len(errors)


class SeasonalDecomposer:
    """STL-style seasonal decomposition for forecasting.

    Decomposes time series into trend, seasonal, and residual components.
    Uses centered moving average for trend and seasonal averaging.
    Requires at least 2 full seasonal periods.
    """

    def __init__(self, seasonal_period: int = 288):
        self.m = seasonal_period
        self.trend: list[float] = []
        self.seasonal: list[float] = []
        self.residual: list[float] = []

    def decompose(self, data: list[float]) -> None:
        """Decompose time series into components.

        Args:
            data: Historical time series (must have >= 2*m samples)

        Raises:
            ValueError: If insufficient data
        """
        n = len(data)
        m = self.m

        if n < 2 * m:
            raise ValueError(f"Need at least {2 * m} samples, got {n}")

        # Step 1: Trend via centered moving average
        half = m // 2
        self.trend = [0.0] * n
        for i in range(half, n - half):
            window_sum = sum(data[i - half : i + half + 1])
            self.trend[i] = window_sum / (2 * half + 1)

        # Extend trend to edges using linear extrapolation
        if self.trend[half] != 0 and self.trend[half + 1] != 0:
            slope_start = self.trend[half + 1] - self.trend[half]
            for i in range(half):
                self.trend[i] = self.trend[half] - (half - i) * slope_start

        if self.trend[n - half - 1] != 0 and self.trend[n - half - 2] != 0:
            slope_end = self.trend[n - half - 1] - self.trend[n - half - 2]
            for i in range(n - half, n):
                self.trend[i] = self.trend[n - half - 1] + (i - n + half + 1) * slope_end

        # Step 2: Seasonal = average of (data - trend) at each position
        self.seasonal = [0.0] * m
        counts = [0] * m
        for i in range(n):
            if self.trend[i] != 0:
                pos = i % m
                self.seasonal[pos] += data[i] - self.trend[i]
                counts[pos] += 1

        for i in range(m):
            if counts[i] > 0:
                self.seasonal[i] /= counts[i]

        # Step 3: Residual
        self.residual = [0.0] * n
        for i in range(n):
            self.residual[i] = data[i] - self.trend[i] - self.seasonal[i % m]

    def forecast(self, data: list[float], steps_ahead: int = 288) -> list[float]:
        """Forecast using trend extrapolation + seasonal overlay.

        Args:
            data: Historical data (decompose will be called if not done)
            steps_ahead: Number of steps to predict

        Returns:
            List of predicted values
        """
        if not self.trend:
            self.decompose(data)

        n = len(data)

        # Extrapolate trend linearly
        if n >= 2:
            trend_slope = self.trend[-1] - self.trend[-2]
        else:
            trend_slope = 0.0

        forecasts = []
        for h in range(1, steps_ahead + 1):
            trend_val = self.trend[-1] + h * trend_slope
            season_val = self.seasonal[(n + h - 1) % self.m]
            forecasts.append(trend_val + season_val)

        return forecasts

    def has_seasonality(self, data: list[float]) -> bool:
        """Check if data has significant seasonal pattern.

        Returns True if seasonal component variance is >10% of total variance.
        """
        if not self.seasonal:
            try:
                self.decompose(data)
            except ValueError:
                return False

        if not self.seasonal:
            return False

        seasonal_var = sum(s * s for s in self.seasonal) / len(self.seasonal)
        total_var = sum((d - sum(data) / len(data)) ** 2 for d in data) / len(data)

        if total_var < 1e-10:
            return False

        return seasonal_var / total_var > 0.10
