"""Schema-only verifier for the Zacks MCP new-edge lane.

Validates a pinned tool/column inventory. Does not call the live MCP, does not
store statement values, and does not authorize relationship or strategy code.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PROVENANCE = (
    "research/new_edge/zacks_mcp/data/provenance/zacks_mcp_schema_probe_2026-08.json"
)
DEFAULT_OUTPUT = "docs/research/zacks_mcp/ZACKS_MCP_DATA_MANIFEST_2026-08-22.md"
MIN_STATEMENT_YEARS = 10

REQUIRED_TOOLS = (
    "get_company_snapshot",
    "get_income_statement",
    "get_balance_sheet",
    "get_cash_flow",
    "get_etf_holdings",
)
REQUIRED_SNAPSHOT_COLUMNS = (
    "ticker",
    "period_end",
    "type",
    "revenue",
    "net_income",
    "diluted_eps",
    "operating_cash_flow",
    "total_assets",
    "total_equity",
)
REQUIRED_HOLDINGS_COLUMNS = (
    "ticker",
    "name",
    "weight_pct",
    "shares",
    "as_of_epoch",
)
PEAD_FIELDS = (
    "estimate_observed_ts",
    "announcement_ts",
    "consensus_eps",
    "actual_eps",
)


@dataclass(frozen=True)
class ZacksMcpAudit:
    schema_verdict: str
    alpha_verdict: str
    statement_years_observed: float
    etf_has_as_of_history_param: bool
    pead_fields_present: tuple[str, ...]
    issues: tuple[str, ...]

    @property
    def verdict(self) -> str:
        return self.alpha_verdict


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _statement_years(inventory: dict[str, Any]) -> float:
    probe = inventory.get("live_probe") or {}
    annual = probe.get("snapshot_annual") or {}
    ends = _as_str_tuple(annual.get("period_ends"))
    if len(ends) < 2:
        return float(len(ends))
    years = sorted(int(end[:4]) for end in ends if len(end) >= 4)
    return float(years[-1] - years[0] + 1)


def _missing(required: tuple[str, ...], present: tuple[str, ...]) -> tuple[str, ...]:
    have = set(present)
    return tuple(item for item in required if item not in have)


def audit_inventory(inventory: dict[str, Any]) -> ZacksMcpAudit:
    issues: list[str] = []
    tools = _as_str_tuple(inventory.get("tools"))
    snapshot_cols = _as_str_tuple(inventory.get("snapshot_columns"))
    holdings_cols = _as_str_tuple(inventory.get("etf_holdings_columns"))
    declared_pead = _as_str_tuple(inventory.get("pead_fields_present"))
    income_cols = _as_str_tuple(inventory.get("income_columns"))
    all_columns = snapshot_cols + holdings_cols + income_cols
    leaked_pead = tuple(field for field in PEAD_FIELDS if field in all_columns)
    pead_present = tuple(dict.fromkeys((*declared_pead, *leaked_pead)))

    missing_tools = _missing(REQUIRED_TOOLS, tools)
    if missing_tools:
        issues.append("missing MCP tools: " + ", ".join(missing_tools))
    missing_snapshot = _missing(REQUIRED_SNAPSHOT_COLUMNS, snapshot_cols)
    if missing_snapshot:
        issues.append("missing snapshot columns: " + ", ".join(missing_snapshot))
    missing_holdings = _missing(REQUIRED_HOLDINGS_COLUMNS, holdings_cols)
    if missing_holdings:
        issues.append("missing ETF holdings columns: " + ", ".join(missing_holdings))
    if pead_present:
        issues.append(
            "PEAD fields appeared on the statements/holdings inventory ("
            + ", ".join(pead_present)
            + "); this MCP is still not a PEAD source"
        )

    schema_ok = not missing_tools and not missing_snapshot and not missing_holdings
    schema_verdict = "SCHEMA_PASS" if schema_ok else "BLOCKED"
    years = _statement_years(inventory)
    has_history = bool(inventory.get("etf_has_as_of_history_param"))
    if years < MIN_STATEMENT_YEARS:
        issues.append(
            f"annual statement history is {years:.0f}y; at least "
            f"{MIN_STATEMENT_YEARS}y required for a factor KEEP path"
        )
    if not has_history:
        issues.append(
            "get_etf_holdings has no as-of/history parameter; "
            "holdings-change backtests are unauthorized"
        )
    if not pead_present:
        issues.append(
            "estimate_observed_ts / announcement timestamps are absent; "
            "PEAD remains BLOCKED"
        )

    return ZacksMcpAudit(
        schema_verdict=schema_verdict,
        alpha_verdict="BLOCKED",
        statement_years_observed=years,
        etf_has_as_of_history_param=has_history,
        pead_fields_present=pead_present,
        issues=tuple(issues),
    )


def load_inventory(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def render_manifest(audit: ZacksMcpAudit, provenance: Path) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in audit.issues) or "- none"
    generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    pead = ", ".join(audit.pead_fields_present) or "none"
    return (
        "# Zacks MCP Data Manifest — 2026-08-22\n\n"
        f"Generated `{generated}` from `{provenance.as_posix()}`.\n\n"
        "Source: Zacks Investment Research (MCP `https://mcp.zacksdata.com`). "
        "Numeric statement and holdings values are not stored in-repo.\n\n"
        "| Field | Value |\n|---|---|\n"
        f"| Schema verdict | `{audit.schema_verdict}` |\n"
        f"| Alpha / KEEP-path verdict | `{audit.alpha_verdict}` |\n"
        f"| Annual statement years observed | {audit.statement_years_observed:.0f} |\n"
        f"| ETF as-of history parameter | `{audit.etf_has_as_of_history_param}` |\n"
        f"| PEAD fields present | {pead} |\n\n"
        "## Issues\n\n"
        f"{issue_lines}\n\n"
        "## Allowed next step\n\n"
        "Owner may pin a licensed historical extract (statements >=10y and/or dated "
        "ETF holdings) and re-run this verifier. Relationship and strategy code stay "
        "unauthorized until the ledger records `DATA_PASS`.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    provenance = Path(args.provenance)
    audit = audit_inventory(load_inventory(provenance))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_manifest(audit, provenance), encoding="utf-8")
    print(
        json.dumps(
            {
                "schema_verdict": audit.schema_verdict,
                "alpha_verdict": audit.alpha_verdict,
                "issues": audit.issues,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
