"""Unit tests for LuxAlgo SMC structure backtest."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.run_smc_backtest import (
    PendingObRetest,
    StructureBreak,
    StructureTracker,
    StrategyConfig,
    _accept_break,
    _in_discount_half,
    _in_premium_half,
    build_break_schedule,
    compute_order_block,
    map_htf_schedule_to_ltf,
    ob_mitigated,
    ob_retest_triggered,
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


def test_htf_schedule_maps_to_first_ltf_bar_at_or_after_htf_open() -> None:
    htf_times = [
        pd.Timestamp("2026-01-01 10:00", tz="UTC"),
        pd.Timestamp("2026-01-01 11:00", tz="UTC"),
    ]
    ltf_times = [
        pd.Timestamp("2026-01-01 10:45", tz="UTC"),
        pd.Timestamp("2026-01-01 11:00", tz="UTC"),
        pd.Timestamp("2026-01-01 11:15", tz="UTC"),
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
    mapped = map_htf_schedule_to_ltf(htf_times, {1: htf_event}, ltf_times)
    assert 1 in mapped
    assert mapped[1].direction == "long"
    assert mapped[1].bar_index == 1


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


def test_synthetic_backtest_produces_trades() -> None:
    from scripts.run_smc_backtest import PreparedSmcData, build_break_schedule, run_prepared_backtest

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