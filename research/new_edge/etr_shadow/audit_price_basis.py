"""Audit ETR shadow price levels vs Branch B instrument references.

Research hygiene only — does not claim KEEP / expectancy.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Approximate yfinance continuous references for scale checks (not fills).
# Updated manually when running audits; None means "no reference configured".
ASSET_REFERENCE: dict[str, dict[str, Any]] = {
    "btc": {"label": "BTC-USD spot-ish", "yf": "BTC-USD", "typical_price": 60_000.0},
    "gold": {"label": "GC=F continuous", "yf": "GC=F", "typical_price": 2_400.0},
    "nasdaq": {"label": "NQ=F continuous", "yf": "NQ=F", "typical_price": 20_000.0},
    "oil": {"label": "CL=F continuous", "yf": "CL=F", "typical_price": 70.0},
}


@dataclass(frozen=True)
class AssetBasisSummary:
    asset: str
    n_events: int
    n_prices: int
    median_etr_price: float | None
    reference_typical: float | None
    scale_ratio: float | None
    basis_guess: str
    notes: tuple[str, ...]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _prices_from_events(events: list[dict[str, Any]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for row in events:
        asset = str(row.get("asset") or "").lower()
        for key in ("entry_price", "exit_price", "last_price", "high_water", "low_water"):
            val = row.get(key)
            if isinstance(val, (int, float)):
                out[asset].append(float(val))
        for key in ("invalidation", "tp1", "tp2"):
            val = row.get(key)
            if isinstance(val, (int, float)):
                out[asset].append(float(val))
    return out


def _prices_from_polls(polls: list[dict[str, Any]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for row in polls:
        asset = str(row.get("asset") or "").lower()
        price = row.get("price")
        if isinstance(price, (int, float)):
            out[asset].append(float(price))
    return out


def _guess_basis(asset: str, median_price: float | None, typical: float | None) -> tuple[str, tuple[str, ...]]:
    notes: list[str] = []
    if median_price is None:
        return "unknown", ("no ETR prices found in inputs",)
    if typical is None or typical <= 0:
        return "etr_terminal", ("no reference typical configured",)

    ratio = median_price / typical
    if 0.5 <= ratio <= 2.0:
        return "compatible_with_yf_continuous", (
            f"median/typical ratio {ratio:.3f} within 0.5–2.0 band",
        )
    if ratio < 0.1 or ratio > 10.0:
        notes.append(
            f"median/typical ratio {ratio:.4g} — ETR levels are NOT on the same scale as {ASSET_REFERENCE.get(asset, {}).get('yf')}"
        )
        if asset == "nasdaq":
            notes.append(
                "NASDAQ ETR ~hundreds vs NQ=F ~tens of thousands is expected if Terminal uses an index/CFD scale"
            )
        return "etr_terminal_native", tuple(notes)
    notes.append(f"median/typical ratio {ratio:.3f} outside tight band; treat as terminal-native until mapped")
    return "etr_terminal_uncertain", tuple(notes)


def summarize(
    events: list[dict[str, Any]],
    polls: list[dict[str, Any]],
) -> list[AssetBasisSummary]:
    by_asset: dict[str, list[float]] = defaultdict(list)
    for src in (_prices_from_events(events), _prices_from_polls(polls)):
        for asset, prices in src.items():
            by_asset[asset].extend(prices)

    assets = sorted(set(by_asset) | set(ASSET_REFERENCE))
    summaries: list[AssetBasisSummary] = []
    event_counts: dict[str, int] = defaultdict(int)
    for row in events:
        event_counts[str(row.get("asset") or "").lower()] += 1

    for asset in assets:
        if not asset:
            continue
        prices = by_asset.get(asset) or []
        median = statistics.median(prices) if prices else None
        ref = ASSET_REFERENCE.get(asset)
        typical = float(ref["typical_price"]) if ref else None
        ratio = (median / typical) if (median is not None and typical) else None
        basis, notes = _guess_basis(asset, median, typical)
        summaries.append(
            AssetBasisSummary(
                asset=asset,
                n_events=event_counts.get(asset, 0),
                n_prices=len(prices),
                median_etr_price=median,
                reference_typical=typical,
                scale_ratio=ratio,
                basis_guess=basis,
                notes=notes,
            )
        )
    return summaries


def render_markdown(summaries: list[AssetBasisSummary], *, events_path: Path, polls_path: Path) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# ETR Shadow Price-Basis Audit — 2026-08",
        "",
        f"**Generated:** {now}",
        f"**Events:** `{events_path}`",
        f"**Polls:** `{polls_path}`",
        "",
        "Hygiene track only — not a KEEP / expectancy claim.",
        "",
        "| Asset | Events | Prices | Median ETR | Ref typical | Scale ratio | Basis guess |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for s in summaries:
        lines.append(
            "| {asset} | {n_events} | {n_prices} | {median} | {typical} | {ratio} | {basis} |".format(
                asset=s.asset,
                n_events=s.n_events,
                n_prices=s.n_prices,
                median=f"{s.median_etr_price:.4g}" if s.median_etr_price is not None else "—",
                typical=f"{s.reference_typical:.4g}" if s.reference_typical is not None else "—",
                ratio=f"{s.scale_ratio:.4g}" if s.scale_ratio is not None else "—",
                basis=s.basis_guess,
            )
        )
    lines.extend(["", "## Notes", ""])
    for s in summaries:
        if not s.notes:
            continue
        lines.append(f"### {s.asset}")
        for note in s.notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.extend(
        [
            "## Recommendation",
            "",
            "1. Treat ETR shadow MFE/MAE as **terminal-native** unless basis_guess is "
            "`compatible_with_yf_continuous`.",
            "2. Do not convert shadow outcomes into Branch B / broker P&L without an explicit "
            "per-asset mapping table checked into this folder.",
            "3. Keep collecting shadow evidence; do not promote from thin N.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=Path("logs/etr_shadow_events.jsonl"))
    parser.add_argument("--polls", type=Path, default=Path("logs/etr_shadow_polls.jsonl"))
    parser.add_argument("--open", type=Path, default=Path("logs/etr_shadow_open.json"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/research/etr_shadow/ETR_SHADOW_PRICE_BASIS_AUDIT_2026-08.md"),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    events = _load_jsonl(args.events)
    polls = _load_jsonl(args.polls)
    if args.open.exists():
        try:
            open_payload = json.loads(args.open.read_text(encoding="utf-8"))
            if isinstance(open_payload, dict):
                events.extend(v for v in open_payload.values() if isinstance(v, dict))
        except json.JSONDecodeError:
            logger.warning("Could not parse open events file: %s", args.open)

    summaries = summarize(events, polls)
    markdown = render_markdown(summaries, events_path=args.events, polls_path=args.polls)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    logger.info("Wrote %s (%d assets)", args.output, len(summaries))
    for s in summaries:
        logger.info(
            "%s basis=%s median=%s ratio=%s",
            s.asset,
            s.basis_guess,
            s.median_etr_price,
            s.scale_ratio,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
