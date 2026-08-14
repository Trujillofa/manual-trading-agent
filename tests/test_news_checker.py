"""Tests for NewsChecker."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.news.news_checker import NewsChecker, NewsEvent

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def checker(tmp_path, monkeypatch):
    """Create NewsChecker with 60m before, 30m after lockout."""
    monkeypatch.setattr(NewsChecker, "CACHE_PATH", tmp_path / "news_cache.json")
    return NewsChecker(
        lockout_minutes_before=60,
        lockout_minutes_after=30,
        importance_threshold=3,
    )


class TestNewsChecker:
    """NewsChecker tests."""

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

    def test_parse_timestamp_mm_dd_yyyy_12h(self, checker):
        result = checker._parse_timestamp("06-06-2026", "12:30pm")
        assert result is not None
        assert result == datetime(2026, 6, 6, 12, 30, tzinfo=UTC)

    def test_parse_timestamp_mm_dd_yyyy_24h(self, checker):
        result = checker._parse_timestamp("08-13-2026", "14:30")
        assert result is not None
        assert result == datetime(2026, 8, 13, 14, 30, tzinfo=UTC)


class TestXmlParser:
    def test_published_country_schema(self, checker):
        xml = (FIXTURES / "ff_thisweek_sample.xml").read_text(encoding="utf-8")
        now = datetime(2026, 6, 6, 12, 40, tzinfo=UTC)
        events = checker._select_events_from_xml(xml, now, hours_ahead=24 * 8)
        by_name = {event.name: event for event in events}
        nfp = by_name["Non-Farm Payrolls"]
        assert nfp.currency == "USD"
        assert nfp.country == "USD"
        assert nfp.timestamp == datetime(2026, 6, 6, 12, 30, tzinfo=UTC)
        assert nfp.forecast == "180K"
        assert nfp.previous == "177K"
        assert nfp.actual == ""
        assert nfp.source == "forex_factory"
        assert nfp.actual_observed_at is None
        cpi = by_name["CPI m/m"]
        assert cpi.forecast == "0.3%"
        assert cpi.previous == "0.2%"
        assert cpi.actual == ""

    def test_legacy_currency_schema(self, checker):
        xml = (FIXTURES / "ff_legacy_currency.xml").read_text(encoding="utf-8")
        now = datetime(2026, 8, 13, 14, 40, tzinfo=UTC)
        events = checker._select_events_from_xml(xml, now, hours_ahead=24)
        assert len(events) == 2
        nfp = events[0]
        assert nfp.currency == "USD"
        assert nfp.country == "US"
        assert nfp.forecast == "180K"
        assert nfp.actual == "185K"
        assert nfp.previous == "177K"
        assert nfp.actual_observed_at == now
        cpi = events[1]
        assert cpi.timestamp.hour == 14
        assert cpi.actual == "0.2%"

    def test_post_release_lockout_survives_fresh_parse(self, checker):
        now = datetime(2026, 8, 13, 14, 40, tzinfo=UTC)
        released = now - timedelta(minutes=10)
        xml = f"""
        <weeklyevents>
          <event>
            <title>CPI</title>
            <country>USD</country>
            <date>{released.strftime("%Y-%m-%d")}</date>
            <time>{released.strftime("%H:%M")}</time>
            <impact>High</impact>
            <forecast>0.3%</forecast>
            <previous>0.2%</previous>
          </event>
        </weeklyevents>
        """
        events = checker._select_events_from_xml(xml, now, hours_ahead=24)
        assert len(events) == 1
        checker._events = events
        assert checker.is_blocked("EUR/USD", timestamp=now) is True

    def test_event_beyond_post_release_lockout_is_dropped(self, checker):
        now = datetime(2026, 8, 13, 14, 40, tzinfo=UTC)
        released = now - timedelta(minutes=40)
        xml = f"""
        <weeklyevents>
          <event>
            <title>CPI</title>
            <country>USD</country>
            <date>{released.strftime("%Y-%m-%d")}</date>
            <time>{released.strftime("%H:%M")}</time>
            <impact>High</impact>
            <forecast>0.3%</forecast>
          </event>
        </weeklyevents>
        """
        events = checker._select_events_from_xml(xml, now, hours_ahead=24)
        assert events == []

    def test_preserves_first_actual_observation_across_fetches(self, checker):
        first_now = datetime(2026, 8, 13, 14, 40, tzinfo=UTC)
        second_now = datetime(2026, 8, 13, 14, 50, tzinfo=UTC)
        xml = """
        <weeklyevents>
          <event>
            <title>CPI</title>
            <country>USD</country>
            <date>2026-08-13</date>
            <time>14:30</time>
            <impact>High</impact>
            <forecast>0.3%</forecast>
            <actual>0.4%</actual>
          </event>
        </weeklyevents>
        """
        first = checker._select_events_from_xml(xml, first_now, hours_ahead=24)
        assert first[0].actual_observed_at == first_now
        checker._events = first
        second = checker._select_events_from_xml(xml, second_now, hours_ahead=24)
        assert second[0].actual == "0.4%"
        assert second[0].actual_observed_at == first_now

    def test_restamps_observation_when_actual_changes(self, checker):
        first_now = datetime(2026, 8, 13, 14, 40, tzinfo=UTC)
        revised_now = datetime(2026, 8, 13, 14, 50, tzinfo=UTC)
        xml_first = """
        <weeklyevents>
          <event>
            <title>CPI</title>
            <country>USD</country>
            <date>2026-08-13</date>
            <time>14:30</time>
            <impact>High</impact>
            <forecast>0.3%</forecast>
            <actual>0.4%</actual>
          </event>
        </weeklyevents>
        """
        xml_revised = xml_first.replace("0.4%", "0.5%")
        checker._events = checker._select_events_from_xml(xml_first, first_now)
        revised = checker._select_events_from_xml(xml_revised, revised_now)
        assert revised[0].actual == "0.5%"
        assert revised[0].actual_observed_at == revised_now

    def test_clears_observation_when_actual_disappears(self, checker):
        first_now = datetime(2026, 8, 13, 14, 40, tzinfo=UTC)
        later = datetime(2026, 8, 13, 14, 50, tzinfo=UTC)
        xml_with_actual = """
        <weeklyevents>
          <event>
            <title>CPI</title>
            <country>USD</country>
            <date>2026-08-13</date>
            <time>14:30</time>
            <impact>High</impact>
            <forecast>0.3%</forecast>
            <actual>0.4%</actual>
          </event>
        </weeklyevents>
        """
        xml_without_actual = xml_with_actual.replace("<actual>0.4%</actual>", "")
        checker._events = checker._select_events_from_xml(xml_with_actual, first_now)
        later_events = checker._select_events_from_xml(xml_without_actual, later)
        assert later_events[0].actual == ""
        assert later_events[0].actual_observed_at is None


class TestNewsCache:
    def test_old_cache_payload_still_loads(self, tmp_path, monkeypatch):
        cache_path = tmp_path / "news_cache.json"
        monkeypatch.setattr(NewsChecker, "CACHE_PATH", cache_path)
        stamp = datetime(2026, 8, 14, 12, 30, tzinfo=UTC)
        cache_path.write_text(
            json.dumps(
                {
                    "last_fetch": stamp.isoformat(),
                    "next_allowed_fetch": (stamp + timedelta(minutes=15)).isoformat(),
                    "events": [
                        {
                            "timestamp": stamp.isoformat(),
                            "currency": "USD",
                            "name": "Non-Farm Employment Change",
                            "importance": 3,
                            "country": "US",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        checker = NewsChecker()
        assert len(checker._events) == 1
        event = checker._events[0]
        assert event.currency == "USD"
        assert event.forecast == ""
        assert event.actual == ""
        assert event.previous == ""
        assert event.source == "forex_factory"
        assert event.actual_observed_at is None

    def test_new_cache_payload_round_trips(self, tmp_path, monkeypatch):
        cache_path = tmp_path / "news_cache.json"
        monkeypatch.setattr(NewsChecker, "CACHE_PATH", cache_path)
        checker = NewsChecker()
        observed = datetime(2026, 8, 13, 14, 35, tzinfo=UTC)
        event = NewsEvent(
            timestamp=datetime(2026, 8, 13, 14, 30, tzinfo=UTC),
            currency="USD",
            name="CPI m/m",
            importance=3,
            country="US",
            forecast="0.3%",
            actual="0.4%",
            previous="0.2%",
            source="forex_factory",
            actual_observed_at=observed,
        )
        grok_event = NewsEvent(
            timestamp=datetime(2026, 8, 13, 16, 0, tzinfo=UTC),
            currency="EUR",
            name="ECB Rate Decision",
            importance=3,
            country="EU",
            source="grok",
        )
        checker._events = [event, grok_event]
        checker._last_fetch = observed
        checker._next_allowed_fetch = observed + timedelta(minutes=15)
        checker._save_cache()

        reloaded = NewsChecker()
        assert len(reloaded._events) == 2
        loaded = reloaded._events[0]
        assert loaded.forecast == "0.3%"
        assert loaded.actual == "0.4%"
        assert loaded.previous == "0.2%"
        assert loaded.source == "forex_factory"
        assert loaded.actual_observed_at == observed
        assert reloaded._events[1].source == "grok"
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        assert payload["events"][0]["forecast"] == "0.3%"
        assert payload["events"][1]["source"] == "grok"

    def test_readiness_requires_observation_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(NewsChecker, "CACHE_PATH", tmp_path / "news_cache.json")
        checker = NewsChecker()
        checker._events = [
            NewsEvent(
                timestamp=datetime(2026, 8, 13, 14, 30, tzinfo=UTC),
                currency="USD",
                name="CPI m/m",
                importance=3,
                country="US",
                forecast="0.3%",
                actual="0.4%",
                source="forex_factory",
                actual_observed_at=None,
            )
        ]
        assert checker.has_timestamped_actuals() is False
        assert "BLOCKED" in checker.get_surprise_readiness()
