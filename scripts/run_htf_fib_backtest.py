#!/usr/bin/env python3
"""Cost-aware backtest for the confirmed-HTF-pivot Fibonacci setup.

This is a deterministic Python mirror of
``pine_scripts/htf_pivots_fib_ema_strategy.pine`` on a 15-minute chart.
It deliberately evaluates two predeclared variants instead of optimizing:

* ``marker_baseline``: the pasted indicator's marker logic plus explicit ATR exits.
* ``hardened_mtf``: confirmed 15m/30m/1h RSI, EMA 50/200 trend alignment,
  candle confirmation, swing invalidation, and one entry per confirmed swing.

The pasted indicator had no orders or exits, so ``marker_baseline`` is not a
claim of exact TradingView strategy parity. It is the smallest executable
interpretation of its markers. Market orders fill on the next bar open.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_donchian_backtest import fetch_pair

DEFAULT_PAIRS = (
    "EUR/USD",
    "GBP/USD",
    "GBP/CHF",
    "GBP/JPY",
    "USD/JPY",
    "NZD/JPY",
    "AUD/CAD",
    "USD/CHF",
)
IS_FRACTION = 0.65
MIN_WINDOW_TRADES = 30
MIN_OOS_PROFIT_FACTOR = 1.20


@dataclass(frozen=True)
class StrategyConfig:
    """One preregistered interpretation of the setup."""

    name: str
    fib_timeframe: Literal["4h", "1d"] = "4h"
    left_bars: int = 5
    right_bars: int = 5
    rsi_long: float = 35.0
    rsi_short: float = 65.0
    require_mtf_rsi: bool = False
    require_ema_stack: bool = False
    require_candle: bool = False
    invalidate_swing: bool = False
    one_entry_per_swing: bool = False
    atr_period: int = 14
    tp_atr: float = 1.5
    sl_atr: float = 1.5
    max_hold_bars: int = 32
    # Tool-combo gates (see docs/research/HTF_FIB_TOOL_COMBINATIONS_2026-07.md).
    # When require_liquidity_sweep is True, swing invalidation uses close-through
    # the origin so wick-through + reclaim can fire; otherwise wick invalidation.
    require_liquidity_sweep: bool = False
    require_anchored_vwap: bool = False
    # Refinement dims (2026-07 iterative search).
    # golden=618-786, mid=500-786, wide=382-786, shallow=382-618.
    fib_zone: Literal["golden", "mid", "wide", "shallow"] = "golden"
    # none=never clear swing; wick=any pierce; close=close-through only.
    # When require_liquidity_sweep, close mode is forced for reclaim semantics.
    invalidate_mode: Literal["none", "wick", "close"] = "wick"


def hardened_base(**overrides: object) -> StrategyConfig:
    """Hardened MTF defaults with optional combo overrides."""

    payload: dict[str, object] = {
        "name": "hardened_mtf",
        "rsi_long": 30.0,
        "rsi_short": 70.0,
        "require_mtf_rsi": True,
        "require_ema_stack": True,
        "require_candle": True,
        "invalidate_swing": True,
        "one_entry_per_swing": True,
    }
    payload.update(overrides)
    return StrategyConfig(**payload)  # type: ignore[arg-type]


CONFIGS = (
    StrategyConfig(name="marker_baseline"),
    hardened_base(name="hardened_mtf"),
    hardened_base(name="hardened_sweep", require_liquidity_sweep=True),
    hardened_base(name="hardened_avwap", require_anchored_vwap=True),
    hardened_base(
        name="hardened_sweep_avwap",
        require_liquidity_sweep=True,
        require_anchored_vwap=True,
    ),
)


@dataclass(frozen=True)
class AccountScenario:
    """Fixed-lot USD account used for account-level backtests."""

    name: str
    initial_capital_usd: float
    lot_size: float
    commission_usd_per_lot_side: float = 3.0

    @property
    def base_notional_leverage(self) -> float:
        return self.lot_size * 100_000.0 / self.initial_capital_usd


ACCOUNT_SCENARIOS = (
    AccountScenario("capital_6500_lot_1_05", 6_500.68, 1.05),
    AccountScenario("capital_116502_lot_18_78", 116_502.53, 18.78),
)


@dataclass(frozen=True)
class PivotEvent:
    """A pivot that becomes knowable at ``confirmation_time``."""

    confirmation_time: pd.Timestamp
    pivot_time: pd.Timestamp
    kind: Literal["high", "low"]
    price: float


@dataclass
class SwingState:
    """Alternating confirmed-pivot state used to create directional Fibs."""

    last_kind: Literal["high", "low"] | None = None
    last_price: float | None = None
    last_time: pd.Timestamp | None = None
    high: float | None = None
    high_time: pd.Timestamp | None = None
    low: float | None = None
    low_time: pd.Timestamp | None = None
    direction: int = 0
    version: int = 0
    fib382: float | None = None
    fib500: float | None = None
    fib618: float | None = None
    fib786: float | None = None

    def _set_fib(self) -> bool:
        if self.high is None or self.low is None or self.high <= self.low:
            return False
        swing_range = self.high - self.low
        if self.direction == 1:
            self.fib382 = self.high - swing_range * 0.382
            self.fib500 = self.high - swing_range * 0.500
            self.fib618 = self.high - swing_range * 0.618
            self.fib786 = self.high - swing_range * 0.786
        elif self.direction == -1:
            self.fib382 = self.low + swing_range * 0.382
            self.fib500 = self.low + swing_range * 0.500
            self.fib618 = self.low + swing_range * 0.618
            self.fib786 = self.low + swing_range * 0.786
        else:
            return False
        self.version += 1
        return True

    def update(self, event: PivotEvent) -> bool:
        """Consume one confirmed event and return whether the Fib changed."""

        if self.last_kind is None:
            self.last_kind = event.kind
            self.last_price = event.price
            self.last_time = event.pivot_time
            return False

        if event.kind == self.last_kind:
            is_more_extreme = (
                event.kind == "high"
                and self.last_price is not None
                and event.price > self.last_price
            ) or (
                event.kind == "low"
                and self.last_price is not None
                and event.price < self.last_price
            )
            if not is_more_extreme:
                return False
            self.last_price = event.price
            self.last_time = event.pivot_time
            if event.kind == "high" and self.direction == 1:
                self.high = event.price
                self.high_time = event.pivot_time
                return self._set_fib()
            if event.kind == "low" and self.direction == -1:
                self.low = event.price
                self.low_time = event.pivot_time
                return self._set_fib()
            return False

        assert self.last_price is not None
        assert self.last_time is not None
        if event.kind == "high" and event.price > self.last_price:
            self.low = self.last_price
            self.low_time = self.last_time
            self.high = event.price
            self.high_time = event.pivot_time
            self.direction = 1
        elif event.kind == "low" and event.price < self.last_price:
            self.high = self.last_price
            self.high_time = self.last_time
            self.low = event.price
            self.low_time = event.pivot_time
            self.direction = -1
        else:
            self.last_kind = event.kind
            self.last_price = event.price
            self.last_time = event.pivot_time
            return False

        self.last_kind = event.kind
        self.last_price = event.price
        self.last_time = event.pivot_time
        return self._set_fib()

    def invalidate(self) -> None:
        """Clear a broken swing while retaining the latest confirmed pivot."""

        self.direction = 0
        self.high = None
        self.high_time = None
        self.low = None
        self.low_time = None
        self.fib382 = None
        self.fib500 = None
        self.fib618 = None
        self.fib786 = None
        self.version += 1


def fib_zone_bounds(
    direction: int,
    fib_zone: str,
    fib382: float,
    fib500: float,
    fib618: float,
    fib786: float,
) -> tuple[float, float]:
    """Return (lo, hi) inclusive price band for the configured Fib zone."""

    if direction == 1:
        # Bullish retrace: deeper = lower prices.
        if fib_zone == "golden":
            return fib786, fib618
        if fib_zone == "mid":
            return fib786, fib500
        if fib_zone == "wide":
            return fib786, fib382
        if fib_zone == "shallow":
            return fib618, fib382
    else:
        # Bearish retrace: deeper = higher prices.
        if fib_zone == "golden":
            return fib618, fib786
        if fib_zone == "mid":
            return fib500, fib786
        if fib_zone == "wide":
            return fib382, fib786
        if fib_zone == "shallow":
            return fib382, fib618
    raise ValueError(f"unknown fib_zone={fib_zone!r} for direction={direction}")


@dataclass(frozen=True)
class Trade:
    pair: str
    config: str
    account_name: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: Literal["long", "short"]
    entry_price: float
    exit_price: float
    exit_reason: str
    gross_r: float
    net_r: float
    net_pnl_usd: float
    net_pnl_pct: float
    lots: float


@dataclass
class BacktestResult:
    pair: str
    config: str
    account_name: str = "risk_fraction"
    initial_capital_usd: float = 100_000.0
    ending_balance_usd: float = 100_000.0
    trades: list[Trade] = field(default_factory=list)


@dataclass
class PreparedBacktestData:
    """Market features reused across configuration evaluations."""

    timestamps: list[pd.Timestamp]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float]
    ema50: list[float]
    ema200: list[float]
    rsi15: list[float]
    rsi30: list[float]
    rsi1h: list[float]
    usd_per_quote: list[float]
    atr_by_period: dict[int, list[float]]
    events_by_spec: dict[
        tuple[str, int, int],
        dict[pd.Timestamp, list[PivotEvent]],
    ]


@dataclass(frozen=True)
class WindowStats:
    trades: int
    win_rate: float
    gross_profit_factor: float
    net_profit_factor: float
    total_net_pnl_pct: float
    max_drawdown_pct: float
    profitable_pairs: int
    tested_pairs: int


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Return Wilder RSI without using future bars."""

    values = close.astype(float).to_numpy()
    result = [math.nan] * len(values)
    if len(values) <= period:
        return pd.Series(result, index=close.index, dtype=float)
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    avg_gain = sum(max(delta, 0.0) for delta in deltas[:period]) / period
    avg_loss = sum(max(-delta, 0.0) for delta in deltas[:period]) / period

    def to_rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = to_rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = deltas[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        result[i] = to_rsi(avg_gain, avg_loss)
    return pd.Series(result, index=close.index, dtype=float)


def _wilder_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """Return Wilder ATR aligned to the input index."""

    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        (
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    result = pd.Series(math.nan, index=data.index, dtype=float)
    if len(true_range) < period:
        return result
    average = float(true_range.iloc[:period].mean())
    result.iloc[period - 1] = average
    for i in range(period, len(true_range)):
        average = (average * (period - 1) + float(true_range.iloc[i])) / period
        result.iloc[i] = average
    return result


def _resample_ohlc(data: pd.DataFrame, frequency: str) -> pd.DataFrame:
    """Build UTC-aligned OHLC bars from 15-minute data."""

    result = (
        data[["open", "high", "low", "close"]]
        .resample(
            frequency,
            label="left",
            closed="left",
            origin="epoch",
        )
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    )
    return result.dropna()


def confirmed_pivot_events(
    htf: pd.DataFrame,
    left_bars: int,
    right_bars: int,
) -> list[PivotEvent]:
    """Return non-lookahead pivot events.

    The extra ``+1`` bar delay mirrors the Pine request pattern
    ``pivot[1]`` with ``barmerge.lookahead_on``.
    """

    events: list[PivotEvent] = []
    highs = htf["high"].astype(float).tolist()
    lows = htf["low"].astype(float).tolist()
    index = list(htf.index)
    final_candidate = len(htf) - right_bars - 1
    for candidate in range(left_bars, final_candidate + 1):
        confirmation_index = candidate + right_bars + 1
        if confirmation_index >= len(htf):
            break
        left_highs = highs[candidate - left_bars : candidate]
        right_highs = highs[candidate + 1 : candidate + right_bars + 1]
        left_lows = lows[candidate - left_bars : candidate]
        right_lows = lows[candidate + 1 : candidate + right_bars + 1]
        is_high = highs[candidate] >= max(left_highs) and highs[candidate] > max(right_highs)
        is_low = lows[candidate] <= min(left_lows) and lows[candidate] < min(right_lows)
        confirmation_time = pd.Timestamp(index[confirmation_index])
        pivot_time = pd.Timestamp(index[candidate])
        if is_high:
            events.append(PivotEvent(confirmation_time, pivot_time, "high", highs[candidate]))
        if is_low:
            events.append(PivotEvent(confirmation_time, pivot_time, "low", lows[candidate]))
    return sorted(events, key=lambda event: (event.confirmation_time, event.pivot_time, event.kind))


def _confirmed_htf_rsi(data: pd.DataFrame, frequency: str, period: int) -> pd.Series:
    """Map only the previous closed HTF RSI value onto each 15-minute bar."""

    htf = _resample_ohlc(data, frequency)
    confirmed = _wilder_rsi(htf["close"], period).shift(1)
    return confirmed.reindex(data.index, method="ffill")


def _pip_size(pair: str) -> float:
    return 0.01 if "JPY" in pair else 0.0001


def _profit_factor(values: list[float]) -> float:
    gains = sum(value for value in values if value > 0)
    losses = sum(-value for value in values if value < 0)
    if losses == 0:
        return 99.0 if gains > 0 else 0.0
    return gains / losses


def usd_conversion_pair(pair: str) -> str | None:
    """Return the USD/quote pair needed to convert quote P&L to USD."""

    base, quote = pair.split("/")
    if quote == "USD" or base == "USD":
        return None
    return f"USD/{quote}"


def load_usd_conversion_closes(
    pair_data: dict[str, pd.DataFrame],
    days: int,
) -> dict[str, pd.Series | None]:
    """Load historical conversion closes required by the tested cross pairs."""

    conversion_frames: dict[str, pd.DataFrame] = {}
    required = {
        conversion for pair in pair_data if (conversion := usd_conversion_pair(pair)) is not None
    }
    for conversion_pair in sorted(required):
        if conversion_pair in pair_data:
            conversion_frames[conversion_pair] = pair_data[conversion_pair]
            continue
        frames = fetch_pair(conversion_pair, days)
        if frames is None:
            raise RuntimeError(f"missing required conversion data: {conversion_pair}")
        conversion_frames[conversion_pair] = frames["15m"].sort_index()

    return {
        pair: (
            conversion_frames[conversion]["close"]
            if (conversion := usd_conversion_pair(pair)) is not None
            else None
        )
        for pair in pair_data
    }


def _usd_per_quote_values(
    pair: str,
    data: pd.DataFrame,
    usd_quote_close: pd.Series | None,
) -> list[float]:
    """Return historical USD value of one unit of the pair's quote currency."""

    base, quote = pair.split("/")
    if quote == "USD":
        return [1.0] * len(data)
    if base == "USD":
        return [float(value) for value in (1.0 / data["close"].astype(float)).tolist()]
    if usd_quote_close is None:
        raise ValueError(f"{pair} requires {usd_conversion_pair(pair)} data for USD P&L")
    aligned = usd_quote_close.sort_index().reindex(data.index, method="ffill")
    # Cover leading gaps when conversion cache starts later than the pair.
    aligned = aligned.bfill()
    values: list[float] = []
    for value in aligned.astype(float).tolist():
        if value is None or (isinstance(value, float) and math.isnan(value)) or value == 0.0:
            values.append(math.nan)
        else:
            values.append(float(1.0 / value))
    return values


def fixed_lot_net_pnl_usd(
    net_price_move: float,
    usd_per_quote: float,
    account: AccountScenario,
) -> float:
    """Convert one fixed-lot trade's price move and commission to USD."""

    quantity = account.lot_size * 100_000.0
    commission = 2.0 * account.commission_usd_per_lot_side * account.lot_size
    return quantity * net_price_move * usd_per_quote - commission


def capital_fraction_stop_distance(
    account: AccountScenario,
    usd_per_quote: float,
    exit_slippage_price: float,
    capital_fraction: float,
) -> float:
    """Return fill-to-stop distance targeting a fixed fraction of starting capital."""

    if not 0.0 < capital_fraction < 1.0:
        raise ValueError("capital_fraction must be between 0 and 1")
    quantity = account.lot_size * 100_000.0
    commission = 2.0 * account.commission_usd_per_lot_side * account.lot_size
    loss_budget = account.initial_capital_usd * capital_fraction
    distance = (loss_budget - commission) / (quantity * usd_per_quote)
    distance -= exit_slippage_price
    if distance <= 0:
        raise ValueError("capital-fraction stop is not positive after costs")
    return distance


def prepare_backtest_data(
    pair: str,
    data_15m: pd.DataFrame,
    *,
    pivot_specs: set[tuple[Literal["4h", "1d"], int, int]],
    atr_periods: set[int],
    usd_quote_close: pd.Series | None = None,
) -> PreparedBacktestData:
    """Calculate invariant market features once for a bounded search."""

    cols = ["open", "high", "low", "close"]
    if "volume" in data_15m.columns:
        cols = [*cols, "volume"]
    data = data_15m[cols].copy().sort_index()
    if "volume" not in data.columns:
        data["volume"] = 0.0
    events_by_spec: dict[
        tuple[str, int, int],
        dict[pd.Timestamp, list[PivotEvent]],
    ] = {}
    for timeframe, left_bars, right_bars in pivot_specs:
        events_by_time: dict[pd.Timestamp, list[PivotEvent]] = {}
        for event in confirmed_pivot_events(
            _resample_ohlc(data, timeframe),
            left_bars,
            right_bars,
        ):
            events_by_time.setdefault(event.confirmation_time, []).append(event)
        events_by_spec[(timeframe, left_bars, right_bars)] = events_by_time

    return PreparedBacktestData(
        timestamps=[pd.Timestamp(timestamp) for timestamp in data.index],
        opens=data["open"].astype(float).tolist(),
        highs=data["high"].astype(float).tolist(),
        lows=data["low"].astype(float).tolist(),
        closes=data["close"].astype(float).tolist(),
        volumes=data["volume"].astype(float).fillna(0.0).tolist(),
        ema50=data["close"].ewm(span=50, adjust=False).mean().astype(float).tolist(),
        ema200=data["close"].ewm(span=200, adjust=False).mean().astype(float).tolist(),
        rsi15=_wilder_rsi(data["close"], 14).astype(float).tolist(),
        rsi30=_confirmed_htf_rsi(data, "30min", 14).astype(float).tolist(),
        rsi1h=_confirmed_htf_rsi(data, "1h", 14).astype(float).tolist(),
        usd_per_quote=_usd_per_quote_values(pair, data, usd_quote_close),
        atr_by_period={
            period: _wilder_atr(data, period).astype(float).tolist() for period in atr_periods
        },
        events_by_spec=events_by_spec,
    )


def run_prepared_backtest(
    pair: str,
    prepared: PreparedBacktestData,
    config: StrategyConfig,
    *,
    spread_pips: float = 2.0,
    slippage_pips: float = 2.0,
    commission_per_order: float = 3.0,
    initial_balance: float = 100_000.0,
    risk_fraction: float = 0.01,
    account: AccountScenario | None = None,
    stop_capital_fraction: float | None = None,
) -> BacktestResult:
    """Run one prepared pair/config with pessimistic same-bar exits."""

    ema50 = prepared.ema50
    ema200 = prepared.ema200
    rsi15 = prepared.rsi15
    rsi30 = prepared.rsi30
    rsi1h = prepared.rsi1h
    atr = prepared.atr_by_period[config.atr_period]
    events_by_time = prepared.events_by_spec[
        (config.fib_timeframe, config.left_bars, config.right_bars)
    ]

    state = SwingState()
    starting_capital = account.initial_capital_usd if account is not None else initial_balance
    result = BacktestResult(
        pair=pair,
        config=config.name,
        account_name=account.name if account is not None else "risk_fraction",
        initial_capital_usd=starting_capital,
        ending_balance_usd=starting_capital,
    )
    balance = starting_capital
    position: Literal["long", "short"] | None = None
    pending_direction: Literal["long", "short"] | None = None
    pending_atr = 0.0
    entry_mid = entry_price = stop_price = target_price = risk_distance = 0.0
    entry_index = 0
    entry_time = prepared.timestamps[0]
    previous_long_condition = False
    previous_short_condition = False
    traded_swing_version = -1
    avwap_version = -1
    avwap_cum_pv = 0.0
    avwap_cum_v = 0.0
    avwap_value = math.nan
    timestamp_to_index = {ts: idx for idx, ts in enumerate(prepared.timestamps)}
    pip = _pip_size(pair)
    spread = spread_pips * pip
    slippage = slippage_pips * pip

    def _seed_anchored_vwap(through_index: int) -> None:
        """Rebuild tick-volume AVWAP from the knowable swing origin through ``through_index``."""

        nonlocal avwap_cum_pv, avwap_cum_v, avwap_value, avwap_version
        avwap_cum_pv = 0.0
        avwap_cum_v = 0.0
        avwap_value = math.nan
        avwap_version = state.version
        if state.direction == 0:
            return
        anchor_time = state.low_time if state.direction == 1 else state.high_time
        if anchor_time is None:
            return
        start_index = timestamp_to_index.get(anchor_time)
        if start_index is None:
            # Pivot may sit on an HTF boundary not present as a 15m open; start at first bar ≥ pivot.
            start_index = next(
                (
                    idx
                    for idx, ts in enumerate(prepared.timestamps)
                    if ts >= anchor_time
                ),
                through_index,
            )
        start_index = min(start_index, through_index)
        for j in range(start_index, through_index + 1):
            typical = (prepared.highs[j] + prepared.lows[j] + prepared.closes[j]) / 3.0
            weight = prepared.volumes[j] if prepared.volumes[j] > 0.0 else 1.0
            avwap_cum_pv += typical * weight
            avwap_cum_v += weight
        if avwap_cum_v > 0.0:
            avwap_value = avwap_cum_pv / avwap_cum_v

    for i, timestamp in enumerate(prepared.timestamps):
        open_price = prepared.opens[i]
        high = prepared.highs[i]
        low = prepared.lows[i]
        close = prepared.closes[i]

        if pending_direction is not None and position is None:
            position = pending_direction
            entry_index = i
            entry_time = timestamp
            entry_mid = open_price
            if stop_capital_fraction is not None:
                if account is None:
                    raise ValueError("stop_capital_fraction requires a fixed-lot account")
                entry_usd_per_quote = prepared.usd_per_quote[i]
                if math.isnan(entry_usd_per_quote):
                    raise ValueError(
                        f"missing {usd_conversion_pair(pair)} conversion at {timestamp}"
                    )
                stop_distance = capital_fraction_stop_distance(
                    account,
                    entry_usd_per_quote,
                    slippage,
                    stop_capital_fraction,
                )
            else:
                stop_distance = pending_atr * config.sl_atr
            if position == "long":
                entry_price = entry_mid + spread + slippage
                stop_price = entry_price - stop_distance
                target_price = entry_price + pending_atr * config.tp_atr
            else:
                entry_price = entry_mid - spread - slippage
                stop_price = entry_price + stop_distance
                target_price = entry_price - pending_atr * config.tp_atr
            risk_distance = abs(entry_price - stop_price)
            pending_direction = None

        if position is not None:
            exit_price: float | None = None
            exit_reason = ""
            if i - entry_index > config.max_hold_bars:
                exit_price = open_price + (slippage if position == "short" else -slippage)
                exit_reason = "time"
            elif position == "long":
                if low <= stop_price:
                    exit_price = stop_price - slippage
                    exit_reason = "stop"
                elif high >= target_price:
                    exit_price = target_price - slippage
                    exit_reason = "target"
            else:
                if high >= stop_price:
                    exit_price = stop_price + slippage
                    exit_reason = "stop"
                elif low <= target_price:
                    exit_price = target_price + slippage
                    exit_reason = "target"
            if exit_price is not None and risk_distance > 0:
                net_move = (
                    exit_price - entry_price if position == "long" else entry_price - exit_price
                )
                if exit_reason == "stop":
                    gross_r = -1.0
                elif exit_reason == "target":
                    gross_r = config.tp_atr / config.sl_atr
                else:
                    mid_move = (
                        open_price - entry_mid if position == "long" else entry_mid - open_price
                    )
                    gross_r = mid_move / risk_distance
                usd_per_quote = prepared.usd_per_quote[i]
                if math.isnan(usd_per_quote):
                    # Skip P&L for this exit if conversion is unavailable (cache gap).
                    position = None
                    continue
                if account is None:
                    risk_cash = balance * risk_fraction
                    quantity = risk_cash / (risk_distance * usd_per_quote)
                    lots = quantity / 100_000.0
                    commission = 2.0 * commission_per_order
                else:
                    lots = account.lot_size
                    quantity = lots * 100_000.0
                    risk_cash = quantity * risk_distance * usd_per_quote
                    commission = 0.0
                net_cash = (
                    quantity * net_move * usd_per_quote - commission
                    if account is None
                    else fixed_lot_net_pnl_usd(net_move, usd_per_quote, account)
                )
                net_r = net_cash / risk_cash if risk_cash > 0 else 0.0
                net_pnl_pct = net_cash / starting_capital * 100.0
                balance += net_cash
                result.trades.append(
                    Trade(
                        pair=pair,
                        config=config.name,
                        account_name=result.account_name,
                        entry_time=entry_time,
                        exit_time=timestamp,
                        direction=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        gross_r=gross_r,
                        net_r=net_r,
                        net_pnl_usd=net_cash,
                        net_pnl_pct=net_pnl_pct,
                        lots=lots,
                    )
                )
                result.ending_balance_usd = balance
                position = None

        current_events = events_by_time.get(timestamp, [])
        if (
            len(current_events) == 2
            and current_events[0].pivot_time == current_events[1].pivot_time
        ):
            current_events = []
        fib_changed = False
        for event in current_events:
            fib_changed = state.update(event) or fib_changed

        # Invalidation: none | wick | close. Sweep stacks force close so reclaim works.
        inv_mode = config.invalidate_mode
        if config.require_liquidity_sweep:
            inv_mode = "close"
        elif not config.invalidate_swing:
            inv_mode = "none"
        if inv_mode != "none" and state.direction == 1 and state.low is not None:
            broken = close < state.low if inv_mode == "close" else low < state.low
            if broken:
                state.invalidate()
                fib_changed = True
        elif inv_mode != "none" and state.direction == -1 and state.high is not None:
            broken = close > state.high if inv_mode == "close" else high > state.high
            if broken:
                state.invalidate()
                fib_changed = True

        if config.require_anchored_vwap:
            if state.direction == 0:
                avwap_version = -1
                avwap_cum_pv = 0.0
                avwap_cum_v = 0.0
                avwap_value = math.nan
            elif fib_changed or state.version != avwap_version:
                _seed_anchored_vwap(i)
            else:
                typical = (high + low + close) / 3.0
                weight = prepared.volumes[i] if prepared.volumes[i] > 0.0 else 1.0
                avwap_cum_pv += typical * weight
                avwap_cum_v += weight
                avwap_value = avwap_cum_pv / avwap_cum_v if avwap_cum_v > 0.0 else math.nan

        current_atr = atr[i]
        current_rsi15 = rsi15[i]
        current_rsi30 = rsi30[i]
        current_rsi1h = rsi1h[i]
        if (
            position is not None
            or pending_direction is not None
            or state.direction == 0
            or state.fib382 is None
            or state.fib500 is None
            or state.fib618 is None
            or state.fib786 is None
            or any(
                math.isnan(value)
                for value in (current_atr, current_rsi15, current_rsi30, current_rsi1h)
            )
        ):
            previous_long_condition = False
            previous_short_condition = False
            continue

        zone_lo, zone_hi = fib_zone_bounds(
            state.direction,
            config.fib_zone,
            state.fib382,
            state.fib500,
            state.fib618,
            state.fib786,
        )
        bull_zone = state.direction == 1 and zone_lo <= close <= zone_hi
        bear_zone = state.direction == -1 and zone_lo <= close <= zone_hi
        long_rsi_ok = current_rsi15 <= config.rsi_long
        short_rsi_ok = current_rsi15 >= config.rsi_short
        if config.require_mtf_rsi:
            long_rsi_ok = long_rsi_ok and current_rsi30 <= config.rsi_long
            long_rsi_ok = long_rsi_ok and current_rsi1h <= config.rsi_long
            short_rsi_ok = short_rsi_ok and current_rsi30 >= config.rsi_short
            short_rsi_ok = short_rsi_ok and current_rsi1h >= config.rsi_short

        if config.require_ema_stack:
            long_trend_ok = close > ema50[i] > ema200[i]
            short_trend_ok = close < ema50[i] < ema200[i]
        else:
            long_trend_ok = close > ema200[i]
            short_trend_ok = close < ema200[i]

        previous_close = prepared.closes[i - 1] if i else close
        bullish_candle = close > open_price and close > previous_close
        bearish_candle = close < open_price and close < previous_close

        long_sweep_ok = True
        short_sweep_ok = True
        if config.require_liquidity_sweep:
            # Wick beyond swing origin liquidity, close reclaims back above/below it.
            long_sweep_ok = (
                state.low is not None and low < state.low and close > state.low
            )
            short_sweep_ok = (
                state.high is not None and high > state.high and close < state.high
            )

        long_vwap_ok = True
        short_vwap_ok = True
        if config.require_anchored_vwap:
            if math.isnan(avwap_value):
                long_vwap_ok = False
                short_vwap_ok = False
            else:
                long_vwap_ok = close >= avwap_value
                short_vwap_ok = close <= avwap_value

        long_condition = (
            bull_zone
            and long_trend_ok
            and long_rsi_ok
            and long_sweep_ok
            and long_vwap_ok
            and (not config.require_candle or bullish_candle)
        )
        short_condition = (
            bear_zone
            and short_trend_ok
            and short_rsi_ok
            and short_sweep_ok
            and short_vwap_ok
            and (not config.require_candle or bearish_candle)
        )
        long_trigger = long_condition and not previous_long_condition
        short_trigger = short_condition and not previous_short_condition
        swing_available = not config.one_entry_per_swing or traded_swing_version != state.version

        if i + 1 < len(prepared.timestamps) and swing_available:
            if long_trigger:
                pending_direction = "long"
                pending_atr = current_atr
                traded_swing_version = state.version
            elif short_trigger:
                pending_direction = "short"
                pending_atr = current_atr
                traded_swing_version = state.version
        previous_long_condition = long_condition
        previous_short_condition = short_condition

    return result


def run_backtest(
    pair: str,
    data_15m: pd.DataFrame,
    config: StrategyConfig,
    *,
    spread_pips: float = 2.0,
    slippage_pips: float = 2.0,
    commission_per_order: float = 3.0,
    initial_balance: float = 100_000.0,
    risk_fraction: float = 0.01,
    account: AccountScenario | None = None,
    usd_quote_close: pd.Series | None = None,
    stop_capital_fraction: float | None = None,
) -> BacktestResult:
    """Prepare one dataset and run one configuration."""

    prepared = prepare_backtest_data(
        pair,
        data_15m,
        pivot_specs={(config.fib_timeframe, config.left_bars, config.right_bars)},
        atr_periods={config.atr_period},
        usd_quote_close=usd_quote_close,
    )
    return run_prepared_backtest(
        pair,
        prepared,
        config,
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
        commission_per_order=commission_per_order,
        initial_balance=initial_balance,
        risk_fraction=risk_fraction,
        account=account,
        stop_capital_fraction=stop_capital_fraction,
    )


def aggregate_window(
    results: list[BacktestResult],
    cutoff_by_pair: dict[str, pd.Timestamp],
    *,
    oos: bool,
) -> WindowStats:
    """Aggregate trades whose entries belong to one chronological window."""

    trades: list[Trade] = []
    profitable_pairs = 0
    pair_count = 0
    for result in results:
        cutoff = cutoff_by_pair[result.pair]
        selected = [trade for trade in result.trades if (trade.entry_time > cutoff) == oos]
        pair_count += 1
        pair_pnl = sum(trade.net_pnl_usd for trade in selected)
        profitable_pairs += int(pair_pnl > 0)
        trades.extend(selected)

    net_values = [trade.net_r for trade in trades]
    gross_values = [trade.gross_r for trade in trades]
    wins = sum(value > 0 for value in net_values)
    initial_capital = results[0].initial_capital_usd if results else 100_000.0
    equity = peak = initial_capital
    max_drawdown = 0.0
    for trade in sorted(trades, key=lambda item: item.exit_time):
        equity += trade.net_pnl_usd
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
    return WindowStats(
        trades=len(trades),
        win_rate=wins / len(trades) if trades else 0.0,
        gross_profit_factor=_profit_factor(gross_values),
        net_profit_factor=_profit_factor(net_values),
        total_net_pnl_pct=(equity - initial_capital) / initial_capital * 100.0,
        max_drawdown_pct=max_drawdown,
        profitable_pairs=profitable_pairs,
        tested_pairs=pair_count,
    )


def verdict(in_sample: WindowStats, out_of_sample: WindowStats) -> tuple[str, list[str]]:
    """Apply the repository's locked minimum validation gates."""

    reasons: list[str] = []
    if in_sample.trades < MIN_WINDOW_TRADES:
        reasons.append(f"IS trades {in_sample.trades} < {MIN_WINDOW_TRADES}")
    if out_of_sample.trades < MIN_WINDOW_TRADES:
        reasons.append(f"OOS trades {out_of_sample.trades} < {MIN_WINDOW_TRADES}")
    if out_of_sample.net_profit_factor < MIN_OOS_PROFIT_FACTOR:
        reasons.append(
            f"OOS net PF {out_of_sample.net_profit_factor:.2f} < {MIN_OOS_PROFIT_FACTOR:.2f}"
        )
    if in_sample.total_net_pnl_pct <= 0:
        reasons.append(f"IS net PnL {in_sample.total_net_pnl_pct:.2f}% <= 0")
    if out_of_sample.total_net_pnl_pct <= 0:
        reasons.append(f"OOS net PnL {out_of_sample.total_net_pnl_pct:.2f}% <= 0")
    return ("KEEP", []) if not reasons else ("DISCARD", reasons)


def _write_report(
    output_dir: Path,
    rows: list[tuple[StrategyConfig, WindowStats, WindowStats, str, list[str]]],
    trades: list[Trade],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"htf_fib_backtest_{stamp}.md"
    trades_path = output_dir / f"htf_fib_backtest_{stamp}.csv"
    with trades_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "pair",
                "config",
                "account",
                "entry_time",
                "exit_time",
                "direction",
                "entry_price",
                "exit_price",
                "exit_reason",
                "gross_r",
                "net_r",
                "net_pnl_usd",
                "net_pnl_pct",
                "lots",
            )
        )
        for trade in trades:
            writer.writerow(
                (
                    trade.pair,
                    trade.config,
                    trade.account_name,
                    trade.entry_time.isoformat(),
                    trade.exit_time.isoformat(),
                    trade.direction,
                    f"{trade.entry_price:.6f}",
                    f"{trade.exit_price:.6f}",
                    trade.exit_reason,
                    f"{trade.gross_r:.6f}",
                    f"{trade.net_r:.6f}",
                    f"{trade.net_pnl_usd:.6f}",
                    f"{trade.net_pnl_pct:.6f}",
                    f"{trade.lots:.6f}",
                )
            )

    lines = [
        "# Confirmed HTF Fib Strategy Backtest",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Data: cached Dukascopy 15-minute OHLC, chronologically judged at 65% IS / 35% OOS.",
        "Execution: signal on close, market entry at next bar open, stop-first when TP and SL share a bar.",
        "Costs: 2.0 pip spread, 2.0 pip adverse slippage per fill, $3 commission per order.",
        "Sizing: 1% equity risk per trade. Results exclude news because no point-in-time news archive is present.",
        "",
        "## Results",
        "",
        "| Config | Window | Trades | WR | Gross PF | Net PF | Net PnL | Max DD | Pairs + |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for config, ins, oos, decision, reasons in rows:
        for window, stats in (("IS", ins), ("OOS", oos)):
            lines.append(
                f"| `{config.name}` | {window} | {stats.trades} | {stats.win_rate:.1%} | "
                f"{stats.gross_profit_factor:.2f} | {stats.net_profit_factor:.2f} | "
                f"{stats.total_net_pnl_pct:.2f}% | {stats.max_drawdown_pct:.2f}% | "
                f"{stats.profitable_pairs}/{stats.tested_pairs} |"
            )
        lines.extend(
            (
                "",
                f"**{config.name}: {decision}.** "
                + ("; ".join(reasons) if reasons else "All minimum gates passed."),
                "",
            )
        )
    lines.extend(
        (
            "## Interpretation limits",
            "",
            "- The original Pine file was an indicator with no exits. The baseline adds fixed ATR brackets "
            "and a time exit solely to make its markers measurable.",
            "- Dukascopy UTC bars can differ from the broker/session boundaries used by a TradingView symbol.",
            "- No parameter search was performed. The OOS window was used only as a judge.",
            "- Passing these gates would justify further validation, not live trading.",
            "",
        )
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path, trades_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    pairs = [pair.strip() for pair in args.pairs.split(",") if pair.strip()]

    pair_data: dict[str, pd.DataFrame] = {}
    cutoff_by_pair: dict[str, pd.Timestamp] = {}
    for pair in pairs:
        frames = fetch_pair(pair, args.days)
        if frames is None:
            continue
        data_15m = frames["15m"].sort_index()
        pair_data[pair] = data_15m
        cutoff_by_pair[pair] = pd.Timestamp(data_15m.index[int(len(data_15m) * IS_FRACTION)])
    if not pair_data:
        print("No complete cached/fetched datasets were available.")
        return 1
    conversion_closes = load_usd_conversion_closes(pair_data, args.days)

    report_rows: list[tuple[StrategyConfig, WindowStats, WindowStats, str, list[str]]] = []
    all_trades: list[Trade] = []
    for config in CONFIGS:
        results = [
            run_backtest(
                pair,
                data,
                config,
                usd_quote_close=conversion_closes[pair],
            )
            for pair, data in pair_data.items()
        ]
        ins = aggregate_window(results, cutoff_by_pair, oos=False)
        oos = aggregate_window(results, cutoff_by_pair, oos=True)
        decision, reasons = verdict(ins, oos)
        report_rows.append((config, ins, oos, decision, reasons))
        all_trades.extend(trade for result in results for trade in result.trades)
        print(
            f"{config.name}: {decision} | "
            f"IS {ins.trades} trades net PF {ins.net_profit_factor:.2f} "
            f"PnL {ins.total_net_pnl_pct:.2f}% | "
            f"OOS {oos.trades} trades net PF {oos.net_profit_factor:.2f} "
            f"PnL {oos.total_net_pnl_pct:.2f}%"
        )
        if reasons:
            print("  " + "; ".join(reasons))

    report_path, trades_path = _write_report(args.output_dir, report_rows, all_trades)
    print(f"Report: {report_path}")
    print(f"Trades: {trades_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
