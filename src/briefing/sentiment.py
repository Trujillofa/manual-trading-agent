"""Sentiment proxy: cached ETR thesis + calendar headline density + BTC funding.

Labeled as a proxy. Not a Bloomberg-grade (or any numeric) sentiment score.
ETR context_score is the terminal's own thesis field, not a market index.
BTC funding is a public Binance crowding print, only when the fetch works.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.briefing.funding import FundingSnapshot
from src.briefing.models import Pillar
from src.config.settings import BriefingInstrumentConfig
from src.etr.models import EtrReport
from src.news.news_checker import NewsEvent

logger = logging.getLogger(__name__)

_RISK_KEYWORDS = (
    "fomc",
    "cpi",
    "nfp",
    "payroll",
    "powell",
    "pce",
    "jackson hole",
    "interest rate",
    "rate decision",
    "non-farm",
    "nonfarm",
)

_BUCKET_ES = {
    "low": "bajo",
    "mid": "medio",
    "high": "alto",
    "unknown": "—",
}


def _density_label(count: int) -> str:
    if count <= 0:
        return "ligero"
    if count <= 2:
        return "moderado"
    return "denso"


def _keyword_hits(events: list[NewsEvent]) -> list[str]:
    hits: list[str] = []
    for event in events:
        name = event.name.lower()
        for keyword in _RISK_KEYWORDS:
            if keyword in name and keyword not in hits:
                hits.append(keyword)
    return hits


def _etr_lines(
    report: EtrReport,
    polled_at: str | None,
    *,
    score_low: float,
    score_high: float,
) -> list[str]:
    bias = report.bias or "—"
    estado = report.estado or "—"
    direction = report.primary.direction if report.primary else "—"
    lines = [f"Tesis ETR (caché): {bias} · {estado} · dir. {direction}"]
    if report.context_score is not None:
        bucket = _BUCKET_ES.get(report.score_bucket(score_low, score_high), "—")
        lines.append(
            f"Score tesis ETR: {report.context_score:g}/100 "
            f"(cubeta {bucket} · umbral alerta {score_low:g}/{score_high:g} · "
            "no es índice de mercado)"
        )
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
        headline = report.lectura_headline.strip()[:160]
        lines.append(f"Titular ETR: {headline}")
    freshness = polled_at or report.fetched_at or report.updated_at
    if freshness:
        lines.append(f"ETR actualizado: {freshness}")
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
    events: list[NewsEvent],
    etr_report: EtrReport | None,
    etr_polled_at: str | None,
    now: datetime,
    score_low: float = 50.0,
    score_high: float = 80.0,
    btc_funding: FundingSnapshot | None = None,
    funding_error: str | None = None,
) -> Pillar:
    try:
        now_utc = now.astimezone(UTC)
        relevant = [
            event
            for event in events
            if event.currency in {"USD"}
            or any(keyword.lower() in event.name.lower() for keyword in instrument.news_keywords)
        ]
        lines = [
            "Proxy — no es un score de sentimiento de mercado",
        ]
        if etr_report is not None:
            lines.extend(
                _etr_lines(etr_report, etr_polled_at, score_low=score_low, score_high=score_high)
            )
        else:
            lines.append("Tesis ETR: no disponible (sin caché)")

        if instrument.id == "BTC/USD":
            if btc_funding is not None:
                lines.append(_funding_line(btc_funding))
            elif funding_error:
                lines.append(f"Funding BTCUSDT: no disponible ({funding_error})")

        upcoming = sum(1 for event in relevant if event.timestamp >= now_utc)
        lines.append(
            f"Densidad de calendario 3★: {_density_label(upcoming)} "
            f"({upcoming} próximos relevantes · proxy de titulares)"
        )
        hits = _keyword_hits(relevant)
        if hits:
            lines.append(f"Palabras clave: {', '.join(hits)} (proxy de titular, no sesgo)")
        return Pillar(
            name="sentiment",
            available=True,
            lines=tuple(lines),
            source="etr_cache+forex_factory_headlines",
        )
    except Exception as exc:
        logger.warning("sentiment pillar failed for %s: %s", instrument.id, exc)
        return Pillar(
            name="sentiment",
            available=False,
            unavailable_reason=str(exc),
            source="etr_cache+forex_factory_headlines",
        )
