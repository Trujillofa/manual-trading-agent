"""Shared same-bar TP/SL resolution for offline walks.

Stop-first when both levels trade in one bar (pessimistic). This is the #49
fill contract used by the enhanced engine and the live_mtf_rsi harness.

Not a live-go path and not a strategy.
"""

from __future__ import annotations

from typing import Literal

_BUY_SIDES = frozenset({"buy", "long"})
_SELL_SIDES = frozenset({"sell", "short"})


def same_bar_exit(
    side: str,
    high: float,
    low: float,
    tp: float,
    sl: float,
) -> Literal["tp", "sl"] | None:
    """Return which level is hit on this bar, or None.

    ``side`` accepts buy/long/sell/short (any case). When both TP and SL
    trade in the range, the stop wins.
    """
    key = side.lower()
    if key in _BUY_SIDES:
        if low <= sl:
            return "sl"
        if high >= tp:
            return "tp"
        return None
    if key in _SELL_SIDES:
        if high >= sl:
            return "sl"
        if low <= tp:
            return "tp"
        return None
    raise ValueError(f"unknown side {side!r}")
