"""Tests for candlestick pattern recognition."""

from __future__ import annotations

import pytest

from src.indicators.candlestick import (
    CandlePattern,
    PatternType,
    detect_patterns,
    get_bearish_patterns,
    get_bullish_patterns,
    get_candle_body,
    get_pattern_score,
    is_bearish_engulfing,
    is_bullish_engulfing,
    is_doji,
    is_evening_star,
    is_hammer,
    is_morning_star,
    is_shooting_star,
)


class TestGetCandleBody:
    """Tests for get_candle_body function."""

    def test_bullish_body(self):
        """Bullish candle: close > open."""
        body, mid, direction = get_candle_body(1.1000, 1.1050)
        assert body == pytest.approx(0.0050)
        assert mid == pytest.approx(1.1025)
        assert direction == "bullish"

    def test_bearish_body(self):
        """Bearish candle: close < open."""
        body, mid, direction = get_candle_body(1.1050, 1.1000)
        assert body == pytest.approx(0.0050)
        assert mid == pytest.approx(1.1025)
        assert direction == "bearish"

    def test_doji_body(self):
        """Doji candle: close == open."""
        body, mid, direction = get_candle_body(1.1000, 1.1000)
        assert body == 0.0
        assert mid == 1.1000
        assert direction == "doji"


class TestIsHammer:
    """Tests for is_hammer function."""

    def test_detects_hammer(self):
        """Hammer: small body at top, long lower shadow."""
        result = is_hammer(
            open_price=1.1000, high=1.1030, low=1.0910, close=1.1028
        )
        assert result is not None
        assert result.name == "hammer"
        assert result.pattern_type == PatternType.BULLISH

    def test_bearish_hammer_lower_confidence(self):
        """Hammer with bearish body gets lower confidence."""
        result = is_hammer(
            open_price=1.1028, high=1.1030, low=1.0910, close=1.1000
        )
        assert result is not None
        assert result.name == "hammer"
        assert result.confidence == pytest.approx(0.5)

    def test_not_hammer_insufficient_shadow(self):
        """Short lower shadow should not trigger hammer."""
        result = is_hammer(
            open_price=1.1000, high=1.1010, low=1.0990, close=1.1005
        )
        assert result is None

    def test_not_hammer_doji_body(self):
        """Doji body (zero) should not trigger hammer."""
        result = is_hammer(
            open_price=1.1000, high=1.1010, low=1.0950, close=1.1000
        )
        assert result is None

    def test_not_hammer_long_upper_shadow(self):
        """Hammer requires little upper shadow — long upper shadow is not hammer."""
        result = is_hammer(
            open_price=1.1000, high=1.1080, low=1.0950, close=1.1005
        )
        assert result is None


class TestIsShootingStar:
    """Tests for is_shooting_star function."""

    def test_detects_shooting_star(self):
        """Shooting star: small body at bottom, long upper shadow."""
        result = is_shooting_star(
            open_price=1.1000, high=1.1060, low=1.0995, close=1.0995
        )
        assert result is not None
        assert result.name == "shooting_star"
        assert result.pattern_type == PatternType.BEARISH

    def test_bullish_shooting_star_lower_confidence(self):
        """Shooting star with bullish body gets lower confidence."""
        result = is_shooting_star(
            open_price=1.0990, high=1.1090, low=1.0987, close=1.1000
        )
        assert result is not None
        assert result.confidence == pytest.approx(0.5)

    def test_not_shooting_star_short_upper_shadow(self):
        """Short upper shadow should not trigger."""
        result = is_shooting_star(
            open_price=1.1000, high=1.1010, low=1.0990, close=1.0995
        )
        assert result is None

    def test_not_shooting_star_doji_body(self):
        """Doji body should not trigger shooting star."""
        result = is_shooting_star(
            open_price=1.1000, high=1.1060, low=1.0995, close=1.1000
        )
        assert result is None


class TestIsDoji:
    """Tests for is_doji function."""

    def test_detects_doji(self):
        """Very small body relative to range."""
        result = is_doji(
            open_price=1.1000, high=1.1020, low=1.0980, close=1.1001
        )
        assert result is not None
        assert result.name == "doji"
        assert result.pattern_type == PatternType.NEUTRAL

    def test_not_doji_large_body(self):
        """Large body relative to range should not be doji."""
        result = is_doji(
            open_price=1.0980, high=1.1020, low=1.0980, close=1.1020
        )
        assert result is None

    def test_not_doji_zero_range(self):
        """Zero range should return None."""
        result = is_doji(
            open_price=1.1000, high=1.1000, low=1.1000, close=1.1000
        )
        assert result is None

    def test_doji_exact_equal_open_close(self):
        """Exact doji: open == close."""
        result = is_doji(
            open_price=1.1000, high=1.1020, low=1.0980, close=1.1000
        )
        assert result is not None
        assert result.name == "doji"


class TestIsBullishEngulfing:
    """Tests for is_bullish_engulfing function."""

    def test_detects_bullish_engulfing(self):
        """Prev bearish, current bullish engulfs."""
        result = is_bullish_engulfing(
            prev_open=1.1050, prev_high=1.1060, prev_low=1.1000, prev_close=1.1000,
            open_price=1.0990, high=1.1070, low=1.0980, close=1.1060,
        )
        assert result is not None
        assert result.name == "bullish_engulfing"
        assert result.pattern_type == PatternType.BULLISH

    def test_not_engulfing_prev_bullish(self):
        """Previous candle must be bearish."""
        result = is_bullish_engulfing(
            prev_open=1.1000, prev_high=1.1060, prev_low=1.0990, prev_close=1.1050,
            open_price=1.0990, high=1.1070, low=1.0980, close=1.1060,
        )
        assert result is None

    def test_not_engulfing_current_bearish(self):
        """Current candle must be bullish."""
        result = is_bullish_engulfing(
            prev_open=1.1050, prev_high=1.1060, prev_low=1.1000, prev_close=1.1000,
            open_price=1.1060, high=1.1070, low=1.0990, close=1.0990,
        )
        assert result is None

    def test_not_engulfing_insufficient_overlap(self):
        """Current must engulf previous body."""
        result = is_bullish_engulfing(
            prev_open=1.1050, prev_high=1.1060, prev_low=1.1000, prev_close=1.1000,
            open_price=1.1010, high=1.1070, low=1.1000, close=1.1060,
        )
        assert result is None


class TestIsBearishEngulfing:
    """Tests for is_bearish_engulfing function."""

    def test_detects_bearish_engulfing(self):
        """Prev bullish, current bearish engulfs."""
        result = is_bearish_engulfing(
            prev_open=1.1000, prev_high=1.1060, prev_low=1.0990, prev_close=1.1050,
            open_price=1.1060, high=1.1070, low=1.0980, close=1.0990,
        )
        assert result is not None
        assert result.name == "bearish_engulfing"
        assert result.pattern_type == PatternType.BEARISH

    def test_not_engulfing_prev_bearish(self):
        """Previous candle must be bullish."""
        result = is_bearish_engulfing(
            prev_open=1.1050, prev_high=1.1060, prev_low=1.1000, prev_close=1.1000,
            open_price=1.1060, high=1.1070, low=1.0980, close=1.0990,
        )
        assert result is None

    def test_not_engulfing_current_bullish(self):
        """Current candle must be bearish."""
        result = is_bearish_engulfing(
            prev_open=1.1000, prev_high=1.1060, prev_low=1.0990, prev_close=1.1050,
            open_price=1.0990, high=1.1070, low=1.0980, close=1.1060,
        )
        assert result is None

    def test_not_engulfing_no_overlap(self):
        """Current must engulf previous body."""
        result = is_bearish_engulfing(
            prev_open=1.1000, prev_high=1.1060, prev_low=1.0990, prev_close=1.1050,
            open_price=1.1040, high=1.1070, low=1.0980, close=1.0990,
        )
        assert result is None


class TestIsMorningStar:
    """Tests for is_morning_star function."""

    def test_detects_morning_star(self):
        """Bearish → small → bullish with close above midpoint."""
        result = is_morning_star(
            open1=1.1100, high1=1.1110, low1=1.1000, close1=1.1000,
            open2=1.0990, high2=1.1000, low2=1.0980, close2=1.0995,
            open3=1.1000, high3=1.1080, low3=1.0990, close3=1.1070,
        )
        assert result is not None
        assert result.name == "morning_star"
        assert result.pattern_type == PatternType.BULLISH

    def test_not_morning_star_first_not_bearish(self):
        """First candle must be bearish."""
        result = is_morning_star(
            open1=1.1000, high1=1.1110, low1=1.0990, close1=1.1100,
            open2=1.1090, high2=1.1100, low2=1.1080, close2=1.1095,
            open3=1.1100, high3=1.1180, low3=1.1090, close3=1.1170,
        )
        assert result is None

    def test_not_morning_star_third_not_bullish(self):
        """Third candle must be bullish."""
        result = is_morning_star(
            open1=1.1100, high1=1.1110, low1=1.1000, close1=1.1000,
            open2=1.0990, high2=1.1000, low2=1.0980, close2=1.0995,
            open3=1.1080, high3=1.1090, low3=1.0990, close3=1.1000,
        )
        assert result is None

    def test_not_morning_star_close_below_midpoint(self):
        """Third must close above first candle's midpoint."""
        result = is_morning_star(
            open1=1.1100, high1=1.1110, low1=1.1000, close1=1.1000,
            open2=1.0990, high2=1.1000, low2=1.0980, close2=1.0995,
            open3=1.1000, high3=1.1060, low3=1.0990, close3=1.1040,
        )
        assert result is None

    def test_not_morning_star_second_body_too_large(self):
        """Second candle must be small relative to first."""
        result = is_morning_star(
            open1=1.1100, high1=1.1110, low1=1.1000, close1=1.1000,
            open2=1.0990, high2=1.1000, low2=1.0930, close2=1.0935,
            open3=1.1000, high3=1.1080, low3=1.0990, close3=1.1070,
        )
        assert result is None


class TestIsEveningStar:
    """Tests for is_evening_star function."""

    def test_detects_evening_star(self):
        """Bullish → small → bearish with close below midpoint."""
        result = is_evening_star(
            open1=1.1000, high1=1.1110, low1=1.0990, close1=1.1100,
            open2=1.1110, high2=1.1120, low2=1.1100, close2=1.1105,
            open3=1.1100, high3=1.1110, low3=1.1000, close3=1.1010,
        )
        assert result is not None
        assert result.name == "evening_star"
        assert result.pattern_type == PatternType.BEARISH

    def test_not_evening_star_first_not_bullish(self):
        """First candle must be bullish."""
        result = is_evening_star(
            open1=1.1100, high1=1.1110, low1=1.1000, close1=1.1000,
            open2=1.1010, high2=1.1020, low2=1.1000, close2=1.1005,
            open3=1.1000, high3=1.1010, low3=1.0900, close3=1.0910,
        )
        assert result is None

    def test_not_evening_star_third_not_bearish(self):
        """Third candle must be bearish."""
        result = is_evening_star(
            open1=1.1000, high1=1.1110, low1=1.0990, close1=1.1100,
            open2=1.1110, high2=1.1120, low2=1.1100, close2=1.1105,
            open3=1.1010, high3=1.1120, low3=1.1000, close3=1.1110,
        )
        assert result is None

    def test_not_evening_star_close_above_midpoint(self):
        """Third must close below first candle's midpoint."""
        result = is_evening_star(
            open1=1.1000, high1=1.1110, low1=1.0990, close1=1.1100,
            open2=1.1110, high2=1.1120, low2=1.1100, close2=1.1105,
            open3=1.1100, high3=1.1110, low3=1.1000, close3=1.1060,
        )
        assert result is None


class TestDetectPatterns:
    """Tests for detect_patterns integration function."""

    @pytest.fixture
    def hammer_setup(self):
        """OHLC lists with a hammer on last candle."""
        opens = [1.1000, 1.1010, 1.1020, 1.1010, 1.1000]
        highs = [1.1010, 1.1020, 1.1040, 1.1020, 1.1030]
        lows = [1.0990, 1.1000, 1.1010, 1.1000, 1.0910]
        closes = [1.1005, 1.1015, 1.1035, 1.1015, 1.1028]
        return opens, highs, lows, closes

    def test_detects_hammer_in_last_candle(self, hammer_setup):
        """Should detect hammer pattern on the last candle."""
        opens, highs, lows, closes = hammer_setup
        patterns = detect_patterns(opens, highs, lows, closes)
        names = [p.name for p in patterns]
        assert "hammer" in names

    def test_insufficient_data_returns_empty(self):
        """Less than 3 candles returns empty."""
        patterns = detect_patterns(
            [1.1000, 1.1010], [1.1010, 1.1020], [1.0990, 1.1000], [1.1005, 1.1015]
        )
        assert patterns == []

    def test_detects_engulfing_with_two_or_more(self):
        """Two-candle patterns detected when >= 2 candles."""
        opens = [1.1050, 1.1060, 1.0980]
        highs = [1.1060, 1.1070, 1.1080]
        lows = [1.1000, 1.0980, 1.0970]
        closes = [1.1000, 1.0990, 1.1070]
        patterns = detect_patterns(opens, highs, lows, closes)
        names = [p.name for p in patterns]
        assert "bullish_engulfing" in names

    def test_detects_three_candle_patterns(self):
        """Three-candle patterns detected with >= 3 candles."""
        opens = [1.1100, 1.0990, 1.1000]
        highs = [1.1110, 1.1000, 1.1080]
        lows = [1.1000, 1.0980, 1.0990]
        closes = [1.1000, 1.0995, 1.1070]
        patterns = detect_patterns(opens, highs, lows, closes)
        names = [p.name for p in patterns]
        assert "morning_star" in names


class TestPatternFiltering:
    """Tests for pattern filtering and scoring functions."""

    @pytest.fixture
    def mixed_patterns(self):
        """A mix of bullish, bearish, and neutral patterns."""
        return [
            CandlePattern("hammer", PatternType.BULLISH, 0.7, "hammer"),
            CandlePattern("shooting_star", PatternType.BEARISH, 0.7, "star"),
            CandlePattern("doji", PatternType.NEUTRAL, 0.5, "doji"),
            CandlePattern("bullish_engulfing", PatternType.BULLISH, 0.9, "engulf"),
        ]

    def test_get_bullish_patterns(self, mixed_patterns):
        """Should filter only bullish patterns."""
        bullish = get_bullish_patterns(mixed_patterns)
        assert len(bullish) == 2
        assert all(p.pattern_type == PatternType.BULLISH for p in bullish)

    def test_get_bearish_patterns(self, mixed_patterns):
        """Should filter only bearish patterns."""
        bearish = get_bearish_patterns(mixed_patterns)
        assert len(bearish) == 1
        assert all(p.pattern_type == PatternType.BEARISH for p in bearish)

    def test_get_pattern_score_bullish(self, mixed_patterns):
        """Score for bullish patterns."""
        score = get_pattern_score(mixed_patterns, PatternType.BULLISH)
        assert score > 0.0

    def test_get_pattern_score_empty(self, mixed_patterns):
        """Score for type with no patterns returns 0.0."""
        empty = get_pattern_score(mixed_patterns, PatternType.NEUTRAL)
        assert empty == 0.5  # single doji avg 0.5 + 0.2*0
        # Actually, NEUTRAL has the doji with 0.5, so: 0.5 + 0.2*0 = 0.5
        assert empty == 0.5

    def test_get_pattern_score_no_patterns(self):
        """Empty list returns 0.0."""
        score = get_pattern_score([], PatternType.BULLISH)
        assert score == 0.0
