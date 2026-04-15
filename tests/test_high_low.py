"""Tests for highest high / lowest low indicators."""

from __future__ import annotations

from src.indicators.high_low import (
    highest_high,
    is_breakout_high,
    is_breakout_low,
    lowest_low,
    previous_rolling_highest_high,
    previous_rolling_lowest_low,
    rolling_highest_highs,
    rolling_lowest_lows,
)


class TestHighestHigh:
    """Tests for highest_high function."""

    def test_highest_high_basic(self, sample_highs):
        """Should return max of last lookback elements."""
        result = highest_high(sample_highs, lookback=5)
        # Last 5 elements: [1.0970, 1.0955, 1.0980, 1.0990, 1.0975]
        assert result == 1.0990

    def test_highest_high_insufficient_data(self):
        """Should return None when data too short."""
        assert highest_high([1.0, 2.0], lookback=5) is None

    def test_highest_high_exact_lookback(self):
        """Should work when data length equals lookback."""
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert highest_high(data, lookback=5) == 5.0


class TestLowestLow:
    """Tests for lowest_low function."""

    def test_lowest_low_basic(self, sample_lows):
        """Should return min of last lookback elements."""
        result = lowest_low(sample_lows, lookback=5)
        # Last 5 elements: [1.0870, 1.0855, 1.0880, 1.0890, 1.0875]
        assert result == 1.0855

    def test_lowest_low_insufficient_data(self):
        """Should return None when data too short."""
        assert lowest_low([1.0, 2.0], lookback=5) is None


class TestBreakout:
    """Tests for breakout detection functions."""

    def test_is_breakout_high_true(self):
        """Should detect breakout above highest high."""
        assert is_breakout_high(1.100, 1.095, threshold_pct=0.0) is True

    def test_is_breakout_high_false(self):
        """Should not detect breakout when below."""
        assert is_breakout_high(1.090, 1.095, threshold_pct=0.0) is False

    def test_is_breakout_high_with_threshold(self):
        """Should respect threshold percentage."""
        # 1.096 / 1.095 = 1.0009 (0.09% - below 0.5% threshold)
        assert is_breakout_high(1.096, 1.095, threshold_pct=0.005) is False
        # 1.099 / 1.095 = 1.0037 (0.37% - below 0.5% threshold)
        assert is_breakout_high(1.099, 1.095, threshold_pct=0.005) is False
        # 1.101 / 1.095 = 1.0055 (0.55% - above 0.5% threshold)
        assert is_breakout_high(1.101, 1.095, threshold_pct=0.005) is True

    def test_is_breakout_low_true(self):
        """Should detect breakout below lowest low."""
        assert is_breakout_low(1.075, 1.080, threshold_pct=0.0) is True

    def test_is_breakout_low_false(self):
        """Should not detect breakout when above."""
        assert is_breakout_low(1.085, 1.080, threshold_pct=0.0) is False


class TestRolling:
    """Tests for rolling high/low functions."""

    def test_rolling_highest_highs_length(self, sample_highs):
        """Should return same length as input."""
        result = rolling_highest_highs(sample_highs, lookback=5)
        assert len(result) == len(sample_highs)

    def test_rolling_highest_highs_leading_nones(self, sample_highs):
        """First lookback-1 values should be None."""
        result = rolling_highest_highs(sample_highs, lookback=5)
        assert result[0] is None
        assert result[1] is None
        assert result[2] is None
        assert result[3] is None

    def test_rolling_highest_highs_values(self, sample_highs):
        """Non-None values should be correct."""
        result = rolling_highest_highs(sample_highs, lookback=5)
        assert result[4] == 1.0935
        assert result[-1] == 1.0990

    def test_rolling_lowest_lows_leading_nones(self, sample_lows):
        """First lookback-1 values should be None."""
        result = rolling_lowest_lows(sample_lows, lookback=5)
        assert result[0] is None
        assert result[1] is None
        assert result[2] is None
        assert result[3] is None


class TestBreakoutInvariants:
    """Guard the live-vs-research parity of close-based breakout semantics.

    The live scanner must use prior-bar HH/LL (excluding current bar) so that
    `close > hh` and `close < ll` remain reachable. Including the current bar
    makes these predicates false by OHLC construction, which previously made
    V1 and V2_b>0 signals impossible in production while the bakeoff paths
    (which use previous_rolling_*) continued to report trades.
    """

    def test_previous_rolling_high_permits_breakout(self):
        """A close making a new 20-bar high must trigger is_breakout_high."""
        lookback = 20
        highs = [1.0] * lookback + [1.05]
        closes = [1.0] * lookback + [1.02]
        hh_prev = previous_rolling_highest_high(highs, lookback, len(highs) - 1)
        assert hh_prev == 1.0
        assert is_breakout_high(closes[-1], hh_prev, threshold_pct=0.0)

    def test_previous_rolling_low_permits_breakout(self):
        """A close making a new 20-bar low must trigger is_breakout_low."""
        lookback = 20
        lows = [1.0] * lookback + [0.95]
        closes = [1.0] * lookback + [0.98]
        ll_prev = previous_rolling_lowest_low(lows, lookback, len(lows) - 1)
        assert ll_prev == 1.0
        assert is_breakout_low(closes[-1], ll_prev, threshold_pct=0.0)

    def test_inclusive_high_blocks_breakout_regression(self):
        """Regression: `highest_high` includes the current bar, so a new-high
        bar can never satisfy `close > hh` under the old live-scan code."""
        lookback = 20
        highs = [1.0] * lookback + [1.05]
        closes = [1.0] * lookback + [1.02]
        hh_inclusive = highest_high(highs, lookback=lookback)
        # Current bar's high of 1.05 is now part of hh, so close <= high <= hh
        assert hh_inclusive == 1.05
        assert not is_breakout_high(closes[-1], hh_inclusive, threshold_pct=0.0)

    def test_inclusive_low_blocks_breakout_regression(self):
        """Regression: symmetric case for lowest_low + is_breakout_low."""
        lookback = 20
        lows = [1.0] * lookback + [0.95]
        closes = [1.0] * lookback + [0.98]
        ll_inclusive = lowest_low(lows, lookback=lookback)
        assert ll_inclusive == 0.95
        assert not is_breakout_low(closes[-1], ll_inclusive, threshold_pct=0.0)

    def test_previous_rolling_high_blocks_false_positive(self):
        """Close below prior 20-bar high must not trigger."""
        lookback = 20
        highs = [1.0] * lookback + [0.99]
        closes = [1.0] * lookback + [0.995]
        hh_prev = previous_rolling_highest_high(highs, lookback, len(highs) - 1)
        assert hh_prev == 1.0
        assert not is_breakout_high(closes[-1], hh_prev, threshold_pct=0.0)
