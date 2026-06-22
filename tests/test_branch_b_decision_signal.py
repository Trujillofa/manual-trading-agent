"""Tests for Branch B DecisionSignal payload builder."""

from __future__ import annotations

import ast
import importlib
import inspect
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.evaluation.branch_b_decision_signal import (
    BranchBScanContextError,
    build_branch_b_decision_signal,
    normalize_fx_symbol,
    normalize_utc_timestamp,
)
from src.evaluation.decision_signal_schema import validate_decision_signal


def _base_context(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "ts": datetime(2026, 6, 20, 8, 15, tzinfo=UTC),
        "pair": "EURUSD",
        "direction": "BUY",
        "scan_state": "entry",
        "scan_run_id": "scan-2026-06-20T08:15:00Z",
        "signal_id": "8f3c2e1a-4b5d-6c7e-8f9a-0b1c2d3e4f5a",
        "signal_reasons": [
            "M15/M30/H1 RSI aligned",
            "20-bar low wick reclaim",
            "ADX 22",
        ],
        "rsi_1h": 28.5,
        "rsi_30m": 27.1,
        "rsi_15m": 26.4,
        "adx_1h": 22.0,
        "spread_pips": 1.1,
        "spread_source": "ctrader",
        "news_blocked": False,
        "news_summary": "clear",
        "session_name": "london",
        "trading_allowed": True,
        "entry_ref_price": 1.0850,
        "tp_pips": 20.0,
        "sl_pips": 30.0,
        "confidence": 0.82,
        "profile": "V2_b0.5_c3",
        "ohlc_m15": {
            "status": "available",
            "bar_count": 120,
            "latest_bar_ts": "2026-06-20T08:00:00Z",
        },
        "ohlc_m30": {
            "status": "available",
            "bar_count": 80,
            "latest_bar_ts": "2026-06-20T08:00:00Z",
        },
        "ohlc_h1": {
            "status": "available",
            "bar_count": 48,
            "latest_bar_ts": "2026-06-20T08:00:00Z",
        },
    }
    base.update(overrides)
    return base


def test_builds_valid_alert_signal_payload() -> None:
    payload = build_branch_b_decision_signal(_base_context())
    record = validate_decision_signal(payload)

    assert record.action == "alert"
    assert record.source == "branch_b_scan"
    assert record.status == "active"
    assert record.engine_version == "forex-decision-signal-v1"
    assert record.symbol == "EUR/USD"
    assert record.direction == "BUY"
    assert record.evidence_summary.startswith("M15/M30/H1 RSI aligned")
    assert record.risk_summary is not None
    assert "Spread 1.1 pips" in record.risk_summary
    assert record.data_quality.overall_level == "good"
    assert set(record.data_quality.blocks) == {
        "ohlc_m15",
        "ohlc_m30",
        "ohlc_h1",
        "spread",
        "news",
        "session",
        "broker_account",
    }
    assert record.metadata is not None
    assert record.metadata["rsi_1h"] == 28.5
    assert record.metadata["profile"] == "V2_b0.5_c3"


def test_builds_valid_watch_signal_payload() -> None:
    payload = build_branch_b_decision_signal(
        _base_context(
            scan_state="watch",
            direction="SELL",
            pair="gbp/usd",
            missing_timeframes=["15m"],
            distance=2.5,
            breakout_pending=False,
            signal_reasons=None,
        )
    )
    record = validate_decision_signal(payload)

    assert record.action == "watch"
    assert record.symbol == "GBP/USD"
    assert record.direction == "SELL"
    assert "setup forming" in record.evidence_summary
    assert record.watch_conditions is not None
    assert any("Align remaining timeframes" in item for item in record.watch_conditions)


def test_builds_valid_avoid_signal_for_blocked_context() -> None:
    payload = build_branch_b_decision_signal(
        _base_context(
            scan_state="blocked",
            no_trade_reasons=["Blocked by high-impact news", "Spread unavailable/too wide"],
            blockers={"news": True, "spread_unavailable_or_too_wide": True},
            news_blocked=True,
            news_summary="NFP in 12m",
        )
    )
    record = validate_decision_signal(payload)

    assert record.action == "avoid"
    assert "Avoid BUY" in record.evidence_summary
    assert "Blocked by high-impact news" in record.evidence_summary
    assert record.metadata is not None
    assert record.metadata["blocker_codes"] == ["news", "spread_unavailable_or_too_wide"]
    assert record.watch_conditions is None


def test_normalizes_symbols_and_utc_timestamps() -> None:
    payload = build_branch_b_decision_signal(
        _base_context(
            pair="eur-usd",
            ts="2026-06-20T08:15:00+00:00",
        )
    )
    record = validate_decision_signal(payload)

    assert record.symbol == "EUR/USD"
    assert record.ts == datetime(2026, 6, 20, 8, 15, tzinfo=UTC)


def test_normalize_fx_symbol_and_timestamp_helpers() -> None:
    assert normalize_fx_symbol("eurusd") == "EUR/USD"
    assert normalize_fx_symbol("EUR/USD") == "EUR/USD"
    assert normalize_utc_timestamp("2026-06-20T08:15:00Z") == datetime(
        2026, 6, 20, 8, 15, tzinfo=UTC
    )


def test_does_not_write_to_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit = tmp_path / "signal_audit.jsonl"
    monkeypatch.chdir(tmp_path)

    payload = build_branch_b_decision_signal(_base_context())
    validate_decision_signal(payload)

    assert not audit.exists()
    source = inspect.getsource(build_branch_b_decision_signal)
    assert "record_decision_signal" not in source
    assert ".open(" not in source


def test_module_has_no_forbidden_imports() -> None:
    module_path = Path("src/evaluation/branch_b_decision_signal.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_modules = (
        "src.notifications",
        "src.notifications.telegram",
        "src.cli",
        "src.strategy",
    )
    for forbidden in forbidden_modules:
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for module in imported_modules
        )

    module = importlib.import_module("src.evaluation.branch_b_decision_signal")
    source = inspect.getsource(module)
    forbidden_tokens = [
        "record_decision_signal",
        "telegram",
        "send_signal",
        "execute_order",
        "promote_strategy",
    ]
    for token in forbidden_tokens:
        assert token not in source


@pytest.mark.parametrize("missing_field", ["ts", "pair", "direction", "scan_state"])
def test_rejects_missing_required_context(missing_field: str) -> None:
    context = _base_context()
    context.pop(missing_field)
    with pytest.raises(BranchBScanContextError):
        build_branch_b_decision_signal(context)  # type: ignore[arg-type]


def test_rejects_alert_without_signal_reasons() -> None:
    context = _base_context(signal_reasons=[])
    with pytest.raises(BranchBScanContextError, match="signal_reasons"):
        build_branch_b_decision_signal(context)


def test_rejects_blocked_without_no_trade_reasons() -> None:
    context = _base_context(scan_state="blocked", no_trade_reasons=[])
    with pytest.raises(BranchBScanContextError, match="no_trade_reasons"):
        build_branch_b_decision_signal(context)


def test_rejects_invalid_pair_and_non_utc_timestamp() -> None:
    with pytest.raises(BranchBScanContextError):
        normalize_fx_symbol("NOTPAIR")
    with pytest.raises(BranchBScanContextError):
        normalize_utc_timestamp("2026-06-20T08:15:00")


def test_generates_signal_id_when_missing() -> None:
    context = _base_context()
    context.pop("signal_id")
    payload = build_branch_b_decision_signal(context)
    UUID(str(payload["signal_id"]))


def test_rejects_invalid_data_quality_block_status() -> None:
    context = _base_context(
        ohlc_m15={"status": "bad_status", "bar_count": 120},
    )
    with pytest.raises(ValidationError):
        build_branch_b_decision_signal(context)
