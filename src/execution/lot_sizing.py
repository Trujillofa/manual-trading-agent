"""Lot sizing calculator for forex trading."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LotResult:
    """Result of lot size calculation."""

    lots: float
    risk_usd: float
    sl_distance_pips: float
    accepted: bool
    rejection_reason: str | None = None


@dataclass
class SymbolInfo:
    """Forex symbol information."""

    symbol: str
    digits: int  # Price decimals (e.g., 4 for 1.0895, 2 for 115.50)
    pip_position: int  # Position of pip in price (usually -4 or -2)
    lot_step: float = 0.01
    min_lots: float = 0.01
    max_lots: float = 100.0


class LotSizer:
    """Calculate lot sizes for forex trades."""

    def __init__(self, default_lot: float = 3.0):
        self.default_lot: float = default_lot

    def calculate(
        self,
        entry_price: float,
        sl_price: float,
        symbol_info: SymbolInfo,
        risk_usd: float | None = None,
    ) -> LotResult:
        """Calculate lot size based on entry, stop loss, and risk.

        Args:
            entry_price: Entry price
            sl_price: Stop loss price
            symbol_info: Symbol metadata
            risk_usd: Target risk in USD (optional, uses default if not set)

        Returns:
            LotResult with calculated lot size
        """
        sl_distance = abs(entry_price - sl_price)
        sl_pips = self._price_to_pips(sl_distance, symbol_info)

        if sl_pips == 0:
            return LotResult(
                lots=0.0,
                risk_usd=0.0,
                sl_distance_pips=0.0,
                accepted=False,
                rejection_reason="SL distance is zero",
            )

        pip_value = self._pip_value_usd(symbol_info)

        if risk_usd is None:
            risk_usd = sl_pips * pip_value * self.default_lot

        required_lots = risk_usd / (sl_pips * pip_value) if pip_value > 0 else 0.0

        stepped_lots = round(required_lots / symbol_info.lot_step) * symbol_info.lot_step
        clamped_lots = max(symbol_info.min_lots, min(symbol_info.max_lots, stepped_lots))

        actual_risk = clamped_lots * sl_pips * pip_value

        if clamped_lots < symbol_info.min_lots:
            return LotResult(
                lots=clamped_lots,
                risk_usd=actual_risk,
                sl_distance_pips=sl_pips,
                accepted=False,
                rejection_reason=f"Lot size {clamped_lots} below minimum",
            )

        return LotResult(
            lots=clamped_lots,
            risk_usd=actual_risk,
            sl_distance_pips=sl_pips,
            accepted=True,
        )

    def _price_to_pips(self, price_distance: float, symbol_info: SymbolInfo) -> float:
        """Convert price distance to pips."""
        if symbol_info.digits in {4, 5}:
            return price_distance / (10.0**symbol_info.pip_position)
        return price_distance * 100

    def _pip_value_usd(self, symbol_info: SymbolInfo) -> float:
        """Get pip value in USD (simplified for standard lots)."""
        if symbol_info.digits == 2:
            return 1000.0
        return 10.0

    def can_open_with_margin(
        self,
        available_margin: float,
        symbol_info: SymbolInfo,
        lots: float,
        entry_price: float,
    ) -> bool:
        """Check if enough margin to open position."""
        _ = symbol_info
        margin_per_lot = entry_price * 100000 * 0.01
        required_margin = lots * margin_per_lot
        return available_margin >= required_margin
