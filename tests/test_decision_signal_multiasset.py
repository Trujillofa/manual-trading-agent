"""Decision-signal validation accepts multi-asset registry IDs (not only FX pairs)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from pydantic import ValidationError

from src.config.instruments import get_instrument, reset_registry_to_defaults
from src.evaluation.branch_b_audit import record_branch_b_scan_decision_signal
from src.evaluation.branch_b_decision_signal import (
    BranchBScanContextError,
    build_branch_b_decision_signal,
    normalize_fx_symbol,
)
from src.evaluation.decision_signal_schema import (
    ENGINE_VERSION,
    KIND_DECISION_SIGNAL,
    normalize_decision_symbol,
    parse_decision_signal_jsonl_line,
    validate_decision_signal,
    validate_decision_signal_jsonl,
)
from src.scanner.telemetry import _build_scan_telemetry_payload


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_registry_to_defaults()
    yield
    reset_registry_to_defaults()


def _valid_signal(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": KIND_DECISION_SIGNAL,
        "signal_id": str(uuid4()),
        "ts": "2026-08-10T22:20:57Z",
        "symbol": "EUR/USD",
        "direction": "SELL",
        "action": "watch",
        "source": "branch_b_scan",
        "status": "active",
        "engine_version": ENGINE_VERSION,
        "evidence_summary": "setup forming",
        "data_quality": {
            "overall_level": "limited",
            "limitations": ["spread: missing"],
            "blocks": {
                "ohlc_m15": {"status": "available"},
                "ohlc_m30": {"status": "available"},
                "ohlc_h1": {"status": "available"},
                "spread": {"status": "missing"},
                "news": {"status": "available", "blocked": False, "summary": "clear"},
                "session": {"status": "available", "name": "off_hours"},
                "broker_account": {"status": "missing"},
            },
        },
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("XAU/USD", "XAU/USD"),
        ("BTC/USD", "BTC/USD"),
        ("OIL", "OIL"),
        ("NASDAQ", "NASDAQ"),
        ("oil", "OIL"),  # strip/upper then registry
        ("nasdaq", "NASDAQ"),
        ("xau/usd", "XAU/USD"),
    ],
)
def test_normalize_decision_symbol_accepts_watchlist_ids(
    symbol: str, expected: str
) -> None:
    assert normalize_decision_symbol(symbol) == expected
    assert normalize_fx_symbol(symbol) == expected


@pytest.mark.parametrize("symbol", ["EUR/USD", "GBP/JPY", "eur/usd"])
def test_legacy_fx_pairs_still_pass(symbol: str) -> None:
    normalized = normalize_decision_symbol(symbol)
    assert len(normalized) == 7 and normalized[3] == "/"
    record = validate_decision_signal(_valid_signal(symbol=symbol))
    assert record.symbol == normalized


@pytest.mark.parametrize("junk", ["FOO", "eurusd", "EUR-USD", "NOT_A_PAIR"])
def test_junk_symbols_fail_schema_validation(junk: str) -> None:
    with pytest.raises(ValueError):
        normalize_decision_symbol(junk)
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(symbol=junk))


@pytest.mark.parametrize("junk", ["FOO", "EUR-USD", "NOT_A_PAIR", "NOTPAIR"])
def test_junk_symbols_fail_branch_b_normalize(junk: str) -> None:
    with pytest.raises(BranchBScanContextError):
        normalize_fx_symbol(junk)


def test_eurusd_compact_fails_schema_but_branch_b_helper_still_expands() -> None:
    """Schema is slash-FX or registry only; branch_b keeps EURUSD→EUR/USD for helpers."""
    with pytest.raises(ValidationError):
        validate_decision_signal(_valid_signal(symbol="eurusd"))
    assert normalize_fx_symbol("eurusd") == "EUR/USD"


def test_upper_does_not_break_registry_ids() -> None:
    """Registry ids are already uppercase; upper() is idempotent and returns canonical id."""
    for raw in ("OIL", "oil", "Oil", "NASDAQ", "nasdaq"):
        out = normalize_decision_symbol(raw)
        inst = get_instrument(out)
        assert inst.id == out
        assert out == out.upper() or "/" in out


@pytest.mark.parametrize("symbol", ["XAU/USD", "BTC/USD", "OIL", "NASDAQ"])
def test_schema_validates_all_four_watchlist_ids(symbol: str) -> None:
    record = validate_decision_signal(_valid_signal(symbol=symbol))
    assert record.symbol == symbol


@pytest.mark.parametrize("symbol", ["XAU/USD", "BTC/USD", "OIL", "NASDAQ"])
def test_branch_b_builder_accepts_all_four_watchlist_ids(symbol: str) -> None:
    payload = build_branch_b_decision_signal(
        {
            "ts": datetime(2026, 8, 10, 22, 20, 57, tzinfo=UTC),
            "pair": symbol,
            "direction": "SELL",
            "scan_state": "watch",
            "missing_timeframes": ["15m"],
            "distance": 4.0,
        }
    )
    assert payload["symbol"] == symbol
    assert payload["action"] == "watch"


def test_jsonl_replay_accepts_oil_and_historical_fx(tmp_path: Path) -> None:
    audit = tmp_path / "signal_audit.jsonl"
    oil_line = json.dumps(
        _valid_signal(symbol="OIL", signal_id=str(uuid4())),
        separators=(",", ":"),
    )
    fx_line = json.dumps(
        _valid_signal(symbol="EUR/USD", signal_id=str(uuid4())),
        separators=(",", ":"),
    )
    # Mixed log: telemetry + decision signals
    audit.write_text(
        "\n".join(
            [
                json.dumps({"kind": "scan_telemetry", "pair": "OIL", "state": "watch"}),
                oil_line,
                fx_line,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    oil_rec = parse_decision_signal_jsonl_line(oil_line, line_no=2)
    assert oil_rec.symbol == "OIL"
    fx_rec = parse_decision_signal_jsonl_line(fx_line, line_no=3)
    assert fx_rec.symbol == "EUR/USD"

    report = validate_decision_signal_jsonl(audit)
    assert report.ok
    assert report.validated_signals == 2
    assert report.skipped_rows == 1


def _utc_df(*, periods: int = 4, freq: str = "15min") -> pd.DataFrame:
    index = pd.date_range("2026-08-10T21:00:00Z", periods=periods, freq=freq, tz="UTC")
    values = [100.0 + i for i in range(periods)]
    return pd.DataFrame(
        {"close": values, "high": values, "low": values},
        index=index,
    )


@pytest.mark.parametrize("pair", ["OIL", "NASDAQ", "XAU/USD", "BTC/USD"])
def test_record_branch_b_scan_appends_for_multiasset(
    tmp_path: Path, pair: str, caplog: pytest.LogCaptureFixture
) -> None:
    audit = tmp_path / "signal_audit.jsonl"
    telemetry = _build_scan_telemetry_payload(
        ts="2026-08-10T22:20:57+00:00",
        scan_run_id="scan-multiasset",
        pair=pair,
        state="watch",
        direction="SELL",
        aligned=False,
        breakout_pending=False,
        entry_triggered=False,
        bars_aligned=0,
        confirm_bars=2,
        within_confirm_window=False,
        spread_pips=None,
        max_spread_pips=50.0,
        spread_source=None,
        adx_1h=30.0,
        is_ranging=False,
        rsi_1h=70.0,
        rsi_30m=68.0,
        rsi_15m=66.0,
        no_trade_reasons=[],
    )
    with caplog.at_level(logging.WARNING, logger="src.evaluation.branch_b_audit"):
        ok = record_branch_b_scan_decision_signal(
            ts=datetime(2026, 8, 10, 22, 20, 57, tzinfo=UTC),
            pair=pair,
            scan_run_id="scan-multiasset",
            telemetry_state="watch",
            direction="SELL",
            telemetry_payload=telemetry,
            data_1h=_utc_df(periods=4, freq="1h"),
            data_30m=_utc_df(periods=4, freq="30min"),
            data_15m=_utc_df(),
            missing_timeframes=["15m"],
            distance=4.0,
            audit_path=audit,
        )
    assert ok is True
    assert not any("decision_signal audit append skipped" in r.message for r in caplog.records)
    rows = [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["kind"] == KIND_DECISION_SIGNAL
    assert rows[0]["symbol"] == pair
    assert rows[0]["action"] == "watch"
