"""Closed NY-scalp avoid catalog.

Python emits codes. Spanish comes from a fixed dict. Hermes cannot add,
drop, or rewrite these. Avoids never set or change the V2 desk action.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from src.briefing.fundamental import events_in_lockout
from src.briefing.technical import _clean, _closes, _highs, _lows
from src.etr.models import EtrReport
from src.indicators.adx import calculate_adx
from src.indicators.high_low import highest_high, lowest_low
from src.indicators.rsi import calculate_rsi
from src.news.news_checker import NewsEvent

logger = logging.getLogger(__name__)

AvoidCode = Literal[
    "SESSION_USD_3STAR",
    "AVOID_INTO_3STAR",
    "AVOID_FADE_1H_TREND",
    "AVOID_CHASE_EXTREME_IN_TREND",
    "AVOID_MIDRANGE_CHASE",
    "AVOID_ETR_WRONG_SCALE",
    "AVOID_OTHER_SIDE",
]

SESSION_MAX = 1
INSTRUMENT_MAX = 3
ADX_TREND_THRESHOLD = 25.0
RSI_EXTREME_LOW = 35.0
RSI_EXTREME_HIGH = 65.0
MIDRANGE_LOW = 30.0
MIDRANGE_HIGH = 70.0
RANGE_LOOKBACK = 20
ADX_PERIOD = 14
RSI_PERIOD = 14

# Proven on the card today: sentiment.py prints QQQ vs NQ=F for NASDAQ.
# OIL has no equivalent Brent/WTI line — do not invent one from cache presence.
ETR_WRONG_SCALE_IDS = frozenset({"NASDAQ"})

AVOID_LABELS: dict[str, str] = {
    "SESSION_USD_3STAR": "no scalp USD-beta hacia 3★ USD (ahora o al open NY)",
    "AVOID_INTO_3STAR": "no scalp direccional hacia el 3★ USD",
    "AVOID_FADE_1H_TREND": "no fadear la tendencia 1h (ADX)",
    "AVOID_CHASE_EXTREME_IN_TREND": "no perseguir el rebote/extremo contra la tendencia 1h",
    "AVOID_MIDRANGE_CHASE": "no perseguir medio de rango",
    "AVOID_ETR_WRONG_SCALE": "no usar zona ETR de otra escala",
    "AVOID_OTHER_SIDE": "no scalp del lado contrario a V2",
}

_INSTRUMENT_PRIORITY: tuple[str, ...] = (
    "AVOID_INTO_3STAR",
    "AVOID_CHASE_EXTREME_IN_TREND",
    "AVOID_FADE_1H_TREND",
    "AVOID_MIDRANGE_CHASE",
    "AVOID_ETR_WRONG_SCALE",
    "AVOID_OTHER_SIDE",
)


@dataclass(frozen=True)
class TapeSnapshot:
    adx_1h: float | None = None
    rsi_1h: float | None = None
    range_location_pct: float | None = None


def _as_utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def format_avoid(code: str, *, detail: str = "") -> str:
    """Spanish line for a catalog code. Unknown codes are dropped by callers."""
    if code == "SESSION_USD_3STAR" and detail.strip():
        return f"no scalp USD-beta hacia {detail.strip()}"
    return AVOID_LABELS.get(code, "")


def _star3(events: list[NewsEvent], *, importance_threshold: int) -> list[NewsEvent]:
    return [event for event in events if event.importance >= importance_threshold]


def _short_event_name(name: str) -> str:
    lower = name.lower()
    if "pce" in lower:
        return "PCE"
    if "gdp" in lower:
        return "GDP"
    if "nfp" in lower or "non-farm" in lower or "nonfarm" in lower:
        return "NFP"
    if "cpi" in lower:
        return "CPI"
    if "fomc" in lower or "interest rate" in lower:
        return "FOMC"
    token = name.strip().split()[0] if name.strip() else "3★"
    return token[:16]


def _session_detail(hits: list[NewsEvent]) -> str:
    if not hits:
        return ""
    names: list[str] = []
    seen: set[str] = set()
    earliest = min(_as_utc(event.timestamp) for event in hits)
    for event in hits:
        label = _short_event_name(event.name)
        if label in seen:
            continue
        seen.add(label)
        names.append(label)
        if len(names) >= 2:
            break
    return f"{'/'.join(names)} {earliest.strftime('%H:%M')} UTC"


def build_session_avoids(
    events: list[NewsEvent],
    *,
    now: datetime,
    ny_open: datetime | None,
    lockout_before: int = 60,
    lockout_after: int = 30,
    importance_threshold: int = 3,
) -> tuple[tuple[str, ...], str]:
    """At most one session avoid: USD 3★ lockout now, or at NY open if still ahead."""
    usd = {"USD"}
    stars = _star3(events, importance_threshold=importance_threshold)
    now_utc = _as_utc(now)
    now_hits = events_in_lockout(
        stars,
        usd,
        now_utc,
        lockout_before=lockout_before,
        lockout_after=lockout_after,
    )
    open_hits: list[NewsEvent] = []
    if ny_open is not None:
        open_utc = _as_utc(ny_open)
        if now_utc <= open_utc:
            open_hits = events_in_lockout(
                stars,
                usd,
                open_utc,
                lockout_before=lockout_before,
                lockout_after=lockout_after,
            )
    hits = now_hits or open_hits
    if not hits:
        return (), ""
    return ("SESSION_USD_3STAR",), _session_detail(hits)


def tape_snapshot(
    frames: dict[str, Any] | None,
    *,
    lookback: int = RANGE_LOOKBACK,
    rsi_period: int = RSI_PERIOD,
    adx_period: int = ADX_PERIOD,
) -> TapeSnapshot | None:
    """Same cleaned 1h ADX/RSI and 20×15m location as the technical pillar."""
    if not frames:
        return None
    h1 = _clean(frames.get("1h"))
    m15 = _clean(frames.get("15m"))
    adx = rsi = loc = None
    try:
        if h1 is not None:
            highs = _highs(h1)
            lows = _lows(h1)
            closes = _closes(h1)
            adx = _finite(calculate_adx(highs, lows, closes, adx_period))
            rsi = _finite(calculate_rsi(closes, rsi_period))
        structure = m15 if m15 is not None else h1
        if structure is not None:
            highs_s = _highs(structure)
            lows_s = _lows(structure)
            closes_s = _closes(structure)
            hh = highest_high(highs_s, lookback)
            ll = lowest_low(lows_s, lookback)
            last = closes_s[-1]
            if hh is not None and ll is not None and hh > ll:
                loc = _finite((last - ll) / (hh - ll) * 100.0)
    except Exception:
        logger.warning("avoid tape snapshot failed", exc_info=True)
        return TapeSnapshot(adx_1h=adx, rsi_1h=rsi, range_location_pct=loc)
    return TapeSnapshot(adx_1h=adx, rsi_1h=rsi, range_location_pct=loc)


def build_instrument_avoids(
    instrument_id: str,
    *,
    currencies: set[str],
    session_codes: tuple[str, ...],
    frames: dict[str, Any] | None = None,
    etr_report: EtrReport | None = None,
    action: str = "STAND_ASIDE",
    v2_direction: str | None = None,
    snapshot: TapeSnapshot | None = None,
    adx_trend_threshold: float = ADX_TREND_THRESHOLD,
    rsi_extreme_low: float = RSI_EXTREME_LOW,
    rsi_extreme_high: float = RSI_EXTREME_HIGH,
    midrange_low: float = MIDRANGE_LOW,
    midrange_high: float = MIDRANGE_HIGH,
    lookback: int = RANGE_LOOKBACK,
    rsi_period: int = RSI_PERIOD,
    adx_period: int = ADX_PERIOD,
    max_items: int = INSTRUMENT_MAX,
) -> tuple[str, ...]:
    """At most three instrument avoids. Order is the catalog priority."""
    tape = (
        snapshot
        if snapshot is not None
        else tape_snapshot(frames, lookback=lookback, rsi_period=rsi_period, adx_period=adx_period)
    )
    picked: list[str] = []

    if "SESSION_USD_3STAR" in session_codes and "USD" in {item.upper() for item in currencies}:
        picked.append("AVOID_INTO_3STAR")

    adx = _finite(tape.adx_1h if tape is not None else None)
    rsi = _finite(tape.rsi_1h if tape is not None else None)
    loc = _finite(tape.range_location_pct if tape is not None else None)
    trending = adx is not None and adx >= adx_trend_threshold
    if trending and rsi is not None and (rsi <= rsi_extreme_low or rsi >= rsi_extreme_high):
        picked.append("AVOID_CHASE_EXTREME_IN_TREND")
    if trending:
        picked.append("AVOID_FADE_1H_TREND")
    if (
        adx is not None
        and adx < adx_trend_threshold
        and loc is not None
        and midrange_low <= loc <= midrange_high
    ):
        picked.append("AVOID_MIDRANGE_CHASE")

    if etr_report is not None and instrument_id.upper() in ETR_WRONG_SCALE_IDS:
        picked.append("AVOID_ETR_WRONG_SCALE")

    if action == "ENTER_ONLY_IF" and v2_direction in {"BUY", "SELL"}:
        picked.append("AVOID_OTHER_SIDE")

    ranked = [code for code in _INSTRUMENT_PRIORITY if code in picked]
    return tuple(ranked[: max(0, max_items)])
