"""Exponential Moving Average indicator calculations.

Provides pure functions for computing EMA series, detecting crossovers,
price-EMA touches/breaks, and EMA slope/direction analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class EMACrossoverType(Enum):
    GOLDEN_CROSS = "golden_cross"
    DEATH_CROSS = "death_cross"
    NO_CROSS = "no_cross"


class EMASlopeDirection(Enum):
    RISING = "rising"
    FALLING = "falling"
    FLAT = "flat"


@dataclass
class EMACrossover:
    """A fast/slow EMA crossover event at a specific timeframe."""

    crossover_type: EMACrossoverType
    fast_period: int
    slow_period: int
    fast_value: float
    slow_value: float
    timeframe: str


@dataclass
class EMAPriceTouch:
    """Price touching or breaking through an EMA level."""

    ema_period: int
    ema_value: float
    price: float
    direction: str
    distance_pips: float
    timeframe: str


@dataclass
class EMASlope:
    """EMA slope/direction at a specific timeframe."""

    period: int
    slope_direction: EMASlopeDirection
    current_value: float
    previous_value: float
    timeframe: str


def calculate_ema(prices: list[float], period: int) -> list[float | None]:
    """Calculate EMA for the full price series.

    Uses the standard formula:
        multiplier = 2 / (period + 1)
        EMA = (price * multiplier) + (previous_EMA * (1 - multiplier))

    The first ``period - 1`` entries are ``None`` (not enough data to
    seed the EMA).  Seeding uses the SMA of the first ``period`` values.

    Args:
        prices: Close prices, chronological (oldest first).
        period: EMA period (e.g. 9, 21, 50, 200).

    Returns:
        List of same length as ``prices``; leading Nones until enough data.
    """
    if period <= 0 or not prices:
        logger.debug("calculate_ema: invalid period=%d or empty prices", period)
        return [None] * len(prices)

    if len(prices) < period:
        logger.debug(
            "calculate_ema: insufficient data (need %d, got %d)", period, len(prices)
        )
        return [None] * len(prices)

    multiplier = 2.0 / (period + 1.0)
    result: list[float | None] = [None] * (period - 1)

    # Seed: SMA of first `period` values
    seed = sum(prices[:period]) / period
    result.append(seed)

    # Recursive EMA
    ema = seed
    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1.0 - multiplier))
        result.append(ema)

    return result


def calculate_ema_last(prices: list[float], period: int = 20) -> float | None:
    """Return only the last EMA value (convenient for evaluator)."""
    series = calculate_ema(prices, period)
    return series[-1] if series else None


def get_latest_ema(prices: list[float], period: int) -> float | None:
    """Convenience: return only the latest EMA value or None."""
    return calculate_ema_last(prices, period)


def detect_crossover(
    fast_ema: list[float | None],
    slow_ema: list[float | None],
    timeframe: str,
    fast_period: int,
    slow_period: int,
) -> EMACrossover | None:
    """Detect if a fast/slow EMA crossover occurred on the most recent bar."""
    if len(fast_ema) < 2 or len(slow_ema) < 2:
        return None

    fast_prev = fast_ema[-2]
    fast_now = fast_ema[-1]
    slow_prev = slow_ema[-2]
    slow_now = slow_ema[-1]

    if fast_prev is None or fast_now is None or slow_prev is None or slow_now is None:
        return None

    if fast_prev < slow_prev and fast_now > slow_now:
        return EMACrossover(
            crossover_type=EMACrossoverType.GOLDEN_CROSS,
            fast_period=fast_period,
            slow_period=slow_period,
            fast_value=fast_now,
            slow_value=slow_now,
            timeframe=timeframe,
        )
    if fast_prev > slow_prev and fast_now < slow_now:
        return EMACrossover(
            crossover_type=EMACrossoverType.DEATH_CROSS,
            fast_period=fast_period,
            slow_period=slow_period,
            fast_value=fast_now,
            slow_value=slow_now,
            timeframe=timeframe,
        )

    return None


def detect_price_touch(
    price: float,
    ema_values: list[float | None],
    ema_period: int,
    timeframe: str,
    threshold_pips: float,
    pip_size: float,
) -> EMAPriceTouch | None:
    """Detect if price is within threshold_pips of the EMA level."""
    if not ema_values or ema_values[-1] is None:
        return None

    ema_value = ema_values[-1]
    distance = abs(price - ema_value)
    distance_pips = distance / pip_size

    if distance_pips > threshold_pips:
        return None

    if price > ema_value:
        direction = "above"
    elif price < ema_value:
        direction = "below"
    else:
        direction = "touch"

    return EMAPriceTouch(
        ema_period=ema_period,
        ema_value=ema_value,
        price=price,
        direction=direction,
        distance_pips=distance_pips,
        timeframe=timeframe,
    )


def detect_price_cross(
    price: float,
    prev_price: float | None,
    ema_values: list[float | None],
    ema_period: int,
    timeframe: str,
    threshold_pips: float,
    pip_size: float,
) -> EMAPriceTouch | None:
    """Detect if price crossed through an EMA level."""
    if (
        not ema_values
        or ema_values[-1] is None
        or len(ema_values) < 2
        or ema_values[-2] is None
        or prev_price is None
    ):
        return None

    ema_now = ema_values[-1]
    ema_prev = ema_values[-2]

    price_was_below = prev_price < ema_prev
    price_now_above = price > ema_now

    price_was_above = prev_price > ema_prev
    price_now_below = price < ema_now

    distance_pips = abs(price - ema_now) / pip_size

    if price_was_below and price_now_above:
        return EMAPriceTouch(
            ema_period=ema_period,
            ema_value=ema_now,
            price=price,
            direction="cross_above",
            distance_pips=distance_pips,
            timeframe=timeframe,
        )
    if price_was_above and price_now_below:
        return EMAPriceTouch(
            ema_period=ema_period,
            ema_value=ema_now,
            price=price,
            direction="cross_below",
            distance_pips=distance_pips,
            timeframe=timeframe,
        )

    return None


def detect_slope(
    ema_values: list[float | None],
    ema_period: int,
    timeframe: str,
    lookback: int = 3,
) -> EMASlope | None:
    """Determine if the EMA series is rising, falling, or flat."""
    if not ema_values or len(ema_values) <= lookback:
        return None

    valid: list[float] = [v for v in ema_values if v is not None]
    if len(valid) < lookback + 1:
        return None

    current = valid[-1]
    previous = valid[-1 - lookback]

    delta = current - previous
    flat_threshold = abs(current) * 0.0001

    if abs(delta) <= flat_threshold:
        direction = EMASlopeDirection.FLAT
    elif delta > 0:
        direction = EMASlopeDirection.RISING
    else:
        direction = EMASlopeDirection.FALLING

    return EMASlope(
        period=ema_period,
        slope_direction=direction,
        current_value=current,
        previous_value=previous,
        timeframe=timeframe,
    )
