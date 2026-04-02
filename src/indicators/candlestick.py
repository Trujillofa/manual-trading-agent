"""Candlestick pattern recognition for manual trading agent."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class PatternType(Enum):
    """Type of candlestick pattern."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class CandlePattern:
    """Detected candlestick pattern."""

    name: str
    pattern_type: PatternType
    confidence: float  # 0.0 to 1.0
    description: str


def get_candle_body(open_price: float, close: float) -> tuple[float, float, Literal["bullish", "bearish", "doji"]]:
    """Get candle body size and direction.

    Returns:
        Tuple of (body_size, body_mid, direction)
    """
    body = abs(close - open_price)
    mid = (open_price + close) / 2

    if close > open_price:
        direction: Literal["bullish", "bearish", "doji"] = "bullish"
    elif close < open_price:
        direction = "bearish"
    else:
        direction = "doji"

    return body, mid, direction


def is_hammer(
    open_price: float,
    high: float,
    low: float,
    close: float,
    body_ratio_threshold: float = 2.0,
    shadow_ratio_threshold: float = 0.3,
) -> CandlePattern | None:
    """Detect hammer pattern (bullish reversal).

    Characteristics:
    - Small body at top of trading range
    - Long lower shadow (at least 2x body size)
    - Little or no upper shadow
    """
    body, body_mid, direction = get_candle_body(open_price, close)

    if body == 0:
        return None

    lower_shadow = min(open_price, close) - low
    upper_shadow = high - max(open_price, close)

    # Hammer: long lower shadow, small body at top
    if lower_shadow >= body * body_ratio_threshold and upper_shadow <= body * shadow_ratio_threshold:
        # Determine location: with trend context, hammer at support is stronger
        confidence = 0.7 if direction == "bullish" else 0.5

        return CandlePattern(
            name="hammer",
            pattern_type=PatternType.BULLISH,
            confidence=confidence,
            description=f"Hammer: body={body:.5f}, lower_shadow={lower_shadow:.5f}",
        )

    return None


def is_shooting_star(
    open_price: float,
    high: float,
    low: float,
    close: float,
    body_ratio_threshold: float = 2.0,
    shadow_ratio_threshold: float = 0.3,
) -> CandlePattern | None:
    """Detect shooting star pattern (bearish reversal).

    Characteristics:
    - Small body at bottom of trading range
    - Long upper shadow (at least 2x body size)
    - Little or no lower shadow
    """
    body, body_mid, direction = get_candle_body(open_price, close)

    if body == 0:
        return None

    lower_shadow = min(open_price, close) - low
    upper_shadow = high - max(open_price, close)

    # Shooting star: long upper shadow, small body at bottom
    if upper_shadow >= body * body_ratio_threshold and lower_shadow <= body * shadow_ratio_threshold:
        confidence = 0.7 if direction == "bearish" else 0.5

        return CandlePattern(
            name="shooting_star",
            pattern_type=PatternType.BEARISH,
            confidence=confidence,
            description=f"Shooting star: body={body:.5f}, upper_shadow={upper_shadow:.5f}",
        )

    return None


def is_doji(
    open_price: float,
    high: float,
    low: float,
    close: float,
    doji_threshold: float = 0.1,
) -> CandlePattern | None:
    """Detect doji pattern (indecision).

    Characteristics:
    - Open and close are nearly equal
    - Body is very small relative to range
    """
    body = abs(close - open_price)
    total_range = high - low

    if total_range == 0:
        return None

    body_ratio = body / total_range if total_range > 0 else 0

    if body_ratio < doji_threshold:
        return CandlePattern(
            name="doji",
            pattern_type=PatternType.NEUTRAL,
            confidence=0.5,
            description=f"Doji: body_ratio={body_ratio:.3f}, range={total_range:.5f}",
        )

    return None


def is_bullish_engulfing(
    prev_open: float,
    prev_high: float,
    prev_low: float,
    prev_close: float,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> CandlePattern | None:
    """Detect bullish engulfing pattern.

    Characteristics:
    - Previous candle is bearish (red)
    - Current candle is bullish (green) and engulfs previous body
    """
    prev_body, _, prev_direction = get_candle_body(prev_open, prev_close)
    curr_body, _, curr_direction = get_candle_body(open_price, close)

    if prev_direction != "bearish" or curr_direction != "bullish":
        return None

    # Current bullish candle engulfs previous bearish candle
    if close > prev_open and open_price < prev_close:
        # Stronger if current body is larger
        strength = curr_body / prev_body if prev_body > 0 else 1.0
        confidence = min(0.9, 0.6 + strength * 0.1)

        return CandlePattern(
            name="bullish_engulfing",
            pattern_type=PatternType.BULLISH,
            confidence=confidence,
            description=f"Bullish Engulfing: prev_body={prev_body:.5f}, curr_body={curr_body:.5f}",
        )

    return None


def is_bearish_engulfing(
    prev_open: float,
    prev_high: float,
    prev_low: float,
    prev_close: float,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> CandlePattern | None:
    """Detect bearish engulfing pattern.

    Characteristics:
    - Previous candle is bullish (green)
    - Current candle is bearish (red) and engulfs previous body
    """
    prev_body, _, prev_direction = get_candle_body(prev_open, prev_close)
    curr_body, _, curr_direction = get_candle_body(open_price, close)

    if prev_direction != "bullish" or curr_direction != "bearish":
        return None

    # Current bearish candle engulfs previous bullish candle
    if open_price > prev_close and close < prev_open:
        strength = curr_body / prev_body if prev_body > 0 else 1.0
        confidence = min(0.9, 0.6 + strength * 0.1)

        return CandlePattern(
            name="bearish_engulfing",
            pattern_type=PatternType.BEARISH,
            confidence=confidence,
            description=f"Bearish Engulfing: prev_body={prev_body:.5f}, curr_body={curr_body:.5f}",
        )

    return None


def is_morning_star(
    open1: float, high1: float, low1: float, close1: float,
    open2: float, high2: float, low2: float, close2: float,
    open3: float, high3: float, low3: float, close3: float,
) -> CandlePattern | None:
    """Detect morning star pattern (bullish reversal).

    Characteristics:
    - First candle: large bearish
    - Second candle: small body (doji/spinning top) gap down
    - Third candle: large bullish, closes above midpoint of first
    """
    _, _, dir1 = get_candle_body(open1, close1)
    body2, _, dir2 = get_candle_body(open2, close2)
    _, _, dir3 = get_candle_body(open3, close3)

    # First bearish, second small/neutral, third bullish
    if dir1 != "bearish" or dir3 != "bullish":
        return None

    body1 = abs(close1 - open1)
    body3 = abs(close3 - open3)

    # Second candle should be small body
    if body2 > body1 * 0.5:
        return None

    # Third candle should close above midpoint of first
    midpoint1 = (open1 + close1) / 2
    if close3 < midpoint1:
        return None

    confidence = min(0.9, 0.7 + body3 / body1 * 0.1)

    return CandlePattern(
        name="morning_star",
        pattern_type=PatternType.BULLISH,
        confidence=confidence,
        description=f"Morning Star: 3-candle bullish reversal",
    )


def is_evening_star(
    open1: float, high1: float, low1: float, close1: float,
    open2: float, high2: float, low2: float, close2: float,
    open3: float, high3: float, low3: float, close3: float,
) -> CandlePattern | None:
    """Detect evening star pattern (bearish reversal).

    Characteristics:
    - First candle: large bullish
    - Second candle: small body (doji/spinning top) gap up
    - Third candle: large bearish, closes below midpoint of first
    """
    _, _, dir1 = get_candle_body(open1, close1)
    body2, _, dir2 = get_candle_body(open2, close2)
    _, _, dir3 = get_candle_body(open3, close3)

    # First bullish, second small/neutral, third bearish
    if dir1 != "bullish" or dir3 != "bearish":
        return None

    body1 = abs(close1 - open1)
    body3 = abs(close3 - open3)

    # Second candle should be small body
    if body2 > body1 * 0.5:
        return None

    # Third candle should close below midpoint of first
    midpoint1 = (open1 + close1) / 2
    if close3 > midpoint1:
        return None

    confidence = min(0.9, 0.7 + body3 / body1 * 0.1)

    return CandlePattern(
        name="evening_star",
        pattern_type=PatternType.BEARISH,
        confidence=confidence,
        description=f"Evening Star: 3-candle bearish reversal",
    )


def detect_patterns(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    lookback: int = 5,
) -> list[CandlePattern]:
    """Detect all candlestick patterns in the last N candles.

    Args:
        opens: List of open prices
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        lookback: Number of recent candles to check

    Returns:
        List of detected patterns (most recent first)
    """
    if len(closes) < 3:
        return []

    patterns: list[CandlePattern] = []

    # Check single-candle patterns on last candle
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]

    hammer = is_hammer(o, h, l, c)
    if hammer:
        patterns.append(hammer)

    shooting_star = is_shooting_star(o, h, l, c)
    if shooting_star:
        patterns.append(shooting_star)

    doji = is_doji(o, h, l, c)
    if doji:
        patterns.append(doji)

    # Check two-candle patterns (need previous candle)
    if len(closes) >= 2:
        po, ph, pl, pc = opens[-2], highs[-2], lows[-2], closes[-2]

        bullish_eng = is_bullish_engulfing(po, ph, pl, pc, o, h, l, c)
        if bullish_eng:
            patterns.append(bullish_eng)

        bearish_eng = is_bearish_engulfing(po, ph, pl, pc, o, h, l, c)
        if bearish_eng:
            patterns.append(bearish_eng)

    # Check three-candle patterns
    if len(closes) >= 3:
        o1, h1, l1, c1 = opens[-3], highs[-3], lows[-3], closes[-3]
        o2, h2, l2, c2 = opens[-2], highs[-2], lows[-2], closes[-2]
        o3, h3, l3, c3 = opens[-1], highs[-1], lows[-1], closes[-1]

        morning = is_morning_star(o1, h1, l1, c1, o2, h2, l2, c2, o3, h3, l3, c3)
        if morning:
            patterns.append(morning)

        evening = is_evening_star(o1, h1, l1, c1, o2, h2, l2, c2, o3, h3, l3, c3)
        if evening:
            patterns.append(evening)

    return patterns


def get_bullish_patterns(patterns: list[CandlePattern]) -> list[CandlePattern]:
    """Filter for bullish patterns only."""
    return [p for p in patterns if p.pattern_type == PatternType.BULLISH]


def get_bearish_patterns(patterns: list[CandlePattern]) -> list[CandlePattern]:
    """Filter for bearish patterns only."""
    return [p for p in patterns if p.pattern_type == PatternType.BEARISH]


def get_pattern_score(patterns: list[CandlePattern], pattern_type: PatternType) -> float:
    """Calculate combined confidence score for patterns of a given type.

    Returns:
        Combined score from 0.0 to 1.0
    """
    filtered = [p for p in patterns if p.pattern_type == pattern_type]
    if not filtered:
        return 0.0

    # Average confidence, capped at 1.0
    return min(1.0, sum(p.confidence for p in filtered) / len(filtered) + 0.2 * (len(filtered) - 1))