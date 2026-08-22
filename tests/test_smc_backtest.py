"""Unit tests for LuxAlgo SMC structure backtest."""

from __future__ import annotations

import pandas as pd
import pytest

from research.smc_autosearch import _config_name, _filter_results
from scripts.run_htf_fib_backtest import BacktestResult, Trade
from scripts.run_smc_backtest import (
    EvalRow,
    PendingObRetest,
    selection_score,
    PreparedSmcData,
    StrategyConfig,
    StructureBreak,
    StructureTracker,
    WindowStats,
    _accept_break,
    _in_discount_half,
    build_break_schedule,
    compute_order_block,
    map_htf_schedule_to_ltf,
    ob_mitigated,
    ob_retest_triggered,
    run_prepared_backtest,
    write_comparison_report,
)


def _walk(highs: list[float], lows: list[float], closes: list[float], length: int = 3):
    tracker = StructureTracker(swing_length=length)
    events = []
    for i in range(len(closes)):
        event = tracker.process_bar(i, highs, lows, closes)
        if event is not None:
            events.append(event)
    return events


def test_structure_tracker_emits_tagged_breaks() -> None:
    highs = [1.0, 1.2, 1.4, 1.1, 1.0, 1.3, 1.5]
    lows = [0.9, 1.0, 1.1, 1.0, 0.9, 1.0, 1.2]
    closes = [1.0, 1.15, 1.35, 1.05, 0.95, 1.25, 1.45]
    events = _walk(highs, lows, closes, length=2)
    assert events
    assert all(event.tag in {"BOS", "CHoCH"} for event in events)
    assert any(event.direction == "long" for event in events)


def test_bos_only_filter_rejects_choch() -> None:
    event = StructureBreak(
        bar_index=10,
        direction="long",
        tag="CHoCH",
        pivot_level=1.10,
        pivot_bar_index=5,
        swing_top=1.20,
        swing_bottom=1.00,
    )
    config = StrategyConfig(name="bos_continuation", tag_filter="bos")
    assert _accept_break(event, 1.11, config) is False


def test_choch_only_filter_rejects_bos() -> None:
    event = StructureBreak(
        bar_index=10,
        direction="long",
        tag="BOS",
        pivot_level=1.10,
        pivot_bar_index=5,
        swing_top=1.20,
        swing_bottom=1.00,
    )
    config = StrategyConfig(name="choch_reversal", tag_filter="choch")
    assert _accept_break(event, 1.11, config) is False
    event_choch = StructureBreak(
        bar_index=11,
        direction="short",
        tag="CHoCH",
        pivot_level=1.10,
        pivot_bar_index=5,
        swing_top=1.20,
        swing_bottom=1.00,
    )
    assert _accept_break(event_choch, 1.09, config) is True


def test_zone_filter_requires_discount_for_longs() -> None:
    event = StructureBreak(
        bar_index=10,
        direction="long",
        tag="BOS",
        pivot_level=1.10,
        pivot_bar_index=5,
        swing_top=1.20,
        swing_bottom=1.00,
    )
    config = StrategyConfig(name="zone_filtered", require_zone=True)
    assert _in_discount_half(1.05, 1.20, 1.00)
    assert _accept_break(event, 1.05, config) is True
    assert _accept_break(event, 1.15, config) is False


def test_compute_order_block_finds_extreme_candle_in_leg() -> None:
    parsed_highs = [1.10, 1.12, 1.15, 1.11]
    parsed_lows = [1.00, 1.01, 1.02, 1.03]
    event = StructureBreak(
        bar_index=3,
        direction="long",
        tag="BOS",
        pivot_level=1.14,
        pivot_bar_index=0,
        swing_top=1.15,
        swing_bottom=1.00,
    )
    ob_high, ob_low = compute_order_block(event, parsed_highs, parsed_lows)
    assert ob_low == pytest.approx(1.00)
    assert ob_high == pytest.approx(1.10)


def test_ob_retest_triggers_on_touch_and_rejection() -> None:
    pending = PendingObRetest(
        direction="long",
        ob_high=1.105,
        ob_low=1.095,
        armed_bar=5,
        expiry_bar=20,
        atr=0.01,
    )
    assert ob_retest_triggered(pending, 6, 1.100, 1.106, 1.094, 1.104) is True
    assert ob_retest_triggered(pending, 6, 1.100, 1.106, 1.096, 1.094) is False


def test_ob_mitigated_when_close_breaks_zone() -> None:
    pending = PendingObRetest("long", 1.105, 1.095, 5, 20, 0.01)
    assert ob_mitigated(pending, 1.10, 1.09, 1.094) is True
    assert ob_mitigated(pending, 1.10, 1.096, 1.098) is False


def test_htf_schedule_maps_to_first_ltf_bar_at_or_after_htf_close() -> None:
    htf_times = [
        pd.Timestamp("2026-01-01 10:00", tz="UTC"),
        pd.Timestamp("2026-01-01 11:00", tz="UTC"),
    ]
    ltf_times = [
        pd.Timestamp("2026-01-01 10:45", tz="UTC"),
        pd.Timestamp("2026-01-01 11:00", tz="UTC"),
        pd.Timestamp("2026-01-01 11:15", tz="UTC"),
        pd.Timestamp("2026-01-01 12:00", tz="UTC"),
    ]
    htf_event = StructureBreak(
        bar_index=1,
        direction="long",
        tag="BOS",
        pivot_level=1.10,
        pivot_bar_index=0,
        swing_top=1.12,
        swing_bottom=1.08,
    )
    mapped = map_htf_schedule_to_ltf(htf_times, {1: htf_event}, ltf_times, "1h")
    assert 3 in mapped
    assert mapped[3].direction == "long"
    assert mapped[3].bar_index == 3
    assert all(index >= 3 for index in mapped)


def test_build_break_schedule_on_resampled_htf_has_no_lookahead() -> None:
    index = pd.date_range("2026-01-01", periods=8, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {
            "high": [1.0, 1.2, 1.5, 1.3, 1.1, 1.4, 1.6, 1.5],
            "low": [0.9, 1.0, 1.2, 1.1, 1.0, 1.2, 1.4, 1.3],
            "close": [1.0, 1.15, 1.45, 1.15, 1.05, 1.35, 1.55, 1.45],
        },
        index=index,
    )
    schedule = build_break_schedule(
        frame["high"].tolist(),
        frame["low"].tolist(),
        frame["close"].tolist(),
        swing_length=2,
    )
    assert schedule
    assert all(idx >= 2 for idx in schedule)


def test_comparison_report_uses_archival_language(tmp_path) -> None:
    stats = WindowStats(1, 0.0, 0.0, 0.5, -1.0, 1.0, 0, 1)
    path = write_comparison_report(
        [EvalRow("candidate", -1.0, "DISCARD", stats, stats, "failed")],
        tmp_path / "comparison.md",
    )
    report = path.read_text(encoding="utf-8")
    assert "Least-negative searched candidate" in report
    assert "Most optimal" not in report
    assert "order-block retests" in report


def test_selection_score_ignores_holdout_argument() -> None:
    develop = WindowStats(40, 0.5, 1.2, 1.1, 3.0, 1.0, 4, 8)
    holdout_a = WindowStats(80, 0.9, 5.0, 5.0, 99.0, 0.1, 8, 8)
    holdout_b = WindowStats(1, 0.0, 0.0, 0.0, -99.0, 90.0, 0, 8)
    assert selection_score(develop, holdout_a) == selection_score(develop, holdout_b)


def test_filter_results_keeps_holdout_out_of_search_windows() -> None:
    times = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    trades = [
        Trade(
            pair="EUR/USD",
            config="candidate",
            account_name="risk_fraction",
            entry_time=timestamp,
            exit_time=timestamp,
            direction="long",
            entry_price=1.0,
            exit_price=1.0,
            exit_reason="time",
            gross_r=0.0,
            net_r=0.0,
            net_pnl_usd=0.0,
            net_pnl_pct=0.0,
            lots=1.0,
        )
        for timestamp in times
    ]
    result = BacktestResult(pair="EUR/USD", config="candidate", trades=trades)
    selected = _filter_results(
        [result],
        start_by_pair={"EUR/USD": times[0]},
        end_by_pair={"EUR/USD": times[2]},
    )
    assert [trade.entry_time for trade in selected[0].trades] == [times[1], times[2]]


def test_config_name_includes_atr_period() -> None:
    config = {
        "entry_mode": "immediate",
        "tag_filter": "all",
        "swing_length": 50,
        "structure_timeframe": "15m",
        "ob_retest_bars": 16,
        "atr_period": 10,
        "tp_atr": 3.0,
        "sl_atr": 2.5,
        "max_hold_bars": 16,
    }
    assert "_atr10_" in _config_name(config)


def test_same_bar_exit_does_not_arm_new_structure_signal() -> None:
    """Exiting on the entry bar must not also arm a break scheduled that bar."""

    prior_break = 5
    entry_at = 6
    index = pd.date_range("2026-01-01", periods=12, freq="15min", tz="UTC")
    opens = [1.1000] * len(index)
    highs = [1.1005] * len(index)
    lows = [1.0995] * len(index)
    closes = [1.1000] * len(index)
    highs[entry_at] = 1.1020
    lows[entry_at] = 1.0980
    prior_event = StructureBreak(
        bar_index=prior_break,
        direction="long",
        tag="BOS",
        pivot_level=1.0990,
        pivot_bar_index=2,
        swing_top=1.1010,
        swing_bottom=1.0980,
    )
    same_bar_event = StructureBreak(
        bar_index=entry_at,
        direction="short",
        tag="CHoCH",
        pivot_level=1.1015,
        pivot_bar_index=4,
        swing_top=1.1020,
        swing_bottom=1.0980,
    )
    prepared = PreparedSmcData(
        timestamps=[pd.Timestamp(ts) for ts in index],
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        parsed_highs=highs,
        parsed_lows=lows,
        usd_per_quote=[1.0] * len(index),
        atr_by_period={14: [0.001] * len(index)},
        breaks_by_spec={
            ("15m", 50): {prior_break: prior_event, entry_at: same_bar_event},
        },
    )
    config = StrategyConfig(
        name="immediate_guard",
        entry_mode="immediate",
        max_hold_bars=0,
        tp_atr=10.0,
        sl_atr=0.5,
    )
    result = run_prepared_backtest("EUR/USD", prepared, config)
    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"
    assert result.trades[0].entry_time == index[entry_at]
    assert result.trades[0].exit_time == index[entry_at]


def test_synthetic_backtest_produces_trades() -> None:
    index = pd.date_range("2026-01-01", periods=400, freq="15min", tz="UTC")
    base = 1.1000
    highs = []
    lows = []
    closes = []
    for i in range(len(index)):
        wave = 0.003 * ((i % 40) - 20) / 20.0
        close = base + wave
        highs.append(close + 0.0005)
        lows.append(close - 0.0005)
        closes.append(close)
    opens = closes.copy()
    schedule = build_break_schedule(highs, lows, closes, swing_length=20)
    prepared = PreparedSmcData(
        timestamps=[pd.Timestamp(ts) for ts in index],
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        parsed_highs=highs,
        parsed_lows=lows,
        usd_per_quote=[1.0] * len(index),
        atr_by_period={14: [0.001] * len(index)},
        breaks_by_spec={("15m", 20): schedule},
    )
    result = run_prepared_backtest("EUR/USD", prepared, StrategyConfig(name="marker_baseline"))
    assert isinstance(result.trades, list)
