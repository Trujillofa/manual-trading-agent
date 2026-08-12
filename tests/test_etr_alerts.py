"""Alert formatting tests."""

from __future__ import annotations

from src.etr.alerts import (
    chunk_telegram,
    format_change_alert,
    format_compact_summary,
    format_full_report,
)
from src.etr.models import EtrChange, EtrReport, EtrScenario, PriceZone


def _sample() -> EtrReport:
    return EtrReport(
        asset="btc",
        label="Bitcoin",
        price=63715.4,
        updated_at="11/08/2026",
        context_score=72.0,
        bias="bajista",
        estado="Zona de reacción",
        lectura_headline="Bitcoin en soporte clave",
        lectura_body="Contexto 4H bajista con BOS.",
        h4_context="Bajista · RSI 34",
        m5_execution="Alcista · RSI 61",
        structure="BOS Bajista",
        primary=EtrScenario(
            name="Escenario de continuación o reacción bajista",
            direction="Bajista",
            status="Esperando confirmación",
            role="Principal",
            activation_zone=PriceZone(63900.6, 64131.2),
            invalidation=64131.2,
            tp1=63261.3,
            tp2=62669.4,
            score=88.0,
        ),
    )


def test_change_alert_contains_fields() -> None:
    msg = format_change_alert(
        _sample(),
        [EtrChange(field="bias", old="alcista", new="bajista", severity="action")],
    )
    assert "ETR" in msg
    assert "BTC" in msg
    assert "bajista" in msg
    assert len(msg) < 4000
    # Code spans must not Markdown-escape (would show as \_)
    assert "\\_" not in msg
    assert "`alcista`" in msg
    assert "`bajista`" in msg


def test_change_alert_no_escape_inside_code_with_underscore() -> None:
    """Values with underscores stay literal inside backticks."""
    msg = format_change_alert(
        _sample(),
        [
            EtrChange(
                field="primary_status",
                old="waiting_confirm",
                new="active_now",
                severity="info",
            )
        ],
    )
    assert "`waiting_confirm`" in msg
    assert "`active_now`" in msg
    assert "\\_" not in msg


def test_full_report_has_disclaimer() -> None:
    msg = format_full_report(_sample())
    assert "recomendación" in msg.lower() or "informativo" in msg.lower()
    assert "64131.2" in msg or "64131" in msg


def test_chunk_telegram() -> None:
    text = "x" * 9000
    chunks = chunk_telegram(text, limit=4000)
    assert len(chunks) >= 3
    assert all(len(c) <= 4000 for c in chunks)


def test_compact_summary() -> None:
    msg = format_compact_summary([_sample()])
    assert "BTC" in msg
