"""Forward paper-shadow open/resolve tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.etr.diff import diff_reports
from src.etr.models import EtrChange, EtrReport, EtrScenario, PriceZone
from src.etr.shadow import format_shadow_summary, process_shadow_for_report, shadow_summary


@pytest.fixture
def shadow_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path))
    return tmp_path


def _report(price: float, **overrides: object) -> EtrReport:
    primary = EtrScenario(
        name="Principal",
        direction="Bajista",
        status="Esperando",
        role="Principal",
        activation_zone=PriceZone(64000.0, 66000.0),
        invalidation=67000.0,
        tp1=62000.0,
        tp2=60000.0,
        score=90.0,
    )
    base = EtrReport(
        asset="btc",
        label="Bitcoin",
        price=price,
        updated_at="t",
        context_score=70.0,
        bias="bajista",
        estado="Zona",
        lectura_headline="h",
        lectura_body="b",
        h4_context="Bajista",
        m5_execution="Mixto",
        structure="BOS",
        primary=primary,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_zone_entry_opens_event(shadow_logs: Path) -> None:
    prev = _report(63000.0)
    curr = _report(65000.0)
    changes = diff_reports(prev, curr, prev_in_zone=False)
    summary = process_shadow_for_report(
        curr, changes=changes, now_iso="2026-08-12T12:00:00+00:00"
    )
    assert summary["opened"] == 1
    open_file = shadow_logs / "etr_shadow_open.json"
    assert open_file.exists()
    polls = (shadow_logs / "etr_shadow_polls.jsonl").read_text()
    assert "btc" in polls


def test_hit_tp1_resolves(shadow_logs: Path) -> None:
    prev = _report(63000.0)
    in_zone = _report(65000.0)
    changes = diff_reports(prev, in_zone, prev_in_zone=False)
    process_shadow_for_report(in_zone, changes=changes, now_iso="2026-08-12T12:00:00+00:00")

    # Price moves to TP1 for short
    at_tp = _report(61900.0)
    summary = process_shadow_for_report(
        at_tp, changes=[], now_iso="2026-08-12T13:00:00+00:00"
    )
    assert summary["resolved"] == 1
    events = (shadow_logs / "etr_shadow_events.jsonl").read_text()
    assert "hit_tp1" in events
    stats = shadow_summary()
    assert stats["closed"] == 1
    assert stats["open"] == 0
    assert "TP1" in format_shadow_summary() or "hit_tp1" in format_shadow_summary()


def test_invalidation_resolves(shadow_logs: Path) -> None:
    prev = _report(63000.0)
    in_zone = _report(65000.0)
    changes = diff_reports(prev, in_zone, prev_in_zone=False)
    process_shadow_for_report(in_zone, changes=changes, now_iso="2026-08-12T12:00:00+00:00")

    stopped = _report(67100.0)
    summary = process_shadow_for_report(
        stopped, changes=[], now_iso="2026-08-12T14:00:00+00:00"
    )
    assert summary["resolved"] == 1
    assert "hit_invalidation" in (shadow_logs / "etr_shadow_events.jsonl").read_text()


def test_no_open_without_zone_entry(shadow_logs: Path) -> None:
    report = _report(63000.0)
    summary = process_shadow_for_report(
        report,
        changes=[EtrChange(field="bias", old="a", new="b")],
        now_iso="2026-08-12T12:00:00+00:00",
    )
    assert summary["opened"] == 0
