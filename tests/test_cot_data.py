"""Tests for the COT positioning data-proof lane."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from research.new_edge.cot_positioning.data.cftc_legacy import (
    FIXED_UNIVERSE,
    FetchResult,
    MarketSpec,
    build_query_params,
    canonical_payload_sha256,
    normalize_rows,
)
from research.new_edge.cot_positioning.data.verify_cot_data import (
    MIN_PASSING_MARKETS,
    audit_cot_data,
    build_manifest,
    build_provenance,
)

START = date(2010, 1, 1)
END = date(2026, 6, 16)


def _source_row(market: MarketSpec, report_date: pd.Timestamp) -> dict[str, str]:
    return {
        "cftc_contract_market_code": market.code,
        "market_and_exchange_names": market.market_name,
        "report_date_as_yyyy_mm_dd": report_date.strftime("%Y-%m-%dT00:00:00.000"),
        "open_interest_all": "1000",
        "noncomm_positions_long_all": "600",
        "noncomm_positions_short_all": "400",
        "commodity_group_name": "TEST GROUP",
        "commodity_subgroup_name": "TEST SUBGROUP",
        "commodity_name": market.symbol,
        "contract_units": "TEST CONTRACTS",
        "futonly_or_combined": "FutOnly",
    }


def _weekly_rows(universe: tuple[MarketSpec, ...]) -> list[dict[str, str]]:
    dates = pd.date_range("2010-01-05", "2026-06-16", freq="W-TUE")
    return [_source_row(market, report_date) for market in universe for report_date in dates]


def test_build_query_params_is_inclusive_and_deterministic():
    universe = (FIXED_UNIVERSE[0], FIXED_UNIVERSE[-1])
    params = build_query_params(START, END, universe, offset=50_000)

    assert params["$select"].startswith("cftc_contract_market_code")
    assert "'002602'" in params["$where"]
    assert "'13874+'" in params["$where"]
    assert "2010-01-01T00:00:00.000" in params["$where"]
    assert "2026-06-16T00:00:00.000" in params["$where"]
    assert params["$order"] == "cftc_contract_market_code,report_date_as_yyyy_mm_dd"
    assert params["$offset"] == "50000"


def test_normalize_rows_derives_positioning_and_standard_availability():
    row = _source_row(FIXED_UNIVERSE[0], pd.Timestamp("2026-06-16"))
    frame = normalize_rows([row])

    assert frame.loc[0, "market_code"] == "002602"
    assert frame.loc[0, "symbol"] == "CORN"
    assert frame.loc[0, "net_noncommercial"] == 200
    assert frame.loc[0, "net_noncommercial_pct_oi"] == pytest.approx(0.2)
    assert frame.loc[0, "available_date"] == pd.Timestamp("2026-06-22")


def test_normalize_rows_rejects_missing_required_source_field():
    row = _source_row(FIXED_UNIVERSE[0], pd.Timestamp("2026-06-16"))
    del row["open_interest_all"]

    with pytest.raises(ValueError, match="missing required fields"):
        normalize_rows([row])


def test_audit_passes_fixed_fifteen_market_data_gate():
    universe = FIXED_UNIVERSE[:MIN_PASSING_MARKETS]
    frame = normalize_rows(_weekly_rows(universe), universe)
    audit = audit_cot_data(frame, START, END, universe)

    assert audit.verdict == "DATA_PASS"
    assert audit.passing_markets == MIN_PASSING_MARKETS
    assert not audit.issues
    assert all(market.status == "DATA_PASS" for market in audit.market_audits)
    assert all(market.max_gap_days == 7 for market in audit.market_audits)


def test_audit_blocks_when_fewer_than_fifteen_markets_pass():
    universe = FIXED_UNIVERSE[: MIN_PASSING_MARKETS - 1]
    frame = normalize_rows(_weekly_rows(universe), universe)
    audit = audit_cot_data(frame, START, END, universe)

    assert audit.verdict == "BLOCKED"
    assert audit.passing_markets == MIN_PASSING_MARKETS - 1
    assert "required" in audit.issues[0]


def test_audit_blocks_market_name_drift_and_duplicate_dates():
    universe = FIXED_UNIVERSE[:MIN_PASSING_MARKETS]
    rows = _weekly_rows(universe)
    rows[0]["market_and_exchange_names"] = "UNEXPECTED MARKET NAME"
    rows.append(dict(rows[1]))
    frame = normalize_rows(rows, universe)
    audit = audit_cot_data(frame, START, END, universe)

    corn = next(market for market in audit.market_audits if market.symbol == "CORN")
    assert corn.status == "BLOCKED"
    assert corn.duplicate_market_dates == 1
    assert any("market name mismatch" in issue for issue in corn.issues)
    assert audit.verdict == "BLOCKED"


def test_canonical_hash_ignores_input_row_and_key_order():
    first = _source_row(FIXED_UNIVERSE[0], pd.Timestamp("2026-06-09"))
    second = _source_row(FIXED_UNIVERSE[0], pd.Timestamp("2026-06-16"))
    reversed_keys = dict(reversed(list(first.items())))

    assert canonical_payload_sha256([first, second]) == canonical_payload_sha256(
        [second, reversed_keys]
    )


def test_manifest_and_provenance_state_data_only_scope(tmp_path):
    universe = FIXED_UNIVERSE[:MIN_PASSING_MARKETS]
    rows = _weekly_rows(universe)
    frame = normalize_rows(rows, universe)
    audit = audit_cot_data(frame, START, END, universe)
    fetch_result = FetchResult(
        rows=tuple(rows),
        query_urls=("https://publicreporting.cftc.gov/example",),
        metadata={"name": "Legacy - Futures Only", "rowsUpdatedAt": 123},
        retrieved_at=datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
    )
    provenance_path = tmp_path / "provenance.json"

    manifest = build_manifest(audit, fetch_result, "python verifier", provenance_path)
    provenance = build_provenance(audit, fetch_result)

    assert "## Verdict: DATA_PASS" in manifest
    assert "not evidence of a return" in manifest
    assert "relationship and does not authorize" in manifest
    assert "following Monday" in manifest
    assert "exceptional delayed-release periods" in manifest
    assert provenance["stage"] == "data_proof"
    assert provenance["availability_rule"]["calendar_day_lag"] == 6
    assert provenance["availability_rule"]["status"] == "standard_schedule_only"
    assert provenance["audit"]["passing_markets"] == MIN_PASSING_MARKETS
