"""Service-layer alert gating (diff-only, no fingerprint suppress)."""

from __future__ import annotations

from src.etr.alerts import telegram_alert_changes
from src.etr.diff import diff_reports
from src.etr.models import EtrReport, EtrScenario, PriceZone


def _report(**overrides: object) -> EtrReport:
    primary = EtrScenario(
        name="Principal",
        direction="Bajista",
        status="Esperando confirmación",
        role="Principal",
        activation_zone=PriceZone(64000.0, 66000.0),
        invalidation=66000.0,
        tp1=62000.0,
        tp2=60000.0,
        score=88.0,
    )
    base = EtrReport(
        asset="btc",
        label="Bitcoin",
        price=63000.0,
        updated_at="t",
        context_score=72.0,
        bias="bajista",
        estado="Zona de reacción",
        lectura_headline="h",
        lectura_body="b",
        h4_context="Bajista",
        m5_execution="Alcista",
        structure="BOS",
        primary=primary,
        alternative=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _should_alert(changes: list) -> bool:
    """Mirrors service.py: Telegram fires only on thesis fields."""
    return bool(telegram_alert_changes(changes))


def test_zone_entry_alerts_even_when_fingerprint_unchanged() -> None:
    prev = _report(price=63000.0)
    curr = _report(price=65000.0)
    assert prev.fingerprint() == curr.fingerprint()  # price excluded
    changes = diff_reports(prev, curr, prev_in_zone=False)
    assert any(c.field == "price_in_primary_zone" for c in changes)
    assert _should_alert(changes) is True


def test_score_delta_within_bucket_audited_but_not_telegraphed() -> None:
    prev = _report(context_score=72.0)
    curr = _report(context_score=62.0)
    # Both mid under default 50/80 buckets
    assert prev.score_bucket() == curr.score_bucket() == "mid"
    assert prev.fingerprint() == curr.fingerprint()
    changes = diff_reports(prev, curr, score_delta=10.0)
    assert any(c.field == "context_score" for c in changes)
    assert _should_alert(changes) is False


def test_estado_only_change_not_telegraphed() -> None:
    prev = _report(estado="Zona de reacción")
    curr = _report(estado="Zona de oferta")
    changes = diff_reports(prev, curr)
    assert any(c.field == "estado" for c in changes)
    assert _should_alert(changes) is False


def test_bias_change_is_telegraphed() -> None:
    prev = _report(bias="bajista")
    curr = _report(bias="alcista")
    changes = diff_reports(prev, curr)
    notify = telegram_alert_changes(changes)
    assert [c.field for c in notify] == ["bias"]
    assert _should_alert(changes) is True


def test_price_only_tick_no_alert() -> None:
    prev = _report(price=63000.0)
    curr = _report(price=63100.0)  # still outside zone
    changes = diff_reports(prev, curr, prev_in_zone=False)
    assert changes == []
    assert _should_alert(changes) is False
