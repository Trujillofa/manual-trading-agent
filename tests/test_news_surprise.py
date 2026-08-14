"""Tests for descriptive post-release surprise scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.news.news_checker import (
    NewsEvent,
    format_cli_event_line,
    format_cli_news_report,
    format_telegram_event_line,
    format_telegram_news_report,
    score_event,
)
from src.news.surprise import (
    format_surprise_annotation,
    parse_numeric_value,
    score_surprise,
    surprise_readiness_label,
)

NOW = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)
RELEASED = datetime(2026, 8, 13, 14, 30, tzinfo=UTC)
UPCOMING = datetime(2026, 8, 13, 16, 30, tzinfo=UTC)


def _score(**kwargs: object):
    defaults: dict[str, object] = {
        "actual_raw": "185K",
        "forecast_raw": "180K",
        "event_timestamp": RELEASED,
        "now": NOW,
        "source": "forex_factory",
        "observed_at": NOW,
    }
    defaults.update(kwargs)
    return score_surprise(**defaults)  # type: ignore[arg-type]


class TestParseNumericValue:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("180K", 180_000.0),
            ("1.2M", 1_200_000.0),
            ("2B", 2_000_000_000.0),
            ("0.3%", 0.3),
            ("-0.1%", -0.1),
            ("25bp", 0.25),
            ("25bps", 0.25),
            ("1,234.5", 1234.5),
            (" -1.5 ", -1.5),
            ("+2.0", 2.0),
            ("180K|revised", 180_000.0),
        ],
    )
    def test_supported_values(self, raw: str, expected: float) -> None:
        parsed = parse_numeric_value(raw)
        assert parsed is not None
        assert parsed == pytest.approx(expected)

    @pytest.mark.parametrize(
        "raw",
        ["", "N/A", "n/a", "null", "--", "-", "   ", "TBD", "revised", None],
    )
    def test_missing_and_malformed_are_none(self, raw: str | None) -> None:
        assert parse_numeric_value(raw) is None


class TestScoreSurprise:
    def test_scored_above(self) -> None:
        result = _score()
        assert result.status == "scored"
        assert result.parsed_actual == pytest.approx(185_000.0)
        assert result.parsed_forecast == pytest.approx(180_000.0)
        assert result.raw_delta == pytest.approx(5_000.0)
        assert result.relative_delta_pct == pytest.approx(5_000.0 / 180_000.0 * 100.0)
        assert result.direction == "above"

    def test_scored_below(self) -> None:
        result = _score(actual_raw="170K")
        assert result.status == "scored"
        assert result.direction == "below"

    def test_scored_inline(self) -> None:
        result = _score(actual_raw="180K")
        assert result.status == "scored"
        assert result.direction == "inline"
        assert result.raw_delta == pytest.approx(0.0)

    def test_pre_release_even_with_leaked_actual(self) -> None:
        result = _score(event_timestamp=UPCOMING, now=NOW)
        assert result.status == "pre_release"
        assert result.relative_delta_pct is None
        assert result.parsed_actual is None

    def test_missing_actual_unscored(self) -> None:
        result = _score(actual_raw="")
        assert result.status == "missing_actual"
        assert result.relative_delta_pct is None

    def test_missing_forecast_unscored(self) -> None:
        result = _score(forecast_raw="N/A")
        assert result.status == "missing_forecast"
        assert result.relative_delta_pct is None

    def test_unparseable(self) -> None:
        result = _score(actual_raw="revised print")
        assert result.status == "unparseable"
        assert result.relative_delta_pct is None

    def test_zero_forecast_does_not_divide(self) -> None:
        result = _score(actual_raw="1.0", forecast_raw="0")
        assert result.status == "zero_forecast"
        assert result.raw_delta == pytest.approx(1.0)
        assert result.relative_delta_pct is None
        assert result.direction == "above"

    def test_llm_fallback_cannot_score(self) -> None:
        result = _score(source="grok", actual_raw="185K", forecast_raw="180K")
        assert result.status == "non_deterministic_source"
        assert result.parsed_actual is None
        assert result.relative_delta_pct is None

    def test_observed_before_release_is_pre_release(self) -> None:
        result = _score(observed_at=RELEASED - timedelta(minutes=5))
        assert result.status == "pre_release"

    def test_missing_observed_at_does_not_score(self) -> None:
        result = _score(observed_at=None)
        assert result.status == "missing_observed_at"
        assert result.parsed_actual is None
        assert result.relative_delta_pct is None
        assert result.direction is None


class TestPresentation:
    def _event(self, **kwargs: object) -> NewsEvent:
        fields: dict[str, object] = {
            "timestamp": RELEASED,
            "currency": "USD",
            "name": "Non-Farm Payrolls",
            "importance": 3,
            "country": "USD",
            "forecast": "180K",
            "actual": "185K",
            "previous": "177K",
            "source": "forex_factory",
            "actual_observed_at": NOW,
        }
        fields.update(kwargs)
        return NewsEvent(**fields)  # type: ignore[arg-type]

    def test_cli_and_telegram_scored(self) -> None:
        event = self._event()
        cli = format_cli_event_line(event, NOW)
        tg = format_telegram_event_line(event, NOW)
        assert "scored" in cli
        assert "actual 185K vs forecast 180K" in cli
        assert "above" in cli
        assert "bullish" not in cli.lower()
        assert "BUY" not in cli
        assert "scored" in tg
        assert "USD" in tg

    def test_cli_scheduled(self) -> None:
        event = self._event(timestamp=UPCOMING, actual="", actual_observed_at=None)
        line = format_cli_event_line(event, NOW)
        assert "scheduled" in line
        assert "forecast 180K" in line
        assert "scored" not in line

    def test_cli_released_actual_unavailable(self) -> None:
        event = self._event(actual="", actual_observed_at=None)
        line = format_cli_event_line(event, NOW)
        assert "released | actual unavailable" in line
        assert "forecast 180K" in line
        assert "scored" not in line

    def test_telegram_report_blocked(self) -> None:
        event = self._event(actual="", actual_observed_at=None)
        report = format_telegram_news_report(
            source_status="forex_factory_or_grok",
            blocked=["USD"],
            events=[event],
            now=NOW,
            readiness=surprise_readiness_label(False),
        )
        assert "BLOCKED" in report
        assert "no timestamped actuals" in report
        assert "actual unavailable" in report

    def test_cli_report_blocked_empty(self) -> None:
        report = format_cli_news_report(
            [],
            24,
            NOW,
            surprise_readiness_label(False),
        )
        assert "BLOCKED" in report
        assert "No 3-star events" in report

    def test_score_event_uses_event_fields(self) -> None:
        event = self._event()
        result = score_event(event, NOW)
        assert result.status == "scored"
        assert result.direction == "above"

    def test_annotation_missing_forecast_does_not_claim_actual_unavailable(self) -> None:
        result = _score(forecast_raw="N/A")
        text = format_surprise_annotation(
            forecast_raw="N/A",
            actual_raw="185K",
            result=result,
        )
        assert "forecast unavailable" in text
        assert "actual 185K" in text
        assert "actual unavailable" not in text

    def test_annotation_unparseable_does_not_claim_actual_unavailable(self) -> None:
        result = _score(actual_raw="revised print")
        text = format_surprise_annotation(
            forecast_raw="180K",
            actual_raw="revised print",
            result=result,
        )
        assert "unscored" in text
        assert "actual revised print" in text
        assert "forecast 180K" in text
        assert "actual unavailable" not in text

    def test_annotation_zero_forecast_does_not_claim_actual_unavailable(self) -> None:
        result = _score(actual_raw="1.0", forecast_raw="0")
        text = format_surprise_annotation(
            forecast_raw="0",
            actual_raw="1.0",
            result=result,
        )
        assert "unscored zero forecast" in text
        assert "actual 1.0" in text
        assert "actual unavailable" not in text

    def test_annotation_missing_observed_at_does_not_score(self) -> None:
        result = _score(observed_at=None)
        text = format_surprise_annotation(
            forecast_raw="180K",
            actual_raw="185K",
            result=result,
        )
        assert "observation time unavailable" in text
        assert "actual 185K" in text
        assert "actual unavailable" not in text
        assert "scored" not in text

    def test_cli_actual_without_observation_is_unscored(self) -> None:
        event = self._event(actual_observed_at=None)
        line = format_cli_event_line(event, NOW)
        assert "observation time unavailable" in line
        assert "scored" not in line
        assert "actual unavailable" not in line
