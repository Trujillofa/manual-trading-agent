"""Indicators module - technical indicator calculations."""

from __future__ import annotations

from src.indicators.atr import calculate_atr
from src.indicators.candlestick import (
    CandlePattern,
    PatternType,
    detect_patterns,
    get_bearish_patterns,
    get_bullish_patterns,
    get_pattern_score,
    is_bearish_engulfing,
    is_bullish_engulfing,
    is_doji,
    is_evening_star,
    is_hammer,
    is_morning_star,
    is_shooting_star,
)
from src.indicators.high_low import (
    highest_high,
    is_breakout_high,
    is_breakout_low,
    lowest_low,
    previous_rolling_highest_high,
    previous_rolling_lowest_low,
    rolling_highest_highs,
    rolling_lowest_lows,
)
from src.indicators.rsi import (
    Divergence,
    DivergenceType,
    calculate_rsi,
    calculate_rsi_series,
    detect_bearish_divergence,
    detect_bullish_divergence,
    detect_divergence,
    find_peaks,
    find_troughs,
)

__all__ = [
    # ATR
    "calculate_atr",
    # RSI
    "calculate_rsi",
    "calculate_rsi_series",
    "detect_divergence",
    "detect_bullish_divergence",
    "detect_bearish_divergence",
    "find_peaks",
    "find_troughs",
    "Divergence",
    "DivergenceType",
    # High/Low
    "highest_high",
    "lowest_low",
    "is_breakout_high",
    "is_breakout_low",
    "rolling_highest_highs",
    "rolling_lowest_lows",
    "previous_rolling_highest_high",
    "previous_rolling_lowest_low",
    # Candlestick
    "detect_patterns",
    "is_hammer",
    "is_shooting_star",
    "is_doji",
    "is_bullish_engulfing",
    "is_bearish_engulfing",
    "is_morning_star",
    "is_evening_star",
    "get_bullish_patterns",
    "get_bearish_patterns",
    "get_pattern_score",
    "CandlePattern",
    "PatternType",
]
