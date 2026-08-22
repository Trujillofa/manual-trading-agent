"""Adversarial tests for backtest cost books, causality, and holdout rank."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from scripts.optimize_htf_fib_backtest import optimization_score
from scripts.run_htf_fib_backtest import WindowStats
from scripts.run_smc_backtest import selection_score
from src.backtest.cost_book import CostBook
from src.backtest.enhanced_engine import EnhancedBacktestEngine
from tests.test_backtest_engine import _make_ohlcv


def test_cost_book_refuses_silent_mutation() -> None:
    book = CostBook()
    with pytest.raises(FrozenInstanceError):
        book.spread_pips = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        book.lot_size = 99.0  # type: ignore[misc]


def test_cost_book_units_and_fills() -> None:
    book = CostBook(spread_pips=2.0, slippage_pips=2.0, commission_usd_per_lot_side=3.0, lot_size=1.0)
    pip = 0.0001
    assert book.entry_fill(1.1000, "buy", pip) == pytest.approx(1.1004)
    assert book.entry_fill(1.1000, "sell", pip) == pytest.approx(1.0996)
    assert book.exit_fill(1.1000, "buy", pip) == pytest.approx(1.0998)
    assert book.round_trip_commission_usd() == pytest.approx(6.0)


def test_enhanced_engine_exposes_frozen_cost_book() -> None:
    engine = EnhancedBacktestEngine()
    assert engine.spread_pips == 2.0
    assert engine.cost_book.slippage_pips == 2.0
    with pytest.raises(FrozenInstanceError):
        engine.cost_book.spread_pips = 0.0  # type: ignore[misc]


def test_stop_first_when_tp_and_sl_share_a_bar() -> None:
    engine = EnhancedBacktestEngine()
    assert engine._check_tp_sl_hit("buy", 1.0, 1.02, 0.98, 1.03, 0.97) == "sl"
    assert engine._check_tp_sl_hit("sell", 1.0, 0.98, 1.02, 1.03, 0.97) == "sl"


def test_index_conversion_does_not_use_wall_clock() -> None:
    engine = EnhancedBacktestEngine()
    stamp = pd.Timestamp("2024-03-01 12:00", tz="UTC")
    converted = engine._index_to_datetime(stamp)
    assert converted.year == 2024
    assert converted.month == 3
    with pytest.raises((TypeError, ValueError)):
        engine._index_to_datetime(float("nan"))


def test_entry_fills_next_bar_open_not_signal_close() -> None:
    data = _make_ohlcv(n=220, seed=3)
    # Gap every bar so an open-fill is distinguishable from a close-fill.
    data["open"] = data["close"] - 0.35
    data["high"] = data[["open", "high", "close"]].max(axis=1) + 0.05
    data["low"] = data[["open", "low", "close"]].min(axis=1) - 0.05
    engine = EnhancedBacktestEngine(
        use_patterns=False,
        use_divergence=False,
        use_sma_alignment=False,
        use_rsi_ma=False,
        adx_threshold=100.0,
        max_hold_bars=12,
    )
    result = engine.run("EUR/USD", data)
    assert result.trades
    pip = 0.0001
    for trade in result.trades:
        loc = data.index.get_loc(pd.Timestamp(trade.entry_time))
        open_px = float(data["open"].iloc[loc])
        close_px = float(data["close"].iloc[loc])
        assert trade.entry_price == pytest.approx(engine.cost_book.entry_fill(open_px, trade.side, pip))
        assert trade.entry_price != pytest.approx(engine.cost_book.entry_fill(close_px, trade.side, pip))


def test_mutating_future_bars_does_not_change_earlier_fills() -> None:
    data = _make_ohlcv(n=240, seed=11)
    engine = EnhancedBacktestEngine(
        use_patterns=False,
        use_divergence=False,
        use_sma_alignment=False,
        use_rsi_ma=False,
        adx_threshold=100.0,
    )
    first = engine.run("EUR/USD", data)
    cutoff = data.index[170]
    peeked = data.copy()
    peeked.loc[peeked.index > cutoff, ["open", "high", "low", "close"]] += 40.0
    second = engine.run("EUR/USD", peeked)
    early_first = [
        (trade.entry_time, trade.side, trade.entry_price)
        for trade in first.trades
        if trade.entry_time < cutoff
    ]
    early_second = [
        (trade.entry_time, trade.side, trade.entry_price)
        for trade in second.trades
        if trade.entry_time < cutoff
    ]
    assert early_first == early_second


def test_swapping_holdout_metrics_does_not_change_htf_rank() -> None:
    """Most-used optimizer ranks on develop/IS WindowStats only."""

    develop_a = WindowStats(40, 0.5, 1.2, 1.1, 2.0, 1.0, 4, 8)
    develop_b = WindowStats(40, 0.5, 1.2, 1.1, 0.5, 1.0, 4, 8)
    juicy_holdout = WindowStats(40, 0.9, 3.0, 3.0, 50.0, 0.1, 8, 8)
    awful_holdout = WindowStats(5, 0.1, 0.2, 0.2, -50.0, 80.0, 0, 8)
    rows = [("a", develop_a, juicy_holdout), ("b", develop_b, awful_holdout)]
    swapped = [("a", develop_a, awful_holdout), ("b", develop_b, juicy_holdout)]
    ranked = sorted(rows, key=lambda row: optimization_score(row[1]), reverse=True)
    ranked_swapped = sorted(swapped, key=lambda row: optimization_score(row[1]), reverse=True)
    assert [row[0] for row in ranked] == [row[0] for row in ranked_swapped] == ["a", "b"]


def test_swapping_holdout_metrics_does_not_change_smc_selection_rank() -> None:
    develop_a = WindowStats(40, 0.5, 1.2, 1.1, 2.0, 1.0, 4, 8)
    develop_b = WindowStats(40, 0.5, 1.2, 1.1, 0.5, 1.0, 4, 8)
    juicy_holdout = WindowStats(40, 0.9, 3.0, 3.0, 50.0, 0.1, 8, 8)
    awful_holdout = WindowStats(5, 0.1, 0.2, 0.2, -50.0, 80.0, 0, 8)
    rows = [("a", develop_a, juicy_holdout), ("b", develop_b, awful_holdout)]
    swapped = [("a", develop_a, awful_holdout), ("b", develop_b, juicy_holdout)]
    ranked = sorted(rows, key=lambda row: selection_score(row[1], row[2]), reverse=True)
    ranked_swapped = sorted(swapped, key=lambda row: selection_score(row[1], row[2]), reverse=True)
    assert [row[0] for row in ranked] == [row[0] for row in ranked_swapped] == ["a", "b"]
    assert selection_score(develop_a, juicy_holdout) == selection_score(develop_a, awful_holdout)
