"""Average True Range (ATR) indicator calculation."""

from __future__ import annotations


def calculate_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float | None:
    """Calculate Average True Range."""
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return None

    true_ranges = []
    for i in range(1, len(highs)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(tr1, tr2, tr3))

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period
