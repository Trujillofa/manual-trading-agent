"""Adversarial tests for backtest cost books, causality, and holdout rank."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime

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
    book = CostBook(
        spread_pips=2.0, slippage_pips=2.0, commission_usd_per_lot_side=3.0, lot_size=1.0
    )
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
        assert trade.entry_price == pytest.approx(
            engine.cost_book.entry_fill(open_px, trade.side, pip)
        )
        assert trade.entry_price != pytest.approx(
            engine.cost_book.entry_fill(close_px, trade.side, pip)
        )


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


def test_empty_frame_dates_are_epoch_not_wall_clock() -> None:
    engine = EnhancedBacktestEngine()
    data = pd.DataFrame(columns=["open", "high", "low", "close"])
    result = engine.run("EUR/USD", data)
    assert result.start_date == result.end_date == pd.Timestamp("1970-01-01").to_pydatetime()
    assert result.total_trades == 0


def test_short_frame_dates_come_from_index() -> None:
    engine = EnhancedBacktestEngine()
    data = _make_ohlcv(n=20)
    result = engine.run("EUR/USD", data, lookback=20, atr_period=14)
    assert pd.Timestamp(result.start_date) == pd.Timestamp(data.index[0])
    assert pd.Timestamp(result.end_date) == pd.Timestamp(data.index[-1])


def test_closed_trade_pnl_matches_slipped_fills() -> None:
    data = _make_ohlcv(n=240, seed=3)
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
    book = engine.cost_book
    pip = 0.0001
    balance = engine.initial_balance
    for trade in result.trades:
        if trade.exit_reason in {"tp", "sl"}:
            raw_level = trade.tp_price if trade.exit_reason == "tp" else trade.sl_price
            assert trade.exit_price == pytest.approx(book.exit_fill(raw_level, trade.side, pip))
        expected, _pct = engine._fill_cash_pnl(
            side=trade.side,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            sl_price=trade.sl_price,
            balance=balance,
        )
        assert trade.pnl == pytest.approx(expected)
        balance += trade.pnl


def test_donchian_develop_dd_ignores_full_sample_drawdown() -> None:
    from scripts.run_donchian_backtest import ConfigResult, TradeRecord, develop_metrics

    cutoff = pd.Timestamp("2024-06-01")
    develop_trade = TradeRecord(
        pair="EUR/USD",
        direction="buy",
        entry_time=pd.Timestamp("2024-01-15"),
        exit_time=pd.Timestamp("2024-01-16"),
        entry_price=1.1,
        exit_price=1.11,
        tp_price=1.12,
        sl_price=1.09,
        pnl=100.0,
        pnl_pct=0.1,
        exit_reason="tp",
        bars_held=4,
        rsi_15m=25.0,
        rsi_30m=25.0,
        rsi_1h=25.0,
    )
    holdout_loss = TradeRecord(
        pair="EUR/USD",
        direction="buy",
        entry_time=pd.Timestamp("2024-07-15"),
        exit_time=pd.Timestamp("2024-07-16"),
        entry_price=1.1,
        exit_price=1.0,
        tp_price=1.12,
        sl_price=1.0,
        pnl=-50_000.0,
        pnl_pct=-50.0,
        exit_reason="sl",
        bars_held=4,
        rsi_15m=25.0,
        rsi_30m=25.0,
        rsi_1h=25.0,
    )
    result = ConfigResult(
        pair="EUR/USD",
        config_label="x",
        upper_bound=70,
        lower_bound=30,
        use_fixed_pip=True,
        tp_pips=20,
        sl_pips=20,
        tp_atr_mult=1,
        sl_atr_mult=1,
        lookback=20,
        confirm_bars=5,
        buffer_pips=2,
        use_di_filter=False,
        di_ratio=1.0,
        use_adx_filter=False,
        max_adx=25,
        use_session=False,
        use_mom_fade=False,
        mom_fade_bars=3,
        use_trailing=False,
        trail_atr_mult=1.0,
        use_breakeven=False,
        be_trigger_pct=50,
        use_time_exit=False,
        max_bars_exit=16,
        spread_pips=2,
        commission_per_order=3,
        slippage_pips=2,
        max_drawdown_pct=80.0,
        trades_list=[develop_trade, holdout_loss],
    )
    _n, _w, _pnl, _pf, max_dd = develop_metrics([result], {"EUR/USD": cutoff}, holdout=False)
    assert max_dd < 1.0


def test_pivot_develop_wr_ignores_holdout_trades() -> None:
    from scripts.run_pivot_backtest import ConfigResult, TradeRecord, develop_metrics

    cutoff = pd.Timestamp("2024-06-01")
    develop_win = TradeRecord(
        pair="EUR/USD",
        direction="buy",
        entry_price=1.1,
        exit_price=1.11,
        pnl_pct=1.0,
        exit_reason="tp",
        bars_held=2,
        pivot_level="s1",
        entry_time=pd.Timestamp("2024-01-15"),
    )
    holdout_loss = TradeRecord(
        pair="EUR/USD",
        direction="buy",
        entry_price=1.1,
        exit_price=1.0,
        pnl_pct=-2.0,
        exit_reason="sl",
        bars_held=2,
        pivot_level="s1",
        entry_time=pd.Timestamp("2024-07-15"),
    )
    result = ConfigResult(
        pair="EUR/USD",
        config_label="WEEKLY_S1",
        entry_type="WEEKLY",
        level_set="S1",
        proximity_pips=5,
        confirm_bars=0,
        tp_mult=1.0,
        sl_mult=2.0,
        win_rate=0.5,
        trades_list=[develop_win, holdout_loss],
    )
    trades, _pnl, _pf, wr = develop_metrics([result], {"EUR/USD": cutoff}, holdout=False)
    assert trades == 1
    assert wr == pytest.approx(1.0)


def test_same_bar_exit_is_stop_first() -> None:
    from src.backtest.exits import same_bar_exit

    assert same_bar_exit("buy", high=1.03, low=0.97, tp=1.02, sl=0.98) == "sl"
    assert same_bar_exit("BUY", high=1.03, low=0.99, tp=1.02, sl=0.98) == "tp"
    assert same_bar_exit("buy", high=1.01, low=0.97, tp=1.02, sl=0.98) == "sl"
    assert same_bar_exit("sell", high=1.03, low=0.97, tp=0.98, sl=1.02) == "sl"
    assert same_bar_exit("SELL", high=1.01, low=0.97, tp=0.98, sl=1.02) == "tp"
    assert same_bar_exit("sell", high=1.03, low=0.99, tp=0.98, sl=1.02) == "sl"
    assert same_bar_exit("buy", high=1.01, low=0.99, tp=1.02, sl=0.98) is None
    with pytest.raises(ValueError, match="unknown side"):
        same_bar_exit("flat", 1.0, 1.0, 1.0, 1.0)


def test_cutoff_at_uses_bar_index_not_wall_clock() -> None:
    from src.backtest.windows import cutoff_at

    index = pd.date_range("2024-01-01", periods=100, freq="h")
    cutoff = cutoff_at(index)
    assert cutoff is not None
    assert pd.Timestamp(cutoff) == pd.Timestamp(index[65])
    assert cutoff_at(pd.Index([])) is None


def test_window_metrics_ignore_holdout_pnls() -> None:
    from src.backtest.windows import (
        format_window_line,
        metrics_from_pnls,
        split_trade_metrics,
    )

    @dataclass
    class _T:
        entry_time: datetime
        pnl: float

    cutoff = datetime(2024, 6, 1)
    develop_win = _T(datetime(2024, 1, 15), 100.0)
    holdout_loss = _T(datetime(2024, 7, 15), -50_000.0)
    juicy_holdout = _T(datetime(2024, 7, 15), 50_000.0)
    develop, holdout = split_trade_metrics(
        [develop_win, holdout_loss], cutoff, initial_balance=10_000.0
    )
    swapped_develop, swapped_holdout = split_trade_metrics(
        [develop_win, juicy_holdout], cutoff, initial_balance=10_000.0
    )
    assert develop.trades == 1
    assert develop.win_rate == pytest.approx(1.0)
    assert develop.pnl == pytest.approx(100.0)
    assert develop.pnl_pct == pytest.approx(1.0)
    assert develop.profit_factor == float("inf")
    assert holdout.trades == 1
    assert holdout.profit_factor == pytest.approx(0.0)
    assert develop == swapped_develop
    assert swapped_holdout.profit_factor == float("inf")
    empty = metrics_from_pnls([])
    assert empty.trades == 0
    assert empty.profit_factor == 0.0
    assert format_window_line("Develop (first 65%)", empty) == "  Develop (first 65%): 0 trades"
    assert "PF inf" in format_window_line("Develop (first 65%)", develop)
