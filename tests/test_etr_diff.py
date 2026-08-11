"""Change detection for ETR reports."""

from __future__ import annotations

from src.etr.diff import diff_reports
from src.etr.models import EtrReport, EtrScenario, PriceZone


def _report(**overrides: object) -> EtrReport:
    primary = EtrScenario(
        name="Principal",
        direction="Bajista",
        status="Esperando confirmación",
        role="Principal",
        activation_zone=PriceZone(63900.6, 64131.2),
        invalidation=64131.2,
        tp1=63261.3,
        tp2=62669.4,
        score=88.0,
    )
    base = EtrReport(
        asset="btc",
        label="Bitcoin",
        price=63715.4,
        updated_at="11/08/2026",
        context_score=72.0,
        bias="bajista",
        estado="Zona de reacción",
        lectura_headline="headline",
        lectura_body="body",
        h4_context="Bajista",
        m5_execution="Alcista",
        structure="Bajista",
        primary=primary,
        alternative=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_price_only_change_is_not_alerted() -> None:
    prev = _report(price=63715.4)
    curr = _report(price=63800.0)
    assert diff_reports(prev, curr) == []


def test_bias_flip_is_action() -> None:
    prev = _report(bias="bajista")
    curr = _report(bias="alcista")
    changes = diff_reports(prev, curr)
    assert any(c.field == "bias" and c.severity == "action" for c in changes)


def test_invalidation_change() -> None:
    prev = _report()
    new_primary = EtrScenario(
        name="Principal",
        direction="Bajista",
        status="Esperando confirmación",
        role="Principal",
        activation_zone=PriceZone(63900.6, 64131.2),
        invalidation=65000.0,
        tp1=63261.3,
        tp2=62669.4,
        score=88.0,
    )
    curr = _report(primary=new_primary)
    changes = diff_reports(prev, curr)
    assert any(c.field == "primary_invalidation" for c in changes)


def test_enter_primary_zone() -> None:
    prev = _report(price=63700.0)
    curr = _report(price=64000.0)
    changes = diff_reports(prev, curr, prev_in_zone=False)
    assert any(c.field == "price_in_primary_zone" and c.new == "yes" for c in changes)


def test_score_delta_alerts() -> None:
    prev = _report(context_score=72.0)
    curr = _report(context_score=55.0)
    changes = diff_reports(prev, curr, score_delta=10.0)
    assert any(c.field == "context_score" for c in changes)


def test_first_report_no_diff() -> None:
    curr = _report()
    assert diff_reports(None, curr) == []
