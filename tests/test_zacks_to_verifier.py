"""Tests for Zacks → verify_pead_data ETL adapter."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.new_edge.pead.data.verify_pead_data import audit_snapshot
from research.new_edge.pead.data.zacks_to_verifier import (
    JoinPolicy,
    announcement_ts_from_es_row,
    build_earnings_events,
    build_security_master,
    collision_report,
    estimate_observed_ts_from_obs_date,
    write_snapshot,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "research/new_edge/pead/data/fixtures/zacks_sample"
)


def _load_fixture(name: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURE_DIR / name)


def test_estimate_observed_ts_precedes_amc_announcement() -> None:
    obs = estimate_observed_ts_from_obs_date("2020-01-28")
    row = _load_fixture("es.csv").iloc[0]
    announce = announcement_ts_from_es_row(row)
    assert obs < announce


def test_strict_join_uses_prior_day_obs_when_available() -> None:
    eeh = _load_fixture("eeh.csv")
    es = _load_fixture("es.csv").iloc[[0]]
    events = build_earnings_events(eeh, es, policy=JoinPolicy.STRICT)
    assert len(events) == 1
    assert events.iloc[0]["consensus_eps"] == 2.55


def test_strict_join_drops_when_only_same_day_obs_exists() -> None:
    eeh = pd.DataFrame(
        [
            {
                "m_ticker": "ONLY",
                "per_end_date": "2019-12-31",
                "per_type": "Q",
                "obs_date": "2020-01-29",
                "eps_mean_est": 1.0,
            }
        ]
    )
    es = pd.DataFrame(
        [
            {
                "m_ticker": "ONLY",
                "ticker": "ONLY",
                "per_end_date": "2019-12-31",
                "per_type": "Q",
                "act_rpt_date": "2020-01-29",
                "act_rpt_time": "16:30",
                "act_rpt_code": "AMC",
                "eps_act": 1.1,
                "eps_mean_est": 1.0,
                "per_fisc_year": 2020,
                "per_fisc_qtr": 1,
            }
        ]
    )
    events = build_earnings_events(eeh, es, policy=JoinPolicy.STRICT)
    assert events.empty


def test_relaxed_amc_join_retains_same_day_revision() -> None:
    eeh = pd.DataFrame(
        [
            {
                "m_ticker": "ONLY",
                "per_end_date": "2019-12-31",
                "per_type": "Q",
                "obs_date": "2020-01-29",
                "eps_mean_est": 2.60,
            }
        ]
    )
    es = pd.DataFrame(
        [
            {
                "m_ticker": "ONLY",
                "ticker": "ONLY",
                "per_end_date": "2019-12-31",
                "per_type": "Q",
                "act_rpt_date": "2020-01-29",
                "act_rpt_time": "16:30",
                "act_rpt_code": "AMC",
                "eps_act": 2.65,
                "eps_mean_est": 2.60,
                "per_fisc_year": 2020,
                "per_fisc_qtr": 1,
            }
        ]
    )
    events = build_earnings_events(eeh, es, policy=JoinPolicy.RELAXED_AMC)
    assert len(events) == 1
    assert events.iloc[0]["consensus_eps"] == 2.60
    assert events.iloc[0]["actual_eps"] == 2.65


def test_collision_report_counts_same_day_by_session_code() -> None:
    report = collision_report(_load_fixture("eeh.csv"), _load_fixture("es.csv"))
    assert report.joined_rows == 3
    assert report.same_day_total >= 1
    assert report.strict_retained >= 1
    assert report.relaxed_retained >= report.strict_retained


def test_write_snapshot_produces_verifier_tables(tmp_path: Path) -> None:
    eeh = _load_fixture("eeh.csv")
    es = _load_fixture("es.csv").iloc[[1]]
    mt = _load_fixture("mt.csv")
    prices = pd.DataFrame(
        {
            "security_id": ["ZACKS_MSFT"],
            "date": ["2020-01-29"],
            "open": [160.0],
            "high": [161.0],
            "low": [159.0],
            "close": [160.5],
            "volume": [1000000],
        }
    )
    out = tmp_path / "snap"
    write_snapshot(
        out,
        eeh=eeh,
        es=es,
        mt=mt,
        prices=prices,
        policy=JoinPolicy.STRICT,
    )
    assert (out / "earnings_events.csv").exists()
    assert (out / "security_master.csv").exists()

    audit = audit_snapshot(
        out,
        start="2016-01-01",
        end="2026-01-01",
        source_label="zacks_fixture",
    )
    assert audit.events_eligible >= 1
    assert audit.verdict == "BLOCKED"
    assert audit.eligible_stocks_peak < 500


def test_cli_smoke_run_writes_snapshot_and_manifest(tmp_path: Path) -> None:
    out = tmp_path / "verifier_snapshot"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.new_edge.pead.data.zacks_to_verifier",
            "--pin",
            str(FIXTURE_DIR),
            "--policy",
            "strict",
            "--out-snapshot",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert (out / "earnings_events.csv").exists()
    assert (out / "security_master.csv").exists()
    assert (out / "etl_manifest.json").exists()

    manifest = json.loads((out / "etl_manifest.json").read_text(encoding="utf-8"))
    assert manifest["join_policy"] == "strict"
    assert "python -m research.new_edge.pead.data.zacks_to_verifier" in manifest["command"]
    assert manifest["events_written"] >= 1


def test_build_security_master_filters_non_common() -> None:
    mt = _load_fixture("mt.csv")
    mt = pd.concat(
        [
            mt,
            pd.DataFrame(
                [
                    {
                        "m_ticker": "SPY",
                        "ticker": "SPY",
                        "active_ticker_flag": "Y",
                        "asset_type": "ETF",
                        "zacks_x_sector_desc": "ETF",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    master = build_security_master(mt)
    assert len(master) == 2
    assert "ZACKS_SPY" not in master["security_id"].values