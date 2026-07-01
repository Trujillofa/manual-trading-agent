"""Verify official CFTC COT coverage before any relationship or strategy test."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from research.new_edge.cot_positioning.data.cftc_legacy import (
    CFTC_DATASET_ID,
    CFTC_DATASET_NAME,
    CFTC_METADATA_URL,
    CFTC_REPORT_DESCRIPTION,
    CFTC_RESOURCE_URL,
    CFTC_SOURCE_PAGE,
    FIXED_UNIVERSE,
    FetchResult,
    MarketSpec,
    canonical_metadata_sha256,
    canonical_payload_sha256,
    fetch_legacy_rows,
    normalize_rows,
)

logger = logging.getLogger(__name__)

MIN_PASSING_MARKETS = 15
MIN_COVERAGE_YEARS = 15.0
MIN_OBSERVATIONS_PER_YEAR = 48
MIN_OBSERVATIONS = int(MIN_COVERAGE_YEARS * MIN_OBSERVATIONS_PER_YEAR)
MAX_REPORT_GAP_DAYS = 14
MAX_LATEST_LAG_DAYS = 14
AVAILABILITY_LAG_DAYS = 6

DEFAULT_START = "2010-01-01"
DEFAULT_END = "2026-06-16"
DEFAULT_OUTPUT = "docs/research/cot_positioning/COT_DATA_MANIFEST_2026-06-25.md"
DEFAULT_PROVENANCE = (
    "research/new_edge/cot_positioning/data/provenance/"
    "cftc_legacy_futures_only_2010-01-01_2026-06-16.json"
)


@dataclass(frozen=True)
class MarketAudit:
    """Coverage result for one fixed-universe market."""

    code: str
    symbol: str
    sector: str
    expected_name: str
    observed_names: tuple[str, ...]
    rows: int
    first_report: str | None
    last_report: str | None
    coverage_years: float
    max_gap_days: int | None
    duplicate_market_dates: int
    availability_violations: int
    status: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class CotDataAudit:
    """Whole-lane data proof result."""

    verdict: str
    requested_start: str
    requested_end: str
    source_latest_report: str | None
    source_rows: int
    passing_markets: int
    required_passing_markets: int
    market_audits: tuple[MarketAudit, ...]
    issues: tuple[str, ...]


def _coverage_years(first_report: pd.Timestamp, last_report: pd.Timestamp) -> float:
    return float((last_report - first_report).days / 365.2425)


def _audit_market(
    frame: pd.DataFrame,
    market: MarketSpec,
    source_latest: pd.Timestamp,
) -> MarketAudit:
    market_frame = frame[frame["market_code"] == market.code].copy()
    issues: list[str] = []

    if market_frame.empty:
        return MarketAudit(
            code=market.code,
            symbol=market.symbol,
            sector=market.sector,
            expected_name=market.market_name,
            observed_names=(),
            rows=0,
            first_report=None,
            last_report=None,
            coverage_years=0.0,
            max_gap_days=None,
            duplicate_market_dates=0,
            availability_violations=0,
            status="BLOCKED",
            issues=("no rows returned",),
        )

    report_dates = market_frame["report_date"].sort_values()
    first_report = report_dates.iloc[0]
    last_report = report_dates.iloc[-1]
    coverage_years = _coverage_years(first_report, last_report)
    gaps = report_dates.diff().dt.days.dropna()
    max_gap_days = int(gaps.max()) if not gaps.empty else 0
    duplicates = int(market_frame.duplicated(["market_code", "report_date"]).sum())
    expected_available = market_frame["report_date"] + pd.Timedelta(days=AVAILABILITY_LAG_DAYS)
    availability_violations = int((market_frame["available_date"] != expected_available).sum())
    observed_names = tuple(sorted(set(market_frame["market_name"].astype(str))))

    if coverage_years < MIN_COVERAGE_YEARS:
        issues.append(f"coverage {coverage_years:.2f}y is below {MIN_COVERAGE_YEARS:.0f}y")
    if len(market_frame) < MIN_OBSERVATIONS:
        issues.append(
            f"{len(market_frame)} rows is below {MIN_OBSERVATIONS} "
            f"({MIN_OBSERVATIONS_PER_YEAR}/year floor)"
        )
    latest_lag = (source_latest - last_report).days
    if latest_lag > MAX_LATEST_LAG_DAYS:
        issues.append(f"latest report lags source by {latest_lag} days")
    if max_gap_days > MAX_REPORT_GAP_DAYS:
        issues.append(f"maximum report gap is {max_gap_days} days")
    if duplicates:
        issues.append(f"{duplicates} duplicate market-date rows")
    if availability_violations:
        issues.append(f"{availability_violations} availability-date violations")
    if observed_names != (market.market_name,):
        issues.append(
            f"market name mismatch: expected {market.market_name!r}, observed {observed_names!r}"
        )

    return MarketAudit(
        code=market.code,
        symbol=market.symbol,
        sector=market.sector,
        expected_name=market.market_name,
        observed_names=observed_names,
        rows=len(market_frame),
        first_report=first_report.date().isoformat(),
        last_report=last_report.date().isoformat(),
        coverage_years=coverage_years,
        max_gap_days=max_gap_days,
        duplicate_market_dates=duplicates,
        availability_violations=availability_violations,
        status="DATA_PASS" if not issues else "BLOCKED",
        issues=tuple(issues),
    )


def audit_cot_data(
    frame: pd.DataFrame,
    start: date,
    end: date,
    universe: tuple[MarketSpec, ...] = FIXED_UNIVERSE,
) -> CotDataAudit:
    """Apply the pre-written data gates to normalized COT rows."""
    issues: list[str] = []
    if frame.empty:
        return CotDataAudit(
            verdict="BLOCKED",
            requested_start=start.isoformat(),
            requested_end=end.isoformat(),
            source_latest_report=None,
            source_rows=0,
            passing_markets=0,
            required_passing_markets=MIN_PASSING_MARKETS,
            market_audits=tuple(
                _audit_market(frame, market, pd.Timestamp(end)) for market in universe
            ),
            issues=("no normalized rows available",),
        )

    source_latest = frame["report_date"].max()
    market_audits = tuple(_audit_market(frame, market, source_latest) for market in universe)
    passing_markets = sum(audit.status == "DATA_PASS" for audit in market_audits)

    outside_window = int(
        (
            (frame["report_date"] < pd.Timestamp(start))
            | (frame["report_date"] > pd.Timestamp(end))
        ).sum()
    )
    if outside_window:
        issues.append(f"{outside_window} rows fall outside the requested window")
    if passing_markets < MIN_PASSING_MARKETS:
        issues.append(f"only {passing_markets} markets passed; {MIN_PASSING_MARKETS} required")

    verdict = "DATA_PASS" if not issues and passing_markets >= MIN_PASSING_MARKETS else "BLOCKED"
    return CotDataAudit(
        verdict=verdict,
        requested_start=start.isoformat(),
        requested_end=end.isoformat(),
        source_latest_report=source_latest.date().isoformat(),
        source_rows=len(frame),
        passing_markets=passing_markets,
        required_passing_markets=MIN_PASSING_MARKETS,
        market_audits=market_audits,
        issues=tuple(issues),
    )


def build_manifest(
    audit: CotDataAudit,
    fetch_result: FetchResult,
    command: str,
    provenance_path: Path,
) -> str:
    """Build the human-readable COT data-proof manifest."""
    payload_hash = canonical_payload_sha256(fetch_result.rows)
    metadata_hash = canonical_metadata_sha256(fetch_result.metadata)
    lines = [
        "# COT Positioning Data Manifest — 2026-06-25",
        "",
        f"## Verdict: {audit.verdict}",
        "",
        "This is a data-availability verdict only. It is not evidence of a return",
        "relationship and does not authorize a strategy, classifier, or backtest.",
        "",
        "## Command",
        "",
        "```bash",
        command,
        "```",
        "",
        "## Fixed data gate",
        "",
        f"- Requested window: `{audit.requested_start}` through `{audit.requested_end}`",
        f"- Source rows: {audit.source_rows:,}",
        (
            f"- Passing markets: {audit.passing_markets}/{len(audit.market_audits)} "
            f"(minimum {audit.required_passing_markets})"
        ),
        f"- Minimum coverage per market: {MIN_COVERAGE_YEARS:.0f} years",
        f"- Minimum observations per market: {MIN_OBSERVATIONS}",
        f"- Maximum report gap: {MAX_REPORT_GAP_DAYS} calendar days",
        f"- Source latest report: `{audit.source_latest_report}`",
        "",
        "## Per-market verification",
        "",
        "| Code | Symbol | Sector | Rows | First | Last | Years | Max gap | Status |",
        "| --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |",
    ]

    for market in audit.market_audits:
        max_gap = "" if market.max_gap_days is None else str(market.max_gap_days)
        lines.append(
            f"| `{market.code}` | {market.symbol} | {market.sector} | "
            f"{market.rows:,} | {market.first_report or ''} | {market.last_report or ''} | "
            f"{market.coverage_years:.2f} | {max_gap} | {market.status} |"
        )

    blocked_markets = [market for market in audit.market_audits if market.issues]
    lines.extend(["", "## Exceptions", ""])
    if not audit.issues and not blocked_markets:
        lines.append("- None.")
    else:
        lines.extend(f"- Program gate: {issue}" for issue in audit.issues)
        for market in blocked_markets:
            lines.append(f"- {market.symbol}: {'; '.join(market.issues)}")

    lines.extend(
        [
            "",
            "## Source and schema",
            "",
            f"- Official dataset: CFTC PRE `{CFTC_DATASET_NAME}` (`{CFTC_DATASET_ID}`)",
            f"- Resource endpoint: `{CFTC_RESOURCE_URL}`",
            f"- Dataset metadata: `{CFTC_METADATA_URL}`",
            f"- CFTC COT source page: {CFTC_SOURCE_PAGE}",
            f"- CFTC report description: {CFTC_REPORT_DESCRIPTION}",
            "- Report type: Legacy Futures Only (`FutOnly`)",
            "- Position fields: non-commercial long, non-commercial short, open interest",
            "- Stable join key: CFTC contract market code plus report date",
            "",
            "## No-lookahead rule",
            "",
            "- CFTC positions are measured as of Tuesday and generally released Friday afternoon.",
            "- CFTC states that historical exact release dates are not available beyond its",
            "  rolling release schedule and holidays can shift publication.",
            "- Every normalized row receives a standard-schedule `available_date` equal to the",
            "  following Monday (`report_date + 6 calendar days`).",
            "- This derived date is not sufficient for exceptional delayed-release periods.",
            "  Before relationship testing, delayed weeks MUST be identified from CFTC special",
            "  announcements / available schedules and either assigned verified release dates",
            "  or excluded.",
            "- Relationship tests MUST NOT use the Tuesday report date as the information-",
            "  availability date.",
            "",
            "## Provenance",
            "",
            f"- Retrieved at UTC: `{fetch_result.retrieved_at.isoformat()}`",
            f"- Canonical source-row SHA256: `{payload_hash}`",
            f"- Dataset-metadata SHA256: `{metadata_hash}`",
            f"- Machine-readable record: `{provenance_path}`",
            "",
            "## Next dependency",
            "",
            (
                "If `DATA_PASS`: first implement the delayed-release exclusion / verified-"
                "timestamp control, then build frequency tables and fixed OLS relationship "
                "tests. Do not build a classifier or trading strategy."
            ),
            (
                "If `BLOCKED`: repair only the documented data issue. "
                "Do not substitute markets after seeing relationship results."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def build_provenance(
    audit: CotDataAudit,
    fetch_result: FetchResult,
) -> dict[str, Any]:
    """Build the machine-readable provenance record."""
    return {
        "schema_version": 1,
        "lane": "cot_positioning",
        "stage": "data_proof",
        "verdict": audit.verdict,
        "retrieved_at_utc": fetch_result.retrieved_at.isoformat(),
        "source": {
            "publisher": "U.S. Commodity Futures Trading Commission",
            "dataset_id": CFTC_DATASET_ID,
            "dataset_name": fetch_result.metadata.get("name"),
            "resource_url": CFTC_RESOURCE_URL,
            "metadata_url": CFTC_METADATA_URL,
            "source_page": CFTC_SOURCE_PAGE,
            "query_urls": list(fetch_result.query_urls),
            "rows_updated_at": fetch_result.metadata.get("rowsUpdatedAt"),
        },
        "request": {
            "start": audit.requested_start,
            "end": audit.requested_end,
            "market_codes": [market.code for market in FIXED_UNIVERSE],
        },
        "integrity": {
            "source_rows": len(fetch_result.rows),
            "canonical_source_rows_sha256": canonical_payload_sha256(fetch_result.rows),
            "dataset_metadata_sha256": canonical_metadata_sha256(fetch_result.metadata),
        },
        "availability_rule": {
            "source_report_date": "Tuesday position date",
            "standard_schedule_usable_date": "following Monday",
            "calendar_day_lag": AVAILABILITY_LAG_DAYS,
            "exception_policy": (
                "Before relationship testing, delayed-release weeks require verified "
                "release dates or exclusion."
            ),
            "status": "standard_schedule_only",
        },
        "audit": {
            "source_latest_report": audit.source_latest_report,
            "passing_markets": audit.passing_markets,
            "required_passing_markets": audit.required_passing_markets,
            "issues": list(audit.issues),
            "markets": [asdict(market) for market in audit.market_audits],
        },
    }


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def _command(args: argparse.Namespace) -> str:
    return (
        "python -m research.new_edge.cot_positioning.data.verify_cot_data "
        f"--start {args.start.isoformat()} --end {args.end.isoformat()} "
        f"--output {args.output} --provenance {args.provenance}"
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_parse_date, default=_parse_date(DEFAULT_START))
    parser.add_argument("--end", type=_parse_date, default=_parse_date(DEFAULT_END))
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    args = parser.parse_args()

    if args.start > args.end:
        parser.error("--start must not be after --end")

    output_path = Path(args.output)
    provenance_path = Path(args.provenance)

    try:
        fetch_result = fetch_legacy_rows(args.start, args.end)
        frame = normalize_rows(fetch_result.rows)
        audit = audit_cot_data(frame, args.start, args.end)
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("COT data proof failed", extra={"error": str(exc)})
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_manifest(audit, fetch_result, _command(args), provenance_path),
        encoding="utf-8",
    )
    provenance_path.write_text(
        json.dumps(build_provenance(audit, fetch_result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    logger.info(
        "COT data proof complete",
        extra={
            "verdict": audit.verdict,
            "passing_markets": audit.passing_markets,
            "source_rows": audit.source_rows,
            "output": str(output_path),
        },
    )
    return 0 if audit.verdict == "DATA_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
