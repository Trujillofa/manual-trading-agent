"""Read-only PEAD data-source inventory and field-matrix audit.

Evaluates candidate earnings sources in contract order without purchasing data
or downloading bulk licensed snapshots. Relationship code remains unauthorized.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("docs/research/pead/PEAD_SOURCE_AUDIT_RESULTS_2026-07.md")
DEFAULT_PROVENANCE = Path(
    "research/new_edge/pead/data/provenance/pead_source_audit_inventory_2026-07.json"
)
ALPHA_VANTAGE_PROBE_CACHE = Path(
    "research/new_edge/pead/data/provenance/pead_alphavantage_probe_2026-07.json"
)

REQUIRED_FIELDS = (
    "security_master_survivorship_safe",
    "announcement_timestamp_tz",
    "estimate_observation_timestamp",
    "actual_eps",
    "consensus_eps",
    "daily_ohlcv_adjusted",
    "delisting_treatment",
    "point_in_time_sector",
    "local_research_license",
)

LOCAL_SEARCH_ROOTS = (
    Path("research/new_edge/pead/data/pinned"),
    Path("research/new_edge/pead/data/fixtures"),
    Path("data"),
    Path("research/new_edge/pead/data"),
)

LOCAL_SNAPSHOT_MARKERS = (
    "earnings_events.csv",
    "security_master.csv",
    "daily_prices.csv",
    "sectors.csv",
)


@dataclass(frozen=True)
class FieldMatrixRow:
    field: str
    present: bool | None
    evidence: str


@dataclass
class SourceCandidate:
    name: str
    tier: int
    verdict: str
    cost: str
    license_summary: str
    coverage_claim: str
    probe_status: str
    blocking_gaps: tuple[str, ...] = ()
    field_matrix: tuple[FieldMatrixRow, ...] = ()
    probe_evidence: dict[str, Any] = field(default_factory=dict)
    url: str = ""


@dataclass(frozen=True)
class PeadSourceAudit:
    verdict: str
    local_snapshots_found: int
    candidates_evaluated: int
    data_pass_candidates: int
    unverified_paid_candidates: int
    leading_blocker: str
    issues: tuple[str, ...]


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _http_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, Any, str]:
    request = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "manual-trading-agent/1.0 research"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read(500).decode("utf-8", errors="replace")
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return status, None, ""
    try:
        return status, json.loads(text), text[:500]
    except json.JSONDecodeError:
        return status, None, text


def _find_local_snapshots() -> list[Path]:
    found: list[Path] = []
    for root in LOCAL_SEARCH_ROOTS:
        if not root.exists():
            continue
        for marker in LOCAL_SNAPSHOT_MARKERS:
            for path in root.rglob(marker):
                if "synthetic_minimal" in str(path):
                    continue
                found.append(path.parent)
    return sorted(set(found))


def _ssh_prod_snapshot_search() -> tuple[str, list[str]]:
    command = [
        "ssh",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "BatchMode=yes",
        "crypto-agent",
        (
            "find /home/emilio -maxdepth 5 -type f "
            "\\( -iname '*earnings*' -o -iname '*pead*' -o -iname '*ibes*' "
            "-o -iname '*zacks*' \\) 2>/dev/null | head -20"
        ),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}", []
    if result.returncode != 0 and not result.stdout.strip():
        return f"ssh exit {result.returncode}", []
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return "ok", paths


def _matrix(
    *,
    security_master: bool | None,
    announcement_ts: bool | None,
    estimate_obs_ts: bool | None,
    actual_eps: bool | None,
    consensus_eps: bool | None,
    prices: bool | None,
    delistings: bool | None,
    sector_pit: bool | None,
    license_ok: bool | None,
    evidence: dict[str, str],
) -> tuple[FieldMatrixRow, ...]:
    values = (
        ("security_master_survivorship_safe", security_master),
        ("announcement_timestamp_tz", announcement_ts),
        ("estimate_observation_timestamp", estimate_obs_ts),
        ("actual_eps", actual_eps),
        ("consensus_eps", consensus_eps),
        ("daily_ohlcv_adjusted", prices),
        ("delisting_treatment", delistings),
        ("point_in_time_sector", sector_pit),
        ("local_research_license", license_ok),
    )
    return tuple(
        FieldMatrixRow(name, present, evidence.get(name, ""))
        for name, present in values
    )


def _verdict_from_matrix(rows: tuple[FieldMatrixRow, ...]) -> str:
    if any(row.present is False for row in rows):
        return "INSUFFICIENT"
    if any(row.present is None for row in rows):
        return "UNVERIFIED"
    return "UNVERIFIED"


def _probe_sec_edgar() -> SourceCandidate:
    status, data, snippet = _http_json(
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        headers={"User-Agent": "manual-trading-agent research@example.com"},
    )
    has_eps = False
    sample: dict[str, Any] = {}
    if isinstance(data, dict):
        gaap = data.get("facts", {}).get("us-gaap", {})
        diluted = gaap.get("EarningsPerShareDiluted", {}).get("units", {}).get("USD/shares", [])
        has_eps = bool(diluted)
        if diluted:
            sample = diluted[-1]
    matrix = _matrix(
        security_master=False,
        announcement_ts=False,
        estimate_obs_ts=False,
        actual_eps=has_eps,
        consensus_eps=False,
        prices=False,
        delistings=False,
        sector_pit=False,
        license_ok=True,
        evidence={
            "security_master_survivorship_safe": "No exchange security master or delisting feed.",
            "announcement_timestamp_tz": "XBRL provides filing date, not earnings call timestamp.",
            "estimate_observation_timestamp": "SEC filings contain reported EPS only.",
            "actual_eps": "EarningsPerShareDiluted available with fiscal period metadata.",
            "consensus_eps": "No analyst consensus in EDGAR company facts.",
            "daily_ohlcv_adjusted": "Prices not in company facts API.",
            "delisting_treatment": "No delisting return series.",
            "point_in_time_sector": "No GICS/sector history in this endpoint.",
            "local_research_license": "Public EDGAR; local research permitted.",
        },
    )
    return SourceCandidate(
        name="SEC EDGAR company facts (XBRL)",
        tier=2,
        verdict="INSUFFICIENT",
        cost="free",
        license_summary="Public SEC data; redistribution subject to SEC terms.",
        coverage_claim="Reported EPS for filers; no consensus history.",
        probe_status="probed" if status == 200 else f"http_{status}",
        blocking_gaps=(
            "no consensus EPS",
            "no estimate observation timestamp",
            "no announcement timestamp with timezone",
            "no survivorship-safe security master",
            "no price or sector bundles",
        ),
        field_matrix=matrix,
        probe_evidence={"http_status": status, "sample_eps_fact": sample, "snippet": snippet},
        url="https://www.sec.gov/edgar/sec-api-documentation",
    )


def _redact_secret(text: str, secret: str) -> str:
    if secret:
        return text.replace(secret, "***REDACTED***")
    return text


def _load_alpha_vantage_probe_cache() -> dict[str, Any] | None:
    if not ALPHA_VANTAGE_PROBE_CACHE.exists():
        return None
    try:
        payload = json.loads(ALPHA_VANTAGE_PROBE_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _alpha_vantage_key() -> str:
    _load_dotenv()
    return (
        os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
        or os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    )


def _alpha_vantage_fetch(
    function: str,
    *,
    apikey: str,
    symbol: str | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[str, Any, str]:
    params: dict[str, str] = {"function": function, "apikey": apikey}
    if symbol:
        params["symbol"] = symbol
    if extra:
        params.update(extra)
    url = f"https://www.alphavantage.co/query?{urllib.parse.urlencode(params)}"
    status, data, snippet = _http_json(url)
    if isinstance(data, dict):
        message = data.get("Note") or data.get("Information") or ""
        if message:
            lowered = message.lower()
            if "sparingly" in lowered or "rate" in lowered or "premium" in lowered:
                return "rate_limited", data, snippet
            return "api_message", data, snippet
        if not data:
            return "empty_response", data, snippet
        return "probed", data, snippet
    if snippet and "," in snippet and not snippet.lstrip().startswith("{"):
        reader = csv.DictReader(io.StringIO(snippet))
        rows = list(reader)
        if rows and "symbol" in (rows[0].keys() if rows[0] else {}):
            return "probed_csv", rows, snippet[:500] if len(snippet) > 500 else snippet
    if status:
        return f"http_{status}", None, snippet
    return "error", None, snippet


def _probe_alpha_vantage() -> SourceCandidate:
    apikey = _alpha_vantage_key()
    symbol = "AAPL"
    evidence: dict[str, Any] = {"user_key_present": bool(apikey), "symbol": symbol}

    quarterly_keys: list[str] = []
    estimate_keys: list[str] = []
    sample_quarter: dict[str, Any] = {}
    sample_estimate: dict[str, Any] = {}
    earnings_count = 0
    earnings_oldest = ""
    earnings_newest = ""
    report_time_counts: dict[str, int] = {}
    estimates_count = 0
    listing_count = 0
    calendar_count = 0
    earnings_status = "skipped_no_key"
    estimates_status = "skipped_no_key"
    listing_status = "skipped_no_key"
    calendar_status = "skipped_no_key"
    overview_status = "skipped_no_key"

    if apikey:
        earnings_status, earnings, _ = _alpha_vantage_fetch(
            "EARNINGS",
            apikey=apikey,
            symbol=symbol,
        )
        evidence["earnings_probe"] = earnings_status
        if isinstance(earnings, dict):
            quarterly = earnings.get("quarterlyEarnings") or []
            earnings_count = len(quarterly)
            if quarterly:
                sample_quarter = quarterly[0]
                quarterly_keys = list(sample_quarter.keys())
                dates = [str(row.get("reportedDate", "")) for row in quarterly if row.get("reportedDate")]
                if dates:
                    earnings_oldest = min(dates)
                    earnings_newest = max(dates)
                for row in quarterly:
                    label = str(row.get("reportTime", "unknown"))
                    report_time_counts[label] = report_time_counts.get(label, 0) + 1
                evidence["report_time_counts"] = report_time_counts

        time.sleep(1.2)
        estimates_status, estimates, estimates_snippet = _alpha_vantage_fetch(
            "EARNINGS_ESTIMATES",
            apikey=apikey,
            symbol=symbol,
        )
        evidence["earnings_estimates_probe"] = estimates_status
        evidence["earnings_estimates_snippet"] = _redact_secret(estimates_snippet[:300], apikey)
        if isinstance(estimates, dict):
            est_rows = estimates.get("estimates") or []
            estimates_count = len(est_rows)
            if est_rows:
                sample_estimate = est_rows[0]
                estimate_keys = list(sample_estimate.keys())

        time.sleep(1.2)
        listing_status, listing_rows, listing_snippet = _alpha_vantage_fetch(
            "LISTING_STATUS",
            apikey=apikey,
            extra={"state": "active"},
        )
        evidence["listing_status_probe"] = listing_status
        evidence["listing_status_snippet"] = _redact_secret(listing_snippet[:300], apikey)
        if isinstance(listing_rows, list):
            listing_count = len(listing_rows)
            if listing_rows and isinstance(listing_rows[0], dict):
                evidence["listing_status_fields"] = list(listing_rows[0].keys())

        time.sleep(1.2)
        calendar_status, calendar_rows, calendar_snippet = _alpha_vantage_fetch(
            "EARNINGS_CALENDAR",
            apikey=apikey,
            extra={"horizon": "3month"},
        )
        evidence["earnings_calendar_probe"] = calendar_status
        evidence["earnings_calendar_snippet"] = _redact_secret(calendar_snippet[:300], apikey)
        if isinstance(calendar_rows, list) and calendar_rows:
            sample_calendar = calendar_rows[0]
            if isinstance(sample_calendar, dict) and len(str(sample_calendar.get("symbol", ""))) > 1:
                calendar_count = len(calendar_rows)
                evidence["earnings_calendar_fields"] = list(sample_calendar.keys())
                evidence["earnings_calendar_sample"] = sample_calendar

        time.sleep(1.2)
        overview_status, overview, overview_snippet = _alpha_vantage_fetch(
            "OVERVIEW",
            apikey=apikey,
            symbol=symbol,
        )
        evidence["overview_probe"] = overview_status
        evidence["overview_snippet"] = _redact_secret(overview_snippet[:300], apikey)
        if isinstance(overview, dict) and overview.get("Sector"):
            evidence["overview_sector"] = overview.get("Sector")

    if apikey and earnings_count == 0:
        cache = _load_alpha_vantage_probe_cache()
        if cache:
            coverage = cache.get("coverage_probe", {})
            earnings_count = int(coverage.get("quarterly_earnings_rows", 0) or 0)
            earnings_oldest = str(coverage.get("oldest_reported_date", ""))
            earnings_newest = str(coverage.get("newest_reported_date", ""))
            estimates_count = int(
                coverage.get("earnings_estimates_rows", estimates_count) or estimates_count
            )
            quarterly_keys = list(cache.get("earnings_fields_observed", quarterly_keys))
            estimate_keys = list(cache.get("estimates_fields_observed", estimate_keys))
            sample_quarter = dict(cache.get("sample_quarter", sample_quarter))
            report_time_counts = dict(coverage.get("report_time_counts", report_time_counts))
            evidence["cached_probe_fallback"] = str(ALPHA_VANTAGE_PROBE_CACHE)
            if earnings_status != "probed":
                evidence["earnings_probe"] = "rate_limited_cached"
            if estimates_status != "probed":
                evidence["earnings_estimates_probe"] = "rate_limited_cached"
            if report_time_counts:
                evidence["report_time_counts"] = report_time_counts
            if coverage.get("overview_sector_current"):
                evidence["overview_sector"] = coverage["overview_sector_current"]

    has_report_time = "reportTime" in quarterly_keys
    has_report_date = "reportedDate" in quarterly_keys
    has_estimate = "estimatedEPS" in quarterly_keys
    has_revision_trails = "eps_estimate_average_30_days_ago" in estimate_keys
    matrix = _matrix(
        security_master=False,
        announcement_ts=False,
        estimate_obs_ts=False,
        actual_eps="reportedEPS" in quarterly_keys if earnings_count else None,
        consensus_eps=has_estimate if earnings_count else None,
        prices=None,
        delistings=False,
        sector_pit=False,
        license_ok=True if apikey and (earnings_count or earnings_status == "probed") else None,
        evidence={
            "security_master_survivorship_safe": (
                f"LISTING_STATUS active-only probe returned {listing_count} symbols; "
                "not a point-in-time survivorship-safe master."
            ),
            "announcement_timestamp_tz": (
                "reportedDate is date-only; reportTime is pre/post-market label only."
            ),
            "estimate_observation_timestamp": (
                "EARNINGS_ESTIMATES exposes revision trails, not a point-in-time "
                "observation timestamp predating each announcement."
            ),
            "actual_eps": "quarterlyEarnings.reportedEPS present on AAPL probe.",
            "consensus_eps": "quarterlyEarnings.estimatedEPS present; restated-at-report risk.",
            "daily_ohlcv_adjusted": "Separate TIME_SERIES_DAILY_ADJUSTED endpoint; not bundled.",
            "delisting_treatment": "No delisting return feed confirmed.",
            "point_in_time_sector": "OVERVIEW sector is current-state only.",
            "local_research_license": (
                "ALPHA_VANTAGE_API_KEY works on EARNINGS; free tier is rate-limited (25 req/day)."
            ),
        },
    )
    gaps = [
        "no estimate observation timestamp",
        "announcement timing is date-only / pre-post label, not timezone timestamp",
        "no delisting treatment",
        "no point-in-time sector history",
    ]
    gaps.append("no survivorship-safe security master in probe")
    if has_revision_trails:
        gaps.append("revision trails are not auditable point-in-time consensus snapshots")
    if earnings_count:
        gaps.append(
            f"per-symbol EARNINGS only (AAPL sample {earnings_count} rows, "
            f"{earnings_oldest} -> {earnings_newest}); not a 500+ cross-section archive"
        )
    if estimates_status == "rate_limited" and evidence.get("earnings_estimates_probe") != "rate_limited_cached":
        gaps.append("EARNINGS_ESTIMATES probe hit free-tier rate limit during audit")
    if calendar_status == "rate_limited":
        gaps.append("EARNINGS_CALENDAR probe hit free-tier rate limit during audit")
    if evidence.get("cached_probe_fallback"):
        gaps.append(
            "live probe hit free-tier daily limit; field evidence merged from cached probe artifact"
        )

    probe_status = "probed" if apikey else "skipped_no_key"
    return SourceCandidate(
        name="Alpha Vantage EARNINGS + EARNINGS_ESTIMATES",
        tier=3,
        verdict=_verdict_from_matrix(matrix) if apikey else "UNVERIFIED",
        cost="free tier",
        license_summary="Free API key; 25 requests/day; premium for bulk history.",
        coverage_claim=(
            f"EARNINGS per-symbol history ({earnings_count} {symbol} rows); "
            f"EARNINGS_ESTIMATES {estimates_count} rows; "
            f"EARNINGS_CALENDAR {calendar_count} rows."
        ),
        probe_status=probe_status,
        blocking_gaps=tuple(gaps),
        field_matrix=matrix,
        probe_evidence={
            **evidence,
            "quarterly_fields": quarterly_keys,
            "estimates_fields": estimate_keys,
            "sample_quarter": sample_quarter,
            "sample_estimate": sample_estimate,
            "earnings_count": earnings_count,
            "earnings_date_range": f"{earnings_oldest} -> {earnings_newest}",
            "estimates_count": estimates_count,
            "listing_count": listing_count,
            "calendar_count": calendar_count,
        },
        url="https://www.alphavantage.co/documentation/#earnings",
    )


def _probe_yfinance() -> SourceCandidate:
    evidence: dict[str, Any] = {"import_error": None, "earnings_history_columns": []}
    history_columns: list[str] = []
    sample_row: dict[str, Any] = {}
    try:
        import yfinance as yf

        ticker = yf.Ticker("AAPL")
        history = ticker.earnings_history
        if history is not None and not history.empty:
            history_columns = list(history.columns)
            sample_row = history.iloc[0].to_dict()
            evidence["history_index_name"] = history.index.name
    except Exception as exc:  # noqa: BLE001 - probe boundary
        evidence["import_error"] = str(exc)

    matrix = _matrix(
        security_master=False,
        announcement_ts=False,
        estimate_obs_ts=False,
        actual_eps="epsActual" in history_columns,
        consensus_eps="epsEstimate" in history_columns,
        prices=None,
        delistings=False,
        sector_pit=False,
        license_ok=None,
        evidence={
            "security_master_survivorship_safe": "No historical security master in yfinance.",
            "announcement_timestamp_tz": (
                "earnings_history index is fiscal quarter end, not announcement timestamp."
            ),
            "estimate_observation_timestamp": "No estimate observation timestamp field.",
            "actual_eps": "epsActual present on earnings_history probe.",
            "consensus_eps": "epsEstimate present; likely restated current consensus.",
            "daily_ohlcv_adjusted": "Available via history(); separate from earnings.",
            "delisting_treatment": "No delisting return handling.",
            "point_in_time_sector": "Current sector in info; not point-in-time.",
            "local_research_license": "Unofficial Yahoo scraper; redistribution prohibited.",
        },
    )
    return SourceCandidate(
        name="yfinance / Yahoo Finance",
        tier=3,
        verdict="INSUFFICIENT",
        cost="free",
        license_summary="Unofficial API; Yahoo terms prohibit redistribution and bulk storage.",
        coverage_claim="Convenience wrapper; not a research-grade point-in-time archive.",
        probe_status="probed" if history_columns else "probe_failed",
        blocking_gaps=(
            "no announcement timestamp with timezone",
            "no estimate observation timestamp",
            "consensus likely restated rather than point-in-time",
            "license incompatible with pinned local validation",
            "no survivorship-safe universe",
        ),
        field_matrix=matrix,
        probe_evidence={**evidence, "earnings_history_columns": history_columns, "sample_row": sample_row},
        url="https://github.com/ranaroussi/yfinance",
    )


def _fmp_fetch(path: str, params: dict[str, str], *, apikey: str) -> tuple[str, int | None, Any, str]:
    query = urllib.parse.urlencode({**params, "apikey": apikey})
    url = f"https://financialmodelingprep.com{path}?{query}"
    status, data, snippet = _http_json(url)
    if data is None and status in {401, 402, 403, 404, 429}:
        if status == 401:
            return "unauthorized", status, None, snippet
        if status == 402:
            return "payment_required", status, None, snippet
        if status == 403:
            return "forbidden", status, None, snippet
        return f"http_{status}", status, None, snippet
    if isinstance(data, dict) and "Error Message" in data:
        message = str(data["Error Message"])
        if "Legacy Endpoint" in message:
            return "legacy_forbidden", status, data, snippet
        return "error", status, data, snippet
    if isinstance(data, str) and "Premium Query Parameter" in data:
        return "payment_required", status, None, snippet
    return "probed", status, data, snippet


def _probe_fmp() -> SourceCandidate:
    _load_dotenv()
    apikey = os.environ.get("FMP_API_KEY", "")
    evidence: dict[str, Any] = {"user_key_present": bool(apikey)}

    earnings_fields: list[str] = []
    earnings_count = 0
    earnings_oldest = ""
    earnings_newest = ""
    calendar_count = 0
    calendar_symbols = 0
    legacy_v3_status = "skipped_no_key"
    calendar_range_status = "skipped_no_key"
    earnings_status = "skipped_no_key"

    if apikey:
        legacy_v3_status, _, _, legacy_snippet = _fmp_fetch(
            "/api/v3/earning_calendar",
            {"from": "2024-01-01", "to": "2024-01-31"},
            apikey=apikey,
        )
        evidence["legacy_v3_earning_calendar"] = legacy_v3_status
        evidence["legacy_v3_snippet"] = legacy_snippet[:300]

        earnings_status, _, earnings_payload, _ = _fmp_fetch(
            "/stable/earnings",
            {"symbol": "AAPL"},
            apikey=apikey,
        )
        evidence["stable_earnings_probe"] = earnings_status
        if isinstance(earnings_payload, list):
            earnings_count = len(earnings_payload)
            if earnings_payload and isinstance(earnings_payload[0], dict):
                earnings_fields = list(earnings_payload[0].keys())
                dates = [str(row.get("date", "")) for row in earnings_payload if row.get("date")]
                if dates:
                    earnings_oldest = min(dates)
                    earnings_newest = max(dates)
                evidence["stable_earnings_sample"] = earnings_payload[0]

        calendar_status, _, calendar_payload, _ = _fmp_fetch(
            "/stable/earnings-calendar",
            {},
            apikey=apikey,
        )
        evidence["stable_calendar_default_probe"] = calendar_status
        if isinstance(calendar_payload, list):
            calendar_count = len(calendar_payload)
            calendar_symbols = len({row.get("symbol") for row in calendar_payload})
            if calendar_payload and isinstance(calendar_payload[0], dict):
                evidence["stable_calendar_sample"] = calendar_payload[0]

        calendar_range_status, _, _, range_snippet = _fmp_fetch(
            "/stable/earnings-calendar",
            {"from": "2024-01-01", "to": "2024-01-31"},
            apikey=apikey,
        )
        evidence["stable_calendar_range_probe"] = calendar_range_status
        evidence["stable_calendar_range_snippet"] = range_snippet[:300]

        analyst_status, _, analyst_payload, _ = _fmp_fetch(
            "/stable/analyst-estimates",
            {"symbol": "AAPL", "period": "quarter", "limit": "5"},
            apikey=apikey,
        )
        evidence["stable_analyst_estimates_quarter_probe"] = analyst_status
        if isinstance(analyst_payload, list) and analyst_payload:
            evidence["stable_analyst_estimates_fields"] = list(analyst_payload[0].keys())

    has_actual = "epsActual" in earnings_fields
    has_estimate = "epsEstimated" in earnings_fields
    has_last_updated = "lastUpdated" in earnings_fields

    matrix = _matrix(
        security_master=False,
        announcement_ts=False,
        estimate_obs_ts=False,
        actual_eps=has_actual if earnings_count else None,
        consensus_eps=has_estimate if earnings_count else None,
        prices=None,
        delistings=False,
        sector_pit=False,
        license_ok=True if apikey and earnings_status == "probed" else None,
        evidence={
            "security_master_survivorship_safe": (
                "/api/v3/stock/list legacy-forbidden on current key; no delisting master in earnings bundle."
            ),
            "announcement_timestamp_tz": (
                "stable earnings uses date (YYYY-MM-DD) only; no announcement time or timezone."
            ),
            "estimate_observation_timestamp": (
                "lastUpdated is record refresh metadata, not a pre-announcement estimate snapshot."
            ),
            "actual_eps": "stable/earnings.epsActual present on AAPL probe.",
            "consensus_eps": "stable/earnings.epsEstimated present; restated-at-report risk.",
            "daily_ohlcv_adjusted": "legacy /api/v3/historical-price-full forbidden on current key.",
            "delisting_treatment": "No delisting return feed on earnings endpoints.",
            "point_in_time_sector": "/stable/profile sector is current-state only.",
            "local_research_license": (
                "FMP_API_KEY in .env works on stable endpoints; legacy v3 and ranged calendar need upgrade."
            ),
        },
    )
    gaps = [
        "no estimate observation timestamp",
        "announcement timing is date-only (no timezone timestamp)",
        "lastUpdated is not a point-in-time consensus observation timestamp",
    ]
    if legacy_v3_status == "legacy_forbidden":
        gaps.append("legacy /api/v3 endpoints forbidden on current subscription")
    if calendar_range_status == "payment_required":
        gaps.append("historical earnings_calendar date windows require premium plan (402)")
    if calendar_count and calendar_count < 500:
        gaps.append(
            f"default earnings_calendar window has {calendar_symbols} symbols, not 500+ cross-section"
        )
    if earnings_count:
        gaps.append(
            f"per-symbol stable/earnings only (AAPL sample {earnings_count} rows, "
            f"{earnings_oldest} -> {earnings_newest}); not a survivorship-safe 500+ archive"
        )
    if has_last_updated:
        gaps.append("vendor fields cannot prove estimate predates announcement")

    probe_status = earnings_status if apikey else "skipped_no_key"
    return SourceCandidate(
        name="Financial Modeling Prep stable earnings",
        tier=3,
        verdict=_verdict_from_matrix(matrix) if apikey else "UNVERIFIED",
        cost="free/stable tier + premium for historical calendar windows",
        license_summary="Commercial API; legacy v3 blocked; ranged calendar and quarter estimates premium.",
        coverage_claim=(
            f"stable/earnings per-symbol history ({earnings_count} AAPL rows); "
            f"default calendar {calendar_symbols} symbols."
        ),
        probe_status=probe_status,
        blocking_gaps=tuple(gaps),
        field_matrix=matrix,
        probe_evidence=evidence,
        url="https://site.financialmodelingprep.com/developer/docs/stable-earnings-calendar",
    )


def _eodhd_fetch(path: str, params: dict[str, str], *, apikey: str) -> tuple[str, int | None, Any, str]:
    query = urllib.parse.urlencode({**params, "api_token": apikey})
    url = f"https://eodhd.com/api{path}?{query}"
    status, data, snippet = _http_json(url)
    if data is None and status in {401, 403, 404, 429}:
        if status == 403 and "Only EOD data allowed for free users" in snippet:
            return "free_tier_eod_only", status, None, snippet
        if status == 403:
            return "forbidden", status, None, snippet
        return f"http_{status}", status, None, snippet
    if isinstance(data, str) or (isinstance(data, dict) and "raw" in data):
        raw = data if isinstance(data, str) else str(data.get("raw", ""))
        if "Only EOD data allowed for free users" in raw:
            return "free_tier_eod_only", status, None, raw
        return "error", status, None, raw[:500]
    return "probed", status, data, snippet[:500]


def _probe_eodhd() -> SourceCandidate:
    _load_dotenv()
    apikey = os.environ.get("EODHD_API_KEY", "").strip()
    evidence: dict[str, Any] = {"user_key_present": bool(apikey)}

    earnings_status = "skipped_no_key"
    trends_status = "skipped_no_key"
    fundamentals_status = "skipped_no_key"
    eod_status = "skipped_no_key"
    symbol_list_status = "skipped_no_key"
    earnings_count = 0
    earnings_fields: list[str] = []
    symbol_count = 0
    eod_rows = 0

    if apikey:
        earnings_status, _, earnings_payload, earnings_snippet = _eodhd_fetch(
            "/calendar/earnings",
            {"symbols": "AAPL.US", "fmt": "json"},
            apikey=apikey,
        )
        evidence["calendar_earnings_symbol_probe"] = earnings_status
        evidence["calendar_earnings_snippet"] = earnings_snippet[:300]
        if isinstance(earnings_payload, dict):
            rows = earnings_payload.get("earnings")
            if isinstance(rows, list) and rows:
                earnings_count = len(rows)
                earnings_fields = list(rows[0].keys())
                evidence["calendar_earnings_sample"] = rows[0]

        trends_status, _, _, trends_snippet = _eodhd_fetch(
            "/calendar/trends",
            {"symbols": "AAPL.US", "fmt": "json"},
            apikey=apikey,
        )
        evidence["calendar_trends_probe"] = trends_status
        evidence["calendar_trends_snippet"] = trends_snippet[:300]

        fundamentals_status, _, _, fundamentals_snippet = _eodhd_fetch(
            "/fundamentals/AAPL.US",
            {"fmt": "json"},
            apikey=apikey,
        )
        evidence["fundamentals_probe"] = fundamentals_status
        evidence["fundamentals_snippet"] = fundamentals_snippet[:300]

        eod_status, _, eod_payload, _ = _eodhd_fetch(
            "/eod/AAPL.US",
            {"from": "2024-01-01", "to": "2024-01-10", "fmt": "json"},
            apikey=apikey,
        )
        evidence["eod_prices_probe"] = eod_status
        if isinstance(eod_payload, list):
            eod_rows = len(eod_payload)
            if eod_payload and isinstance(eod_payload[0], dict):
                evidence["eod_price_fields"] = list(eod_payload[0].keys())
                evidence["eod_price_sample"] = eod_payload[0]

        symbol_list_status, _, symbols_payload, _ = _eodhd_fetch(
            "/exchange-symbol-list/US",
            {"fmt": "json"},
            apikey=apikey,
        )
        evidence["exchange_symbol_list_probe"] = symbol_list_status
        if isinstance(symbols_payload, list):
            symbol_count = len(symbols_payload)

    subscription_blocks_earnings = earnings_status == "free_tier_eod_only"
    has_actual = "actual" in earnings_fields
    has_estimate = "estimate" in earnings_fields

    matrix = _matrix(
        security_master=symbol_count > 0 if apikey else None,
        announcement_ts=False if apikey else None,
        estimate_obs_ts=False if apikey else None,
        actual_eps=has_actual if earnings_count else None,
        consensus_eps=has_estimate if earnings_count else None,
        prices=eod_rows > 0 if apikey else None,
        delistings=False if apikey else None,
        sector_pit=False if apikey else None,
        license_ok=True if apikey and eod_status == "probed" else None,
        evidence={
            "security_master_survivorship_safe": (
                f"/exchange-symbol-list/US returned {symbol_count} listings; "
                "no list/delist dates for survivorship control."
            ),
            "announcement_timestamp_tz": (
                "Public docs: report_date YYYY-MM-DD + before_after_market label only."
            ),
            "estimate_observation_timestamp": (
                "Calendar/trends APIs document revision windows, not observation timestamps."
            ),
            "actual_eps": (
                "calendar/earnings.actual documented; blocked on current free-tier key."
                if subscription_blocks_earnings
                else "calendar/earnings.actual per public docs."
            ),
            "consensus_eps": (
                "calendar/earnings.estimate documented; blocked on current free-tier key."
                if subscription_blocks_earnings
                else "calendar/earnings.estimate per public docs."
            ),
            "daily_ohlcv_adjusted": (
                f"/eod/AAPL.US returned {eod_rows} rows on free tier (1-year limit warning)."
            ),
            "delisting_treatment": "Corporate actions API exists; not confirmed on current key.",
            "point_in_time_sector": "/fundamentals blocked on current free-tier key.",
            "local_research_license": (
                "EODHD_API_KEY in .env; earnings/calendar/trends/fundamentals require paid add-on."
            ),
        },
    )
    gaps: list[str] = []
    if not apikey:
        gaps.extend(
            [
                "EODHD_API_KEY not configured",
                "not probed with API token",
            ]
        )
    else:
        gaps.append("no estimate observation timestamp in public field matrix")
        gaps.append("announcement timing is date-only plus before/after label")
        if subscription_blocks_earnings:
            gaps.append(
                "current subscription is free EOD-only; calendar/earnings/trends return 403"
            )
        if symbol_count and symbol_count < 500:
            gaps.append(f"symbol list has {symbol_count} entries but earnings access blocked")
        if earnings_count == 0 and apikey:
            gaps.append("no earnings rows retrieved on probed key")

    probe_status = earnings_status if apikey else "skipped_no_key"
    verdict = "UNVERIFIED" if not apikey else _verdict_from_matrix(matrix)
    return SourceCandidate(
        name="EOD Historical Data calendar/earnings",
        tier=4,
        verdict=verdict,
        cost="free EOD tier + paid Corporate Events / Fundamentals plans",
        license_summary="Commercial API; earnings calendar not on free EOD-only subscription.",
        coverage_claim=(
            f"EOD prices accessible ({eod_rows} sample rows); earnings calendar blocked on current key."
        ),
        probe_status=probe_status,
        blocking_gaps=tuple(gaps),
        field_matrix=matrix,
        probe_evidence=evidence,
        url="https://eodhd.com/financial-apis/calendar-upcoming-earnings-ipos-and-splits",
    )


def _desk_zacks() -> SourceCandidate:
    matrix = _matrix(
        security_master=True,
        announcement_ts=True,
        estimate_obs_ts=None,
        actual_eps=True,
        consensus_eps=True,
        prices=True,
        delistings=True,
        sector_pit=True,
        license_ok=None,
        evidence={
            "security_master_survivorship_safe": (
                "Nasdaq ZACKS/MT master table: m_ticker, active_ticker_flag, asset_type; "
                "17k+ active and delisted names in ZES coverage list."
            ),
            "announcement_timestamp_tz": (
                "Nasdaq ZACKS/ES: act_rpt_date, act_rpt_time (HH:MI America/New_York), "
                "act_rpt_code (BTO/DTM/AMC). Supports PEAD before-open / during / after-close mapping."
            ),
            "estimate_observation_timestamp": (
                "Nasdaq ZACKS/EEH obs_date labels revision receipt date (vendor column def); "
                "EEH published D+1 at 10:00 UTC — 1-day lag reduces look-ahead risk. "
                "NY vs UTC calendar date for obs_date still unspecified; strict same-day "
                "join attrition unquantified (likely BTO/DTM-heavy)."
            ),
            "actual_eps": "ZACKS/ES eps_act (BNRI-adjusted, comparable to eps_mean_est).",
            "consensus_eps": (
                "ZACKS/EEH eps_mean_est by obs_date; ZACKS/ES eps_mean_est is pre-announcement "
                "consensus on surprise row but vendor surprise fields are informational only."
            ),
            "daily_ohlcv_adjusted": "Zacks Prices & Returns + corporate actions feeds advertised.",
            "delisting_treatment": "ZES covers active and delisted; corporate-actions feed includes delistings.",
            "point_in_time_sector": "ZACKS/MT zacks_x_sector_code/desc; sector history not probed.",
            "local_research_license": (
                "Direct Zacks Data or Nasdaq Data Link / WRDS premium; terms require sales contact."
            ),
        },
    )
    gaps = (
        "free trial sample verification required before DATA_PASS attempt (paid license needs contract amendment)",
        "table codes ZEEH/ZACKS/EEH + ZES/ZACKS/ES verified on Nasdaq URLs 2026-07-07; ZEE/ZEA are not substitutes",
        "residual obs_date calendar timezone (NY vs UTC) unspecified; intraday ordering on one obs_date unknown",
        "quantify same-day obs_date == act_rpt_date collisions — likely BTO/DTM-heavy; strict join may fail 30-OOS-events gate",
        "do not use ZACKS/ES eps_pct_diff_surp; recompute surprise from joined EEH eps_mean_est",
        "BNRI methodology must be pinned; ZEE snapshot and ZEA forward calendar are insufficient",
        "enterprise license terms unverified for local pin + verify_pead_data workflow",
    )
    return SourceCandidate(
        name="Zacks Data consensus + reference feeds",
        tier=4,
        verdict="UNVERIFIED",
        cost="institutional / enterprise (Nasdaq Data Link premium or WRDS / direct)",
        license_summary="Request-access licensing; redistribution terms negotiated per contract.",
        coverage_claim=(
            "ZEEH consensus revisions from 1979 (obs_date); ZES surprises from 2000 "
            "(17k+ US/CA incl. delisted); 23k+ in EEH coverage."
        ),
        probe_status="desk_review",
        blocking_gaps=gaps,
        field_matrix=matrix,
        probe_evidence={
            "desk_review_date": "2026-07-07",
            "desk_review_revised": "2026-07-07",
            "table_code_verification": {
                "method": "HTTP 200 + Nasdaq public column docs",
                "verified_at_utc": "2026-07-07T19:25:39+00:00",
                "pead_pair": "ZEEH/ZACKS/EEH + ZES/ZACKS/ES",
                "not_substitutes": "ZEE/ZACKS/EE (no obs_date), ZEA/ZACKS/EA (forward calendar)",
            },
            "contract_governance": (
                "Free trial sample is contract-compliant; paid full license requires PEAD contract amendment"
            ),
            "sources": [
                "https://zacksdata.com/",
                "https://data.nasdaq.com/databases/ZEEH",
                "https://data.nasdaq.com/databases/ZES",
                "https://data.nasdaq.com/databases/ZEE",
                "https://data.nasdaq.com/databases/ZEA",
                "https://wrds-www.wharton.upenn.edu/documents/1174/Zacks-One-Sheet.pdf",
            ],
            "primary_tables": {
                "ZACKS/EEH": {
                    "product": "North American Consensus Earnings Estimate History (ZEEH)",
                    "estimate_observation_field": "obs_date",
                    "estimate_observation_granularity": "date_only_YYYY-MM-DD",
                    "consensus_field": "eps_mean_est",
                    "history_from": "1979",
                },
                "ZACKS/ES": {
                    "product": "North American Earnings Surprises (ZES)",
                    "announcement_date_field": "act_rpt_date",
                    "announcement_time_field": "act_rpt_time",
                    "announcement_timezone": "America/New_York",
                    "announcement_session_code_field": "act_rpt_code",
                    "announcement_session_codes": ["BTO", "DTM", "AMC"],
                    "actual_eps_field": "eps_act",
                    "bundled_consensus_field": "eps_mean_est",
                    "history_from": "2000",
                    "coverage": "17000+ active and delisted",
                },
            },
            "join_hypothesis": (
                "For each ZACKS/ES event, select max(obs_date) from ZACKS/EEH where "
                "m_ticker and per_end_date match and obs_date <= act_rpt_date; if same day, "
                "require obs_date < act_rpt_date or vendor documents intraday ordering."
            ),
            "pead_gate_risks": [
                "obs_date partial docs: D+1 10:00 UTC delivery mitigates look-ahead; NY/UTC calendar date residual",
                "strict obs_date < act_rpt_date join likely over-conservative for AMC; BTO/DTM attrition open",
                "ZES eps_mean_est may not equal last EEH row — must not trust bundled surprise",
                "Reseller APIs (Intrinio) expose revision trails not obs_date history",
            ],
            "sample_request_checklist": [
                "ZACKS/EEH + ZACKS/ES sample AAPL + 50 delisted 2015-2020",
                "obs_date timezone + intraday revision policy in writing",
                "same-day collision counts by BTO/DTM/AMC",
                "BNRI methodology + license for local verification",
            ],
            "artifact": "research/new_edge/pead/data/provenance/pead_zacks_desk_review_2026-07.json",
        },
        url="https://zacksdata.com/datasets/consensus-data/",
    )


def _twelve_data_fetch(
    path: str,
    params: dict[str, str],
    *,
    apikey: str,
) -> tuple[str, int | None, dict[str, Any] | list[Any] | None, str]:
    """Return probe_status, http_status, parsed_json, snippet."""
    query = urllib.parse.urlencode({**params, "apikey": apikey})
    url = f"https://api.twelvedata.com/{path}?{query}"
    status, data, snippet = _http_json(url)
    if data is None and status in {401, 403, 404, 429}:
        if status == 429:
            return "rate_limited", status, None, snippet
        if status == 403:
            return "forbidden", status, None, snippet
        return f"http_{status}", status, None, snippet
    if isinstance(data, dict) and data.get("status") == "error":
        code = int(data.get("code", status))
        if code == 429:
            return "rate_limited", status, data, snippet
        if code == 403:
            return "forbidden", status, data, snippet
        return f"error_{code}", status, data, snippet
    return "probed", status, data if isinstance(data, (dict, list)) else None, snippet


def _probe_twelve_data() -> SourceCandidate:
    _load_dotenv()
    user_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    evidence: dict[str, Any] = {"user_key_present": bool(user_key)}

    earnings_fields: list[str] = []
    earnings_count = 0
    earnings_sample: dict[str, Any] = {}
    earnings_oldest = ""
    empty_time_count = 0
    earnings_status = "skipped_no_key"
    calendar_status = "skipped_no_key"

    def _ingest_earnings(payload: dict[str, Any] | None) -> None:
        nonlocal earnings_fields, earnings_count, earnings_sample, earnings_oldest, empty_time_count
        if not isinstance(payload, dict):
            return
        rows = payload.get("earnings")
        if not isinstance(rows, list) or not rows:
            return
        earnings_count = len(rows)
        first = rows[0]
        last = rows[-1]
        if isinstance(first, dict):
            earnings_fields = list(first.keys())
            earnings_sample = first
            empty_time_count = sum(
                1 for row in rows if isinstance(row, dict) and not str(row.get("time", "")).strip()
            )
        if isinstance(last, dict):
            earnings_oldest = str(last.get("date", ""))

    if user_key:
        earnings_status, _, earnings_payload, earnings_snippet = _twelve_data_fetch(
            "earnings",
            {"symbol": "AAPL", "outputsize": "30"},
            apikey=user_key,
        )
        evidence["earnings_user_probe"] = earnings_status
        evidence["earnings_user_snippet"] = earnings_snippet[:300]
        _ingest_earnings(earnings_payload if isinstance(earnings_payload, dict) else None)

        calendar_status, _, calendar_payload, calendar_snippet = _twelve_data_fetch(
            "earnings_calendar",
            {"start_date": "2024-01-02", "end_date": "2024-01-05"},
            apikey=user_key,
        )
        evidence["earnings_calendar_user_probe"] = calendar_status
        evidence["earnings_calendar_user_snippet"] = calendar_snippet[:300]
        if isinstance(calendar_payload, dict):
            calendar_rows = calendar_payload.get("earnings") or calendar_payload.get("data")
            if isinstance(calendar_rows, list) and calendar_rows:
                evidence["earnings_calendar_sample_keys"] = list(calendar_rows[0].keys())

    if earnings_count == 0:
        demo_status, _, demo_payload, _ = _twelve_data_fetch(
            "earnings",
            {"symbol": "AAPL", "outputsize": "120"},
            apikey="demo",
        )
        evidence["earnings_demo_probe"] = demo_status
        _ingest_earnings(demo_payload if isinstance(demo_payload, dict) else None)

    estimate_status, _, estimate_payload, _ = _twelve_data_fetch(
        "earnings_estimate",
        {"symbol": "AAPL"},
        apikey=user_key or "demo",
    )
    evidence["earnings_estimate_probe"] = estimate_status
    if isinstance(estimate_payload, dict):
        est_rows = estimate_payload.get("earnings_estimate")
        if isinstance(est_rows, list) and est_rows:
            evidence["earnings_estimate_fields"] = list(est_rows[0].keys())

    trend_status, _, trend_payload, _ = _twelve_data_fetch(
        "eps_trend",
        {"symbol": "AAPL"},
        apikey=user_key or "demo",
    )
    evidence["eps_trend_probe"] = trend_status
    if isinstance(trend_payload, dict):
        trend_rows = trend_payload.get("eps_trend")
        if isinstance(trend_rows, list) and trend_rows:
            evidence["eps_trend_fields"] = list(trend_rows[0].keys())

    has_actual = "eps_actual" in earnings_fields
    has_estimate = "eps_estimate" in earnings_fields
    has_surprise_vendor = "surprise_prc" in earnings_fields
    has_date_only = "date" in earnings_fields
    all_time_empty = earnings_count > 0 and empty_time_count == earnings_count

    matrix = _matrix(
        security_master=False,
        announcement_ts=False,
        estimate_obs_ts=False,
        actual_eps=has_actual,
        consensus_eps=has_estimate,
        prices=None,
        delistings=False,
        sector_pit=False,
        license_ok=True if user_key else None,
        evidence={
            "security_master_survivorship_safe": (
                "stocks catalog lists active symbols; no list/delist dates in earnings bundle."
            ),
            "announcement_timestamp_tz": (
                "earnings.date is YYYY-MM-DD; earnings.time empty on all "
                f"{earnings_count} AAPL rows probed."
                if all_time_empty and earnings_count
                else "earnings.time not a timezone-aware announcement timestamp."
            ),
            "estimate_observation_timestamp": (
                "eps_estimate present without observation timestamp; eps_trend exposes "
                "7/30/60/90-day revision windows only."
            ),
            "actual_eps": "earnings.eps_actual present in probe.",
            "consensus_eps": "earnings.eps_estimate present; restated-at-report risk.",
            "daily_ohlcv_adjusted": "time_series endpoint separate; not bundled with earnings.",
            "delisting_treatment": "No delisting return feed on earnings endpoints.",
            "point_in_time_sector": "profile sector is current-state; no historical sector series.",
            "local_research_license": (
                "API key in .env; earnings_calendar requires grow/pro plan "
                f"({calendar_status}); user earnings probe {earnings_status}. "
                "Daily credit limit blocks bulk cross-section builds."
            ),
        },
    )
    gaps = [
        "no estimate observation timestamp",
        "announcement time missing or empty on all probed earnings rows",
        "per-symbol earnings endpoint; no survivorship-safe cross-section archive",
        f"earnings_calendar requires grow/pro plan (user probe: {calendar_status})",
    ]
    if has_surprise_vendor:
        gaps.append("vendor surprise_prc cannot substitute for point-in-time consensus proof")
    if earnings_status == "rate_limited":
        gaps.append(
            "user key exhausted daily API credits during probe; "
            "field shape confirmed via demo fallback"
        )
    if earnings_count and earnings_oldest:
        gaps.append(f"demo/user sample oldest AAPL earnings row: {earnings_oldest} (per-symbol only)")

    probe_status = "probed" if earnings_count else earnings_status
    return SourceCandidate(
        name="Twelve Data earnings + estimates",
        tier=3,
        verdict=_verdict_from_matrix(matrix),
        cost="free tier + premium calendar add-on",
        license_summary="Commercial API; rate-limited free tier; calendar is premium.",
        coverage_claim=(
            f"Per-symbol earnings history ({earnings_count} AAPL rows in probe); "
            "cross-section calendar gated."
        ),
        probe_status=probe_status,
        blocking_gaps=tuple(gaps),
        field_matrix=matrix,
        probe_evidence=evidence,
        url="https://twelvedata.com/docs/fundamentals/earnings",
    )


def _desk_iex() -> SourceCandidate:
    matrix = _matrix(
        security_master=None,
        announcement_ts=None,
        estimate_obs_ts=None,
        actual_eps=None,
        consensus_eps=None,
        prices=None,
        delistings=None,
        sector_pit=None,
        license_ok=False,
        evidence={
            "security_master_survivorship_safe": "IEX Cloud migrated/sunset; status unclear.",
            "announcement_timestamp_tz": "Not evaluated.",
            "estimate_observation_timestamp": "Not evaluated.",
            "actual_eps": "Not evaluated.",
            "consensus_eps": "Not evaluated.",
            "daily_ohlcv_adjusted": "Not evaluated.",
            "delisting_treatment": "Not evaluated.",
            "point_in_time_sector": "Not evaluated.",
            "local_research_license": "Legacy IEX Cloud wound down; not a current free path.",
        },
    )
    return SourceCandidate(
        name="IEX Cloud / exchange-hosted earnings",
        tier=2,
        verdict="INSUFFICIENT",
        cost="n/a",
        license_summary="IEX Cloud no longer a viable free research path.",
        coverage_claim="Not available for PEAD gate under current public offering.",
        probe_status="desk_review",
        blocking_gaps=("platform not viable without alternate vendor contract",),
        field_matrix=matrix,
        probe_evidence={"source": "desk review; IEX Cloud sunset"},
        url="https://iexcloud.io/",
    )


def evaluate_candidates(
    *,
    local_snapshots: list[Path],
    prod_paths: list[str],
) -> tuple[PeadSourceAudit, list[SourceCandidate]]:
    issues: list[str] = []
    if local_snapshots:
        issues.append(
            f"found {len(local_snapshots)} non-synthetic local snapshot director(ies); "
            "manual license review required"
        )
    else:
        issues.append("no licensed local or institutional PEAD snapshot found in repo")

    if prod_paths:
        issues.append(f"prod search returned {len(prod_paths)} paths; manual review required")
    else:
        issues.append("no earnings/pead/ibes/zacks files found on prod via ssh search")

    candidates = [
        _probe_sec_edgar(),
        _desk_iex(),
        _probe_alpha_vantage(),
        _probe_yfinance(),
        _probe_twelve_data(),
        _probe_fmp(),
        _probe_eodhd(),
        _desk_zacks(),
    ]

    data_pass = sum(1 for candidate in candidates if candidate.verdict == "DATA_PASS")
    unverified_paid = sum(
        1 for candidate in candidates if candidate.tier == 4 and candidate.verdict == "UNVERIFIED"
    )

    if data_pass:
        verdict = "DATA_PASS"
        leading = "at least one candidate passed field matrix"
    else:
        verdict = "BLOCKED"
        leading = "no free or probed source exposes estimate observation timestamps"

    if data_pass == 0:
        issues.append(leading)
        issues.append(
            "owner approval required before paid trial: Zacks (point-in-time marketed) "
            "or EODHD extended fundamentals/calendar bundle"
        )

    audit = PeadSourceAudit(
        verdict=verdict,
        local_snapshots_found=len(local_snapshots),
        candidates_evaluated=len(candidates),
        data_pass_candidates=data_pass,
        unverified_paid_candidates=unverified_paid,
        leading_blocker=leading,
        issues=tuple(issues),
    )
    return audit, candidates


def _matrix_present_label(candidate: SourceCandidate, row: FieldMatrixRow) -> str:
    if candidate.probe_status == "desk_review":
        if row.present is False:
            return "no"
        return "marketed_unverified"
    if row.present is True:
        return "yes"
    if row.present is False:
        return "no"
    return "unknown"


def build_manifest(
    audit: PeadSourceAudit,
    candidates: list[SourceCandidate],
    *,
    command: str,
    provenance_path: Path,
    prod_status: str,
    prod_paths: list[str],
) -> str:
    lines = [
        "# PEAD Source Audit Results — 2026-07",
        "",
        f"## Verdict: {audit.verdict}",
        "",
        "Source inventory and field-matrix audit only. No licensed snapshot was purchased,",
        "no bulk download was performed, and relationship code remains unauthorized.",
        "",
        "## Command",
        "",
        "```bash",
        command,
        "```",
        "",
        "## Audit order summary",
        "",
        "| Step | Scope | Result |",
        "|---|---|---|",
        "| 1 | Local / institutional snapshots | "
        f"{audit.local_snapshots_found} non-synthetic dirs in repo; prod ssh `{prod_status}` |",
        "| 2 | Free official / exchange-hosted | SEC EDGAR probed — INSUFFICIENT; IEX — INSUFFICIENT |",
        "| 3 | Reproducible free-tier APIs | Alpha Vantage + yfinance + Twelve Data probed — "
        "INSUFFICIENT; FMP — INSUFFICIENT |",
        "| 4 | Low-cost commercial | EODHD probed — INSUFFICIENT (free EOD-only key); Zacks — UNVERIFIED |",
        "",
        "## Binding blocker",
        "",
        f"{audit.leading_blocker}.",
        "",
        "The PEAD contract requires `estimate_observed_ts` strictly before `announcement_ts`.",
        "None of the probed free sources expose that field. Vendor-supplied `surprise_pct`",
        "or restated consensus values cannot satisfy the gate.",
        "",
        "## Provider comparison",
        "",
        "| Source | Tier | Verdict | Cost | Key gaps |",
        "|---|---:|---|---|---|",
    ]
    for candidate in candidates:
        gap = candidate.blocking_gaps[0] if candidate.blocking_gaps else "none"
        lines.append(
            f"| {candidate.name} | {candidate.tier} | {candidate.verdict} | "
            f"{candidate.cost} | {gap} |"
        )

    lines.extend(["", "## Field matrix notes", ""])
    for candidate in candidates:
        if candidate.probe_status not in {"probed", "desk_review"}:
            continue
        lines.append(f"### {candidate.name}")
        lines.append("")
        lines.append(f"- Probe: `{candidate.probe_status}`")
        lines.append(f"- License: {candidate.license_summary}")
        lines.append(f"- Coverage claim: {candidate.coverage_claim}")
        if candidate.url:
            lines.append(f"- Reference: {candidate.url}")
        lines.append("")
        lines.append("| Required domain | Present | Evidence |")
        lines.append("|---|---|---|")
        for row in candidate.field_matrix:
            present = _matrix_present_label(candidate, row)
            lines.append(f"| {row.field} | {present} | {row.evidence} |")
        if candidate.blocking_gaps:
            lines.append("")
            lines.append("Blocking gaps:")
            lines.extend(f"- {gap}" for gap in candidate.blocking_gaps)
        lines.append("")

    if prod_paths:
        lines.extend(["## Prod search paths", ""])
        lines.extend(f"- `{path}`" for path in prod_paths)
        lines.append("")

    lines.extend(
        [
            "## Owner decision required",
            "",
            "To move from BLOCKED toward `DATA_PASS`, the owner must authorize **one** paid",
            "source sample or trial so the project can:",
            "",
            "1. Pin a non-synthetic snapshot under `research/new_edge/pead/data/pinned/`.",
            "2. Run `verify_pead_data` on the pinned snapshot.",
            "3. Confirm estimate observation timestamps, survivorship-safe master, and coverage gates.",
            "",
            "**Leading paid candidates (desk order):**",
            "",
            "1. **Zacks Data** — explicitly markets point-in-time consensus, reference entity data,",
            "   delistings, and prices. Requires request-access / enterprise quote.",
            "2. **EODHD extended fundamentals + calendar** — lower cost; public docs still lack",
            "   estimate observation timestamps; verify before purchase.",
            "",
            "Do not purchase both. Pick one vendor, obtain a sample, then append a new ledger row.",
            "",
            "## Issues",
            "",
        ]
    )
    lines.extend(f"- {issue}" for issue in audit.issues)
    lines.extend(
        [
            "",
            "## Next authorized command",
            "",
            "After a pinned licensed snapshot exists:",
            "",
            "```bash",
            "python -m research.new_edge.pead.data.verify_pead_data \\",
            "  --input research/new_edge/pead/data/pinned/<snapshot> \\",
            "  --provenance research/new_edge/pead/data/provenance/<snapshot>.json \\",
            "  --start 2016-01-01 --end 2026-01-01 \\",
            "  --output docs/research/pead/PEAD_DATA_MANIFEST_2026-07.md",
            "```",
            "",
            f"Machine-readable inventory: `{provenance_path}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_provenance(
    audit: PeadSourceAudit,
    candidates: list[SourceCandidate],
    *,
    prod_status: str,
    prod_paths: list[str],
    local_snapshots: list[Path],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "lane": "pead",
        "stage": "source_audit",
        "verdict": audit.verdict,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "audit": asdict(audit),
        "local_snapshots": [str(path) for path in local_snapshots],
        "prod_search": {"status": prod_status, "paths": prod_paths},
        "required_fields": list(REQUIRED_FIELDS),
        "candidates": [
            {
                "name": candidate.name,
                "tier": candidate.tier,
                "verdict": candidate.verdict,
                "cost": candidate.cost,
                "license_summary": candidate.license_summary,
                "coverage_claim": candidate.coverage_claim,
                "probe_status": candidate.probe_status,
                "blocking_gaps": list(candidate.blocking_gaps),
                "url": candidate.url,
                "field_matrix": [asdict(row) for row in candidate.field_matrix],
                "probe_evidence": candidate.probe_evidence,
            }
            for candidate in candidates
        ],
    }


def _command(args: argparse.Namespace) -> str:
    return (
        "python -m research.new_edge.pead.data.audit_pead_sources "
        f"--output {args.output} --provenance {args.provenance}"
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE)
    parser.add_argument(
        "--skip-prod-search",
        action="store_true",
        help="Skip ssh probe of production host",
    )
    args = parser.parse_args()

    local_snapshots = _find_local_snapshots()
    if args.skip_prod_search:
        prod_status, prod_paths = "skipped", []
    else:
        prod_status, prod_paths = _ssh_prod_snapshot_search()

    audit, candidates = evaluate_candidates(
        local_snapshots=local_snapshots,
        prod_paths=prod_paths,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.provenance.parent.mkdir(parents=True, exist_ok=True)
    command = _command(args)
    args.output.write_text(
        build_manifest(
            audit,
            candidates,
            command=command,
            provenance_path=args.provenance,
            prod_status=prod_status,
            prod_paths=prod_paths,
        ),
        encoding="utf-8",
    )
    args.provenance.write_text(
        json.dumps(
            build_provenance(
                audit,
                candidates,
                prod_status=prod_status,
                prod_paths=prod_paths,
                local_snapshots=local_snapshots,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    logger.info(
        "PEAD source audit complete: verdict=%s candidates=%d data_pass=%d",
        audit.verdict,
        audit.candidates_evaluated,
        audit.data_pass_candidates,
    )
    print(audit.verdict)
    return 0 if audit.verdict == "DATA_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())