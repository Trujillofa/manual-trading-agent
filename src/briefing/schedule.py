"""Once-per-day pre-NY window (UTC).

NY open is ``12:00`` UTC by default. That matches the historical FX NY window
``12-21`` UTC used in this repo (``StrategyConfig.session_allowed_utc`` /
CLAUDE.md). It is ~08:00 America/New_York during EDT and ~07:00 during EST.
There is no DST flip. This is not CME Globex reopen and not US cash equity
open (13:30 UTC EDT / 14:30 UTC EST).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta


def parse_hhmm(value: str) -> time:
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"ny_open_utc must be HH:MM, got {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"ny_open_utc must be HH:MM, got {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"ny_open_utc out of range: {value!r}")
    return time(hour, minute)


def session_date_for(now: datetime) -> date:
    """UTC calendar date of the NY session we would brief."""
    return now.astimezone(UTC).date()


def ny_open_at(session_date: date, ny_open_utc: str) -> datetime:
    clock = parse_hhmm(ny_open_utc)
    return datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        clock.hour,
        clock.minute,
        tzinfo=UTC,
    )


def is_weekend(session_date: date) -> bool:
    return session_date.weekday() >= 5


def in_pre_ny_window(now: datetime, ny_open_utc: str, lead_minutes: int) -> bool:
    now_utc = now.astimezone(UTC)
    open_at = ny_open_at(now_utc.date(), ny_open_utc)
    start = open_at - timedelta(minutes=lead_minutes)
    return start <= now_utc < open_at


def should_send_briefing(
    *,
    now: datetime,
    ny_open_utc: str,
    lead_minutes: int,
    last_session_date: str | None,
    skip_weekends: bool,
    force: bool = False,
) -> tuple[bool, str, date]:
    """Return (should_send, reason, session_date)."""
    session_date = session_date_for(now)
    if force:
        return True, "force", session_date
    if skip_weekends and is_weekend(session_date):
        return False, "weekend", session_date
    if not in_pre_ny_window(now, ny_open_utc, lead_minutes):
        return False, "outside_window", session_date
    if last_session_date == session_date.isoformat():
        return False, "already_sent", session_date
    return True, "in_window", session_date
