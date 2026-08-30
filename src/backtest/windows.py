"""Chronological develop/holdout split for offline replay.

First ``DEVELOP_FRAC`` of the *bar index* is develop. The tail is holdout.
Holdout is a judge, never a rank key. Not a live-go path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd

DEVELOP_FRAC = 0.65


class PnlTrade(Protocol):
    """Minimal trade shape for window metrics."""

    entry_time: datetime
    pnl: float


@dataclass(frozen=True)
class WindowMetrics:
    """Additive cash metrics for one chronological window."""

    trades: int
    wins: int
    losses: int
    win_rate: float
    pnl: float
    pnl_pct: float
    profit_factor: float


def cutoff_at(index: pd.Index, frac: float = DEVELOP_FRAC) -> datetime | None:
    """Return the last develop-bar timestamp, or None when the index is empty."""

    if len(index) == 0:
        return None
    loc = min(max(int(len(index) * frac), 0), len(index) - 1)
    stamp = pd.Timestamp(index[loc])
    if pd.isna(stamp):
        raise TypeError(f"window cutoff index value is not a timestamp: {index[loc]!r}")
    converted = stamp.to_pydatetime()
    if not isinstance(converted, datetime):
        raise TypeError(f"window cutoff index value is not a timestamp: {index[loc]!r}")
    return converted


def trade_in_holdout(entry_time: object, cutoff: datetime) -> bool:
    """True when the trade's entry is strictly after the develop cutoff."""

    return bool(pd.Timestamp(entry_time) > pd.Timestamp(cutoff))


def metrics_from_pnls(
    pnls: Sequence[float],
    *,
    initial_balance: float = 10000.0,
) -> WindowMetrics:
    """Build window metrics from cash PnL values (no walk-forward compounding)."""

    trades = len(pnls)
    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = sum(1 for pnl in pnls if pnl <= 0)
    pnl = float(sum(pnls))
    pnl_pct = (pnl / initial_balance * 100.0) if initial_balance else 0.0
    win_rate = wins / trades if trades else 0.0
    win_sum = sum(value for value in pnls if value > 0)
    loss_sum = sum(-value for value in pnls if value <= 0)
    if trades == 0:
        profit_factor = 0.0
    elif loss_sum == 0:
        profit_factor = float("inf") if win_sum > 0 else 0.0
    else:
        profit_factor = win_sum / loss_sum
    return WindowMetrics(
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        pnl=pnl,
        pnl_pct=pnl_pct,
        profit_factor=profit_factor,
    )


def split_trade_metrics(
    trades: Sequence[PnlTrade],
    cutoff: datetime,
    *,
    initial_balance: float = 10000.0,
) -> tuple[WindowMetrics, WindowMetrics]:
    """Split trades on ``entry_time`` and return (develop, holdout) metrics."""

    develop = [trade.pnl for trade in trades if not trade_in_holdout(trade.entry_time, cutoff)]
    holdout = [trade.pnl for trade in trades if trade_in_holdout(trade.entry_time, cutoff)]
    return (
        metrics_from_pnls(develop, initial_balance=initial_balance),
        metrics_from_pnls(holdout, initial_balance=initial_balance),
    )


def format_window_line(label: str, metrics: WindowMetrics) -> str:
    """One CLI line: counts plus WR / cash PnL / PF. Empty windows stay explicit."""

    if metrics.trades == 0:
        return f"  {label}: 0 trades"
    pf = "inf" if metrics.profit_factor == float("inf") else f"{metrics.profit_factor:.2f}"
    return (
        f"  {label}: {metrics.trades} trades | WR {metrics.win_rate:.1%} | "
        f"PnL ${metrics.pnl:.2f} ({metrics.pnl_pct:.2f}%) | PF {pf}"
    )
