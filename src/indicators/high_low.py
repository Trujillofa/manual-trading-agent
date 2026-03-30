"""Rolling highest high / lowest low calculations for breakout checks."""

from __future__ import annotations

from collections import deque


def _validate_lookback(lookback: int) -> None:
    if lookback <= 0:
        raise ValueError(f"lookback must be greater than 0, got {lookback}")


def _validate_threshold_pct(threshold_pct: float) -> None:
    if threshold_pct < 0:
        raise ValueError(f"threshold_pct must be >= 0, got {threshold_pct}")


def highest_high(highs: list[float], lookback: int) -> float | None:
    """Return the highest high over the lookback period."""
    _validate_lookback(lookback)
    if len(highs) < lookback:
        return None
    return max(highs[-lookback:])


def lowest_low(lows: list[float], lookback: int) -> float | None:
    """Return the lowest low over the lookback period."""
    _validate_lookback(lookback)
    if len(lows) < lookback:
        return None
    return min(lows[-lookback:])


def is_breakout_high(close: float, highest_high: float, threshold_pct: float = 0.0) -> bool:
    """Check if close breaks above highest high by optional threshold."""
    _validate_threshold_pct(threshold_pct)
    trigger = highest_high * (1.0 + threshold_pct)
    return close > trigger


def is_breakout_low(close: float, lowest_low: float, threshold_pct: float = 0.0) -> bool:
    """Check if close breaks below lowest low by optional threshold."""
    _validate_threshold_pct(threshold_pct)
    trigger = lowest_low * (1.0 - threshold_pct)
    return close < trigger


def rolling_highest_highs(highs: list[float], lookback: int) -> list[float | None]:
    """Return list of highest highs for each bar (None for first lookback-1 bars)."""
    _validate_lookback(lookback)
    window: deque[int] = deque()
    result: list[float | None] = []

    for index, value in enumerate(highs):
        while window and window[0] <= index - lookback:
            _ = window.popleft()

        while window and highs[window[-1]] <= value:
            _ = window.pop()

        window.append(index)

        if index < lookback - 1:
            result.append(None)
        else:
            result.append(highs[window[0]])

    return result


def rolling_lowest_lows(lows: list[float], lookback: int) -> list[float | None]:
    """Return list of lowest lows for each bar (None for first lookback-1 bars)."""
    _validate_lookback(lookback)
    window: deque[int] = deque()
    result: list[float | None] = []

    for index, value in enumerate(lows):
        while window and window[0] <= index - lookback:
            _ = window.popleft()

        while window and lows[window[-1]] >= value:
            _ = window.pop()

        window.append(index)

        if index < lookback - 1:
            result.append(None)
        else:
            result.append(lows[window[0]])

    return result


def previous_rolling_highest_high(highs: list[float], lookback: int, index: int) -> float | None:
    """Return highest high of bars [index-lookback, index-1] (excludes current bar)."""
    if index < lookback:
        return None
    start = index - lookback
    end = index
    if start < 0 or end > len(highs):
        return None
    return max(highs[start:end])


def previous_rolling_lowest_low(lows: list[float], lookback: int, index: int) -> float | None:
    """Return lowest low of bars [index-lookback, index-1] (excludes current bar)."""
    if index < lookback:
        return None
    start = index - lookback
    end = index
    if start < 0 or end > len(lows):
        return None
    return min(lows[start:end])
