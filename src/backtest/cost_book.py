"""Frozen execution-cost book for offline backtest runners.

This is a tiny shared helper, not a strategy engine. Each runner still owns
its walk loop. Costs are frozen so a run cannot silently mutate spread,
slippage, commission, or size after the book is built.

Units
-----
spread_pips
    Half-spread applied to the *entry* fill, in pair pips.
    JPY quotes: 1 pip = 0.01. Otherwise: 1 pip = 0.0001.
slippage_pips
    Adverse slippage applied to *each* fill (entry and exit), in pair pips.
commission_usd_per_lot_side
    USD cash commission per 1.0 lot per side.
    Round-trip = ``2 * commission_usd_per_lot_side * lot_size``.
lot_size
    Position size in lots (1.0 lot = 100_000 units of base). Risk-fraction
    runners may size from equity and still use this book for commission
    when they scale by lots; default 1.0 means $3/side → $6 round-trip.

This module does not send broker orders and is not a live-go path.
"""

from __future__ import annotations

from dataclasses import dataclass

_BUY_SIDES = frozenset({"buy", "long"})
_SELL_SIDES = frozenset({"sell", "short"})


def pip_size_for_pair(pair: str) -> float:
    """Return the pair's pip increment (JPY quotes use 0.01)."""

    return 0.01 if "JPY" in pair else 0.0001


@dataclass(frozen=True)
class CostBook:
    """Immutable cost assumptions for one backtest walk."""

    spread_pips: float = 2.0
    slippage_pips: float = 2.0
    commission_usd_per_lot_side: float = 3.0
    lot_size: float = 1.0

    def __post_init__(self) -> None:
        if self.spread_pips < 0 or self.slippage_pips < 0:
            raise ValueError("spread_pips and slippage_pips must be >= 0")
        if self.commission_usd_per_lot_side < 0:
            raise ValueError("commission_usd_per_lot_side must be >= 0")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be > 0")

    def entry_fill(self, mid: float, side: str, pip_size: float) -> float:
        """Return an adverse market-entry fill from a mid (typically next-bar open)."""

        offset = (self.spread_pips + self.slippage_pips) * pip_size
        if side in _BUY_SIDES:
            return mid + offset
        if side in _SELL_SIDES:
            return mid - offset
        raise ValueError(f"unknown side {side!r}")

    def exit_fill(self, price: float, side: str, pip_size: float) -> float:
        """Return an adverse exit fill from a stop, target, or time-exit mid."""

        slip = self.slippage_pips * pip_size
        if side in _BUY_SIDES:
            return price - slip
        if side in _SELL_SIDES:
            return price + slip
        raise ValueError(f"unknown side {side!r}")

    def round_trip_commission_usd(self) -> float:
        """USD commission for one entry and one exit at ``lot_size``."""

        return 2.0 * self.commission_usd_per_lot_side * self.lot_size
