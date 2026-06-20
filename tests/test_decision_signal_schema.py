"""Tests for forex DecisionSignal JSONL schema validation."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.evaluation.decision_signal_schema import (
    ENGINE_VERSION,
    parse_decision_signal_jsonl_line,
    validate_decision_signal,
    validate_decision_signal_jsonl,
)


def _valid_signal(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "decision_signal",
        "signal_id": "8f3c2e1a-4b5d-6c7e-8f9a-0b1c2d3e4f5a",
        "ts": "2026-06-20T08:15:00Z",
        "symbol": "EUR/USD",
        "direction": "BUY",
        "action": "alert",
        "source": "branch_b_scan",
        "status": "active",
        "engine_version": ENGINE_VERSION,
        "evidence_summary": "M15/M30/H1 RSI aligned; 20-bar low wick reclaim; ADX 22",
        "watch_conditions": ["Invalidate on 15m RSI cross below 50"],
        "risk_summary": "Spread 1.1 pips; news clear; London session",
        "entry_ref_price": 1.0850,
        "tp_pips": 20.0,
        "sl_pips": 30.0,
        "data_quality": {
            "overall_level": "good",
            "limitations": [],
            "blocks": {
                "ohlc_m15": {"status": "available", "latest_bar_ts": "2026-06-20T08:00:00Z"},
                "ohlc_m30": {"status": "available", "latest_bar_ts": "2026-06-20T08:00:00Z"},
                "ohlc_h1": {"status": "available", "latest_bar_ts": "2026-06-20T08:00:00Z"},
                "spread": {"status": "available", "value_pips": 1.1},
                "news": {"status": "available", "blocked": False, "summary": "clear"},
                "session": {"status": "available", "name": "london"},
                "broker_account": {"status": "available", "trading_allowed": True},
            },
        },
    }
    base.update(overrides)
    return base


def test_contract_example_record_validates() -> None:
    record = validate_decision_signal(_valid_signal())
    assert record.symbol == "EUR/USD"
    assert record.action == "alert"
    assert record.engine_version == ENGINE_VERSION
    assert record.data_quality.overall_level == "good"
    assert record.ts.tzinfo == UTC


def test_ts_with_z_suffix_passes() -> None:
    record = validate_decision_signal(_valid_signal(ts="2026-06-20T08:15:00Z"))
    assert record.ts.tzinfo == UTC


def test_ts_with_utc_offset_passes() -> None:
    record = validate_decision_signal(_valid_signal(ts="2026-06-20T08:15:00+00:00"))
    assert record.ts.tzinfo == UTC


def test_naive_ts_fails() -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(ts="2026-06-20T08:15:00"))


def test_non_utc_ts_fails() -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(ts="2026-06-20T08:15:00+02:00"))


def test_expires_at_with_z_passes() -> None:
    record = validate_decision_signal(_valid_signal(expires_at="2026-06-21T08:15:00Z"))
    assert record.expires_at is not None
    assert record.expires_at.tzinfo == UTC


def test_naive_expires_at_fails() -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(expires_at="2026-06-21T08:15:00"))


def test_non_utc_expires_at_fails() -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(expires_at="2026-06-21T08:15:00-05:00"))


def test_symbol_normalizes_to_uppercase() -> None:
    record = validate_decision_signal(_valid_signal(symbol="eur/usd"))
    assert record.symbol == "EUR/USD"


@pytest.mark.parametrize(
    "field",
    [
        "signal_id",
        "ts",
        "symbol",
        "direction",
        "action",
        "source",
        "status",
        "engine_version",
        "evidence_summary",
        "data_quality",
    ],
)
def test_missing_required_field_fails(field: str) -> None:
    payload = _valid_signal()
    payload.pop(field)
    with pytest.raises(ValidationError):
        validate_decision_signal(payload)


@pytest.mark.parametrize("action", ["buy", "sell", "hold", "reduce"])
def test_disallowed_action_fails(action: str) -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(action=action))


@pytest.mark.parametrize("direction", ["LONG", "SHORT", "buy"])
def test_invalid_direction_fails(direction: str) -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(direction=direction))


def test_evidence_summary_max_length_enforced() -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(evidence_summary="x" * 501))


def test_risk_summary_max_length_enforced() -> None:
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(risk_summary="x" * 301))


def test_unknown_data_quality_block_key_fails() -> None:
    payload = _valid_signal()
    dq = dict(payload["data_quality"])  # type: ignore[arg-type]
    blocks = dict(dq["blocks"])  # type: ignore[index]
    blocks["crypto_feed"] = {"status": "available"}
    dq["blocks"] = blocks
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(data_quality=dq))


def test_block_requires_status_field() -> None:
    payload = _valid_signal()
    dq = dict(payload["data_quality"])  # type: ignore[arg-type]
    dq["blocks"] = {"spread": {"value_pips": 1.2}}
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(data_quality=dq))


def test_parse_jsonl_line_round_trip() -> None:
    payload = _valid_signal()
    line = json.dumps(payload)
    record = parse_decision_signal_jsonl_line(line, line_no=3)
    assert str(record.signal_id) == payload["signal_id"]
    assert record.ts.isoformat().startswith("2026-06-20T08:15:00")
    assert record.ts.tzinfo == UTC


def test_parse_jsonl_line_reports_line_number_on_error() -> None:
    with pytest.raises(Exception) as exc_info:
        parse_decision_signal_jsonl_line('{"kind":"decision_signal"}', line_no=7)
    assert "line 7" in str(exc_info.value)


def test_validate_jsonl_skips_non_signal_rows(tmp_path: Path) -> None:
    audit = tmp_path / "signal_audit.jsonl"
    audit.write_text(
        "\n".join(
            [
                json.dumps({"kind": "scan_telemetry", "ts": "2026-06-20T08:00:00Z"}),
                json.dumps(_valid_signal()),
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_decision_signal_jsonl(audit)
    assert report.ok
    assert report.validated_signals == 1
    assert report.skipped_rows == 2


def test_validate_jsonl_collects_errors(tmp_path: Path) -> None:
    audit = tmp_path / "signal_audit.jsonl"
    audit.write_text(
        json.dumps({"kind": "decision_signal", "symbol": "EUR/USD"}) + "\n",
        encoding="utf-8",
    )

    report = validate_decision_signal_jsonl(audit)
    assert not report.ok
    assert report.validated_signals == 0
    assert len(report.errors) == 1
    assert report.errors[0].line_no == 1


def test_validate_jsonl_missing_file(tmp_path: Path) -> None:
    report = validate_decision_signal_jsonl(tmp_path / "missing.jsonl")
    assert not report.ok
    assert report.errors[0].line_no == 0
