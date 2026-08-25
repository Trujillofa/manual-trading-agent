"""Tests for Branch B DecisionSignal audit wiring."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from src.evaluation.branch_b_audit import record_branch_b_scan_decision_signal
from src.evaluation.decision_signal_schema import (
    KIND_DECISION_SIGNAL,
    validate_decision_signal_jsonl,
)
from src.scanner.state import _append_audit_log
from src.scanner.telemetry import _build_scan_telemetry_payload


def _utc_df(*, periods: int = 4, freq: str = "15min") -> pd.DataFrame:
    index = pd.date_range("2026-06-20T07:00:00Z", periods=periods, freq=freq, tz="UTC")
    values = [1.0850 + (i * 0.0001) for i in range(periods)]
    return pd.DataFrame(
        {"close": values, "high": values, "low": values},
        index=index,
    )


def _telemetry_payload(
    *,
    state: str,
    direction: str,
    reasons: list[str] | None = None,
) -> dict[str, object]:
    return _build_scan_telemetry_payload(
        ts="2026-06-20T08:15:00+00:00",
        scan_run_id="scan-2026-06-20T08:15:00+00:00",
        pair="EUR/USD",
        state=state,
        direction=direction,
        aligned=state in {"entry", "aligned_pending_breakout", "blocked"},
        breakout_pending=state == "aligned_pending_breakout",
        entry_triggered=state == "entry",
        bars_aligned=2,
        confirm_bars=3,
        within_confirm_window=True,
        spread_pips=1.1,
        max_spread_pips=2.0,
        spread_source="ctrader",
        adx_1h=22.0,
        is_ranging=True,
        rsi_1h=28.0,
        rsi_30m=27.0,
        rsi_15m=26.0,
        no_trade_reasons=reasons or [],
    )


def _record_for_state(
    tmp_path: Path,
    *,
    state: str,
    direction: str = "BUY",
    signal_reasons: list[str] | None = None,
    no_trade_reasons: list[str] | None = None,
    missing_timeframes: list[str] | None = None,
    distance: float = 2.0,
) -> dict[str, object]:
    audit = tmp_path / "signal_audit.jsonl"
    telemetry = _telemetry_payload(
        state=state,
        direction=direction,
        reasons=no_trade_reasons,
    )
    record_branch_b_scan_decision_signal(
        ts=datetime(2026, 6, 20, 8, 15, tzinfo=UTC),
        pair="EUR/USD",
        scan_run_id="scan-2026-06-20T08:15:00+00:00",
        telemetry_state=state,
        direction=direction,
        telemetry_payload=telemetry,
        data_1h=_utc_df(periods=4, freq="1h"),
        data_30m=_utc_df(periods=4, freq="30min"),
        data_15m=_utc_df(),
        signal_reasons=signal_reasons,
        no_trade_reasons=no_trade_reasons,
        missing_timeframes=missing_timeframes,
        distance=distance,
        breakout_pending=state == "aligned_pending_breakout",
        news_blocked=False,
        audit_path=audit,
    )
    rows = [
        json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    decision_rows = [row for row in rows if row.get("kind") == KIND_DECISION_SIGNAL]
    assert len(decision_rows) == 1
    return decision_rows[0]


def test_entry_scan_appends_decision_signal_alert(tmp_path: Path) -> None:
    row = _record_for_state(
        tmp_path,
        state="entry",
        signal_reasons=["M15/M30/H1 RSI aligned", "20-bar low wick reclaim"],
    )
    assert row["action"] == "alert"
    assert row["direction"] == "BUY"
    assert row["source"] == "branch_b_scan"


def test_watch_scan_appends_decision_signal_watch(tmp_path: Path) -> None:
    row = _record_for_state(
        tmp_path,
        state="watch",
        missing_timeframes=["15m"],
    )
    assert row["action"] == "watch"


def test_aligned_pending_breakout_appends_decision_signal_watch(tmp_path: Path) -> None:
    row = _record_for_state(tmp_path, state="aligned_pending_breakout")
    assert row["action"] == "watch"
    assert row["metadata"]["breakout_pending"] is True


def test_blocked_candidate_appends_decision_signal_avoid(tmp_path: Path) -> None:
    row = _record_for_state(
        tmp_path,
        state="blocked",
        no_trade_reasons=["Blocked by high-impact news", "Spread unavailable/too wide"],
    )
    assert row["action"] == "avoid"
    assert "Blocked by high-impact news" in row["evidence_summary"]


def test_existing_scan_telemetry_rows_still_append_as_before(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path))
    audit = tmp_path / "signal_audit.jsonl"
    telemetry = _telemetry_payload(state="entry", direction="BUY")

    _append_audit_log(telemetry)
    record_branch_b_scan_decision_signal(
        ts=datetime(2026, 6, 20, 8, 15, tzinfo=UTC),
        pair="EUR/USD",
        scan_run_id="scan-2026-06-20T08:15:00+00:00",
        telemetry_state="entry",
        direction="BUY",
        telemetry_payload=telemetry,
        data_1h=_utc_df(periods=4, freq="1h"),
        data_30m=_utc_df(periods=4, freq="30min"),
        data_15m=_utc_df(),
        signal_reasons=["M15/M30/H1 RSI aligned"],
        audit_path=audit,
    )

    rows = [
        json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert rows[0]["kind"] == "scan_telemetry"
    assert rows[0]["state"] == "entry"
    assert rows[1]["kind"] == KIND_DECISION_SIGNAL


def test_builder_failure_does_not_prevent_legacy_telemetry_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(tmp_path))
    audit = tmp_path / "signal_audit.jsonl"
    telemetry = _telemetry_payload(state="entry", direction="BUY")

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise ValueError("builder failed")

    monkeypatch.setattr(
        "src.evaluation.branch_b_audit.build_branch_b_decision_signal",
        _raise,
    )

    _append_audit_log(telemetry)
    recorded = record_branch_b_scan_decision_signal(
        ts=datetime(2026, 6, 20, 8, 15, tzinfo=UTC),
        pair="EUR/USD",
        scan_run_id="scan-2026-06-20T08:15:00+00:00",
        telemetry_state="entry",
        direction="BUY",
        telemetry_payload=telemetry,
        data_1h=_utc_df(periods=4, freq="1h"),
        data_30m=_utc_df(periods=4, freq="30min"),
        data_15m=_utc_df(),
        signal_reasons=["M15/M30/H1 RSI aligned"],
        audit_path=audit,
    )

    assert recorded is False
    rows = [
        json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["kind"] == "scan_telemetry"


def test_cli_has_no_telegram_behavior_changes() -> None:
    cli_source = Path("src/cli.py").read_text(encoding="utf-8")
    scan_source = Path("src/scanner/scan_service.py").read_text(encoding="utf-8")
    assert "run_scan(" in cli_source
    assert "await notifier.send_signal(" in scan_source
    assert "record_branch_b_scan_decision_signal(" in scan_source
    assert scan_source.index("record_branch_b_scan_decision_signal(") > scan_source.index(
        "_append_audit_log(telemetry_payload)"
    )


def test_evaluator_module_unchanged_by_audit_wiring() -> None:
    evaluator_source = Path("src/scanner/evaluator.py").read_text(encoding="utf-8")
    assert "record_branch_b_scan_decision_signal" not in evaluator_source
    assert "build_branch_b_decision_signal" not in evaluator_source


def test_recorded_rows_validate_against_schema(tmp_path: Path) -> None:
    audit = tmp_path / "signal_audit.jsonl"
    _record_for_state(
        tmp_path,
        state="entry",
        signal_reasons=["M15/M30/H1 RSI aligned"],
    )
    report = validate_decision_signal_jsonl(audit)
    assert report.ok
    assert report.validated_signals == 1


def test_ohlc_latest_bar_ts_are_utc_strings(tmp_path: Path) -> None:
    row = _record_for_state(
        tmp_path,
        state="watch",
        missing_timeframes=["30m"],
    )
    for block_key in ("ohlc_m15", "ohlc_m30", "ohlc_h1"):
        latest = row["data_quality"]["blocks"][block_key]["latest_bar_ts"]
        assert isinstance(latest, str)
        assert latest.endswith("Z")
