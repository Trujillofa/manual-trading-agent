"""Spanish Telegram formatting for the pre-NY briefing (ETR-style Markdown)."""

from __future__ import annotations

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
}

_EMOJI = {
    "XAU/USD": "🥇",
    "BTC/USD": "₿",
    "NASDAQ": "📈",
    "OIL": "🛢️",
}


def _esc(text: str) -> str:
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def _pillar_block(pillar: Pillar) -> list[str]:
    title = _PILLAR_TITLES.get(pillar.name, pillar.name.title())
    lines = [f"*{title}*"]
    for line in pillar.render_lines():
        if "`" in line or line.startswith("•"):
            lines.append(line)
        else:
            lines.append(_esc(line))
    return lines


def _instrument_block(item: InstrumentBriefing) -> str:
    emoji = _EMOJI.get(item.instrument_id, "•")
    header = f"{emoji} *{_esc(item.display_name)}* (`{item.instrument_id}` · `{item.yf_symbol}`)"
    parts = [header]
    if item.data_as_of is not None:
        parts.append(f"OHLC al: `{item.data_as_of.strftime('%Y-%m-%d %H:%M')} UTC`")
    parts.append("")
    parts.extend(_pillar_block(item.technical))
    parts.append("")
    parts.extend(_pillar_block(item.fundamental))
    parts.append("")
    parts.extend(_pillar_block(item.sentiment))
    return "\n".join(parts)


def format_pre_ny_briefing(briefing: PreNyBriefing) -> str:
    generated = briefing.generated_at.strftime("%Y-%m-%d %H:%M")
    try:
        session = briefing.generated_at.date()
        weekday = _WEEKDAYS[session.weekday()]
        date_label = f"{weekday} {briefing.session_date}"
    except Exception:
        date_label = briefing.session_date

    lines = [
        "📋 *Briefing pre-sesión NY*",
        f"Sesión: `{date_label}` · Enviado: `{generated} UTC`",
        (
            f"Apertura NY (FX): `{briefing.ny_open_utc} UTC` "
            f"(lead {briefing.lead_minutes} min · ventana histórica 12–21 UTC; sin DST)"
        ),
        "_No es señal de entrada ni autorización para operar._",
    ]
    if briefing.news_source_status:
        lines.append(f"News: `{_esc(briefing.news_source_status)}`")
    if briefing.caveats:
        lines.append("· ".join(_esc(caveat) for caveat in briefing.caveats))

    for item in briefing.instruments:
        lines.extend(["", "━━━━━━━━", "", _instrument_block(item)])

    lines.extend(
        [
            "",
            "Fuentes: yfinance OHLC · Forex Factory 3★ · tesis ETR en caché · funding Binance (BTC) · scanner Rule C (si hay)",
            "_Informativo · Branch B · no es recomendación de inversión._",
        ]
    )
    return "\n".join(lines)
