"""Unit tests for vol-regime range compression breakout falsifier helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from research.new_edge.vol_regime.range_compression_breakout_test import (
    COMPRESSION_PERSISTENCE,
    DONCHIAN_WINDOW,
    TIME_STOP_BARS,
    breakout_direction,
    compute_donchian_features,
    determine_verdict,
    gross_pips,
    in_entry_window,
    simulate_pair_trades,
    trade_stats,
)


def _make_h1_bars(n: int, base: float = 1.1000, freq_hours: int = 1) -> pd.DataFrame:
    idx = pd.date_range("2020-01-06 08:00", periods=n, freq=f"{freq_hours}h", tz="UTC")
    close = base + np.linspace(0, 0.001, n)
    return pd.DataFrame(
        {
            "open": close - 0.0001,
            "high": close + 0.0002,
            "low": close - 0.0002,
            "close": close,
        },
        index=idx,
    )


def test_breakout_direction():
    assert breakout_direction(1.1050, 1.1040, 1.1000) == 1
    assert breakout_direction(1.0990, 1.1040, 1.1000) == -1
    assert breakout_direction(1.1020, 1.1040, 1.1000) == 0


def test_gross_pips_buy_and_sell():
    assert gross_pips(1, 1.1000, 1.1010, "EUR/USD") == pytest.approx(10.0)
    assert gross_pips(-1, 1.1000, 1.1010, "EUR/USD") == pytest.approx(-10.0)
    assert gross_pips(1, 110.00, 110.10, "USD/JPY") == pytest.approx(10.0)


def test_in_entry_window():
    assert in_entry_window(pd.Timestamp("2020-01-06 07:00", tz="UTC"))
    assert in_entry_window(pd.Timestamp("2020-01-06 16:59", tz="UTC"))
    assert not in_entry_window(pd.Timestamp("2020-01-06 06:59", tz="UTC"))
    assert not in_entry_window(pd.Timestamp("2020-01-06 17:00", tz="UTC"))


def test_compute_donchian_features_columns():
    bars = _make_h1_bars(300)
    frame = compute_donchian_features(bars)
    assert "donchian_high" in frame.columns
    assert "donchian_low" in frame.columns
    assert "donchian_range" in frame.columns
    assert "compressed" in frame.columns
    assert frame["donchian_range"].iloc[DONCHIAN_WINDOW - 1 : DONCHIAN_WINDOW + 5].notna().all()


def test_simulate_pair_trades_compression_breakout():
    n = 350
    idx = pd.date_range("2020-01-06 08:00", periods=n, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": 1.1000,
            "high": 1.1005,
            "low": 1.0995,
            "close": 1.1000,
            "donchian_high": 1.1005,
            "donchian_low": 1.0995,
            "donchian_range": 0.0010,
            "compressed": False,
        },
        index=idx,
    )

    arm_idx = 300
    for j in range(arm_idx, arm_idx + COMPRESSION_PERSISTENCE):
        frame.iloc[j, frame.columns.get_loc("compressed")] = True

    breakout_idx = arm_idx + COMPRESSION_PERSISTENCE
    while breakout_idx < n and idx[breakout_idx].hour not in range(7, 17):
        breakout_idx += 1
    assert breakout_idx < n - TIME_STOP_BARS
    frame.iloc[breakout_idx - 1, frame.columns.get_loc("donchian_high")] = 1.1010
    frame.iloc[breakout_idx, frame.columns.get_loc("close")] = 1.1012

    exit_idx = breakout_idx + TIME_STOP_BARS
    frame.iloc[exit_idx, frame.columns.get_loc("close")] = 1.1020

    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2021, 1, 1, tzinfo=UTC)
    trades = simulate_pair_trades("EUR/USD", frame, start, end)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.direction == 1
    assert trade.gross_pips == pytest.approx(8.0)


def test_determine_verdict_gross_pass_and_discard():
    pass_stats = {"trades": 50, "gross_pf": 1.15}
    discard_low_pf = {"trades": 50, "gross_pf": 1.02}
    discard_few_trades = {"trades": 10, "gross_pf": 1.20}

    assert determine_verdict(pass_stats, 0.30) == ("GROSS_PASS", "N/A")
    assert determine_verdict(discard_low_pf, 0.30)[0] == "DISCARD"
    assert determine_verdict(discard_few_trades, 0.30)[0] == "DISCARD"
    assert determine_verdict(pass_stats, 0.60)[0] == "DISCARD"


def test_trade_stats_empty():
    stats = trade_stats([])
    assert stats["trades"] == 0
    assert stats["gross_pf"] == 0.0
