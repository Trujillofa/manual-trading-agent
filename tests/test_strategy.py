"""Tests for MTF RSI strategy."""

from __future__ import annotations

import pytest

from src.strategy.multi_timeframe import MTFRSIStrategy
from src.strategy.signals import SignalConfidence, SignalType


class TestMTFRSIStrategy:
    """MTF RSI strategy tests."""

    @pytest.fixture
    def strategy(self):
        """Create strategy instance with mock news checker."""
        return MTFRSIStrategy(news_checker=None)

    @pytest.fixture
    def aligned_bullish_indicators(self):
        """RSI aligned oversold across all timeframes."""
        return {
            "rsi_1h": 12.0,
            "rsi_30m": 10.0,
            "rsi_15m": 8.0,
            "hh_15m": 1.0950,
            "ll_15m": 1.0850,
            "close_15m": 1.0830,
        }

    @pytest.fixture
    def aligned_bearish_indicators(self):
        """RSI aligned overbought across all timeframes."""
        return {
            "rsi_1h": 78.0,
            "rsi_30m": 82.0,
            "rsi_15m": 75.0,
            "hh_15m": 1.0950,
            "ll_15m": 1.0850,
            "close_15m": 1.0960,
        }

    @pytest.fixture
    def misaligned_indicators(self):
        """RSI not aligned across timeframes."""
        return {
            "rsi_1h": 75.0,
            "rsi_30m": 50.0,
            "rsi_15m": 25.0,
            "hh_15m": 1.0950,
            "ll_15m": 1.0850,
            "close_15m": 1.0900,
        }

    @pytest.fixture
    def weak_oversold_indicators(self):
        """RSI just barely oversold, below threshold but weak."""
        return {
            "rsi_1h": 32.0,
            "rsi_30m": 28.0,
            "rsi_15m": 29.0,
            "hh_15m": 1.0950,
            "ll_15m": 1.0850,
            "close_15m": 1.0830,
        }

    @pytest.mark.asyncio
    async def test_buy_signal_aligned_oversold_breakout(self, strategy, aligned_bullish_indicators):
        """Should generate BUY signal when RSI oversold aligned and breakout low."""
        result = await strategy.evaluate("EUR/USD", aligned_bullish_indicators)
        assert result is not None
        assert result.signal_type == SignalType.BUY
        assert result.side == "buy"
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_sell_signal_aligned_overbought_breakout(
        self, strategy, aligned_bearish_indicators
    ):
        """Should generate SELL signal when RSI overbought aligned and breakout high."""
        result = await strategy.evaluate("EUR/USD", aligned_bearish_indicators)
        assert result is not None
        assert result.signal_type == SignalType.SELL
        assert result.side == "sell"

    @pytest.mark.asyncio
    async def test_no_signal_misaligned_rsi(self, strategy, misaligned_indicators):
        """Should not generate signal when RSI not aligned."""
        result = await strategy.evaluate("EUR/USD", misaligned_indicators)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_signal_no_breakout(self, strategy, weak_oversold_indicators):
        """Should not generate signal when no breakout."""
        indicators = weak_oversold_indicators.copy()
        indicators["close_15m"] = 1.0870
        result = await strategy.evaluate("EUR/USD", indicators)
        assert result is None

    def test_classify_mtf_signal_aligned_bullish(self, strategy, aligned_bullish_indicators):
        """Should classify as aligned bullish."""
        mtf = strategy._classify_mtf_signal(aligned_bullish_indicators)
        assert mtf is not None
        assert mtf.aligned is True
        assert mtf.direction == "bullish"

    def test_classify_mtf_signal_aligned_bearish(self, strategy, aligned_bearish_indicators):
        """Should classify as aligned bearish."""
        mtf = strategy._classify_mtf_signal(aligned_bearish_indicators)
        assert mtf is not None
        assert mtf.aligned is True
        assert mtf.direction == "bearish"

    def test_classify_mtf_signal_misaligned(self, strategy, misaligned_indicators):
        """Should not classify as aligned."""
        mtf = strategy._classify_mtf_signal(misaligned_indicators)
        assert mtf is not None
        assert mtf.aligned is False

    def test_classify_mtf_signal_missing_data(self, strategy):
        """Should return None when missing indicator data."""
        incomplete = {"rsi_1h": 25.0}
        mtf = strategy._classify_mtf_signal(incomplete)
        assert mtf is None

    def test_check_breakout_bullish(self, strategy, aligned_bullish_indicators):
        """Should detect bullish breakout (breakout low)."""
        mtf = strategy._classify_mtf_signal(aligned_bullish_indicators)
        assert mtf is not None
        assert strategy._check_breakout(mtf) is True

    def test_check_breakout_bearish(self, strategy, aligned_bearish_indicators):
        """Should detect bearish breakout (breakout high)."""
        mtf = strategy._classify_mtf_signal(aligned_bearish_indicators)
        assert mtf is not None
        assert strategy._check_breakout(mtf) is True

    def test_confidence_calculation_high(self, strategy, aligned_bullish_indicators):
        """Should calculate high confidence for extreme RSI."""
        mtf = strategy._classify_mtf_signal(aligned_bullish_indicators)
        assert mtf is not None
        confidence, level = strategy._calculate_confidence(mtf)
        assert level == SignalConfidence.HIGH
        assert confidence >= 0.8

    def test_confidence_calculation_low(self, strategy, weak_oversold_indicators):
        """Should calculate lower confidence for weak signal."""
        mtf = strategy._classify_mtf_signal(weak_oversold_indicators)
        assert mtf is not None
        confidence, level = strategy._calculate_confidence(mtf)
        assert level in {SignalConfidence.LOW, SignalConfidence.MEDIUM}
        assert confidence < 0.8

    def test_strategy_name(self, strategy):
        """Should return strategy class name."""
        assert strategy.get_name() == "MTFRSIStrategy"

    def test_required_timeframes(self):
        """Should declare required timeframes."""
        assert MTFRSIStrategy.REQUIRED_TIMEFRAMES == {
            "regime": "1h",
            "momentum": "30m",
            "entry": "15m",
        }
