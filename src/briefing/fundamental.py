"""Fundamental pillar: Forex Factory calendar + instrument-specific lines.

Uses in-repo surprise annotations when a timestamped actual exists.
Does not invent inventory/API prints that are not on the calendar.
Lockout math matches ``NewsChecker.is_blocked`` (before/after minutes).
Shared USD/all-3★ lines live in ``build_shared_fundamental_pillar`` so the
Telegram message does not repeat the same calendar four times.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from src.briefing.models import Pillar
from src.config.settings import BriefingInstrumentConfig
from src.news.news_checker import NewsEvent, format_telegram_event_line, score_event

logger = logging.getLogger(__name__)

SHARED_MAX_EVENTS = 8

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

_RISK_KEYWORDS = (
    "fomc",
    "cpi",
    "nfp",
    "payroll",
    "powell",
    "pce",
    "gdp",
    "jackson hole",
    "interest rate",
    "rate decision",
    "non-farm",
    "nonfarm",
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


def select_keyword_events(
    events: list[NewsEvent],
    instrument: BriefingInstrumentConfig,
) -> list[NewsEvent]:
    """Events whose title matches the instrument keywords (not currency-only)."""
    keywords = tuple(keyword.lower() for keyword in instrument.news_keywords if keyword)
    if not keywords:
        return []
    found: list[NewsEvent] = []
    for event in events:
        name = event.name.lower()
        if any(keyword in name for keyword in keywords):
            found.append(event)
    return found


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


_RISK_LABELS: dict[str, str] = {
    "fomc": "FOMC",
    "cpi": "CPI",
    "nfp": "NFP",
    "payroll": "NFP",
    "powell": "Powell",
    "pce": "PCE",
    "gdp": "GDP",
    "jackson hole": "Jackson Hole",
    "interest rate": "tasas",
    "rate decision": "tasas",
    "non-farm": "NFP",
    "nonfarm": "NFP",
}


def _keyword_hits(events: list[NewsEvent]) -> list[str]:
    hits: list[str] = []
    for event in events:
        name = event.name.lower()
        for keyword in _RISK_KEYWORDS:
            if keyword in name and keyword not in hits:
                hits.append(keyword)
    return hits


def _risk_label(event: NewsEvent) -> str | None:
    name = event.name.lower()
    for keyword in _RISK_KEYWORDS:
        if keyword in name:
            short = _RISK_LABELS.get(keyword, keyword.upper())
            currency = event.currency.upper()
            if currency and currency != "USD":
                return f"{currency} {short}"
            return short
    return None


def build_header_synthesis(
    *,
    events: list[NewsEvent],
    now: datetime,
    news_error: str | None = None,
    lockout_before: int = 60,
    lockout_after: int = 30,
    max_risk: int = 2,
) -> str:
    """One-line Spanish header from the same 3★ calendar + USD lockout."""
    if news_error and not events:
        return "hoy: calendario no disponible"

    now_utc = now.astimezone(UTC)
    now_hits = events_in_lockout(
        events,
        {"USD"},
        now_utc,
        lockout_before=lockout_before,
        lockout_after=lockout_after,
    )
    if now_hits:
        names: list[str] = []
        for event in now_hits[:2]:
            hit = _risk_label(event) or event.name
            if hit not in names:
                names.append(hit)
        extra = f" +{len(now_hits) - 2}" if len(now_hits) > 2 else ""
        lockout = f"lockout USD ({', '.join(names)}{extra})"
    else:
        lockout = "sin lockout"

    lockout_keys = {(event.currency, event.name, event.timestamp) for event in now_hits}
    upcoming = sorted(
        (event for event in events if event.timestamp >= now_utc),
        key=lambda event: event.timestamp,
    )
    risk_labels: list[str] = []
    for event in upcoming:
        if (event.currency, event.name, event.timestamp) in lockout_keys:
            continue
        label = _risk_label(event)
        if label and label not in risk_labels:
            risk_labels.append(label)
        if len(risk_labels) >= max_risk:
            break
    risk = " / ".join(risk_labels) if risk_labels else "sin claves 3★"
    return f"hoy: {lockout}; riesgo = {risk}"


def _append_event_section(
    lines: list[str],
    *,
    heading: str,
    empty_line: str,
    events: list[NewsEvent],
    now_utc: datetime,
    max_events: int,
    newest_last: bool = False,
) -> list[NewsEvent]:
    if not events:
        lines.append(empty_line)
        return []
    shown = events[-max_events:] if newest_last else events[:max_events]
    lines.append(heading)
    lines.extend(format_telegram_event_line(event, now_utc) for event in shown)
    hidden = len(events) - len(shown)
    if hidden > 0:
        lines.append(f"… y {hidden} más")
    return shown


def build_shared_fundamental_pillar(
    *,
    events: list[NewsEvent],
    now: datetime,
    news_error: str | None = None,
    ny_open: datetime | None = None,
    lockout_before: int = 60,
    lockout_after: int = 30,
    lead_minutes: int = 60,
    max_events: int = SHARED_MAX_EVENTS,
) -> Pillar:
    """One calendar for the whole briefing (all 3★ in the fetched window)."""
    try:
        if news_error and not events:
            return Pillar(
                name="macro",
                available=False,
                unavailable_reason=news_error,
                source="forex_factory",
            )

        now_utc = now.astimezone(UTC)
        upcoming = [event for event in events if event.timestamp >= now_utc]
        recent = [event for event in events if event.timestamp < now_utc]
        lines: list[str] = []
        _append_event_section(
            lines,
            heading="Próximos:",
            empty_line="Próximos: ninguno en la ventana",
            events=upcoming,
            now_utc=now_utc,
            max_events=max_events,
        )
        shown_recent = _append_event_section(
            lines,
            heading="Recientes / sorpresa:",
            empty_line="Recientes: ninguno en la ventana",
            events=recent,
            now_utc=now_utc,
            max_events=max_events,
            newest_last=True,
        )
        if shown_recent:
            scored = [
                event for event in shown_recent if score_event(event, now_utc).status == "scored"
            ]
            if not scored:
                lines.append("Sorpresa: sin actual+forecast con marca de tiempo")

        now_hits = events_in_lockout(
            events,
            {"USD"},
            now_utc,
            lockout_before=lockout_before,
            lockout_after=lockout_after,
        )
        lines.append(_lockout_line("Lockout USD ahora", now_hits))
        if ny_open is not None:
            open_utc = ny_open.astimezone(UTC)
            open_hits = events_in_lockout(
                events,
                {"USD"},
                open_utc,
                lockout_before=lockout_before,
                lockout_after=lockout_after,
            )
            lines.append(
                _lockout_line(f"Lockout USD a las {open_utc.strftime('%H:%M')} UTC", open_hits)
            )
            window = events_in_ny_window(
                events,
                ny_open=open_utc,
                lead_minutes=lead_minutes,
            )
            start = (open_utc - timedelta(minutes=lead_minutes)).strftime("%H:%M")
            end = (open_utc + timedelta(hours=2)).strftime("%H:%M")
            lines.append(f"3★ en ventana NY ({start}–{end} UTC): {len(window)}")

        hits = _keyword_hits(events)
        if hits:
            lines.append(f"Claves: {', '.join(hits)} (titular, no sesgo)")
        return Pillar(
            name="macro",
            available=True,
            lines=tuple(lines),
            source="forex_factory",
        )
    except Exception as exc:
        logger.warning("shared fundamental pillar failed: %s", exc)
        return Pillar(
            name="macro",
            available=False,
            unavailable_reason=str(exc),
            source="forex_factory",
        )


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
    del currencies, ny_open, lockout_before, lockout_after, lead_minutes
    try:
        if news_error and not events:
            return Pillar(
                name="fundamental",
                available=False,
                unavailable_reason=news_error,
                source="forex_factory",
            )

        matched = select_keyword_events(events, instrument)
        now_utc = now.astimezone(UTC)
        upcoming = [event for event in matched if event.timestamp >= now_utc]
        recent = [event for event in matched if event.timestamp < now_utc]
        lines: list[str] = []
        if upcoming or recent:
            _append_event_section(
                lines,
                heading="Propios (título):",
                empty_line="Propios: ninguno",
                events=upcoming,
                now_utc=now_utc,
                max_events=max_events,
            )
            if recent:
                _append_event_section(
                    lines,
                    heading="Propios recientes:",
                    empty_line="",
                    events=recent,
                    now_utc=now_utc,
                    max_events=max_events,
                    newest_last=True,
                )
        else:
            lines.append("sin 3★ propios · ver macro")

        if instrument.id == "OIL":
            inventory = inventory_calendar_events(matched)
            listed = set(upcoming[:max_events]) | set(recent[-max_events:])
            extra_inventory = [event for event in inventory if event not in listed]
            if extra_inventory:
                lines.append("Inventarios (solo si están en FF):")
                lines.extend(
                    format_telegram_event_line(event, now_utc)
                    for event in extra_inventory[:max_events]
                )
            elif not inventory:
                lines.append("Inventarios: ninguno en el calendario FF de esta ventana")

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
