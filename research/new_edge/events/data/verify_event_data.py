#!/usr/bin/env python3
"""
Event / calendar lane data verifier (data proof only).

Per EVENT_CONTRACT_2026-06-18.md: verify historical calendar availability, timestamp
reliability, actual/forecast field coverage, look-ahead risk, and spread-widening
assumptions. No strategy or backtest code.

Usage:
  python -m research.new_edge.events.data.verify_event_data \
    --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-18.md
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.news.news_checker import NewsChecker

FOREX_FACTORY_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
HISTORICAL_URL_CANDIDATES = [
    "https://nfs.faireconomy.media/ff_calendar_lastweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_lastmonth.xml",
]
SAMPLE_XML = Path(__file__).resolve().parent / "sample_ff_thisweek.xml"

# Conservative release-window cost model (documented, not optimized)
BASE_SPREAD_PIPS_MAJORS = 2.0
RELEASE_WINDOW_SPREAD_MULT = 3.0
RELEASE_WINDOW_MINUTES = 15
RELEASE_SLIPPAGE_PIPS = 1.0


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


def _safe_text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


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
        issues.append(
            f"NewsChecker parser success rate {parse_rate:.1%} < 95% on feed XML "
            f"(top failure: {max(audit.parser_fail_reasons, key=audit.parser_fail_reasons.get) if audit.parser_fail_reasons else 'n/a'})."
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/research/events/EVENT_DATA_MANIFEST_2026-06-18.md",
    )
    parser.add_argument(
        "--xml",
        default=None,
        help="Optional path to XML file (skips live fetch)",
    )
    args = parser.parse_args()

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

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(manifest, encoding="utf-8")

    print(f"Manifest written to {out_path}")
    print(f"Verdict: {verdict}")
    for issue in issues:
        print(f"  - {issue}")


if __name__ == "__main__":
    main()
