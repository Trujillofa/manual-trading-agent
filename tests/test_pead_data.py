"""Tests for the PEAD data verifier."""

from __future__ import annotations

from pathlib import Path

from research.new_edge.pead.data.verify_pead_data import audit_snapshot

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "research/new_edge/pead/data/fixtures/synthetic_minimal"
)


def test_audit_snapshot_blocks_insufficient_coverage() -> None:
    audit = audit_snapshot(
        FIXTURE_DIR,
        start="2016-01-01",
        end="2026-01-01",
        source_label="synthetic_minimal_fixture",
    )

    assert audit.verdict == "BLOCKED"
    assert audit.events_eligible >= 1
    assert audit.eligible_stocks_peak < 500
    assert any("eligible stock count" in issue for issue in audit.issues)


def test_audit_snapshot_rejects_estimate_after_announcement(tmp_path: Path) -> None:
    fixture = tmp_path / "snap"
    fixture.mkdir()
    (fixture / "security_master.csv").write_text(
        "security_id,ticker,security_type,list_date\nSEC001,AAPL,common,2010-01-01\n",
        encoding="utf-8",
    )
    (fixture / "earnings_events.csv").write_text(
        "security_id,ticker,fiscal_period,announcement_ts,estimate_observed_ts,actual_eps,consensus_eps\n"
        "SEC001,AAPL,2024Q4,2025-01-30T13:00:00+00:00,2025-01-30T14:00:00+00:00,2.0,1.9\n",
        encoding="utf-8",
    )
    (fixture / "daily_prices.csv").write_text(
        "security_id,date,open,high,low,close,volume\nSEC001,2025-01-30,1,2,1,2,1\n",
        encoding="utf-8",
    )
    (fixture / "sectors.csv").write_text(
        "security_id,as_of_date,sector\nSEC001,2025-01-01,Technology\n",
        encoding="utf-8",
    )

    audit = audit_snapshot(
        fixture,
        start="2016-01-01",
        end="2026-01-01",
        source_label="tmp",
    )

    assert audit.verdict == "BLOCKED"
    assert audit.events_eligible == 0
    assert any("estimate observed at or after announcement" in issue for issue in audit.issues)
