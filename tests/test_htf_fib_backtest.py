"""Unit tests for the confirmed-pivot Fibonacci backtest."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from scripts.optimize_htf_fib_backtest import (
    entry_configurations,
    optimization_score,
)
from scripts.run_htf_fib_backtest import (
    ACCOUNT_SCENARIOS,
    AccountScenario,
    PivotEvent,
    SwingState,
    _profit_factor,
    _usd_per_quote_values,
    _wilder_rsi,
    capital_fraction_stop_distance,
    confirmed_pivot_events,
    fixed_lot_net_pnl_usd,
)


def _event(kind: str, price: float, hour: int) -> PivotEvent:
    pivot_time = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=hour)
    return PivotEvent(
        confirmation_time=pivot_time + pd.Timedelta(hours=4),
        pivot_time=pivot_time,
        kind=kind,  # type: ignore[arg-type]
        price=price,
    )


def test_confirmed_pivot_is_delayed_one_bar_after_right_window() -> None:
    index = pd.date_range("2026-01-01", periods=8, freq="4h", tz="UTC")
    frame = pd.DataFrame(
        {
            "high": [1, 2, 5, 3, 2, 2, 2, 2],
            "low": [0, 0, 1, 0, 0, 0, 0, 0],
        },
        index=index,
    )

    events = confirmed_pivot_events(frame, left_bars=2, right_bars=2)

    high = next(event for event in events if event.kind == "high")
    assert high.pivot_time == index[2]
    assert high.confirmation_time == index[5]


def test_swing_state_builds_bullish_fib_from_low_then_high() -> None:
    state = SwingState()
    assert state.update(_event("low", 100.0, 0)) is False

    assert state.update(_event("high", 120.0, 4)) is True

    assert state.direction == 1
    assert state.fib618 == pytest.approx(107.64)
    assert state.fib786 == pytest.approx(104.28)


def test_swing_state_builds_bearish_fib_from_high_then_low() -> None:
    state = SwingState()
    state.update(_event("high", 120.0, 0))

    assert state.update(_event("low", 100.0, 4)) is True

    assert state.direction == -1
    assert state.fib618 == pytest.approx(112.36)
    assert state.fib786 == pytest.approx(115.72)


def test_same_kind_more_extreme_pivot_updates_active_swing() -> None:
    state = SwingState()
    state.update(_event("low", 100.0, 0))
    state.update(_event("high", 120.0, 4))
    original_version = state.version

    assert state.update(_event("high", 125.0, 8)) is True

    assert state.high == 125.0
    assert state.version == original_version + 1


def test_invalidation_clears_fib_levels() -> None:
    state = SwingState()
    state.update(_event("low", 100.0, 0))
    state.update(_event("high", 120.0, 4))

    state.invalidate()

    assert state.direction == 0
    assert state.fib618 is None
    assert state.fib786 is None


def test_wilder_rsi_has_no_values_before_full_period() -> None:
    close = pd.Series(range(20), dtype=float)

    rsi = _wilder_rsi(close, period=14)

    assert rsi.iloc[:14].isna().all()
    assert rsi.iloc[14] == 100.0
    assert not math.isnan(float(rsi.iloc[-1]))


def test_profit_factor_handles_no_loss_and_no_trade_cases() -> None:
    assert _profit_factor([]) == 0.0
    assert _profit_factor([1.0, 2.0]) == 99.0
    assert _profit_factor([2.0, -1.0]) == 2.0


def test_entry_grid_is_bounded_and_unique() -> None:
    configs = entry_configurations()

    assert len(configs) == 128
    assert len({config.name for config in configs}) == len(configs)


def test_optimization_score_penalizes_thin_samples() -> None:
    from scripts.run_htf_fib_backtest import WindowStats

    robust = WindowStats(30, 0.5, 1.2, 1.1, 1.0, 1.0, 4, 8)
    thin = WindowStats(5, 0.8, 3.0, 2.5, 1.0, 1.0, 4, 8)

    assert optimization_score(robust) > optimization_score(thin)


def test_requested_accounts_have_equivalent_notional_leverage() -> None:
    small, large = ACCOUNT_SCENARIOS

    assert small.base_notional_leverage == pytest.approx(16.15, rel=0.001)
    assert large.base_notional_leverage == pytest.approx(16.12, rel=0.001)


def test_fixed_lot_pnl_scales_commission_by_lot() -> None:
    account = AccountScenario("test", 6_500.68, 1.05)

    pnl = fixed_lot_net_pnl_usd(0.0010, 1.0, account)

    assert pnl == pytest.approx(98.70)


def test_usd_per_quote_conversion_for_jpy_cross() -> None:
    index = pd.date_range("2026-01-01", periods=2, freq="15min", tz="UTC")
    pair_data = pd.DataFrame({"close": [200.0, 201.0]}, index=index)
    usd_jpy = pd.Series([150.0, 151.0], index=index)

    values = _usd_per_quote_values("GBP/JPY", pair_data, usd_jpy)

    assert values == pytest.approx([1 / 150.0, 1 / 151.0])


def test_capital_fraction_stop_includes_exit_slippage_and_commission() -> None:
    account = AccountScenario("test", 6_500.68, 1.05)
    slippage = 0.0002
    distance = capital_fraction_stop_distance(account, 1.0, slippage, 0.80)

    stopped_pnl = fixed_lot_net_pnl_usd(-(distance + slippage), 1.0, account)

    assert stopped_pnl == pytest.approx(-account.initial_capital_usd * 0.80)
