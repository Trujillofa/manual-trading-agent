"""Signal dataclasses for trading signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Literal


class SignalType(Enum):
    """Trading signal types."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class SignalConfidence(Enum):
    """Signal confidence levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Signal:
    """Trading signal with entry, stop loss, and take profit levels."""

    symbol: str
    side: Literal["buy", "sell"]
    signal_type: SignalType
    confidence: float  # 0.0 to 1.0
    confidence_level: SignalConfidence
    entry_price: float | None
    tp_price: float | None
    sl_price: float | None
    lot_size: float
    reason: str
    timestamp_utc: datetime
    indicators: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {self.side}")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"Confidence must be 0-1, got {self.confidence}")
        if self.timestamp_utc.tzinfo != UTC:
            raise ValueError("timestamp_utc must use UTC timezone")
