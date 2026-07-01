"""Audit the free CME settlement source against the pre-committed Tier-A gate."""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from research.new_edge.term_structure.data.cme_span import (
    CME_SPAN_ARCHIVE_ROOT,
    CME_SPAN_LAYOUT,
    CME_SPAN_SOURCE_PAGE,
    ArchiveInventory,
    CMEArchiveClient,
    normalize_archive,
)
from research.new_edge.term_structure.data.metadata import FIXED_UNIVERSE

logger = logging.getLogger(__name__)

MIN_COVERAGE_YEARS = 15.0
MIN_PASSING_MARKETS = 10
REQUIRED_FIELDS = ("open", "high", "low", "settle", "open_interest")
PA2_FIELDS = ("settle",)
DEFAULT_OUTPUT = "docs/research/term_structure/CME_FREE_DATA_AUDIT_2026-06-30.md"
DEFAULT_PROVENANCE = (
    "research/new_edge/term_structure/data/provenance/cme_free_data_audit_2026-06-30.json"
)


@dataclass(frozen=True)
class TermStructureDataAudit:
    """Whole-source result for the free CME path."""

    verdict: str
    first_available_date: str | None
    last_available_date: str | None
    available_years: float
    archive_files: int
    sample_rows: int
    sample_symbols: tuple[str, ...]
    passing_markets: int
    required_passing_markets: int
    required_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    issues: tuple[str, ...]


def _coverage_years(first: date | None, last: date | None) -> float:
    if first is None or last is None:
        return 0.0
    return (last - first).days / 365.2425


def audit_inventory(
    inventory: ArchiveInventory,
    sample_rows: int = 0,
    sample_symbols: tuple[str, ...] = (),
) -> TermStructureDataAudit:
    """Apply non-negotiable source-level gates before bulk downloading."""
    first = inventory.first_date
    last = inventory.last_date
    coverage_years = _coverage_years(first, last)
    issues: list[str] = []

    if coverage_years < MIN_COVERAGE_YEARS:
        issues.append(
            f"public SPAN archive coverage is {coverage_years:.2f}y; "
            f"{MIN_COVERAGE_YEARS:.0f} complete years required"
        )
    missing_fields = sorted(set(REQUIRED_FIELDS) - set(PA2_FIELDS))
    if missing_fields:
        issues.append(
            "expanded PA2 settlement files do not contain required fields: "
            + ", ".join(missing_fields)
        )
    issues.append(
        "contract-month open interest is unavailable, so the OI-confirmed roll calendar "
        "cannot be derived"
    )
    if sample_rows and len(sample_symbols) < len(FIXED_UNIVERSE):
        issues.append(
            f"sample parser found {len(sample_symbols)}/{len(FIXED_UNIVERSE)} fixed-universe markets"
        )

    return TermStructureDataAudit(
        verdict="BLOCKED",
        first_available_date=first.isoformat() if first else None,
        last_available_date=last.isoformat() if last else None,
        available_years=coverage_years,
        archive_files=inventory.file_count,
        sample_rows=sample_rows,
        sample_symbols=sample_symbols,
        passing_markets=0,
        required_passing_markets=MIN_PASSING_MARKETS,
        required_fields=REQUIRED_FIELDS,
        available_fields=PA2_FIELDS,
        issues=tuple(issues),
    )


def build_manifest(
    audit: TermStructureDataAudit,
    command: str,
    provenance_path: Path,
) -> str:
    """Build the human-readable free-source audit."""
    lines = [
        "# CME Free Settlement Data Audit — 2026-06-30",
        "",
        f"## Verdict: {audit.verdict}",
        "",
        "This is a Tier-A data verdict only. It does not test roll yield and does not",
        "authorize strategy simulation, parameter changes, or live trading.",
        "",
        "## Command",
        "",
        "```bash",
        command,
        "```",
        "",
        "## What works",
        "",
        "- Anonymous CME FTP access works without credentials.",
        "- The adapter downloads final expanded PA2 files and parses type-P plus paired",
        "  type-81/type-82 records into decimal contract-month settlements.",
        "- Positive and negative settlements are preserved without ratio adjustment.",
        "- The normalized CSV includes source file and SHA256 provenance per row.",
        (
            f"- Parser smoke: {audit.sample_rows} settlements across "
            f"{len(audit.sample_symbols)}/{len(FIXED_UNIVERSE)} fixed-universe markets."
        ),
        "",
        "## Hard-gate result",
        "",
        f"- First public archive date: `{audit.first_available_date}`",
        f"- Last public archive date: `{audit.last_available_date}`",
        f"- Observed archive files: {audit.archive_files:,}",
        f"- Observed coverage: {audit.available_years:.2f} years",
        f"- Required coverage: {MIN_COVERAGE_YEARS:.0f} complete years per market",
        (
            f"- Passing markets: {audit.passing_markets}/{len(FIXED_UNIVERSE)} "
            f"(minimum {audit.required_passing_markets})"
        ),
        f"- Required daily fields: {', '.join(audit.required_fields)}",
        f"- PA2 fields available: {', '.join(audit.available_fields)}",
        "",
        "## Blocking evidence",
        "",
    ]
    lines.extend(f"- {issue}" for issue in audit.issues)
    lines.extend(
        [
            "",
            "## Source",
            "",
            f"- Public FTP root: `ftp://ftp.cmegroup.com{CME_SPAN_ARCHIVE_ROOT}/`",
            f"- CME SPAN page: {CME_SPAN_SOURCE_PAGE}",
            f"- Official expanded PA2 layout: {CME_SPAN_LAYOUT}",
            "- CME type-81 records carry high-precision settlement prices.",
            "- CME type-82 records carry the settlement sign used for negative prices.",
            "- CME type-P records carry decimal locators and contract value factors.",
            "",
            "## Decision",
            "",
            "The free CME PA2 path is implemented, but it cannot produce `DATA_PASS` under",
            "the existing lane contract. Bulk downloading the archive would consume substantial",
            "bandwidth and storage without repairing either hard blocker, so the verifier stops",
            "at source inventory plus parser smoke.",
            "",
            "Do not start Tier B. Continue only if a free official source is found for",
            "contract-month OHLC/open interest with at least 15 complete years, or if the owner",
            "authorizes a paid individual-contract source. Do not relax the 15-year or OI gates.",
            "",
            f"Machine-readable provenance: `{provenance_path}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_provenance(
    audit: TermStructureDataAudit,
    inventory: ArchiveInventory,
) -> dict[str, Any]:
    """Build machine-readable evidence for the data verdict."""
    return {
        "schema_version": 1,
        "lane": "term_structure_roll_yield",
        "stage": "tier_a_data_proof",
        "verdict": audit.verdict,
        "retrieved_at_utc": inventory.retrieved_at.isoformat(),
        "source": {
            "publisher": "CME Group",
            "ftp_host": "ftp.cmegroup.com",
            "ftp_root": CME_SPAN_ARCHIVE_ROOT,
            "span_source_page": CME_SPAN_SOURCE_PAGE,
            "span_layout": CME_SPAN_LAYOUT,
            "years": list(inventory.years),
            "files_by_year": {
                str(year): {
                    "count": len(names),
                    "first": names[0] if names else None,
                    "last": names[-1] if names else None,
                }
                for year, names in inventory.files_by_year.items()
            },
        },
        "audit": asdict(audit),
        "gate": {
            "minimum_coverage_years": MIN_COVERAGE_YEARS,
            "minimum_passing_markets": MIN_PASSING_MARKETS,
            "fixed_universe": [market.symbol for market in FIXED_UNIVERSE],
            "required_fields": list(REQUIRED_FIELDS),
        },
    }


def _command(args: argparse.Namespace) -> str:
    sample = (
        f" --sample-archive {args.sample_archive} --sample-date {args.sample_date.isoformat()}"
        if args.sample_archive
        else ""
    )
    return (
        "python -m research.new_edge.term_structure.data.verify_term_structure_data"
        f"{sample} --output {args.output} --provenance {args.provenance}"
    )


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def main() -> int:
    """Run the live free-source audit and optional parser smoke."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-archive", type=Path)
    parser.add_argument("--sample-date", type=_parse_date)
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--provenance", type=Path, default=Path(DEFAULT_PROVENANCE))
    args = parser.parse_args()
    if bool(args.sample_archive) != bool(args.sample_date):
        parser.error("--sample-archive and --sample-date must be supplied together")

    sample_rows = 0
    sample_symbols: tuple[str, ...] = ()
    try:
        inventory = CMEArchiveClient().inventory()
        if args.sample_archive:
            with tempfile.TemporaryDirectory() as directory:
                sample_dir = Path(directory)
                csv_path, _ = normalize_archive(
                    args.sample_archive,
                    args.sample_date,
                    sample_dir,
                )
                import pandas as pd

                frame = pd.read_csv(csv_path)
                sample_rows = len(frame)
                sample_symbols = tuple(sorted(set(frame["symbol"].astype(str))))
        audit = audit_inventory(inventory, sample_rows, sample_symbols)
    except (OSError, ValueError) as exc:
        logger.error("CME free-source audit failed: %s", exc)
        return 3

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.provenance.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        build_manifest(audit, _command(args), args.provenance),
        encoding="utf-8",
    )
    args.provenance.write_text(
        json.dumps(build_provenance(audit, inventory), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "CME free-source audit complete: verdict=%s files=%d coverage=%.2fy",
        audit.verdict,
        audit.archive_files,
        audit.available_years,
    )
    return 0 if audit.verdict == "DATA_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
