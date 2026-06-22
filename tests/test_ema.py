"""Tests for EMA indicator module."""

from __future__ import annotations

from src.indicators.ema import (
    EMACrossoverType,
    EMASlopeDirection,
    calculate_ema,
    detect_crossover,
    detect_price_cross,
    detect_price_touch,
    detect_slope,
)


class TestCalculateEMA:
    """Basic EMA calculation."""

    def test_returns_same_length(self, sample_prices: list[float]) -> None:
        result = calculate_ema(sample_prices, period=9)
        assert len(result) == len(sample_prices)

    def test_leading_nones(self, sample_prices: list[float]) -> None:
        result = calculate_ema(sample_prices, period=9)
        assert all(v is None for v in result[:8])
        assert result[8] is not None

    def test_none_insufficient_data(self) -> None:
        result = calculate_ema([1.0, 2.0, 3.0], period=14)
        assert all(v is None for v in result)

    def test_none_empty_input(self) -> None:
        result = calculate_ema([], period=14)
        assert result == []

    def test_none_zero_period(self) -> None:
        result = calculate_ema([1.0, 2.0, 3.0], period=0)
        assert all(v is None for v in result)

    def test_ema_tracks_uptrend(self, uptrend_prices: list[float]) -> None:
        result = calculate_ema(uptrend_prices, period=5)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        # In an uptrend, later EMAs should be higher
        assert valid[-1] > valid[0]

    def test_ema_tracks_downtrend(self, downtrend_prices: list[float]) -> None:
        result = calculate_ema(downtrend_prices, period=5)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        assert valid[-1] < valid[0]

    def test_ema_responds_faster_than_sma(self) -> None:
        # Sharp drop then reversal: EMA catches quicker
        prices = [100.0] * 10 + [90.0, 80.0, 70.0, 71.0, 72.0, 73.0, 74.0]
        ema = calculate_ema(prices, period=5)
        sma_vals = []
        for i in range(5, len(prices)):
            sma_vals.append(sum(prices[i - 5 : i]) / 5)
        ema_valid = [v for v in ema if v is not None]
        # After the reversal, EMA should be higher than SMA (faster to react)
        if len(ema_valid) >= 2 and len(sma_vals) >= 2:
            assert ema_valid[-1] > sma_vals[-1]


class TestDetectCrossover:
    """EMA crossover detection."""

    def test_golden_cross_detected(self) -> None:
        """Fast EMA crosses above slow EMA = golden cross."""
        # Build series where fast overtakes slow at the last bar
        slow = [100.0, 100.0, 100.0, 100.0, 100.0]
        fast = [99.0, 99.2, 99.5, 99.8, 100.1]
        result = detect_crossover(fast, slow, "1h", 9, 21)
        assert result is not None
        assert result.crossover_type == EMACrossoverType.GOLDEN_CROSS
        assert result.fast_period == 9
        assert result.slow_period == 21
        assert result.timeframe == "1h"

    def test_death_cross_detected(self) -> None:
        """Fast EMA crosses below slow EMA = death cross."""
        slow = [100.0, 100.0, 100.0, 100.0, 100.0]
        fast = [101.0, 100.8, 100.5, 100.2, 99.9]
        result = detect_crossover(fast, slow, "30m", 9, 21)
        assert result is not None
        assert result.crossover_type == EMACrossoverType.DEATH_CROSS

    def test_no_cross_when_maintaining_position(self) -> None:
        """No crossover when fast stays on the same side of slow."""
        slow = [None] * 20 + [100.0, 100.0, 100.0, 100.0, 100.0]
        fast = [None] * 20 + [99.0, 99.0, 99.0, 99.0, 99.0]
        result = detect_crossover(fast, slow, "15m", 9, 21)
        assert result is None

    def test_no_cross_insufficient_data(self) -> None:
        result = detect_crossover([None], [None], "1h", 9, 21)
        assert result is None

    def test_no_cross_with_nones(self) -> None:
        result = detect_crossover([None, None], [None, 100.0], "1h", 9, 21)
        assert result is None


class TestDetectPriceTouch:
    """Price-EMA touch/break detection."""

    def test_touch_above_detected(self) -> None:
        ema_vals = [None] * 49 + [99.9995]
        # Price 100.0, EMA 99.9995, distance = 0.0005 / 0.0001 = 5 pips
        # With threshold 10 pips, should be detected
        result = detect_price_touch(100.0, ema_vals, 50, "1h", 10.0, 0.0001)
        assert result is not None
        assert result.direction == "above"
        assert result.ema_period == 50
        assert result.timeframe == "1h"

    def test_touch_below_detected(self) -> None:
        ema_vals = [None] * 49 + [100.0005]
        result = detect_price_touch(100.0, ema_vals, 200, "30m", 10.0, 0.0001)
        assert result is not None
        assert result.direction == "below"

    def test_no_touch_beyond_threshold(self) -> None:
        ema_vals = [None] * 49 + [99.9]
        # Distance = 0.1 / 0.0001 = 1000 pips, way beyond threshold of 5
        result = detect_price_touch(100.0, ema_vals, 50, "1h", 5.0, 0.0001)
        assert result is None

    def test_no_touch_no_ema(self) -> None:
        result = detect_price_touch(100.0, [None], 50, "1h", 1.0, 0.0001)
        assert result is None

    def test_exact_touch(self) -> None:
        ema_vals = [None] * 49 + [100.0]
        result = detect_price_touch(100.0, ema_vals, 50, "15m", 1.0, 0.0001)
        assert result is not None
        assert result.direction == "touch"


class TestDetectPriceCross:
    """Price crossing through EMA detection."""

    def test_cross_above_detected(self) -> None:
        """Price was below EMA, now above = cross_above."""
        ema_vals = [None] * 49 + [100.0, 100.0]
        result = detect_price_cross(101.0, 99.0, ema_vals, 50, "1h", 10.0, 0.0001)
        assert result is not None
        assert result.direction == "cross_above"

    def test_cross_below_detected(self) -> None:
        """Price was above EMA, now below = cross_below."""
        ema_vals = [None] * 49 + [100.0, 100.0]
        result = detect_price_cross(99.0, 101.0, ema_vals, 50, "1h", 10.0, 0.0001)
        assert result is not None
        assert result.direction == "cross_below"

    def test_no_cross_when_staying(self) -> None:
        ema_vals = [None] * 49 + [100.0, 100.0]
        result = detect_price_cross(101.0, 101.0, ema_vals, 50, "1h", 10.0, 0.0001)
        assert result is None

    def test_no_cross_insufficient_data(self) -> None:
        result = detect_price_cross(100.0, 99.0, [None], 50, "1h", 10.0, 0.0001)
        assert result is None

    def test_no_cross_no_prev_price(self) -> None:
        ema_vals = [None] * 49 + [100.0, 100.0]
        result = detect_price_cross(101.0, None, ema_vals, 50, "1h", 10.0, 0.0001)
        assert result is None


class TestDetectSlope:
    """EMA slope/direction detection."""

    def test_rising_detected(self) -> None:
        """EMA values increasing = rising."""
        ema_vals = [None] * 49 + [100.0, 100.5, 101.0, 101.5]
        result = detect_slope(ema_vals, 9, "1h", lookback=3)
        assert result is not None
        assert result.slope_direction == EMASlopeDirection.RISING
        assert result.period == 9

    def test_falling_detected(self) -> None:
        """EMA values decreasing = falling."""
        ema_vals = [None] * 49 + [101.5, 101.0, 100.5, 100.0]
        result = detect_slope(ema_vals, 21, "30m", lookback=3)
        assert result is not None
        assert result.slope_direction == EMASlopeDirection.FALLING

    def test_flat_detected(self) -> None:
        """EMA values nearly unchanged = flat."""
        ema_vals = [None] * 49 + [100.0, 100.0001, 100.0002, 100.0001]
        result = detect_slope(ema_vals, 50, "15m", lookback=3)
        assert result is not None
        assert result.slope_direction == EMASlopeDirection.FLAT

    def test_insufficient_data(self) -> None:
        result = detect_slope([None, None], 9, "1h")
        assert result is None

    def test_insufficient_valid_values(self) -> None:
        ema_vals = [None] * 50 + [100.0, 100.1]
        result = detect_slope(ema_vals, 9, "1h", lookback=5)
        assert result is None
