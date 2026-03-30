"""Tests for RSI indicator."""

from __future__ import annotations

import math

from src.indicators.rsi import calculate_rsi


class TestRSI:
    """RSI calculation tests."""

    def test_rsi_returns_value_in_range(self, sample_prices):
        """RSI should return value between 0 and 100."""
        result = calculate_rsi(sample_prices, period=14)
        assert result is not None
        assert 0 <= result <= 100

    def test_rsi_none_insufficient_data(self):
        """RSI returns None when insufficient data."""
        assert calculate_rsi([1.0, 2.0, 3.0], period=14) is None
        assert calculate_rsi([], period=14) is None

    def test_rsi_overbought_strong_uptrend(self, uptrend_prices):
        """RSI should be > 70 in strong uptrend."""
        result = calculate_rsi(uptrend_prices, period=14)
        assert result is not None
        assert result > 70

    def test_rsi_oversold_strong_downtrend(self, downtrend_prices):
        """RSI should be < 30 in strong downtrend."""
        result = calculate_rsi(downtrend_prices, period=14)
        assert result is not None
        assert result < 30

    def test_rsi_exact_period(self):
        """RSI with exactly period+1 data should work."""
        prices = [100.0 + (i % 5) * 0.2 for i in range(15)]
        result = calculate_rsi(prices, period=14)
        assert result is not None
        assert 0 <= result <= 100

    def test_rsi_neutral_zone(self):
        """RSI around 50 in sideways market."""
        prices = [100.0 + 5.0 * math.sin(i * 0.5) for i in range(30)]
        result = calculate_rsi(prices, period=14)
        assert result is not None
        assert 30 < result < 70
