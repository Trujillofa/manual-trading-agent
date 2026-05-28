"""Tests for RSI-MA calculations."""

from __future__ import annotations

import pytest

from src.indicators.rsi import (
    calculate_rsi_ma_series,
    detect_rsi_curl,
    detect_rsi_slope_change,
    rsi_ma_distance,
)


class TestCalculateRsiMaSeries:
    """Tests for calculate_rsi_ma_series function."""

    def test_sma_over_rsi_values(self):
        """SMA of RSI values with period 3."""
        rsi = [30.0, 32.0, 31.0, 33.0, 35.0, 34.0]
        result = calculate_rsi_ma_series(rsi, ma_period=3)
        # index 0, 1: None (< 3 values)
        # index 2: (30+32+31)/3 = 31.0
        # index 3: (32+31+33)/3 = 32.0
        # index 4: (31+33+35)/3 = 33.0
        # index 5: (33+35+34)/3 = 34.0
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(31.0)
        assert result[3] == pytest.approx(32.0)
        assert result[4] == pytest.approx(33.0)
        assert result[5] == pytest.approx(34.0)

    def test_none_handling_skips_gaps(self):
        """None values are skipped, window uses only valid RSI values."""
        rsi = [30.0, None, 31.0, None, 32.0, 33.0, 34.0]
        result = calculate_rsi_ma_series(rsi, ma_period=3)
        # Window fills from valid values: 30, 31, 32 at index 4 → avg 31.0
        assert result[0] is None
        assert result[1] is None
        assert result[2] is None
        assert result[3] is None
        # index 4: [30, 31, 32] avg 31.0
        assert result[4] == pytest.approx(31.0)
        # index 5: [31, 32, 33] avg 32.0
        assert result[5] == pytest.approx(32.0)
        # index 6: [32, 33, 34] avg 33.0
        assert result[6] == pytest.approx(33.0)

    def test_all_none_returns_all_none(self):
        """All-None input returns all-None output."""
        rsi = [None, None, None, None]
        result = calculate_rsi_ma_series(rsi, ma_period=3)
        assert all(v is None for v in result)

    def test_insufficient_valid_data(self):
        """Not enough valid data for a full window."""
        rsi = [30.0, None, None, 31.0, None]
        result = calculate_rsi_ma_series(rsi, ma_period=3)
        # Only 2 valid values, need 3
        assert all(v is None for v in result)

    def test_output_length_matches_input(self):
        """Output list is same length as input."""
        rsi = [30.0, 32.0, 31.0, 33.0, 35.0]
        result = calculate_rsi_ma_series(rsi, ma_period=3)
        assert len(result) == len(rsi)


class TestDetectRsiCurl:
    """Tests for detect_rsi_curl function."""

    @pytest.fixture
    def buy_curl_data(self):
        """RSI crosses above its MA (buy curl)."""
        # RSI: 25, 27, 29, 31 — crossing above MA which is at 30
        rsi = [20.0, 22.0, 25.0, 27.0, 29.0, 31.0]
        rsi_ma = [None, None, 30.0, 30.0, 30.0, 30.0]
        return rsi, rsi_ma

    @pytest.fixture
    def sell_curl_data(self):
        """RSI crosses below its MA (sell curl)."""
        # RSI: 78, 75, 73, 71 — crossing below MA which is at 72
        rsi = [80.0, 78.0, 75.0, 73.0, 71.0, 69.0]
        rsi_ma = [None, None, 72.0, 72.0, 72.0, 72.0]
        return rsi, rsi_ma

    def test_buy_curl_detected(self, buy_curl_data):
        """RSI crosses above MA → buy curl."""
        rsi, rsi_ma = buy_curl_data
        assert detect_rsi_curl(rsi, rsi_ma, "buy", lookback=3) is True

    def test_sell_curl_detected(self, sell_curl_data):
        """RSI crosses below MA → sell curl."""
        rsi, rsi_ma = sell_curl_data
        assert detect_rsi_curl(rsi, rsi_ma, "sell", lookback=3) is True

    def test_no_curl_when_always_above(self):
        """RSI stays above MA — no curl."""
        rsi = [40.0, 42.0, 44.0]
        rsi_ma = [30.0, 30.0, 30.0]
        assert detect_rsi_curl(rsi, rsi_ma, "buy", lookback=3) is False

    def test_no_curl_when_always_below(self):
        """RSI stays below MA — no curl."""
        rsi = [20.0, 22.0, 24.0]
        rsi_ma = [30.0, 30.0, 30.0]
        assert detect_rsi_curl(rsi, rsi_ma, "sell", lookback=3) is False

    def test_insufficient_data_returns_false(self):
        """Not enough data returns False."""
        rsi = [30.0, 32.0]
        rsi_ma = [30.0, 30.0]
        assert detect_rsi_curl(rsi, rsi_ma, "buy", lookback=3) is False

    def test_none_values_skipped(self):
        """None values in data are skipped gracefully."""
        rsi = [None, 25.0, 31.0]
        rsi_ma = [None, 30.0, 30.0]
        assert detect_rsi_curl(rsi, rsi_ma, "buy", lookback=2) is True


class TestDetectRsiSlopeChange:
    """Tests for detect_rsi_slope_change function."""

    def test_buy_inflection(self):
        """RSI-MA was falling, now flattening/turning up."""
        # Early slope: 28→26 = -2 (falling), Recent slope: 27→28 = +1 (rising)
        rsi_ma = [30.0, 29.0, 28.0, 27.0, 26.0, 27.0, 28.0]
        assert detect_rsi_slope_change(rsi_ma, "buy", lookback=3) is True

    def test_sell_inflection(self):
        """RSI-MA was rising, now flattening/turning down."""
        # Early slope: 72→74 = +2 (rising), Recent slope: 73→71 = -2 (falling)
        rsi_ma = [70.0, 71.0, 72.0, 73.0, 74.0, 73.0, 71.0]
        assert detect_rsi_slope_change(rsi_ma, "sell", lookback=3) is True

    def test_no_inflection_still_falling(self):
        """RSI-MA continues falling — no buy inflection."""
        rsi_ma = [50.0, 48.0, 46.0, 44.0, 42.0, 40.0, 38.0]
        assert detect_rsi_slope_change(rsi_ma, "buy", lookback=3) is False

    def test_no_inflection_still_rising(self):
        """RSI-MA continues rising — no sell inflection."""
        rsi_ma = [50.0, 52.0, 54.0, 56.0, 58.0, 60.0, 62.0]
        assert detect_rsi_slope_change(rsi_ma, "sell", lookback=3) is False

    def test_insufficient_data_returns_false(self):
        """Not enough values returns False."""
        rsi_ma = [30.0, 32.0]
        assert detect_rsi_slope_change(rsi_ma, "buy") is False

    def test_none_values_cause_false(self):
        """Too many None values means not enough valid data."""
        rsi_ma = [None, None, None, None, 30.0, None, None]
        assert detect_rsi_slope_change(rsi_ma, "buy", lookback=3) is False


class TestRsiMaDistance:
    """Tests for rsi_ma_distance function."""

    def test_buy_within_range(self):
        """RSI below MA within acceptable distance."""
        # RSI 25, MA 30 → diff = -5, distance = 5 (between 3 and 15)
        assert rsi_ma_distance(25.0, 30.0, "buy", min_distance=3.0, max_distance=15.0) is True

    def test_buy_below_minimum(self):
        """RSI too close to MA — not enough distance."""
        # RSI 29, MA 30 → diff = -1, distance = 1 (< 3)
        assert rsi_ma_distance(29.0, 30.0, "buy", min_distance=3.0, max_distance=15.0) is False

    def test_buy_above_maximum(self):
        """RSI too far below MA — extreme/pushback."""
        # RSI 10, MA 30 → diff = -20, distance = 20 (> 15)
        assert rsi_ma_distance(10.0, 30.0, "buy", min_distance=3.0, max_distance=15.0) is False

    def test_sell_within_range(self):
        """RSI above MA within acceptable distance."""
        # RSI 78, MA 72 → diff = 6 (between 3 and 15)
        assert rsi_ma_distance(78.0, 72.0, "sell", min_distance=3.0, max_distance=15.0) is True

    def test_sell_below_minimum(self):
        """RSI too close to MA — not enough distance."""
        # RSI 74, MA 72 → diff = 2 (< 3)
        assert rsi_ma_distance(74.0, 72.0, "sell", min_distance=3.0, max_distance=15.0) is False

    def test_sell_above_maximum(self):
        """RSI too far above MA — extreme."""
        # RSI 90, MA 72 → diff = 18 (> 15)
        assert rsi_ma_distance(90.0, 72.0, "sell", min_distance=3.0, max_distance=15.0) is False

    def test_buy_wrong_direction(self):
        """RSI above MA for buy signal — wrong direction."""
        # RSI 35, MA 30 → diff = +5 (RSI above MA, should be below for buy)
        assert rsi_ma_distance(35.0, 30.0, "buy", min_distance=3.0, max_distance=15.0) is False

    def test_sell_wrong_direction(self):
        """RSI below MA for sell signal — wrong direction."""
        # RSI 65, MA 72 → diff = -7 (RSI below MA, should be above for sell)
        assert rsi_ma_distance(65.0, 72.0, "sell", min_distance=3.0, max_distance=15.0) is False
