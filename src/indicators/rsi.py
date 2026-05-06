"""Relative Strength Index (RSI) indicator calculations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DivergenceType(Enum):
    """Type of RSI divergence."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


@dataclass
class Divergence:
    """RSI divergence detection result."""

    divergence_type: DivergenceType
    price_peak_index: int
    rsi_peak_index: int
    price_value: float
    rsi_value: float
    strength: float  # 0.0 to 1.0


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


def calculate_rsi_series(prices: list[float], period: int = 14) -> list[float | None]:
    """
    Calculate RSI for entire price series.

    Args:
        prices: List of closing prices
        period: RSI period (default 14)

    Returns:
        List of RSI values (None for insufficient data points)
    """
    if len(prices) < period + 1:
        return [None] * len(prices)

    result: list[float | None] = [None] * len(prices)

    for i in range(period, len(prices)):
        rsi = calculate_rsi(prices[: i + 1], period)
        result[i] = rsi if rsi is not None else None

    return result


def find_peaks(values: list[float], lookback: int = 5) -> list[tuple[int, float]]:
    """Find local peaks (highs) in a series.

    Args:
        values: List of values
        lookback: Number of bars on each side to confirm peak

    Returns:
        List of (index, value) tuples for peaks
    """
    peaks: list[tuple[int, float]] = []

    for i in range(lookback, len(values) - lookback):
        is_peak = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and j < len(values) and values[j] > values[i]:
                is_peak = False
                break

        if is_peak:
            peaks.append((i, values[i]))

    return peaks


def find_troughs(values: list[float], lookback: int = 5) -> list[tuple[int, float]]:
    """Find local troughs (lows) in a series.

    Args:
        values: List of values
        lookback: Number of bars on each side to confirm trough

    Returns:
        List of (index, value) tuples for troughs
    """
    troughs: list[tuple[int, float]] = []

    for i in range(lookback, len(values) - lookback):
        is_trough = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and j < len(values) and values[j] < values[i]:
                is_trough = False
                break

        if is_trough:
            troughs.append((i, values[i]))

    return troughs


def detect_bullish_divergence(
    prices: list[float],
    rsi_values: list[float | None],
    lookback: int = 5,
    min_separation: int = 5,
) -> Divergence | None:
    """Detect bullish RSI divergence.

    Bullish divergence occurs when:
    - Price makes a lower low
    - RSI makes a higher low
    - This suggests weakening downward momentum

    Args:
        prices: List of closing prices
        rsi_values: List of RSI values (can contain None)
        lookback: Bars to check for peaks/troughs
        min_separation: Minimum bars between troughs

    Returns:
        Divergence object if detected, None otherwise
    """
    if len(prices) < lookback * 2 + min_separation:
        return None

    # Find price troughs
    price_troughs = find_troughs(prices, lookback)

    # Find RSI troughs (using valid RSI values only)
    valid_rsi = [(i, v) for i, v in enumerate(rsi_values) if v is not None]
    if len(valid_rsi) < lookback * 2 + min_separation:
        return None

    rsi_series = [v if v is not None else 50.0 for v in rsi_values]
    rsi_troughs = find_troughs(rsi_series, lookback)

    # Need at least 2 troughs to detect divergence
    if len(price_troughs) < 2 or len(rsi_troughs) < 2:
        return None

    # Check for bullish divergence: price LL, RSI HL
    for i in range(1, len(price_troughs)):
        recent_price_idx, recent_price_val = price_troughs[i]
        prev_price_idx, prev_price_val = price_troughs[i - 1]

        # Must have sufficient separation
        if recent_price_idx - prev_price_idx < min_separation:
            continue

        # Price made lower low
        if recent_price_val >= prev_price_val:
            continue

        # Find corresponding RSI troughs
        recent_rsi_troughs = [(idx, val) for idx, val in rsi_troughs if idx <= recent_price_idx]
        prev_rsi_troughs = [(idx, val) for idx, val in rsi_troughs if idx <= prev_price_idx]

        if not recent_rsi_troughs or not prev_rsi_troughs:
            continue

        recent_rsi_idx, recent_rsi_val = recent_rsi_troughs[-1]
        prev_rsi_idx, prev_rsi_val = prev_rsi_troughs[-1]

        # RSI made higher low (divergence confirmed)
        if recent_rsi_val > prev_rsi_val:
            strength = min(1.0, (prev_rsi_val - recent_rsi_val) / 30 + 0.5)

            return Divergence(
                divergence_type=DivergenceType.BULLISH,
                price_peak_index=recent_price_idx,
                rsi_peak_index=recent_rsi_idx,
                price_value=recent_price_val,
                rsi_value=recent_rsi_val,
                strength=strength,
            )

    return None


def detect_bearish_divergence(
    prices: list[float],
    rsi_values: list[float | None],
    lookback: int = 5,
    min_separation: int = 5,
) -> Divergence | None:
    """Detect bearish RSI divergence.

    Bearish divergence occurs when:
    - Price makes a higher high
    - RSI makes a lower high
    - This suggests weakening upward momentum

    Args:
        prices: List of closing prices
        rsi_values: List of RSI values (can contain None)
        lookback: Bars to check for peaks
        min_separation: Minimum bars between peaks

    Returns:
        Divergence object if detected, None otherwise
    """
    if len(prices) < lookback * 2 + min_separation:
        return None

    # Find price peaks
    price_peaks = find_peaks(prices, lookback)

    # Find RSI peaks
    valid_rsi = [(i, v) for i, v in enumerate(rsi_values) if v is not None]
    if len(valid_rsi) < lookback * 2 + min_separation:
        return None

    rsi_series = [v if v is not None else 50.0 for v in rsi_values]
    rsi_peaks = find_peaks(rsi_series, lookback)

    # Need at least 2 peaks to detect divergence
    if len(price_peaks) < 2 or len(rsi_peaks) < 2:
        return None

    # Check for bearish divergence: price HH, RSI LH
    for i in range(1, len(price_peaks)):
        recent_price_idx, recent_price_val = price_peaks[i]
        prev_price_idx, prev_price_val = price_peaks[i - 1]

        # Must have sufficient separation
        if recent_price_idx - prev_price_idx < min_separation:
            continue

        # Price made higher high
        if recent_price_val <= prev_price_val:
            continue

        # Find corresponding RSI peaks
        recent_rsi_peaks = [(idx, val) for idx, val in rsi_peaks if idx <= recent_price_idx]
        prev_rsi_peaks = [(idx, val) for idx, val in rsi_peaks if idx <= prev_price_idx]

        if not recent_rsi_peaks or not prev_rsi_peaks:
            continue

        recent_rsi_idx, recent_rsi_val = recent_rsi_peaks[-1]
        prev_rsi_idx, prev_rsi_val = prev_rsi_peaks[-1]

        # RSI made lower high (divergence confirmed)
        if recent_rsi_val < prev_rsi_val:
            strength = min(1.0, (prev_rsi_val - recent_rsi_val) / 30 + 0.5)

            return Divergence(
                divergence_type=DivergenceType.BEARISH,
                price_peak_index=recent_price_idx,
                rsi_peak_index=recent_rsi_idx,
                price_value=recent_price_val,
                rsi_value=recent_rsi_val,
                strength=strength,
            )

    return None


def detect_divergence(
    prices: list[float],
    rsi_values: list[float | None],
    lookback: int = 5,
    min_separation: int = 5,
) -> Divergence | None:
    """Detect RSI divergence (bullish or bearish).

    Args:
        prices: List of closing prices
        rsi_values: List of RSI values
        lookback: Bars to check for peaks/troughs
        min_separation: Minimum bars between peaks/troughs

    Returns:
        Most recent divergence detected, or None
    """
    # Check bearish first (typically more actionable for reversals)
    bearish = detect_bearish_divergence(prices, rsi_values, lookback, min_separation)
    bullish = detect_bullish_divergence(prices, rsi_values, lookback, min_separation)

    # Return the more recent one
    if bearish and bullish:
        if bearish.price_peak_index > bullish.price_peak_index:
            return bearish
        return bullish

    return bearish or bullish


def calculate_rsi_ma_series(
    rsi_values: list[float | None], ma_period: int = 5
) -> list[float | None]:
    """Calculate SMA over an RSI series.

    Returns a list aligned with rsi_values where each entry is the simple
    moving average of the last ``ma_period`` non-None RSI values.  Bars with
    insufficient lookback are None.

    This is the "RSI-MA" indicator: the trend of RSI itself.  When RSI
    crosses above its own MA, momentum is accelerating upward (curl).
    When RSI crosses below its MA, momentum is decelerating / turning.

    Args:
        rsi_values: List of RSI readings (may contain None).
        ma_period: Lookback for the SMA (default 5).

    Returns:
        List of smoothed RSI values (None where insufficient data).
    """
    result: list[float | None] = [None] * len(rsi_values)
    window: list[float] = []

    for i, v in enumerate(rsi_values):
        if v is None:
            continue
        window.append(float(v))
        if len(window) > ma_period:
            window = window[-ma_period:]
        if len(window) == ma_period:
            result[i] = sum(window) / ma_period

    return result


def detect_rsi_curl(
    rsi_values: list[float | None],
    rsi_ma_values: list[float | None],
    direction: str,
    lookback: int = 3,
) -> bool:
    """Detect RSI momentum curl (RSI crossing back through its MA).

    For BUY signals: RSI was below its MA and is now crossing above
    (momentum turning up after oversold alignment).

    For SELL signals: RSI was above its MA and is now crossing below
    (momentum turning down after overbought alignment).

    Args:
        rsi_values: Raw RSI series.
        rsi_ma_values: Smoothed RSI series (same length).
        direction: "buy" or "sell".
        lookback: How many bars back to look for the cross (default 3).

    Returns:
        True if a curl is detected within the lookback window.
    """
    if len(rsi_values) < lookback + 1 or len(rsi_ma_values) < lookback + 1:
        return False

    # Walk backwards looking for a cross
    for offset in range(lookback):
        idx = len(rsi_values) - 1 - offset
        if idx < 1:
            break
        rsi_now = rsi_values[idx]
        rsi_prev = rsi_values[idx - 1]
        ma_now = rsi_ma_values[idx]
        ma_prev = rsi_ma_values[idx - 1]

        if any(v is None for v in (rsi_now, rsi_prev, ma_now, ma_prev)):
            continue

        if direction == "buy":
            # RSI crossed above its MA (prev: RSI < MA, now: RSI > MA)
            if rsi_prev < ma_prev and rsi_now > ma_now:
                return True
        elif direction == "sell":
            # RSI crossed below its MA (prev: RSI > MA, now: RSI < MA)
            if rsi_prev > ma_prev and rsi_now < ma_now:
                return True

    return False


def detect_rsi_slope_change(
    rsi_ma_values: list[float | None],
    direction: str,
    lookback: int = 3,
) -> bool:
    """Detect RSI-MA slope inflection — free-fall ending, curl starting.

    For BUY: RSI-MA was falling and is now flattening or turning up.
    For SELL: RSI-MA was rising and is now flattening or turning down.

    This catches the inflection point BEFORE the laggy cross,
    giving earlier entries than the curl variant.

    Args:
        rsi_ma_values: Smoothed RSI series.
        direction: "buy" or "sell".
        lookback: Bars for the early vs recent slope comparison (default 3).

    Returns:
        True if slope is inflecting favorably.
    """
    if len(rsi_ma_values) < lookback + 2:
        return False

    # Early window (older bars, pre-inflection)
    early = [v for v in rsi_ma_values[-(lookback + 2):-lookback] if v is not None]
    # Recent window (newer bars, post-inflection)
    recent = [v for v in rsi_ma_values[-lookback:] if v is not None]

    if len(early) < 2 or len(recent) < 2:
        return False

    early_slope = early[-1] - early[0]
    recent_slope = recent[-1] - recent[0]

    if direction == "buy":
        # Was falling (early negative), now less negative or positive
        return early_slope < 0 and recent_slope > early_slope
    # SELL: was rising, now less positive or negative
    return early_slope > 0 and recent_slope < early_slope


def rsi_ma_distance(
    rsi_value: float,
    rsi_ma_value: float,
    direction: str,
    min_distance: float = 3.0,
    max_distance: float = 15.0,
) -> bool:
    """Check if RSI is sufficiently far from its MA in the right direction.

    For BUY (oversold): RSI should be BELOW its MA (fresh oversold).
    For SELL (overbought): RSI should be ABOVE its MA (fresh overbought).

    The distance must be between min_distance and max_distance:
    - Below min_distance: noise, no real momentum
    - Above max_distance: panic/extreme, wait for it to narrow

    Args:
        rsi_value: Current RSI reading.
        rsi_ma_value: Current RSI-MA reading.
        direction: "buy" or "sell".
        min_distance: Minimum RSI points away from MA (default 3).
        max_distance: Maximum RSI points away from MA (default 15).

    Returns:
        True if RSI is at the right distance from its MA.
    """
    diff = rsi_value - rsi_ma_value  # positive = RSI above MA

    if direction == "buy":
        # RSI below MA (negative diff) = fresh oversold momentum
        distance = -diff
        return min_distance <= distance <= max_distance
    # SELL: RSI above MA (positive diff) = fresh overbought momentum
    return min_distance <= diff <= max_distance
