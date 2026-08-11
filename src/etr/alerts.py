"""Telegram message formatting for ETR reports and change alerts."""

from __future__ import annotations

from src.etr.models import EtrChange, EtrReport


def _esc(text: str) -> str:
    """Light Markdown escaping for Telegram legacy Markdown."""
    return (
        text.replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


def _price(report: EtrReport) -> str:
    if report.price is None:
        return "—"
    return f"{report.price:,.4f}".rstrip("0").rstrip(".")


def format_change_alert(report: EtrReport, changes: list[EtrChange]) -> str:
    asset = report.asset.upper()
    lines = [f"📊 *ETR · {asset} · CAMBIO*"]
    field_labels = {
        "bias": "Sesgo",
        "estado": "Estado",
        "primary_direction": "Dir. principal",
        "primary_invalidation": "Invalidación",
        "primary_zone": "Zona principal",
        "primary_status": "Estado escenario",
        "context_score": "Score",
        "price_in_primary_zone": "Precio en zona",
    }
    for change in changes:
        label = field_labels.get(change.field, change.field)
        marker = "⚡" if change.severity == "action" else "•"
        lines.append(f"{marker} {label}: `{_esc(change.old)}` → `{_esc(change.new)}`")

    primary = report.primary
    if primary:
        lines.append("")
        lines.append(
            f"Principal: *{_esc(primary.direction)}* · {_esc(primary.status or '—')}"
        )
        if primary.activation_zone:
            lines.append(f"Zona: `{primary.activation_zone.format()}`")
        if primary.invalidation is not None:
            lines.append(f"Invalidación: `{primary.invalidation:g}`")
        tps = []
        if primary.tp1 is not None:
            tps.append(f"TP1 `{primary.tp1:g}`")
        if primary.tp2 is not None:
            tps.append(f"TP2 `{primary.tp2:g}`")
        if tps:
            lines.append(" · ".join(tps))

    in_zone = report.price_in_primary_zone()
    zone_txt = "SÍ" if in_zone is True else ("NO" if in_zone is False else "—")
    score = f"{report.context_score:g}" if report.context_score is not None else "—"
    lines.append("")
    lines.append(
        f"Precio: `{_price(report)}` · Score: `{score}` · En zona: *{zone_txt}*"
    )
    lines.append(f"/etr {report.asset} para detalle")
    return "\n".join(lines)


def format_full_report(report: EtrReport) -> str:
    asset = report.asset.upper()
    score = f"{report.context_score:g}" if report.context_score is not None else "—"
    lines = [
        f"📊 *ETR Market Terminal · {asset}*",
        f"*{_esc(report.label)}* · `{_price(report)}`",
    ]
    if report.updated_at:
        lines.append(f"Actualizado: {_esc(report.updated_at)}")
    lines.extend(
        [
            f"Sesgo: *{_esc(report.bias or '—')}* · Estado: *{_esc(report.estado or '—')}*",
            f"Context score: `{score}/100`",
        ]
    )
    if report.lectura_headline:
        lines.append("")
        lines.append(f"*{_esc(report.lectura_headline[:300])}*")
    if report.lectura_body:
        body = report.lectura_body[:700]
        lines.append(_esc(body))
    if report.h4_context or report.m5_execution:
        lines.append("")
        lines.append(f"H4: {_esc(report.h4_context or '—')}")
        lines.append(f"M5: {_esc(report.m5_execution or '—')}")
        if report.structure:
            lines.append(f"Estructura: {_esc(report.structure)}")

    for scenario, title in (
        (report.primary, "Escenario principal"),
        (report.alternative, "Escenario alternativo"),
    ):
        if not scenario:
            continue
        lines.append("")
        lines.append(f"*{title}* — {_esc(scenario.direction)} · {_esc(scenario.status or '—')}")
        if scenario.activation_zone:
            lines.append(f"Zona: `{scenario.activation_zone.format()}`")
        if scenario.invalidation is not None:
            lines.append(f"Invalidación: `{scenario.invalidation:g}`")
        if scenario.tp1 is not None or scenario.tp2 is not None:
            tp1 = f"{scenario.tp1:g}" if scenario.tp1 is not None else "—"
            tp2 = f"{scenario.tp2:g}" if scenario.tp2 is not None else "—"
            lines.append(f"TP1 `{tp1}` · TP2 `{tp2}`")
        if scenario.score is not None:
            lines.append(f"Score escenario: `{scenario.score:g}/100`")

    lines.append("")
    lines.append(
        "_Informativo · no es recomendación de inversión. Espera confirmaciones._"
    )
    return "\n".join(lines)


def format_compact_summary(reports: list[EtrReport]) -> str:
    if not reports:
        return "ETR: no hay reportes en caché. Prueba `/etr btc`."
    lines = ["📊 *ETR · resumen*"]
    for report in reports:
        score = f"{report.context_score:g}" if report.context_score is not None else "—"
        in_zone = report.price_in_primary_zone()
        zone_flag = "📍" if in_zone else ""
        primary_dir = report.primary.direction if report.primary else "—"
        lines.append(
            f"*{report.asset.upper()}* {zone_flag}`{_price(report)}` · "
            f"{_esc(report.bias or '—')} · {_esc(report.estado or '—')} · "
            f"score `{score}` · {_esc(primary_dir)}"
        )
    lines.append("")
    lines.append("Detalle: `/etr btc|gold|nasdaq|oil`")
    return "\n".join(lines)


def chunk_telegram(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks
