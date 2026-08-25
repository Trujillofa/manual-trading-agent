"""Sentiment proxy: cached ETR thesis + BTC funding.

Labeled as a proxy. Not a Bloomberg-grade (or any numeric) sentiment score.
ETR context_score is the terminal's own thesis field, not a market index.
BTC funding is a public Binance crowding print, only when the fetch works.
Calendar density lives in the shared macro block, not per instrument.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from src.briefing.funding import FundingSnapshot
from src.briefing.models import Pillar
from src.config.settings import BriefingInstrumentConfig
from src.etr.models import EtrReport
from src.news.news_checker import NewsEvent

logger = logging.getLogger(__name__)

ETR_STALE_HOURS = 24


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _etr_age_hours(report: EtrReport, polled_at: str | None, now: datetime) -> int | None:
    stamp = _parse_timestamp(polled_at) or _parse_timestamp(report.fetched_at)
    if stamp is None:
        return None
    age = now.astimezone(UTC) - stamp
    if age < timedelta(0):
        return 0
    return int(age.total_seconds() // 3600)


def _etr_lines(
    report: EtrReport,
    polled_at: str | None,
    now: datetime,
    *,
    instrument_id: str,
) -> list[str]:
    age_hours = _etr_age_hours(report, polled_at, now)
    if age_hours is not None and age_hours >= ETR_STALE_HOURS:
        return [f"Tesis ETR: caché vieja ~{age_hours}h · no usar"]

    bias = report.bias or "—"
    estado = report.estado or "—"
    direction = report.primary.direction if report.primary else "—"
    lines = [f"Tesis ETR (caché): {bias} · {estado} · dir. {direction}"]
    if report.context_score is not None:
        lines.append(f"Score tesis ETR: {report.context_score:g}/100 (no es índice de mercado)")
    primary = report.primary
    if primary is not None:
        bits: list[str] = []
        if primary.activation_zone is not None:
            bits.append(f"zona {primary.activation_zone.format()}")
        in_zone = report.price_in_primary_zone()
        if in_zone is True:
            bits.append("en zona SÍ")
        elif in_zone is False:
            bits.append("en zona NO")
        if primary.invalidation is not None:
            bits.append(f"inv. {primary.invalidation:g}")
        if primary.status:
            bits.append(primary.status)
        if bits:
            lines.append("Escenario ETR: " + " · ".join(bits))
    if report.lectura_headline:
        headline = report.lectura_headline.strip()[:80]
        lines.append(f"Titular ETR: {headline}")
    freshness = polled_at or report.fetched_at or report.updated_at
    if freshness:
        lines.append(f"ETR actualizado: {freshness}")
    if instrument_id == "NASDAQ":
        lines.append("ETR en escala QQQ, no NQ=F")
    return lines


def _funding_line(snapshot: FundingSnapshot) -> str:
    when = snapshot.funding_time_utc()
    stamp = when.strftime("%Y-%m-%d %H:%M UTC") if when is not None else "—"
    return (
        f"Funding {snapshot.symbol}: {snapshot.rate_pct_label()} "
        f"({stamp} · Binance público · proxy de crowding, no señal)"
    )


def build_sentiment_pillar(
    *,
    instrument: BriefingInstrumentConfig,
    events: list[NewsEvent] | None = None,
    etr_report: EtrReport | None,
    etr_polled_at: str | None,
    now: datetime,
    score_low: float = 50.0,
    score_high: float = 80.0,
    btc_funding: FundingSnapshot | None = None,
    funding_error: str | None = None,
) -> Pillar:
    del events, score_low, score_high
    try:
        lines: list[str] = []
        if etr_report is not None:
            lines.extend(_etr_lines(etr_report, etr_polled_at, now, instrument_id=instrument.id))
        else:
            lines.append("Tesis ETR: no disponible (sin caché)")

        if instrument.id == "BTC/USD":
            if btc_funding is not None:
                lines.append(_funding_line(btc_funding))
            elif funding_error:
                lines.append(f"Funding BTCUSDT: no disponible ({funding_error})")

        return Pillar(
            name="sentiment",
            available=True,
            lines=tuple(lines),
            source="etr_cache+binance_funding",
        )
    except Exception as exc:
        logger.warning("sentiment pillar failed for %s: %s", instrument.id, exc)
        return Pillar(
            name="sentiment",
            available=False,
            unavailable_reason=str(exc),
            source="etr_cache+binance_funding",
        )
