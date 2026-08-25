"""Spanish Telegram formatting for the pre-NY briefing (ETR-style Markdown)."""

from __future__ import annotations

from datetime import date

from src.briefing.hermes import format_ny_plan
from src.briefing.models import InstrumentBriefing, Pillar, PreNyBriefing

_WEEKDAYS = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)

_PILLAR_TITLES = {
    "technical": "Técnico",
    "fundamental": "Fundamental",
    "sentiment": "Sentimiento",
    "macro": "Macro 3★",
}

_EMOJI = {
    "XAU/USD": "🥇",
    "BTC/USD": "₿",
    "NASDAQ": "📈",
    "OIL": "🛢️",
}


def _esc(text: str) -> str:
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def _short_news_status(status: str | None) -> str | None:
    if not status:
        return None
    if "BLOCKED" in status:
        return "FF · sorpresa no puntuable (sin actual+hora)"
    if "available" in status.lower():
        return "FF · sorpresa disponible"
    return status


def _pillar_block(pillar: Pillar, *, title: str | None = None) -> list[str]:
    label = title or _PILLAR_TITLES.get(pillar.name, pillar.name.title())
    lines = [f"*{label}*"]
    for line in pillar.render_lines():
        if "`" in line or line.startswith("•"):
            lines.append(line)
        else:
            lines.append(_esc(line))
    return lines


def _instrument_block(item: InstrumentBriefing) -> str:
    emoji = _EMOJI.get(item.instrument_id, "•")
    header = f"{emoji} *{_esc(item.display_name)}* (`{item.yf_symbol}`)"
    parts = [header]
    if item.data_as_of is not None:
        stamp = item.data_as_of.strftime("%Y-%m-%d %H:%M")
        extra = f" · {item.data_freshness}" if item.data_freshness else ""
        parts.append(f"OHLC al: `{stamp} UTC`{extra}")
    elif item.data_freshness:
        parts.append(_esc(item.data_freshness))
    parts.append("")
    parts.extend(_pillar_block(item.technical))
    parts.append("")
    parts.extend(_pillar_block(item.fundamental))
    parts.append("")
    parts.extend(_pillar_block(item.sentiment))
    if item.ny_plan is not None:
        parts.append("")
        for line in format_ny_plan(item.ny_plan):
            parts.append(line if line.startswith("*") else _esc(line))
    return "\n".join(parts)


def format_pre_ny_briefing(briefing: PreNyBriefing) -> str:
    generated = briefing.generated_at.strftime("%Y-%m-%d %H:%M")
    try:
        session = date.fromisoformat(briefing.session_date)
        date_label = f"{_WEEKDAYS[session.weekday()]} {briefing.session_date}"
    except ValueError:
        date_label = briefing.session_date

    lines = [
        "📋 *Briefing pre-sesión NY*",
        f"`{date_label}` · `{generated} UTC` · NY FX `{briefing.ny_open_utc}`",
    ]
    if briefing.synthesis:
        lines.append(_esc(briefing.synthesis))
    lines.append("_No es señal de entrada ni autorización para operar._")
    news = _short_news_status(briefing.news_source_status)
    if news:
        lines.append(f"News: `{_esc(news)}`")
    if briefing.caveats:
        lines.append(" ".join(_esc(caveat) for caveat in briefing.caveats))

    if briefing.shared_fundamental is not None:
        lines.extend(["", *_pillar_block(briefing.shared_fundamental, title="Macro 3★")])

    for item in briefing.instruments:
        lines.extend(["", "━━━━━━━━", "", _instrument_block(item)])

    lines.extend(
        [
            "",
            "yfinance · FF 3★ · tesis ETR caché · funding Binance (BTC) · Rule C si hay",
            "_Informativo · Branch B · no es recomendación de inversión._",
        ]
    )
    return "\n".join(lines)


def format_instrument_briefing(item: InstrumentBriefing) -> str:
    """Single-symbol card shared by Telegram briefing and ``analyze``."""
    return _instrument_block(item)
