"""Exponential Moving Average (EMA)."""

from __future__ import annotations


def calculate_ema(prices: list[float], period: int = 20) -> list[float | None]:
    """Calculate EMA series using standard smoothing.

    Returns list of same length as prices, with None for initial bars until seeded.
    """
    if not prices or period <= 0:
        return [None] * len(prices)

    n = len(prices)
    ema: list[float | None] = [None] * n
    if n == 0:
        return ema

    # Seed with SMA of first 'period' values (common approach)
    if n < period:
        # Not enough data; use simple average for what we have
        sma = sum(prices) / n
        for i in range(n):
            ema[i] = sma
        return ema

    sma = sum(prices[:period]) / period
    ema[period - 1] = sma
    multiplier = 2 / (period + 1)

    for i in range(period, n):
        ema[i] = (prices[i] * multiplier) + (ema[i - 1] * (1 - multiplier))  # type: ignore

    # Fill earlier with the first valid if desired, or leave None
    for i in range(period - 1):
        if ema[i] is None and i > 0:
            ema[i] = ema[i - 1]
    if ema[period - 1] is not None:
        for i in range(period - 1):
            ema[i] = ema[period - 1]  # backfill seed for simplicity in short slices

    return ema


def calculate_ema_last(prices: list[float], period: int = 20) -> float | None:
    """Return only the last EMA value (convenient for evaluator)."""
    series = calculate_ema(prices, period)
    return series[-1] if series else None
