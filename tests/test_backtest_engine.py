"""Tests for enhanced backtest engine.

NOTE: _calculate_atr_column() was requested but does not exist in the engine.
The engine uses calculate_atr() from src.indicators.atr inline during run().
Tests cover _calculate_rsi_column() instead, plus initialization, run(), result
fields, and empty-data handling.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.backtest.enhanced_engine import (
    EnhancedBacktestEngine,
    EnhancedBacktestResult,
)


def _make_ohlcv(
    n: int = 100,
    start_price: float = 100.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic OHLCV data for backtesting.

    Generates a mildly trending + oscillating price series so RSI
    sometimes touches oversold/overbought zones and triggers signals.
    """
    from random import Random

    rng = Random(seed)
    closes: list[float] = [start_price]
    for _ in range(n - 1):
        change = rng.uniform(-1.5, 1.5)
        closes.append(closes[-1] + change)

    opens = closes.copy()
    highs = [c + rng.uniform(0.2, 0.8) for c in closes]
    lows = [c - rng.uniform(0.2, 0.8) for c in closes]

    index = pd.date_range(start="2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=index,
    )


class TestEnhancedBacktestEngineInit:
    """Tests for EnhancedBacktestEngine initialization."""

    def test_default_initialization(self):
        """Should create engine with sensible defaults."""
        engine = EnhancedBacktestEngine()
        assert engine.initial_balance == 10000.0
        assert engine.risk_per_trade == 0.02
        assert engine.reward_ratio == 1.5
        assert engine.sl_atr_multiplier == 2.0
        assert engine.spread_pips == 2.0
        assert engine.use_patterns is True
        assert engine.use_divergence is True

    def test_custom_initialization(self):
        """Should accept custom parameters."""
        engine = EnhancedBacktestEngine(
            initial_balance=5000.0,
            risk_per_trade=0.01,
            reward_ratio=2.0,
            use_patterns=False,
            use_divergence=False,
            use_rsi_ma=True,
            rsi_ma_variant="slope",
        )
        assert engine.initial_balance == 5000.0
        assert engine.risk_per_trade == 0.01
        assert engine.use_patterns is False
        assert engine.use_divergence is False
        assert engine.use_rsi_ma is True
        assert engine.rsi_ma_variant == "slope"


class TestCalculateRsiColumn:
    """Tests for _calculate_rsi_column method."""

    def test_calculates_rsi_column(self):
        """Should produce RSI values for valid windows."""
        engine = EnhancedBacktestEngine()
        data = _make_ohlcv(n=30)
        rsi_series = engine._calculate_rsi_column(data, period=14)
        assert len(rsi_series) == 30
        # First 14 values should be NaN (needs period+1=15 bars)
        assert rsi_series.iloc[:14].isna().all()
        # From index 14 onward should have values
        assert not rsi_series.iloc[14:].isna().all()

    def test_rsi_values_in_range(self):
        """RSI should be between 0 and 100."""
        engine = EnhancedBacktestEngine()
        data = _make_ohlcv(n=30)
        rsi_series = engine._calculate_rsi_column(data, period=14)
        valid = rsi_series.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()


class TestRun:
    """Tests for the run() method."""

    def test_run_with_synthetic_data(self):
        """Run backtest on synthetic OHLCV data."""
        engine = EnhancedBacktestEngine(
            use_patterns=True,
            use_divergence=True,
            use_mtf_alignment=False,
            use_sma_alignment=False,
            use_rsi_ma=False,
        )
        data = _make_ohlcv(n=200, seed=123)
        result = engine.run("EUR/USD", data)
        assert isinstance(result, EnhancedBacktestResult)
        assert result.symbol == "EUR/USD"

    def test_run_returns_result_with_trades(self):
        """Result should include trades list."""
        engine = EnhancedBacktestEngine(
            use_sma_alignment=False,
            use_rsi_ma=False,
            adx_threshold=100.0,  # disable ADX filter
        )
        data = _make_ohlcv(n=200, seed=456)
        result = engine.run("EUR/USD", data)
        assert isinstance(result.trades, list)
        # At minimum the fields should be present
        assert result.win_rate >= 0.0

    def test_run_with_patterns_and_divergence_disabled(self):
        """Should work with patterns and divergence turned off."""
        engine = EnhancedBacktestEngine(
            use_patterns=False,
            use_divergence=False,
            use_sma_alignment=False,
        )
        data = _make_ohlcv(n=200, seed=789)
        result = engine.run("GBP/USD", data)
        assert result.pattern_trades == 0
        assert result.divergence_trades == 0


class TestEnhancedBacktestResult:
    """Tests for the EnhancedBacktestResult dataclass."""

    def test_has_all_expected_fields(self):
        """Result should have all fields described in the dataclass."""
        now = datetime.now()
        result = EnhancedBacktestResult(
            symbol="EUR/USD",
            start_date=now,
            end_date=now,
            total_trades=10,
            wins=6,
            losses=4,
            win_rate=0.6,
            total_pnl=500.0,
            total_pnl_pct=5.0,
            max_drawdown=200.0,
            max_drawdown_pct=2.0,
            avg_win=150.0,
            avg_loss=100.0,
            profit_factor=1.5,
            sharpe_ratio=1.2,
            trades=[],
            pattern_trades=3,
            pattern_win_rate=0.66,
            divergence_trades=2,
            divergence_win_rate=0.5,
            combined_trades=1,
            combined_win_rate=1.0,
        )
        assert result.symbol == "EUR/USD"
        assert result.total_trades == 10
        assert result.wins == 6
        assert result.losses == 4
        assert result.win_rate == 0.6
        assert result.total_pnl == 500.0
        assert result.max_drawdown == 200.0
        assert result.profit_factor == 1.5
        assert result.sharpe_ratio == 1.2
        assert result.pattern_trades == 3
        assert result.divergence_trades == 2
        assert result.combined_trades == 1


class TestEmptyDataHandling:
    """Tests for empty or insufficient data scenarios."""

    def test_empty_dataframe_returns_zero_trades(self):
        """Empty DataFrame should return result with zero trades."""
        engine = EnhancedBacktestEngine()
        data = pd.DataFrame(columns=["open", "high", "low", "close"])
        result = engine.run("EUR/USD", data)
        assert result.total_trades == 0
        assert result.wins == 0
        assert result.losses == 0

    def test_insufficient_data_returns_zero_trades(self):
        """Too few rows for lookback + ATR should return zero trades."""
        engine = EnhancedBacktestEngine()
        data = _make_ohlcv(n=20)  # need at least 20+14=34 rows minimum
        result = engine.run("EUR/USD", data, lookback=20, atr_period=14)
        assert result.total_trades == 0
