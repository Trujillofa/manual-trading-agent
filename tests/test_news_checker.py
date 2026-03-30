"""Tests for NewsChecker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.news.news_checker import NewsChecker, NewsEvent


class TestNewsChecker:
    """NewsChecker tests."""

    @pytest.fixture
    def checker(self):
        """Create NewsChecker with 60m before, 30m after lockout."""
        return NewsChecker(
            lockout_minutes_before=60,
            lockout_minutes_after=30,
            importance_threshold=3,
        )

    @pytest.fixture
    def future_event(self):
        """Create a 3-star USD event in the future."""
        return NewsEvent(
            timestamp=datetime.now(UTC) + timedelta(hours=2),
            currency="USD",
            name="Non-Farm Employment Change",
            importance=3,
            country="US",
        )

    @pytest.fixture
    def past_event(self):
        """Create a 3-star USD event in the past."""
        return NewsEvent(
            timestamp=datetime.now(UTC) - timedelta(hours=2),
            currency="USD",
            name="CPI",
            importance=3,
            country="US",
        )

    def test_extract_currencies_standard(self, checker):
        """Should extract currencies from standard pair format."""
        assert checker._extract_currencies("EUR/USD") == {"EUR", "USD"}
        assert checker._extract_currencies("GBP/USD") == {"GBP", "USD"}
        assert checker._extract_currencies("USD/JPY") == {"USD", "JPY"}

    def test_extract_currencies_no_separator(self, checker):
        """Should extract currencies from compact format."""
        assert checker._extract_currencies("EURUSD") == {"EUR", "USD"}
        assert checker._extract_currencies("GBPJPY") == {"GBP", "JPY"}

    def test_extract_currencies_with_dash(self, checker):
        """Should handle dash-separated pairs."""
        assert checker._extract_currencies("EUR-USD") == {"EUR", "USD"}

    def test_impact_to_importance(self, checker):
        """Should map impact strings to importance levels."""
        assert checker._impact_to_importance("High") == 3
        assert checker._impact_to_importance("Medium") == 2
        assert checker._impact_to_importance("Low") == 1
        assert checker._impact_to_importance("high") == 3
        assert checker._impact_to_importance("") == 0

    def test_is_blocked_no_events(self, checker):
        """Should not block when no events loaded."""
        assert checker.is_blocked("EUR/USD") is False

    def test_is_blocked_during_lockout(self, checker, future_event):
        """Should block during lockout window."""
        checker._events = [future_event]
        during = future_event.timestamp - timedelta(minutes=30)
        assert checker.is_blocked("USD/JPY", timestamp=during) is True

    def test_is_blocked_outside_lockout(self, checker, future_event):
        """Should not block outside lockout window."""
        checker._events = [future_event]
        before = future_event.timestamp - timedelta(minutes=61)
        assert checker.is_blocked("USD/JPY", timestamp=before) is False
        after = future_event.timestamp + timedelta(minutes=31)
        assert checker.is_blocked("USD/JPY", timestamp=after) is False

    def test_is_blocked_different_currency(self, checker, future_event):
        """Should not block if currency not in symbol."""
        checker._events = [future_event]
        assert checker.is_blocked("EUR/GBP", timestamp=future_event.timestamp) is False

    def test_is_blocked_wrong_importance(self, checker):
        """Should not block events below threshold."""
        low_event = NewsEvent(
            timestamp=datetime.now(UTC) + timedelta(hours=1),
            currency="USD",
            name="Low Impact",
            importance=1,
            country="US",
        )
        checker._events = [low_event]

        assert checker.is_blocked("USD/JPY") is False

    def test_get_resume_time_blocked(self, checker, future_event):
        """Should return lockout end time when blocked."""
        checker._events = [future_event]

        during = future_event.timestamp - timedelta(minutes=30)
        resume = checker.get_resume_time("USD/JPY", timestamp=during)

        expected = future_event.timestamp + timedelta(minutes=30)
        assert resume is not None
        assert abs((resume - expected).total_seconds()) < 2

    def test_get_resume_time_not_blocked(self, checker, future_event):
        """Should return None when not blocked."""
        checker._events = [future_event]

        before = future_event.timestamp - timedelta(minutes=61)
        assert checker.get_resume_time("USD/JPY", timestamp=before) is None

    def test_get_blocked_currencies(self, checker, future_event):
        """Should return set of blocked currencies."""
        checker._events = [future_event]

        during = future_event.timestamp - timedelta(minutes=30)
        blocked = checker.get_blocked_currencies(timestamp=during)

        assert blocked == {"USD"}

    def test_get_blocked_currencies_multiple(self, checker):
        """Should return multiple blocked currencies."""
        now = datetime.now(UTC)
        usd_event = NewsEvent(
            timestamp=now + timedelta(hours=1),
            currency="USD",
            name="USD Event",
            importance=3,
            country="US",
        )
        eur_event = NewsEvent(
            timestamp=now + timedelta(hours=1),  # Same time for overlapping lockout
            currency="EUR",
            name="EUR Event",
            importance=3,
            country="EU",
        )
        checker._events = [usd_event, eur_event]

        during = usd_event.timestamp - timedelta(minutes=30)
        blocked = checker.get_blocked_currencies(timestamp=during)

        assert blocked == {"USD", "EUR"}

    def test_parse_timestamp_standard_format(self, checker):
        """Should parse standard YYYY-MM-DD HH:MM format."""
        result = checker._parse_timestamp("2026-03-29", "14:30")
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_parse_timestamp_12h_format(self, checker):
        """Should parse 12-hour format with AM/PM."""
        result = checker._parse_timestamp("2026-03-29", "02:30PM")
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_parse_timestamp_all_day_skipped(self, checker):
        """Should return None for ALL DAY events."""
        result = checker._parse_timestamp("2026-03-29", "ALL DAY")
        assert result is None

    def test_safe_text_with_content(self, checker):
        """Should extract text content."""
        import xml.etree.ElementTree as ET

        element = ET.Element("test")
        element.text = "  Hello  "
        assert checker._safe_text(element) == "Hello"

    def test_safe_text_none(self, checker):
        """Should return empty string for None."""
        assert checker._safe_text(None) == ""
