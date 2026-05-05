"""Average Directional Index (ADX) for trend strength detection."""

from __future__ import annotations


def calculate_adx_full(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[float, float, float] | None:
    """Calculate ADX, +DI, and -DI using Wilder's smoothing.

    Returns:
        (adx, plus_di, minus_di) or None if insufficient data.
    """
    n = len(highs)
    if n < 2 * period + 1 or len(lows) != n or len(closes) != n:
        return None

    tr_list: list[float] = []
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]
        plus_dm = max(high_diff, 0.0) if high_diff > low_diff else 0.0
        minus_dm = max(low_diff, 0.0) if low_diff > high_diff else 0.0
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr_list.append(max(tr1, tr2, tr3))
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < 2 * period:
        return None

    smoothed_tr = sum(tr_list[:period])
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])

    dx_list: list[float] = []
    last_plus_di = 0.0
    last_minus_di = 0.0

    for i in range(period, len(tr_list)):
        if i > period:
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]
            smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
            smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

        if smoothed_tr == 0:
            continue

        last_plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
        last_minus_di = 100.0 * smoothed_minus_dm / smoothed_tr
        di_sum = last_plus_di + last_minus_di
        dx_list.append(0.0 if di_sum == 0 else 100.0 * abs(last_plus_di - last_minus_di) / di_sum)

    if len(dx_list) < period:
        return None

    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period

    return adx, last_plus_di, last_minus_di


def calculate_adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """Calculate the ADX value using Wilder's smoothing.

    Args:
        highs: List of high prices (needs at least 2*period + 1 values).
        lows: List of low prices.
        closes: List of close prices.
        period: Smoothing period (default 14).

    Returns:
        Current ADX value (0-100), or None if insufficient data.
        ADX < 20-25 → weak trend / ranging market.
        ADX > 25 → trending market.
    """
    n = len(highs)
    if n < 2 * period + 1 or len(lows) != n or len(closes) != n:
        return None

    # Step 1: True Range, +DM, -DM
    tr_list: list[float] = []
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        plus_dm = max(high_diff, 0.0) if high_diff > low_diff else 0.0
        minus_dm = max(low_diff, 0.0) if low_diff > high_diff else 0.0

        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr_list.append(max(tr1, tr2, tr3))
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < 2 * period:
        return None

    # Step 2: Wilder smoothing for first period values
    smoothed_tr = sum(tr_list[:period])
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])

    dx_list: list[float] = []

    for i in range(period, len(tr_list)):
        if i == period:
            # Use initial sums
            pass
        else:
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]
            smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
            smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

        if smoothed_tr == 0:
            continue

        plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
        minus_di = 100.0 * smoothed_minus_dm / smoothed_tr

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_list.append(0.0)
        else:
            dx_list.append(100.0 * abs(plus_di - minus_di) / di_sum)

    if len(dx_list) < period:
        return None

    # Step 3: Smooth DX to get ADX
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period

    return adx
