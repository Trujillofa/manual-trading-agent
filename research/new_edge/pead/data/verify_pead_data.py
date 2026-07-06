"""Read-only PEAD source verifier (data proof only).

Validates a pinned earnings snapshot against the PEAD contract gate.
Relationship code remains unauthorized until the ledger records DATA_PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_START = "2016-01-01"
DEFAULT_END = "2026-01-01"
DEFAULT_OUTPUT = "docs/research/pead/PEAD_DATA_MANIFEST_2026-07.md"
DEFAULT_PROVENANCE = (
    "research/new_edge/pead/data/provenance/pead_source_audit_2026-07.json"
)

MIN_ELIGIBLE_STOCKS = 500
MIN_HISTORY_YEARS = 10
MIN_YEAR_FIELD_COVERAGE = 0.80
MIN_TRADABLE_SESSION_RATE = 0.95
MIN_STABLE_ID_RATE = 0.90
MIN_OOS_QUINTILE_EVENTS = 30

REQUIRED_EVENT_FIELDS = (
    "security_id",
    "ticker",
    "announcement_ts",
    "estimate_observed_ts",
    "actual_eps",
    "consensus_eps",
    "fiscal_period",
)
REQUIRED_MASTER_FIELDS = (
    "security_id",
    "ticker",
    "security_type",
    "list_date",
)
REQUIRED_PRICE_FIELDS = ("security_id", "date", "open", "high", "low", "close", "volume")
REQUIRED_SECTOR_FIELDS = ("security_id", "as_of_date", "sector")


@dataclass(frozen=True)
class PeadDataAudit:
    verdict: str
    requested_start: str
    requested_end: str
    source_label: str
    events_total: int
    events_eligible: int
    eligible_stocks_peak: int
    years_covered: float
    year_field_coverage_min: float
    tradable_session_rate: float
    stable_id_rate: float
    prospective_oos_quintile_min: int
    issues: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_snapshot_table(snapshot_dir: Path, name: str) -> pd.DataFrame | None:
    path = snapshot_dir / name
    if not path.exists():
        return None
    return pd.read_csv(path)


def _parse_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _years_between(start: pd.Timestamp, end: pd.Timestamp) -> float:
    return float((end - start).days / 365.2425)


def _reject_reason_counts(frame: pd.DataFrame, reasons: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in reasons[reasons != ""]:
        counts[str(reason)] = counts.get(str(reason), 0) + 1
    return counts


def audit_snapshot(
    snapshot_dir: Path,
    *,
    start: str,
    end: str,
    source_label: str,
) -> PeadDataAudit:
    """Validate a pinned snapshot against binding PEAD data gates."""
    issues: list[str] = []
    master = _load_snapshot_table(snapshot_dir, "security_master.csv")
    events = _load_snapshot_table(snapshot_dir, "earnings_events.csv")
    prices = _load_snapshot_table(snapshot_dir, "daily_prices.csv")
    sectors = _load_snapshot_table(snapshot_dir, "sectors.csv")

    for label, frame, required in (
        ("security_master.csv", master, REQUIRED_MASTER_FIELDS),
        ("earnings_events.csv", events, REQUIRED_EVENT_FIELDS),
        ("daily_prices.csv", prices, REQUIRED_PRICE_FIELDS),
        ("sectors.csv", sectors, REQUIRED_SECTOR_FIELDS),
    ):
        if frame is None:
            issues.append(f"missing required table: {label}")
            continue
        missing = [field for field in required if field not in frame.columns]
        if missing:
            issues.append(f"{label} missing columns: {', '.join(missing)}")

    if events is None or master is None:
        return PeadDataAudit(
            verdict="BLOCKED",
            requested_start=start,
            requested_end=end,
            source_label=source_label,
            events_total=0,
            events_eligible=0,
            eligible_stocks_peak=0,
            years_covered=0.0,
            year_field_coverage_min=0.0,
            tradable_session_rate=0.0,
            stable_id_rate=0.0,
            prospective_oos_quintile_min=0,
            issues=tuple(issues) or ("snapshot incomplete",),
        )

    events = events.copy()
    events["announcement_ts"] = _parse_ts(events["announcement_ts"])
    events["estimate_observed_ts"] = _parse_ts(events["estimate_observed_ts"])
    events["actual_eps"] = pd.to_numeric(events["actual_eps"], errors="coerce")
    events["consensus_eps"] = pd.to_numeric(events["consensus_eps"], errors="coerce")

    window_start = pd.Timestamp(start, tz=UTC)
    window_end = pd.Timestamp(end, tz=UTC)
    events = events[
        (events["announcement_ts"] >= window_start) & (events["announcement_ts"] < window_end)
    ]

    rejections: list[str] = []
    for _idx, row in events.iterrows():
        if pd.isna(row["announcement_ts"]):
            rejections.append("announcement timestamp missing or unparseable")
        elif pd.isna(row["estimate_observed_ts"]):
            rejections.append("estimate observation timestamp missing")
        elif row["estimate_observed_ts"] >= row["announcement_ts"]:
            rejections.append("estimate observed at or after announcement")
        elif pd.isna(row["actual_eps"]) or pd.isna(row["consensus_eps"]):
            rejections.append("actual or consensus EPS missing")
        elif float(row["consensus_eps"]) == 0.0:
            rejections.append("zero consensus EPS")
        else:
            rejections.append("")

    events["reject_reason"] = rejections
    eligible = events[events["reject_reason"] == ""].copy()

    if master is not None and "security_type" in master.columns:
        allowed_types = {"common", "common_stock", "cs"}
        master_ids = set(
            master[master["security_type"].astype(str).str.lower().isin(allowed_types)][
                "security_id"
            ].astype(str)
        )
        eligible = eligible[eligible["security_id"].astype(str).isin(master_ids)]

    events_total = len(events)
    events_eligible = len(eligible)
    eligible_stocks_peak = int(eligible["security_id"].nunique()) if not eligible.empty else 0

    if eligible.empty:
        years_covered = 0.0
    else:
        years_covered = _years_between(
            eligible["announcement_ts"].min(),
            eligible["announcement_ts"].max(),
        )

    year_field_coverage_min = 0.0
    if not eligible.empty:
        eligible = eligible.copy()
        eligible["year"] = eligible["announcement_ts"].dt.year
        yearly_rates: list[float] = []
        for _, year_frame in eligible.groupby("year"):
            complete = year_frame.dropna(
                subset=["announcement_ts", "estimate_observed_ts", "actual_eps", "consensus_eps"]
            )
            yearly_rates.append(len(complete) / len(year_frame))
        year_field_coverage_min = min(yearly_rates) if yearly_rates else 0.0

    tradable_session_rate = 1.0 if events_eligible else 0.0
    if "tradable_session" in events.columns:
        tradable_session_rate = float(events["tradable_session"].astype(bool).mean())

    stable_id_rate = 1.0
    if "stable_id" in events.columns:
        stable_id_rate = float(events["stable_id"].astype(bool).mean())

    prospective_oos_quintile_min = 0
    if events_eligible:
        oos_start = pd.Timestamp("2024-01-01", tz=UTC)
        oos_end = pd.Timestamp("2026-01-01", tz=UTC)
        oos = eligible[
            (eligible["announcement_ts"] >= oos_start) & (eligible["announcement_ts"] < oos_end)
        ].copy()
        if not oos.empty and (oos["consensus_eps"] != 0).all():
            surprise = (oos["actual_eps"] - oos["consensus_eps"]) / oos["consensus_eps"].abs()
            oos = oos.assign(surprise=surprise)
            if len(oos) >= 5:
                oos["quintile"] = pd.qcut(oos["surprise"], 5, labels=False, duplicates="drop")
                counts = oos["quintile"].value_counts()
                if len(counts) >= 2:
                    prospective_oos_quintile_min = int(counts.min())

    if years_covered < MIN_HISTORY_YEARS:
        issues.append(
            f"coverage window is {years_covered:.2f}y; at least {MIN_HISTORY_YEARS}y required"
        )
    if eligible_stocks_peak < MIN_ELIGIBLE_STOCKS:
        issues.append(
            f"eligible stock count peaks at {eligible_stocks_peak}; "
            f"at least {MIN_ELIGIBLE_STOCKS} required"
        )
    if year_field_coverage_min < MIN_YEAR_FIELD_COVERAGE:
        issues.append(
            f"minimum yearly field completeness is {year_field_coverage_min:.1%}; "
            f"at least {MIN_YEAR_FIELD_COVERAGE:.0%} required"
        )
    if tradable_session_rate < MIN_TRADABLE_SESSION_RATE:
        issues.append(
            f"tradable session mapping rate is {tradable_session_rate:.1%}; "
            f"at least {MIN_TRADABLE_SESSION_RATE:.0%} required"
        )
    if stable_id_rate < MIN_STABLE_ID_RATE:
        issues.append(
            f"stable security-id rate is {stable_id_rate:.1%}; "
            f"at least {MIN_STABLE_ID_RATE:.0%} required"
        )
    if prospective_oos_quintile_min < MIN_OOS_QUINTILE_EVENTS:
        issues.append(
            f"prospective OOS extreme-quintile events min {prospective_oos_quintile_min}; "
            f"at least {MIN_OOS_QUINTILE_EVENTS} required"
        )

    rejection_counts = _reject_reason_counts(events, events["reject_reason"])
    for reason, count in sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0])):
        issues.append(f"rejected {count} events: {reason}")

    verdict = "DATA_PASS" if not issues else "BLOCKED"
    return PeadDataAudit(
        verdict=verdict,
        requested_start=start,
        requested_end=end,
        source_label=source_label,
        events_total=events_total,
        events_eligible=events_eligible,
        eligible_stocks_peak=eligible_stocks_peak,
        years_covered=years_covered,
        year_field_coverage_min=year_field_coverage_min,
        tradable_session_rate=tradable_session_rate,
        stable_id_rate=stable_id_rate,
        prospective_oos_quintile_min=prospective_oos_quintile_min,
        issues=tuple(issues),
    )


def build_manifest(audit: PeadDataAudit, command: str, provenance_path: Path) -> str:
    lines = [
        "# PEAD Data Manifest — 2026-07",
        "",
        f"## Verdict: {audit.verdict}",
        "",
        "Data-proof only. This manifest does not authorize relationship code, strategy",
        "simulation, parameter search, or production integration.",
        "",
        "## Command",
        "",
        "```bash",
        command,
        "```",
        "",
        "## Snapshot summary",
        "",
        f"- Source label: `{audit.source_label}`",
        f"- Requested window: `{audit.requested_start}` → `{audit.requested_end}`",
        f"- Total events in window: {audit.events_total}",
        f"- Eligible events: {audit.events_eligible}",
        f"- Peak eligible stocks: {audit.eligible_stocks_peak}",
        f"- Years covered: {audit.years_covered:.2f}",
        f"- Minimum yearly field completeness: {audit.year_field_coverage_min:.1%}",
        f"- Tradable session mapping rate: {audit.tradable_session_rate:.1%}",
        f"- Stable security-id rate: {audit.stable_id_rate:.1%}",
        f"- Prospective OOS quintile minimum: {audit.prospective_oos_quintile_min}",
        "",
        "## Gate thresholds",
        "",
        f"- Minimum history: {MIN_HISTORY_YEARS} years",
        f"- Minimum eligible stocks: {MIN_ELIGIBLE_STOCKS}",
        f"- Minimum yearly field completeness: {MIN_YEAR_FIELD_COVERAGE:.0%}",
        f"- Minimum tradable session mapping: {MIN_TRADABLE_SESSION_RATE:.0%}",
        f"- Minimum stable security-id coverage: {MIN_STABLE_ID_RATE:.0%}",
        f"- Minimum prospective OOS quintile events: {MIN_OOS_QUINTILE_EVENTS}",
        "",
        "## Issues",
        "",
    ]
    if audit.issues:
        lines.extend(f"- {issue}" for issue in audit.issues)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            (
                "Relationship logic remains unauthorized until the research ledger records "
                "`DATA_PASS` for this lane."
                if audit.verdict != "DATA_PASS"
                else "Data gate passed. The next authorized step is one fixed relationship "
                "falsifier per the PEAD contract."
            ),
            "",
            f"Machine-readable provenance: `{provenance_path}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_provenance(
    audit: PeadDataAudit,
    snapshot_dir: Path,
    provenance_input: dict[str, Any],
) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in sorted(snapshot_dir.glob("*.csv")):
        files[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "rows": len(pd.read_csv(path)),
        }
    return {
        "schema_version": 1,
        "lane": "pead",
        "stage": "data_proof",
        "verdict": audit.verdict,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "source": provenance_input,
        "snapshot_dir": str(snapshot_dir),
        "files": files,
        "audit": asdict(audit),
        "gate": {
            "minimum_history_years": MIN_HISTORY_YEARS,
            "minimum_eligible_stocks": MIN_ELIGIBLE_STOCKS,
            "minimum_year_field_coverage": MIN_YEAR_FIELD_COVERAGE,
            "minimum_tradable_session_rate": MIN_TRADABLE_SESSION_RATE,
            "minimum_stable_id_rate": MIN_STABLE_ID_RATE,
            "minimum_oos_quintile_events": MIN_OOS_QUINTILE_EVENTS,
        },
    }


def _command(args: argparse.Namespace) -> str:
    return (
        "python -m research.new_edge.pead.data.verify_pead_data "
        f"--input {args.input} --provenance {args.provenance} "
        f"--start {args.start} --end {args.end} --output {args.output}"
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Pinned snapshot directory")
    parser.add_argument("--provenance", type=Path, required=True, help="Source provenance JSON")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    args = parser.parse_args()

    if not args.input.is_dir():
        parser.error(f"--input must be an existing directory: {args.input}")

    provenance_src = args.input / "provenance.json"
    if not provenance_src.exists():
        if not args.provenance.exists():
            parser.error(
                f"no provenance.json in {args.input} and --provenance file does not exist"
            )
        provenance_src = args.provenance
    provenance_input = json.loads(provenance_src.read_text(encoding="utf-8"))
    source_label = str(provenance_input.get("label", provenance_input.get("source", "unknown")))

    audit = audit_snapshot(
        args.input,
        start=args.start,
        end=args.end,
        source_label=source_label,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.provenance.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_manifest(audit, _command(args), args.provenance), encoding="utf-8")

    provenance_payload = build_provenance(audit, args.input, provenance_input)
    provenance_out = args.provenance
    provenance_out.write_text(json.dumps(provenance_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    logger.info(
        "PEAD data audit complete: verdict=%s eligible_events=%d stocks=%d",
        audit.verdict,
        audit.events_eligible,
        audit.eligible_stocks_peak,
    )
    print(audit.verdict)
    return 0 if audit.verdict == "DATA_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
