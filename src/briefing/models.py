"""Dataclasses for the pre-NY three-pillar briefing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Pillar:
    """One analysis pillar. ``available=False`` still renders an explicit line."""

    name: str
    available: bool
    lines: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    source: str | None = None

    def render_lines(self) -> tuple[str, ...]:
        if self.available:
            return self.lines
        reason = self.unavailable_reason or "sin datos"
        return (f"No disponible: {reason}", *self.lines)


@dataclass(frozen=True)
class NyPlan:
    """Hermes NY-session judgment. ``available=False`` is an explicit skip/fail."""

    available: bool
    htf_trend: str = ""
    htf_basis: str = ""
    support: tuple[str, ...] = ()
    resistance: tuple[str, ...] = ()
    recommendation: str = ""
    action: str = ""
    why: str = ""
    invalidation: str = ""
    confidence: str = ""
    honesty: str = ""
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class InstrumentBriefing:
    instrument_id: str
    display_name: str
    yf_symbol: str
    technical: Pillar
    fundamental: Pillar
    sentiment: Pillar
    data_as_of: datetime | None = None
    data_freshness: str | None = None
    ny_plan: NyPlan | None = None


@dataclass
class PreNyBriefing:
    session_date: str
    generated_at: datetime
    ny_open_utc: str
    lead_minutes: int
    instruments: list[InstrumentBriefing] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    news_source_status: str | None = None
    shared_fundamental: Pillar | None = None
    synthesis: str | None = None
