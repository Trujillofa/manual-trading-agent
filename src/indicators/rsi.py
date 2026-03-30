"""Relative Strength Index (RSI) indicator calculations."""

from __future__ import annotations


def calculate_rsi(prices: list[float], period: int = 14) -> float | None:
    """
    Calculate RSI (Relative Strength Index) using Wilder's smoothing.

    Args:
        prices: List of closing prices (must have at least period+1 prices)
        period: RSI period (default 14)

    Returns:
        RSI value between 0-100, or None if insufficient data
    """
    if len(prices) < period + 1:
        return None

    deltas = [prices[index] - prices[index - 1] for index in range(1, len(prices))]
    gains = [delta if delta > 0 else 0.0 for delta in deltas]
    losses = [-delta if delta < 0 else 0.0 for delta in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for index in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period

    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
