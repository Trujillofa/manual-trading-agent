"""Tests for the Zacks MCP schema verifier."""

from __future__ import annotations

import json
from pathlib import Path

from research.new_edge.zacks_mcp.data.verify_zacks_mcp import (
    _statement_years,
    audit_inventory,
    main,
)

PROVENANCE = (
    Path(__file__).resolve().parents[1]
    / "research/new_edge/zacks_mcp/data/provenance/zacks_mcp_schema_probe_2026-08.json"
)


def _inventory() -> dict:
    return json.loads(PROVENANCE.read_text(encoding="utf-8"))


def test_pinned_probe_schema_passes_and_alpha_stays_blocked() -> None:
    audit = audit_inventory(_inventory())

    assert audit.schema_verdict == "SCHEMA_PASS"
    assert audit.verdict == "BLOCKED"
    assert audit.statement_years_observed == 5
    assert audit.etf_has_as_of_history_param is False
    assert audit.pead_fields_present == ()
    assert any("PEAD remains BLOCKED" in issue for issue in audit.issues)


def test_missing_tool_blocks_schema() -> None:
    inventory = _inventory()
    inventory["tools"] = ["get_company_snapshot"]

    audit = audit_inventory(inventory)

    assert audit.schema_verdict == "BLOCKED"
    assert any("missing MCP tools" in issue for issue in audit.issues)


def test_missing_snapshot_columns_block_schema() -> None:
    inventory = _inventory()
    inventory["snapshot_columns"] = ["ticker"]

    audit = audit_inventory(inventory)

    assert audit.schema_verdict == "BLOCKED"
    assert any("missing snapshot columns" in issue for issue in audit.issues)


def test_missing_holdings_columns_block_schema() -> None:
    inventory = _inventory()
    inventory["etf_holdings_columns"] = ["ticker"]

    audit = audit_inventory(inventory)

    assert audit.schema_verdict == "BLOCKED"
    assert any("missing ETF holdings columns" in issue for issue in audit.issues)


def test_missing_balance_columns_block_schema() -> None:
    inventory = _inventory()
    inventory["balance_columns"] = ["ticker"]

    audit = audit_inventory(inventory)

    assert audit.schema_verdict == "BLOCKED"
    assert any("missing balance-sheet columns" in issue for issue in audit.issues)


def test_missing_cash_flow_columns_block_schema() -> None:
    inventory = _inventory()
    inventory["cash_flow_columns"] = ["ticker"]

    audit = audit_inventory(inventory)

    assert audit.schema_verdict == "BLOCKED"
    assert any("missing cash-flow columns" in issue for issue in audit.issues)


def test_statement_years_counts_distinct_years_not_span() -> None:
    assert (
        _statement_years(
            {"live_probe": {"snapshot_annual": {"period_ends": ["2021-09-30", "2025-09-30"]}}}
        )
        == 2.0
    )
    assert _statement_years({"live_probe": {"snapshot_annual": {"period_ends": []}}}) == 0.0
    assert (
        _statement_years({"live_probe": {"snapshot_annual": {"period_ends": ["xx", "ab"]}}})
        == 0.0
    )


def test_main_exits_zero_on_schema_pass(tmp_path: Path) -> None:
    output = tmp_path / "manifest.md"
    assert main(["--provenance", str(PROVENANCE), "--output", str(output)]) == 0
    assert output.exists()


def test_main_exits_nonzero_when_schema_blocked(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    payload = _inventory()
    payload["tools"] = []
    broken.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "manifest.md"
    assert main(["--provenance", str(broken), "--output", str(output)]) == 2
