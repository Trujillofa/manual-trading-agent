#!/usr/bin/env python3
"""
Event / calendar lane data verifier (data proof only).

Per EVENT_CONTRACT_2026-06-18.md: verify historical calendar availability, timestamp
reliability, actual/forecast field coverage, look-ahead risk, and spread-widening
assumptions. No strategy or backtest code.

Usage (live/XML probe — legacy faireconomy path):
  python -m research.new_edge.events.data.verify_event_data \
    --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-18.md

Usage (pinned historical snapshot — primary data-proof path):
  python -m research.new_edge.events.data.verify_event_data \
    --input research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv \
    --provenance research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.provenance.json \
    --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-19.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from src.news.news_checker import NewsChecker

FOREX_FACTORY_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
HISTORICAL_URL_CANDIDATES = [
    "https://nfs.faireconomy.media/ff_calendar_lastweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_lastmonth.xml",
]
SAMPLE_XML = Path(__file__).resolve().parent / "sample_ff_thisweek.xml"
DEFAULT_PINNED_CSV = (
    Path(__file__).resolve().parent / "pinned" / "forex_factory_calendar_hf_2026-06-18.csv"
)
DEFAULT_PROVENANCE = (
    Path(__file__).resolve().parent
    / "pinned"
    / "forex_factory_calendar_hf_2026-06-18.provenance.json"
)

# Conservative release-window cost model (documented, not optimized)
BASE_SPREAD_PIPS_MAJORS = 2.0
RELEASE_WINDOW_SPREAD_MULT = 3.0
RELEASE_WINDOW_MINUTES = 15
RELEASE_SLIPPAGE_PIPS = 1.0

MIN_YEARS_COVERAGE = 5
MIN_HIGH_IMPACT_EVENTS = 200
MIN_FIELD_COVERAGE_PCT = 0.80
MIN_TIMESTAMP_PARSE_RATE = 0.95

NUMERIC_VALUE_RE = re.compile(
    r"^[-+]?(\d{1,3}(,\d{3})*|\d+)(\.\d+)?([KMB%]|bp|bps)?$",
    re.IGNORECASE,
)
VOTE_VALUE_RE = re.compile(r"^\d-\d-\d$")
CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")

# High-impact releases expected to carry numeric actual/forecast/previous (surprise lanes).
INDICATOR_EVENT_PATTERNS: tuple[str, ...] = (
    r"cpi|consumer price|ppi|producer price|inflation",
    r"gdp|gross domestic",
    r"non-farm|nfp|employment change|unemployment|jobless|adp non-farm|payroll",
    r"\bpmi\b|ism manufacturing|ism services",
    r"retail sales",
    r"rate decision|interest rate decision|official bank rate",
    r"trade balance|current account",
    r"industrial production",
    r"housing starts|building permits",
    r"durable goods",
    r"confidence|sentiment index",
    r"claimant count",
)


@dataclass(frozen=True)
class FieldAudit:
    total_events: int
    has_title: int
    has_country: int
    has_currency_tag: int
    has_date: int
    has_time: int
    has_impact: int
    has_forecast: int
    has_previous: int
    has_actual: int
    high_impact: int
    parser_success: int
    parser_fail_reasons: dict[str, int]


@dataclass
class HistoricalAudit:
    total_rows: int
    date_min_utc: datetime | None
    date_max_utc: datetime | None
    years_coverage: float
    timestamp_parse_ok: int
    timestamp_parse_failed: int
    timestamp_parse_rate: float
    timezone_offset_counts: dict[str, int]
    high_impact_count: int
    high_impact_numeric_count: int
    indicator_numeric_count: int
    field_coverage: dict[str, float]
    broad_field_coverage: dict[str, float]
    valid_currency_count: int
    invalid_currency_count: int
    invalid_currencies: dict[str, int]
    missing_timestamp_rows: int
    duplicate_event_keys: int
    duplicate_rows: int
    duplicate_warnings: list[str]
    event_family_counts: dict[str, int]
    provenance: dict[str, Any]
    input_sha256: str
    issues: list[str] = field(default_factory=list)


def _safe_text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"null", "nan", "none", "n/a"}


def _is_numeric_release_value(value: Any) -> bool:
    if _is_missing_value(value):
        return False
    text = str(value).strip().replace(" ", "")
    if text in {"--", "-"}:
        return False
    if "|" in text:
        parts = [p for p in text.split("|") if p]
        return bool(parts) and all(
            bool(NUMERIC_VALUE_RE.match(p)) or bool(VOTE_VALUE_RE.match(p)) for p in parts
        )
    return bool(NUMERIC_VALUE_RE.match(text) or VOTE_VALUE_RE.match(text))


def _is_indicator_numeric_event(event: Any) -> bool:
    name = str(event).lower()
    return any(re.search(pattern, name) for pattern in INDICATOR_EVENT_PATTERNS)


def _field_coverage(df: pd.DataFrame, columns: tuple[str, ...]) -> dict[str, float]:
    total = len(df)
    if total == 0:
        return {col.lower(): 0.0 for col in columns}
    return {
        col.lower(): df[col].map(lambda v: not _is_missing_value(v)).sum() / total
        for col in columns
    }


def _is_high_impact(impact: Any) -> bool:
    return "high impact" in str(impact).lower()


def _is_non_economic(impact: Any) -> bool:
    return "non-economic" in str(impact).lower()


def _normalize_currency(value: Any) -> str:
    return str(value).strip().upper()


def load_snapshot(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    raise ValueError(f"Unsupported snapshot format: {suffix} (use .csv or .parquet)")


def load_provenance(path: Path | None, input_path: Path) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "pinned_file": str(input_path),
        "sha256_computed": _sha256_file(input_path),
    }
    if path is None or not path.exists():
        provenance["provenance_file"] = None
        provenance["sha256_recorded"] = None
        provenance["sha256_match"] = None
        return provenance

    recorded = json.loads(path.read_text(encoding="utf-8"))
    provenance["provenance_file"] = str(path)
    provenance.update({k: v for k, v in recorded.items() if k != "sha256"})
    provenance["sha256_recorded"] = recorded.get("sha256")
    provenance["sha256_match"] = provenance["sha256_recorded"] == provenance["sha256_computed"]
    return provenance


def normalize_datetimes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int], int]:
    """Parse DateTime to UTC; return working frame, offset histogram, parse failures."""
    working = df.copy()
    raw = working["DateTime"].astype(str).str.strip()
    parsed = pd.to_datetime(raw, errors="coerce", utc=True)

    offset_counts: dict[str, int] = {}
    for value in raw:
        if "+" in value or value.endswith("Z"):
            key = "Z" if value.endswith("Z") else value[-6:] if len(value) >= 6 else value
            offset_counts[key] = offset_counts.get(key, 0) + 1
        else:
            offset_counts["naive_or_unknown"] = offset_counts.get("naive_or_unknown", 0) + 1

    working["datetime_utc"] = parsed
    failed = int(parsed.isna().sum())
    return working, offset_counts, failed


def audit_historical_snapshot(
    df: pd.DataFrame, provenance: dict[str, Any], input_sha256: str
) -> HistoricalAudit:
    working, offset_counts, parse_failed = normalize_datetimes(df)
    parse_ok = len(working) - parse_failed
    parse_rate = parse_ok / len(working) if len(working) else 0.0

    valid_ts = working["datetime_utc"].dropna()
    date_min = valid_ts.min().to_pydatetime() if not valid_ts.empty else None
    date_max = valid_ts.max().to_pydatetime() if not valid_ts.empty else None
    years_coverage = 0.0
    if date_min and date_max:
        years_coverage = (date_max - date_min).days / 365.25

    currencies = working["Currency"].map(_normalize_currency)
    valid_currency_mask = currencies.map(lambda c: bool(CURRENCY_CODE_RE.match(c)))
    invalid_series = currencies[~valid_currency_mask]
    invalid_currencies = (
        invalid_series.value_counts().head(20).to_dict() if len(invalid_series) else {}
    )

    high_impact_mask = working["Impact"].map(_is_high_impact)
    numeric_hi_mask = high_impact_mask & ~working["Impact"].map(_is_non_economic)
    hi_numeric = working[numeric_hi_mask]
    indicator_mask = numeric_hi_mask & working["Event"].map(_is_indicator_numeric_event)
    hi_indicator = working[indicator_mask]

    broad_field_coverage = _field_coverage(hi_numeric, ("Actual", "Forecast", "Previous"))
    field_coverage = _field_coverage(hi_indicator, ("Actual", "Forecast", "Previous"))

    event_keys = working.assign(
        currency_norm=currencies,
        event_name=working["Event"].astype(str).str.strip(),
    )
    keyed = event_keys.dropna(subset=["datetime_utc"]).copy()
    keyed["dedupe_key"] = (
        keyed["datetime_utc"].astype(str) + "|" + keyed["currency_norm"] + "|" + keyed["event_name"]
    )
    duplicate_rows = int(keyed["dedupe_key"].duplicated(keep=False).sum())
    duplicate_keys = int(keyed["dedupe_key"].duplicated(keep="first").sum())

    event_families: dict[str, int] = {}
    for pattern, label in (
        (r"non-farm|nfp", "nfp"),
        (r"\bcpi\b|consumer price", "cpi"),
        (r"gdp", "gdp"),
        (r"rate decision|interest rate|fomc|ecb|boe|boj", "rate_decision"),
        (r"pmi", "pmi"),
    ):
        count = int(working["Event"].str.contains(pattern, case=False, regex=True, na=False).sum())
        if count:
            event_families[label] = count

    duplicate_warnings: list[str] = []
    if duplicate_keys > 0:
        duplicate_warnings.append(
            f"Informational: {duplicate_keys} duplicate keys ({duplicate_rows} rows); "
            "review before deduplication in research adapter."
        )

    audit = HistoricalAudit(
        total_rows=len(working),
        date_min_utc=date_min,
        date_max_utc=date_max,
        years_coverage=years_coverage,
        timestamp_parse_ok=parse_ok,
        timestamp_parse_failed=parse_failed,
        timestamp_parse_rate=parse_rate,
        timezone_offset_counts=offset_counts,
        high_impact_count=int(high_impact_mask.sum()),
        high_impact_numeric_count=len(hi_numeric),
        indicator_numeric_count=len(hi_indicator),
        field_coverage=field_coverage,
        broad_field_coverage=broad_field_coverage,
        valid_currency_count=int(valid_currency_mask.sum()),
        invalid_currency_count=int((~valid_currency_mask).sum()),
        invalid_currencies={str(k): int(v) for k, v in invalid_currencies.items()},
        missing_timestamp_rows=parse_failed,
        duplicate_event_keys=duplicate_keys,
        duplicate_rows=duplicate_rows,
        duplicate_warnings=duplicate_warnings,
        event_family_counts=event_families,
        provenance=provenance,
        input_sha256=input_sha256,
    )
    audit.issues = _historical_issues(audit)
    return audit


def _historical_issues(audit: HistoricalAudit) -> list[str]:
    issues: list[str] = []

    if audit.years_coverage < MIN_YEARS_COVERAGE:
        issues.append(
            f"Coverage {audit.years_coverage:.2f} years < {MIN_YEARS_COVERAGE} years required."
        )
    if audit.high_impact_count < MIN_HIGH_IMPACT_EVENTS:
        issues.append(
            f"High-impact events {audit.high_impact_count} < {MIN_HIGH_IMPACT_EVENTS} required."
        )
    if audit.timestamp_parse_rate < MIN_TIMESTAMP_PARSE_RATE:
        issues.append(
            f"Timestamp parse rate {audit.timestamp_parse_rate:.1%} < "
            f"{MIN_TIMESTAMP_PARSE_RATE:.0%} required."
        )
    for field_name, rate in audit.field_coverage.items():
        if rate < MIN_FIELD_COVERAGE_PCT:
            issues.append(
                f"{field_name} coverage on indicator-class high-impact events {rate:.1%} < "
                f"{MIN_FIELD_COVERAGE_PCT:.0%} required."
            )
    if audit.invalid_currency_count > 0:
        top = next(iter(audit.invalid_currencies), "unknown")
        issues.append(
            f"Malformed currency codes on {audit.invalid_currency_count} rows (example: {top})."
        )
    if audit.missing_timestamp_rows > 0:
        issues.append(f"Missing/unparseable timestamps on {audit.missing_timestamp_rows} rows.")
    dup_rate = audit.duplicate_event_keys / audit.total_rows if audit.total_rows else 0.0
    if dup_rate > 0.01:
        issues.append(
            f"Duplicate event keys exceed 1% of rows: {audit.duplicate_event_keys} keys "
            f"({audit.duplicate_rows} rows)."
        )
    recorded = audit.provenance.get("sha256_recorded")
    if recorded is not None and recorded != audit.input_sha256:
        issues.append("Recorded provenance SHA256 does not match input file checksum.")
    if audit.indicator_numeric_count == 0:
        issues.append("No indicator-class high-impact numeric events found after filters.")

    return issues


def determine_historical_verdict(audit: HistoricalAudit) -> tuple[str, list[str]]:
    if audit.issues:
        return "BLOCKED_DATA_UNAVAILABLE", audit.issues
    return "DATA_PASS", []


def historical_look_ahead_audit() -> dict[str, str]:
    return {
        "forecast_previous": (
            "Forecast and Previous are treated as pre-release scheduled values in the HF archive. "
            "Safe for scheduled lockout / avoidance research when aligned to event datetime_utc. "
            "Revisions between scrape and release are possible; live use needs scrape-time discipline."
        ),
        "actual": (
            "Actual is post-release historical truth in this archive. Valid for backtest outcome "
            "labels and surprise measurement only when release-time discipline is defined: compare "
            "Actual to Forecast at or after datetime_utc, never before. Do not use Actual for "
            "pre-release decision logic unless publication timing is independently auditable."
        ),
        "production_parser": (
            "Live faireconomy XML remains incompatible with NewsChecker (<country> vs <currency>, "
            "date format mismatch, no <actual>). Historical research uses the pinned snapshot; "
            "production lockout still needs a separate live-feed fix."
        ),
        "provenance": (
            "Third-party community scrape (HuggingFace Ehsanrs2/Forex_Factory_Calendar). "
            "Pinned locally with SHA256; not an official Forex Factory API. "
            "Re-verify checksum before any research run."
        ),
    }


def fetch_live_xml(timeout: float = 15.0) -> tuple[str | None, str]:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(FOREX_FACTORY_THISWEEK)
            if response.status_code == 429:
                return None, "rate_limited_429"
            response.raise_for_status()
            return response.text, "live_fetch_ok"
    except httpx.HTTPError as exc:
        return None, f"http_error:{exc}"


def load_xml(source: str | None, sample_path: Path) -> tuple[str, str]:
    if source:
        return source, "live_or_provided"
    if sample_path.exists():
        return sample_path.read_text(encoding="utf-8", errors="replace"), "offline_sample"
    raise RuntimeError("No live XML and no offline sample available")


def probe_historical_urls() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=10.0) as client:
        for url in HISTORICAL_URL_CANDIDATES:
            try:
                response = client.get(url)
                results.append(
                    {
                        "url": url,
                        "status_code": response.status_code,
                        "available": response.status_code == 200 and len(response.text) > 100,
                    }
                )
            except httpx.HTTPError as exc:
                results.append(
                    {"url": url, "status_code": None, "available": False, "error": str(exc)}
                )
    return results


def audit_xml_fields(xml_text: str) -> FieldAudit:
    root = ET.fromstring(xml_text)
    events = root.findall(".//event")
    checker = NewsChecker()

    counts = {
        "has_title": 0,
        "has_country": 0,
        "has_currency_tag": 0,
        "has_date": 0,
        "has_time": 0,
        "has_impact": 0,
        "has_forecast": 0,
        "has_previous": 0,
        "has_actual": 0,
        "high_impact": 0,
        "parser_success": 0,
    }
    fail_reasons: dict[str, int] = {}

    for event_node in events:
        if _safe_text(event_node.find("title")):
            counts["has_title"] += 1
        if _safe_text(event_node.find("country")):
            counts["has_country"] += 1
        if _safe_text(event_node.find("currency")):
            counts["has_currency_tag"] += 1
        if _safe_text(event_node.find("date")):
            counts["has_date"] += 1
        if _safe_text(event_node.find("time")):
            counts["has_time"] += 1
        impact = _safe_text(event_node.find("impact"))
        if impact:
            counts["has_impact"] += 1
            if "high" in impact.lower():
                counts["high_impact"] += 1
        if _safe_text(event_node.find("forecast")):
            counts["has_forecast"] += 1
        if _safe_text(event_node.find("previous")):
            counts["has_previous"] += 1
        if _safe_text(event_node.find("actual")):
            counts["has_actual"] += 1

        parsed = checker._parse_event_node(event_node)
        if parsed is not None:
            counts["parser_success"] += 1
        else:
            reason = _parser_fail_reason(event_node, checker)
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1

    return FieldAudit(
        total_events=len(events),
        parser_fail_reasons=fail_reasons,
        **counts,
    )


def _parser_fail_reason(event_node: ET.Element, checker: NewsChecker) -> str:
    title = _safe_text(event_node.find("title"))
    currency = _safe_text(event_node.find("currency"))
    country = _safe_text(event_node.find("country"))
    date_text = _safe_text(event_node.find("date"))
    time_text = _safe_text(event_node.find("time"))
    if not currency:
        if country:
            return "missing_currency_tag_uses_country_instead"
        return "missing_currency"
    if not title:
        return "missing_title"
    if not date_text or not time_text:
        return "missing_date_or_time"
    if checker._parse_timestamp(date_text, time_text) is None:
        return "timestamp_parse_failed"
    return "other"


def look_ahead_audit(audit: FieldAudit) -> dict[str, str]:
    return {
        "forecast_previous": (
            "Available pre-event in live XML. Safe for scheduled lockout / avoidance only. "
            "Using forecast as a live surprise signal without timestamp discipline risks look-ahead "
            "if values are revised between scrape and release."
        ),
        "actual": (
            "NOT present in live faireconomy thisweek XML sample. Surprise-based lanes require "
            "a historical source with timestamped actual publication times. Scraping actual after "
            "the fact without release-time metadata is look-ahead leakage."
        ),
        "production_parser": (
            "NewsChecker._parse_event_node requires <currency> and YYYY-MM-DD dates. Live feed uses "
            "<country> for currency codes and MM-DD-YYYY dates. Production live feed parsing is "
            "currently incompatible with the published XML schema."
        ),
    }


def spread_widening_model() -> dict[str, Any]:
    release_spread = BASE_SPREAD_PIPS_MAJORS * RELEASE_WINDOW_SPREAD_MULT
    round_trip = 2 * (release_spread + RELEASE_SLIPPAGE_PIPS)
    return {
        "base_spread_pips_majors": BASE_SPREAD_PIPS_MAJORS,
        "release_window_minutes": RELEASE_WINDOW_MINUTES,
        "release_spread_multiplier": RELEASE_WINDOW_SPREAD_MULT,
        "release_spread_pips": release_spread,
        "release_slippage_pips_per_side": RELEASE_SLIPPAGE_PIPS,
        "round_trip_cost_pips_conservative": round_trip,
        "note": (
            "Conservative default for data-proof phase. Any post-event drift lane must show "
            "expected move exceeds this round-trip on median high-impact events."
        ),
    }


def determine_verdict(
    audit: FieldAudit,
    historical_probes: list[dict[str, Any]],
    xml_source: str,
) -> tuple[str, list[str]]:
    issues: list[str] = []

    any_historical = any(p.get("available") for p in historical_probes)
    if not any_historical:
        issues.append(
            "No historical calendar feed available from faireconomy URLs "
            "(thisweek only; lastweek/nextweek/lastmonth return 404)."
        )

    if audit.total_events == 0:
        issues.append("Zero events parsed from XML sample.")

    parse_rate = audit.parser_success / audit.total_events if audit.total_events else 0.0
    if parse_rate < 0.95:
        top_failure = "n/a"
        if audit.parser_fail_reasons:
            top_failure = max(audit.parser_fail_reasons, key=lambda k: audit.parser_fail_reasons[k])
        issues.append(
            f"NewsChecker parser success rate {parse_rate:.1%} < 95% on feed XML "
            f"(top failure: {top_failure})."
        )

    if audit.has_actual == 0:
        issues.append(
            "No <actual> field in feed XML; surprise lanes blocked without external historical source."
        )

    if audit.has_currency_tag == 0 and audit.has_country > 0:
        issues.append(
            "Feed uses <country> for currency codes; production parser expects <currency> tag."
        )

    if issues:
        return "BLOCKED", issues
    return "PASS", []


def build_historical_manifest(
    audit: HistoricalAudit,
    command: str,
    verdict: str,
    issues: list[str],
    input_path: Path,
) -> str:
    lookahead = historical_look_ahead_audit()
    spread = spread_widening_model()
    prov = audit.provenance

    lines = [
        "# Event / Calendar Data Manifest — HF snapshot (2026-06-19)",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## Command",
        "```bash",
        command,
        "```",
        "",
        "## Historical snapshot",
        "",
        f"- Input: `{input_path}`",
        f"- Rows: {audit.total_rows:,}",
        f"- SHA256 (computed): `{audit.input_sha256}`",
        f"- SHA256 (recorded): `{prov.get('sha256_recorded', 'n/a')}`",
        f"- SHA256 match: {prov.get('sha256_match', 'n/a')}",
        f"- Source: {prov.get('source_url', 'n/a')}",
        f"- Pinned at: {prov.get('pinned_at_utc', 'n/a')}",
        "",
        "## Coverage gates",
        "",
        f"- Date range (UTC): {audit.date_min_utc} → {audit.date_max_utc}",
        f"- Years coverage: {audit.years_coverage:.2f} (gate ≥ {MIN_YEARS_COVERAGE})",
        f"- High-impact events: {audit.high_impact_count:,} (gate ≥ {MIN_HIGH_IMPACT_EVENTS})",
        f"- High-impact non-economic events: {audit.high_impact_numeric_count:,}",
        f"- Indicator-class high-impact events (coverage gate): {audit.indicator_numeric_count:,}",
        "",
        "## Timezone audit",
        "",
        f"- Timestamp parse OK: {audit.timestamp_parse_ok:,}/{audit.total_rows:,} "
        f"({audit.timestamp_parse_rate:.1%}; gate ≥ {MIN_TIMESTAMP_PARSE_RATE:.0%})",
        f"- Parse failures: {audit.timestamp_parse_failed:,}",
        f"- Missing timestamp rows: {audit.missing_timestamp_rows:,}",
        "",
        "### Original offset distribution (pre-UTC normalization)",
        "",
    ]
    for offset, count in sorted(audit.timezone_offset_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- `{offset}`: {count:,}")

    lines.extend(
        [
            "",
            "## Field coverage (indicator-class high-impact events — surprise-lane population)",
            "",
        ]
    )
    for field_name, rate in audit.field_coverage.items():
        lines.append(f"- {field_name}: {rate:.1%} (gate ≥ {MIN_FIELD_COVERAGE_PCT:.0%})")

    lines.extend(
        [
            "",
            "## Field coverage (all high-impact non-economic — informational)",
            "",
        ]
    )
    for field_name, rate in audit.broad_field_coverage.items():
        lines.append(f"- {field_name}: {rate:.1%}")

    lines.extend(
        [
            "",
            "## Currency audit",
            "",
            f"- Valid currency rows: {audit.valid_currency_count:,}/{audit.total_rows:,}",
            f"- Invalid currency rows: {audit.invalid_currency_count:,}",
            "",
        ]
    )
    if audit.invalid_currencies:
        lines.append("### Invalid currency samples")
        lines.append("")
        for code, count in sorted(audit.invalid_currencies.items(), key=lambda x: -x[1]):
            lines.append(f"- {code}: {count}")
        lines.append("")

    lines.extend(
        [
            "## Duplicate / integrity checks",
            "",
            f"- Duplicate keys (datetime+currency+event): {audit.duplicate_event_keys:,}",
            f"- Rows involved in duplicates: {audit.duplicate_rows:,}",
            "",
        ]
    )
    for warning in audit.duplicate_warnings:
        lines.append(f"- {warning}")
    lines.extend(
        [
            "",
            "## Event family counts (indicative)",
            "",
        ]
    )
    for family, count in sorted(audit.event_family_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {family}: {count:,}")

    lines.extend(["", "## Look-ahead audit", ""])
    for key, note in lookahead.items():
        lines.append(f"### {key}")
        lines.append(note)
        lines.append("")

    lines.extend(["## Spread widening model (conservative)", ""])
    for key, value in spread.items():
        lines.append(f"- {key}: {value}")

    if issues:
        lines.extend(["", "## Blocking issues", ""])
        for issue in issues:
            lines.append(f"- {issue}")

    lines.extend(["", "## Next step", ""])
    if verdict == "DATA_PASS":
        lines.append(
            "Historical data proof passed. Proceed to smallest gross-first event falsifier "
            "(avoidance or post-release drift) per EVENT_CONTRACT. Still no optimization. "
            "Actual is label-only after release-time discipline."
        )
    else:
        lines.append(
            "Close Event/Calendar lane as BLOCKED_DATA_UNAVAILABLE. "
            "Move to Volatility Regime / Range Compression Breakout (plan item #4). "
            "**Do not write event strategy or backtest.**"
        )

    return "\n".join(lines)


def build_manifest(
    audit: FieldAudit,
    historical_probes: list[dict[str, Any]],
    live_status: str,
    xml_source: str,
    command: str,
    verdict: str,
    issues: list[str],
) -> str:
    lookahead = look_ahead_audit(audit)
    spread = spread_widening_model()
    parse_rate = audit.parser_success / audit.total_events if audit.total_events else 0.0

    lines = [
        "# Event / Calendar Data Manifest — 2026-06-18",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## Command",
        "```bash",
        command,
        "```",
        "",
        f"## XML source: {xml_source} (live status: {live_status})",
        "",
        "## Historical feed probe",
        "",
    ]
    for probe in historical_probes:
        lines.append(
            f"- `{probe['url']}` → HTTP {probe.get('status_code')} "
            f"(available={probe.get('available')})"
        )
    lines.extend(
        [
            "",
            "## Field coverage (live/sample XML)",
            "",
            f"- Total events: {audit.total_events}",
            f"- High impact: {audit.high_impact}",
            f"- Has title: {audit.has_title}/{audit.total_events}",
            f"- Has country (currency code): {audit.has_country}/{audit.total_events}",
            f"- Has currency tag (parser expects): {audit.has_currency_tag}/{audit.total_events}",
            f"- Has date: {audit.has_date}/{audit.total_events}",
            f"- Has time: {audit.has_time}/{audit.total_events}",
            f"- Has forecast: {audit.has_forecast}/{audit.total_events}",
            f"- Has previous: {audit.has_previous}/{audit.total_events}",
            f"- Has actual: {audit.has_actual}/{audit.total_events}",
            f"- NewsChecker parse success: {audit.parser_success}/{audit.total_events} ({parse_rate:.1%})",
            "",
            "### Parser failure reasons",
            "",
        ]
    )
    if audit.parser_fail_reasons:
        for reason, count in sorted(audit.parser_fail_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- (none)")

    lines.extend(["", "## Look-ahead audit", ""])
    for key, note in lookahead.items():
        lines.append(f"### {key}")
        lines.append(note)
        lines.append("")

    lines.extend(["## Spread widening model (conservative)", ""])
    for key, value in spread.items():
        lines.append(f"- {key}: {value}")

    if issues:
        lines.extend(["", "## Blocking issues", ""])
        for issue in issues:
            lines.append(f"- {issue}")

    lines.extend(
        [
            "",
            "## Next step",
            "",
        ]
    )
    if verdict == "BLOCKED":
        lines.append(
            "Obtain a verified **historical** economic calendar (≥5 years, timestamped actual/forecast) "
            "from a documented third-party archive (e.g. checked-in CSV with provenance). "
            "Fix or bypass NewsChecker schema mismatch. Re-run this verifier. "
            "**Do not write event strategy or backtest until manifest passes.**"
        )
    else:
        lines.append("Proceed to smallest gross-first event falsifier per EVENT_CONTRACT.")

    return "\n".join(lines)


def run_historical_verification(
    input_path: Path,
    provenance_path: Path | None,
    output_path: Path,
) -> tuple[str, list[str]]:
    df = load_snapshot(input_path)
    required_cols = {"DateTime", "Currency", "Impact", "Event", "Actual", "Forecast", "Previous"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Snapshot missing required columns: {sorted(missing)}")

    input_sha256 = _sha256_file(input_path)
    provenance = load_provenance(provenance_path, input_path)
    audit = audit_historical_snapshot(df, provenance, input_sha256)
    verdict, issues = determine_historical_verdict(audit)

    command = (
        "python -m research.new_edge.events.data.verify_event_data "
        f"--input {input_path} "
        f"--provenance {provenance_path or 'none'} "
        f"--output {output_path}"
    )
    manifest = build_historical_manifest(audit, command, verdict, issues, input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(manifest, encoding="utf-8")
    return verdict, issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Event/calendar lane data proof verifier")
    parser.add_argument(
        "--output",
        default="docs/research/events/EVENT_DATA_MANIFEST_2026-06-18.md",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Pinned historical snapshot (.csv or .parquet). Skips XML/faireconomy probe.",
    )
    parser.add_argument(
        "--provenance",
        default=None,
        help="Optional provenance JSON with recorded SHA256 and source metadata.",
    )
    parser.add_argument(
        "--xml",
        default=None,
        help="Optional path to XML file for live-feed probe (legacy mode only)",
    )
    args = parser.parse_args()

    out_path = Path(args.output)

    if args.input:
        input_path = Path(args.input)
        provenance_path = Path(args.provenance) if args.provenance else None
        verdict, issues = run_historical_verification(input_path, provenance_path, out_path)
        print(f"Manifest written to {out_path}")
        print(f"Verdict: {verdict}")
        for issue in issues:
            print(f"  - {issue}")
        return

    command = f"python -m research.new_edge.events.data.verify_event_data --output {args.output}"

    live_xml, live_status = fetch_live_xml()
    if args.xml:
        xml_text = Path(args.xml).read_text(encoding="utf-8", errors="replace")
        xml_source = f"user_file:{args.xml}"
    else:
        xml_text, xml_source = load_xml(live_xml, SAMPLE_XML)
        if live_xml is None:
            live_status = f"{live_status}; using offline sample"

    audit = audit_xml_fields(xml_text)
    historical_probes = probe_historical_urls()
    verdict, issues = determine_verdict(audit, historical_probes, xml_source)
    manifest = build_manifest(
        audit, historical_probes, live_status, xml_source, command, verdict, issues
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(manifest, encoding="utf-8")

    print(f"Manifest written to {out_path}")
    print(f"Verdict: {verdict}")
    for issue in issues:
        print(f"  - {issue}")


if __name__ == "__main__":
    main()
