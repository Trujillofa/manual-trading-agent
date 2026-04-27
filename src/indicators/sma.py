"""Simple Moving Average indicator."""

from __future__ import annotations


def calculate_sma(prices: list[float], period: int) -> float | None:
    """Return the SMA of the last `period` values, or None if insufficient data."""
    if period <= 0 or len(prices) < period:
        return None
    return sum(prices[-period:]) / period
