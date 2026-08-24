"""Tests for the Zacks MCP schema verifier."""

from __future__ import annotations

import json
from pathlib import Path

from research.new_edge.zacks_mcp.data.verify_zacks_mcp import audit_inventory

PROVENANCE = (
    Path(__file__).resolve().parents[1]
    / "research/new_edge/zacks_mcp/data/provenance/zacks_mcp_schema_probe_2026-08.json"
)


def test_pinned_probe_schema_passes_and_alpha_stays_blocked() -> None:
    audit = audit_inventory(json.loads(PROVENANCE.read_text(encoding="utf-8")))

    assert audit.schema_verdict == "SCHEMA_PASS"
    assert audit.verdict == "BLOCKED"
    assert audit.statement_years_observed < 10
    assert audit.etf_has_as_of_history_param is False
    assert audit.pead_fields_present == ()
    assert any("PEAD remains BLOCKED" in issue for issue in audit.issues)


def test_missing_tool_blocks_schema() -> None:
    inventory = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    inventory["tools"] = ["get_company_snapshot"]

    audit = audit_inventory(inventory)

    assert audit.schema_verdict == "BLOCKED"
    assert any("missing MCP tools" in issue for issue in audit.issues)
