"""Tests for RSI divergence detection."""

from __future__ import annotations

import pytest

from src.indicators.rsi import (
    DivergenceType,
    detect_bearish_divergence,
    detect_bullish_divergence,
    detect_divergence,
    find_peaks,
    find_troughs,
)


class TestFindPeaks:
    """Tests for find_peaks function."""

    @pytest.fixture
    def peak_series(self):
        """Series with two clear peaks."""
        # Peak at index 5 (value 10) and index 15 (value 12)
        values = [1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1, 2, 3, 4, 5, 12, 5, 4, 3, 2, 1]
        return values

    def test_finds_peaks(self, peak_series):
        """Should find local maxima."""
        peaks = find_peaks(peak_series, lookback=3)
        assert len(peaks) >= 2
        indices = [p[0] for p in peaks]
        assert 5 in indices
        assert 15 in indices

    def test_finds_peak_values(self, peak_series):
        """Peak values should match."""
        peaks = find_peaks(peak_series, lookback=3)
        peak_by_idx = {p[0]: p[1] for p in peaks}
        assert peak_by_idx.get(5) == 10
        assert peak_by_idx.get(15) == 12

    def test_insufficient_data_no_peaks(self):
        """Not enough data for lookback should return empty."""
        peaks = find_peaks([1, 2, 3, 1, 2], lookback=5)
        assert peaks == []

    def test_monotonic_no_peaks(self):
        """Strictly increasing series has no peaks."""
        peaks = find_peaks(list(range(20)), lookback=3)
        assert peaks == []


class TestFindTroughs:
    """Tests for find_troughs function."""

    @pytest.fixture
    def trough_series(self):
        """Series with two clear troughs."""
        values = [-1, -2, -3, -4, -5, -10, -5, -4, -3, -2, -1, -2, -3, -4, -5, -8, -5, -4, -3, -2, -1]
        return values

    def test_finds_troughs(self, trough_series):
        """Should find local minima."""
        troughs = find_troughs(trough_series, lookback=3)
        assert len(troughs) >= 2
        indices = [t[0] for t in troughs]
        assert 5 in indices
        assert 15 in indices

    def test_finds_trough_values(self, trough_series):
        """Trough values should match."""
        troughs = find_troughs(trough_series, lookback=3)
        trough_by_idx = {t[0]: t[1] for t in troughs}
        assert trough_by_idx.get(5) == -10
        assert trough_by_idx.get(15) == -8

    def test_insufficient_data_no_troughs(self):
        """Not enough data should return empty."""
        troughs = find_troughs([5, 4, 3, 5, 4], lookback=5)
        assert troughs == []

    def test_monotonic_decreasing_no_troughs(self):
        """Strictly decreasing series has no troughs."""
        troughs = find_troughs(list(range(20, 0, -1)), lookback=3)
        assert troughs == []


class TestDetectBullishDivergence:
    """Tests for detect_bullish_divergence."""

    @pytest.fixture
    def bullish_divergence_data(self):
        """Price makes lower low while RSI makes higher low.

        Uses quadratic U-shapes so find_troughs reliably detects the minima.
        Price troughs at indices ~20 (value 100) and ~45 (value 95, lower).
        RSI troughs at same indices: 30 (first) then 40 (second, higher).
        """
        n = 60
        prices = []
        for i in range(n):
            if i < 33:
                prices.append(100 + (i - 20) ** 2 * 0.1)
            else:
                prices.append(95 + (i - 45) ** 2 * 0.1)

        rsi: list[float | None] = [None] * 15
        for i in range(15, 33):
            rsi.append(30 + (i - 20) ** 2 * 0.8)
        for i in range(33, n):
            rsi.append(40 + (i - 45) ** 2 * 0.5)

        return prices, rsi

    def test_detects_bullish_divergence(self, bullish_divergence_data):
        """Should detect bullish divergence: price LL, RSI HL."""
        prices, rsi = bullish_divergence_data
        result = detect_bullish_divergence(prices, rsi, lookback=3, min_separation=5)
        assert result is not None
        assert result.divergence_type == DivergenceType.BULLISH

    def test_no_divergence_no_clear_troughs(self):
        """Smooth data without clear troughs returns None."""
        prices = [100.0 + i * 0.1 for i in range(30)]
        rsi = [50.0] * 30
        result = detect_bullish_divergence(prices, rsi, lookback=3)
        assert result is None

    def test_insufficient_data_returns_none(self):
        """Too little data returns None."""
        prices = [100.0, 99.0, 98.0, 99.0, 100.0]
        rsi = [30.0, 25.0, 20.0, 30.0, 40.0]
        result = detect_bullish_divergence(prices, rsi, lookback=5, min_separation=5)
        assert result is None

    def test_single_trough_no_divergence(self):
        """Only one trough available."""
        prices = [100.0, 99.0, 98.0, 99.0, 100.0, 101.0]
        rsi = [None, None, None, None, None, 50.0]
        result = detect_bullish_divergence(prices, rsi, lookback=1, min_separation=1)
        assert result is None


class TestDetectBearishDivergence:
    """Tests for detect_bearish_divergence."""

    @pytest.fixture
    def bearish_divergence_data(self):
        """Price makes higher high while RSI makes lower high.

        Uses inverted quadratic (n-shaped) so find_peaks reliably detects maxima.
        Price peaks at indices ~20 (value 100) and ~45 (value 105, higher).
        RSI peaks at same indices: 80 (first) then 70 (second, lower).
        """
        n = 60
        prices = []
        for i in range(n):
            if i < 33:
                prices.append(100 - (i - 20) ** 2 * 0.1)
            else:
                prices.append(105 - (i - 45) ** 2 * 0.1)

        rsi: list[float | None] = [None] * 15
        for i in range(15, 33):
            rsi.append(80 - (i - 20) ** 2 * 0.5)
        for i in range(33, n):
            rsi.append(70 - (i - 45) ** 2 * 0.3)

        return prices, rsi

    def test_detects_bearish_divergence(self, bearish_divergence_data):
        """Should detect bearish divergence: price HH, RSI LH."""
        prices, rsi = bearish_divergence_data
        result = detect_bearish_divergence(prices, rsi, lookback=3, min_separation=5)
        assert result is not None
        assert result.divergence_type == DivergenceType.BEARISH

    def test_no_divergence_no_clear_peaks(self):
        """Smooth data without clear peaks returns None."""
        prices = [100.0 - i * 0.1 for i in range(30)]
        rsi = [50.0] * 30
        result = detect_bearish_divergence(prices, rsi, lookback=3)
        assert result is None

    def test_insufficient_data_returns_none(self):
        """Too little data returns None."""
        prices = [100.0, 101.0, 100.0, 99.0, 100.0]
        rsi = [70.0, 75.0, 60.0, 50.0, 55.0]
        result = detect_bearish_divergence(prices, rsi, lookback=5)
        assert result is None

    def test_single_peak_no_divergence(self):
        """Only one peak — should return None."""
        prices = [100.0 + i * 0.5 for i in range(15)] + [107.0 - i * 0.5 for i in range(15)]
        rsi = [50.0] * 30
        result = detect_bearish_divergence(prices, rsi, lookback=5)
        assert result is None


class TestDetectDivergence:
    """Tests for the combined detect_divergence function."""

    @pytest.fixture
    def bullish_divergence_data(self):
        """Price LL, RSI HL — same construction as bullish test above."""
        n = 60
        prices = []
        for i in range(n):
            if i < 33:
                prices.append(100 + (i - 20) ** 2 * 0.1)
            else:
                prices.append(95 + (i - 45) ** 2 * 0.1)

        rsi: list[float | None] = [None] * 15
        for i in range(15, 33):
            rsi.append(30 + (i - 20) ** 2 * 0.8)
        for i in range(33, n):
            rsi.append(40 + (i - 45) ** 2 * 0.5)

        return prices, rsi

    def test_detects_bullish(self, bullish_divergence_data):
        """Should detect bullish divergence."""
        prices, rsi = bullish_divergence_data
        result = detect_divergence(prices, rsi, lookback=3, min_separation=5)
        assert result is not None
        assert result.divergence_type == DivergenceType.BULLISH

    def test_no_divergence_returns_none(self):
        """No divergence returns None."""
        prices = [100.0 + i * 0.2 for i in range(40)]
        rsi = [50.0] * 40
        result = detect_divergence(prices, rsi)
        assert result is None
