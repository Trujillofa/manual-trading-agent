"""Risk management for trading positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Literal


class RiskLevel(Enum):
    """Risk evaluation levels."""

    ALLOWED = "allowed"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class RiskEvaluation:
    """Result of risk evaluation."""

    allowed: bool
    level: RiskLevel
    reason: str
    drawdown_pct: float = 0.0
    open_positions: int = 0


@dataclass
class Position:
    """Open position tracking."""

    symbol: str
    side: Literal["buy", "sell"]
    entry_price: float
    lots: float
    open_time: datetime
    unrealized_pnl: float = 0.0


@dataclass
class DailyStats:
    """Daily trading statistics."""

    date: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    max_drawdown: float = 0.0


class RiskManager:
    """Centralized risk gates for trading."""

    max_concurrent: int
    max_daily_loss: float
    max_drawdown: float

    def __init__(
        self,
        max_concurrent_positions: int = 1,
        max_daily_loss_usd: float = 500.0,
        max_drawdown_pct: float = 0.10,
    ) -> None:
        self.max_concurrent = max_concurrent_positions
        self.max_daily_loss = max_daily_loss_usd
        self.max_drawdown = max_drawdown_pct

        self._positions: dict[str, Position] = {}
        self._daily_stats: DailyStats = DailyStats(date=self._today())
        self._total_equity: float = 10000.0
        self._peak_equity: float = 10000.0

    async def pre_trade_check(self, symbol: str, side: str, lots: float) -> RiskEvaluation:
        """Evaluate if trade passes risk gates.

        Args:
            symbol: Trading pair
            side: buy or sell
            lots: Lot size

        Returns:
            RiskEvaluation with allowed/block status
        """
        _ = side, lots

        if len(self._positions) >= self.max_concurrent:
            return RiskEvaluation(
                allowed=False,
                level=RiskLevel.BLOCK,
                reason=f"Max positions ({self.max_concurrent}) reached",
                open_positions=len(self._positions),
            )

        if symbol in self._positions:
            return RiskEvaluation(
                allowed=False,
                level=RiskLevel.BLOCK,
                reason=f"Position already open for {symbol}",
                open_positions=len(self._positions),
            )

        if self._daily_stats.pnl <= -self.max_daily_loss:
            return RiskEvaluation(
                allowed=False,
                level=RiskLevel.BLOCK,
                reason=f"Daily loss limit reached (${-self._daily_stats.pnl:.2f})",
                drawdown_pct=self._drawdown_pct(),
                open_positions=len(self._positions),
            )

        drawdown = self._drawdown_pct()
        if drawdown >= self.max_drawdown:
            return RiskEvaluation(
                allowed=False,
                level=RiskLevel.BLOCK,
                reason=f"Max drawdown ({drawdown:.1%}) reached",
                drawdown_pct=drawdown,
                open_positions=len(self._positions),
            )

        return RiskEvaluation(
            allowed=True,
            level=RiskLevel.ALLOWED,
            reason="All risk checks passed",
            drawdown_pct=drawdown,
            open_positions=len(self._positions),
        )

    async def record_trade(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        entry_price: float,
        lots: float,
    ) -> Position:
        """Record a new trade position."""
        position = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            lots=lots,
            open_time=datetime.now(UTC),
        )
        self._positions[symbol] = position
        self._daily_stats.trades += 1
        return position

    async def close_position(
        self,
        symbol: str,
        exit_price: float,
        realized_pnl: float,
    ) -> Position | None:
        """Close a position and update stats."""
        if symbol not in self._positions:
            return None

        position = self._positions.pop(symbol)
        self._daily_stats.pnl += realized_pnl
        self._total_equity += realized_pnl

        if realized_pnl > 0:
            self._daily_stats.wins += 1
        else:
            self._daily_stats.losses += 1

        if self._total_equity > self._peak_equity:
            self._peak_equity = self._total_equity

        dd = self._drawdown_pct()
        if dd > self._daily_stats.max_drawdown:
            self._daily_stats.max_drawdown = dd

        today = self._today()
        if self._daily_stats.date != today:
            self._daily_stats = DailyStats(date=today)

        return position

    def update_equity(self, current_equity: float) -> None:
        """Update current equity (for unrealized PnL tracking)."""
        self._total_equity = current_equity
        if self._total_equity > self._peak_equity:
            self._peak_equity = self._total_equity

    def get_open_positions(self) -> dict[str, Position]:
        """Get all open positions."""
        return dict(self._positions)

    def get_daily_stats(self) -> DailyStats:
        """Get today's trading statistics."""
        return self._daily_stats

    def _drawdown_pct(self) -> float:
        """Calculate current drawdown percentage."""
        if self._peak_equity == 0:
            return 0.0
        return max(0.0, (self._peak_equity - self._total_equity) / self._peak_equity)

    @staticmethod
    def _today() -> str:
        """Get today's date string."""
        return datetime.now(UTC).strftime("%Y-%m-%d")
