"""Telemetry helper tests for scan instrumentation."""

from __future__ import annotations

from pathlib import Path

from src.cli import _aggregate_scan_telemetry, _build_scan_telemetry_payload, _logs_dir


def test_logs_dir_falls_back_to_local_logs(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MANUAL_TRADING_AGENT_LOG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _logs_dir() == tmp_path / "logs"


def test_logs_dir_uses_env_override(monkeypatch, tmp_path) -> None:
    override = tmp_path / "custom-logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(override))

    assert _logs_dir() == Path(str(override))


def test_build_scan_telemetry_payload_sets_expected_counts_and_blockers() -> None:
    payload = _build_scan_telemetry_payload(
        ts="2026-04-14T12:00:00+00:00",
        scan_run_id="2026-04-14T12:00:00+00:00",
        pair="GBP/USD",
        state="blocked",
        direction="SELL",
        aligned=True,
        breakout_pending=True,
        entry_triggered=False,
        bars_aligned=3,
        confirm_bars=2,
        within_confirm_window=False,
        spread_pips=2.7,
        max_spread_pips=2.0,
        spread_source="ctrader",
        adx_1h=31.2,
        is_ranging=False,
        rsi_1h=80.0,
        rsi_30m=78.0,
        rsi_15m=71.0,
        no_trade_reasons=[
            "15m breakout high not confirmed",
            "confirmation window expired (3 bars > 2)",
            "spread unavailable/too wide",
            "trending market (ADX 31 >= 25)",
        ],
    )

    assert payload["kind"] == "scan_telemetry"
    assert payload["counts"] == {
        "scan": 1,
        "mtf_alignment": 1,
        "aligned_pending_breakout": 0,
        "entry": 0,
    }
    assert payload["blockers"] == {
        "adx_trending": True,
        "spread_unavailable_or_too_wide": True,
        "session": False,
        "news": False,
        "cooldown": False,
        "confirmation_expired": True,
        "breakout_unconfirmed": True,
        "data_unavailable": False,
    }


def test_aggregate_scan_telemetry_summarizes_by_pair() -> None:
    records = [
        _build_scan_telemetry_payload(
            ts="2026-04-14T12:00:00+00:00",
            scan_run_id="run-1",
            pair="EUR/GBP",
            state="watch",
            direction="BUY",
            aligned=False,
            breakout_pending=False,
            entry_triggered=False,
            bars_aligned=0,
            confirm_bars=2,
            within_confirm_window=False,
            spread_pips=1.0,
            max_spread_pips=1.5,
            spread_source="ctrader",
            adx_1h=18.0,
            is_ranging=True,
            rsi_1h=31.0,
            rsi_30m=29.0,
            rsi_15m=35.0,
            no_trade_reasons=[],
        ),
        _build_scan_telemetry_payload(
            ts="2026-04-14T12:15:00+00:00",
            scan_run_id="run-1",
            pair="EUR/GBP",
            state="aligned_pending_breakout",
            direction="BUY",
            aligned=True,
            breakout_pending=True,
            entry_triggered=False,
            bars_aligned=1,
            confirm_bars=2,
            within_confirm_window=True,
            spread_pips=1.0,
            max_spread_pips=1.5,
            spread_source="ctrader",
            adx_1h=18.0,
            is_ranging=True,
            rsi_1h=29.0,
            rsi_30m=28.0,
            rsi_15m=27.0,
            no_trade_reasons=[],
        ),
        _build_scan_telemetry_payload(
            ts="2026-04-14T12:30:00+00:00",
            scan_run_id="run-1",
            pair="EUR/GBP",
            state="entry",
            direction="BUY",
            aligned=True,
            breakout_pending=False,
            entry_triggered=True,
            bars_aligned=1,
            confirm_bars=2,
            within_confirm_window=True,
            spread_pips=1.0,
            max_spread_pips=1.5,
            spread_source="ctrader",
            adx_1h=18.0,
            is_ranging=True,
            rsi_1h=29.0,
            rsi_30m=28.0,
            rsi_15m=27.0,
            no_trade_reasons=[],
        ),
        _build_scan_telemetry_payload(
            ts="2026-04-14T12:45:00+00:00",
            scan_run_id="run-1",
            pair="GBP/USD",
            state="blocked",
            direction="SELL",
            aligned=True,
            breakout_pending=True,
            entry_triggered=False,
            bars_aligned=3,
            confirm_bars=2,
            within_confirm_window=False,
            spread_pips=2.4,
            max_spread_pips=2.0,
            spread_source="ctrader",
            adx_1h=30.0,
            is_ranging=False,
            rsi_1h=80.0,
            rsi_30m=78.0,
            rsi_15m=71.0,
            no_trade_reasons=[
                "spread unavailable/too wide",
                "trending market (ADX 30 >= 25)",
            ],
        ),
    ]

    summary = _aggregate_scan_telemetry(records)

    assert summary["EUR/GBP"] == {
        "scans": 3,
        "mtf_alignments": 2,
        "aligned_pending_breakout": 1,
        "entries": 1,
        "blockers": {},
    }
    assert summary["GBP/USD"]["scans"] == 1
    assert summary["GBP/USD"]["mtf_alignments"] == 1
    assert summary["GBP/USD"]["aligned_pending_breakout"] == 0
    assert summary["GBP/USD"]["entries"] == 0
    assert summary["GBP/USD"]["blockers"] == {
        "adx_trending": 1,
        "spread_unavailable_or_too_wide": 1,
    }
