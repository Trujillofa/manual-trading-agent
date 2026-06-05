"""Average True Range (ATR) indicator calculation."""

from __future__ import annotations


def calculate_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float | None:
    """Calculate Average True Range (simple average of TRs).

    Requires at least `period + 1` bars to produce a full `period`-period ATR
    (period bars produce only period-1 true ranges). Callers that want a
    reliable current ATR(14) should pass a slice of length >= period + 1
    (see enhanced_engine comment and live scanner usage).
    """
    n = len(highs)
    if n < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None

    true_ranges: list[float] = []
    for i in range(1, n):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(tr1, tr2, tr3))

    # We have at least `period` TRs because of the n >= period+1 guard.
    return sum(true_ranges[-period:]) / period
