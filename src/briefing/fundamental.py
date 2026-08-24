"""Fundamental pillar: Forex Factory calendar + static known drivers.

Uses in-repo surprise annotations when a timestamped actual exists.
Does not invent inventory/API prints that are not on the calendar.
Lockout math matches ``NewsChecker.is_blocked`` (before/after minutes).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from src.briefing.models import Pillar
from src.config.settings import BriefingInstrumentConfig
from src.news.news_checker import NewsEvent, format_telegram_event_line, score_event

logger = logging.getLogger(__name__)

KNOWN_DRIVERS: dict[str, str] = {
    "XAU/USD": "USD, tasas reales, geopolítica",
    "BTC/USD": "liquidez USD, apetito de riesgo",
    "NASDAQ": "tasas EE.UU., mega-caps, riesgo",
    "OIL": "USD, inventarios EIA/API (si salen en el calendario), OPEC+",
}

# Specific enough to avoid matching a bare "API" token in unrelated titles.
_INVENTORY_HINTS = (
    "eia",
    "inventory",
    "inventories",
    "crude stock",
    "api weekly",
    "api crude",
    "api inventory",
)


def _event_matches(
    event: NewsEvent, instrument: BriefingInstrumentConfig, currencies: set[str]
) -> bool:
    if event.currency in currencies:
        return True
    name = event.name.lower()
    return any(keyword.lower() in name for keyword in instrument.news_keywords)


def select_events(
    events: list[NewsEvent],
    instrument: BriefingInstrumentConfig,
    currencies: set[str],
) -> list[NewsEvent]:
    return [event for event in events if _event_matches(event, instrument, currencies)]


def event_in_lockout(
    event: NewsEvent,
    when: datetime,
    *,
    lockout_before: int,
    lockout_after: int,
) -> bool:
    """Same window as ``NewsChecker.is_blocked`` for one event."""
    current = when.astimezone(UTC)
    start = event.timestamp - timedelta(minutes=lockout_before)
    end = event.timestamp + timedelta(minutes=lockout_after)
    return start <= current <= end


def events_in_lockout(
    events: list[NewsEvent],
    currencies: set[str],
    when: datetime,
    *,
    lockout_before: int,
    lockout_after: int,
) -> list[NewsEvent]:
    return [
        event
        for event in events
        if event.currency in currencies
        and event_in_lockout(
            event, when, lockout_before=lockout_before, lockout_after=lockout_after
        )
    ]


def events_in_ny_window(
    events: list[NewsEvent],
    *,
    ny_open: datetime,
    lead_minutes: int,
    after_hours: int = 2,
) -> list[NewsEvent]:
    start = ny_open - timedelta(minutes=lead_minutes)
    end = ny_open + timedelta(hours=after_hours)
    return [event for event in events if start <= event.timestamp < end]


def inventory_calendar_events(events: list[NewsEvent]) -> list[NewsEvent]:
    found: list[NewsEvent] = []
    for event in events:
        name = event.name.lower()
        if any(hint in name for hint in _INVENTORY_HINTS):
            found.append(event)
    return found


def _lockout_line(label: str, hits: list[NewsEvent]) -> str:
    if not hits:
        return f"{label}: no"
    names = ", ".join(event.name for event in hits[:2])
    extra = f" +{len(hits) - 2}" if len(hits) > 2 else ""
    return f"{label}: sí ({names}{extra})"


def build_fundamental_pillar(
    *,
    instrument: BriefingInstrumentConfig,
    currencies: set[str],
    events: list[NewsEvent],
    now: datetime,
    max_events: int,
    news_error: str | None = None,
    ny_open: datetime | None = None,
    lockout_before: int = 60,
    lockout_after: int = 30,
    lead_minutes: int = 60,
) -> Pillar:
    try:
        if news_error and not events:
            return Pillar(
                name="fundamental",
                available=False,
                unavailable_reason=news_error,
                source="forex_factory",
            )

        matched = select_events(events, instrument, currencies)
        now_utc = now.astimezone(UTC)
        upcoming = [event for event in matched if event.timestamp >= now_utc]
        recent = [event for event in matched if event.timestamp < now_utc]
        lines = [
            f"Drivers (contexto fijo, no un feed): {KNOWN_DRIVERS.get(instrument.id, 'USD')}",
        ]
        if upcoming:
            shown = upcoming[:max_events]
            lines.append("Próximos 3★:")
            lines.extend(format_telegram_event_line(event, now_utc) for event in shown)
            extra = len(upcoming) - len(shown)
            if extra > 0:
                lines.append(f"… y {extra} más")
        else:
            lines.append("Próximos 3★: ninguno en la ventana")

        if recent:
            shown_r = recent[-max_events:]
            lines.append("Recientes / sorpresa descriptiva:")
            lines.extend(format_telegram_event_line(event, now_utc) for event in shown_r)
            scored = [event for event in shown_r if score_event(event, now_utc).status == "scored"]
            if not scored:
                lines.append("Sorpresa: sin actual+forecast con marca de tiempo (proxy limitado)")
        else:
            lines.append("Recientes: ninguno en la ventana")

        now_hits = events_in_lockout(
            matched,
            currencies,
            now_utc,
            lockout_before=lockout_before,
            lockout_after=lockout_after,
        )
        lines.append(_lockout_line("Lockout 3★ ahora", now_hits))
        if ny_open is not None:
            open_utc = ny_open.astimezone(UTC)
            open_hits = events_in_lockout(
                matched,
                currencies,
                open_utc,
                lockout_before=lockout_before,
                lockout_after=lockout_after,
            )
            lines.append(
                _lockout_line(f"Lockout 3★ a las {open_utc.strftime('%H:%M')} UTC", open_hits)
            )
            window = events_in_ny_window(
                matched,
                ny_open=open_utc,
                lead_minutes=lead_minutes,
            )
            start = (open_utc - timedelta(minutes=lead_minutes)).strftime("%H:%M")
            end = (open_utc + timedelta(hours=2)).strftime("%H:%M")
            lines.append(f"Eventos 3★ en ventana NY ({start}–{end} UTC): {len(window)}")

        if instrument.id == "OIL":
            inventory = inventory_calendar_events(matched)
            if inventory:
                lines.append("Inventarios (solo si están en FF):")
                lines.extend(
                    format_telegram_event_line(event, now_utc) for event in inventory[:max_events]
                )
            else:
                lines.append("Inventarios: ninguno en el calendario FF de esta ventana")

        lines.append(f"Calendario {instrument.id}: {len(matched)} evento(s) 3★ relevantes")
        return Pillar(
            name="fundamental",
            available=True,
            lines=tuple(lines),
            source="forex_factory",
        )
    except Exception as exc:
        logger.warning("fundamental pillar failed for %s: %s", instrument.id, exc)
        return Pillar(
            name="fundamental",
            available=False,
            unavailable_reason=str(exc),
            source="forex_factory",
        )
