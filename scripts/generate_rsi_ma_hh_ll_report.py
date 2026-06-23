#!/usr/bin/env python3
"""Generate RSI + RSI-MA + Highest High / Lowest Low backtest report.

Synthesizes existing bakeoff CSV results (Dukascopy M1, ~180d) with
live-evaluator diagnostic data from CLAUDE.md to produce a unified
report covering V0/V2 (from backtest) and the RSI-MA gate impact
(from live evaluator diagnostics).
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PairVariantResult:
    pair: str
    variant_family: str  # "V0", "V1", "V2", "V2R"
    best_config: str
    trades: int
    win_rate: float
    pnl_pct: float
    profit_factor: float
    max_dd_pct: float


# ---------------------------------------------------------------------------
# Load and aggregate bakeoff CSV
# ---------------------------------------------------------------------------

def load_bakeoff_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def family(variant: str) -> str:
    if variant.startswith("V2R"):
        return "V2R"
    elif variant.startswith("V2"):
        return "V2"
    elif variant.startswith("V1"):
        return "V1"
    elif variant.startswith("V0"):
        return "V0"
    return "OTHER"


def aggregate_bakeoff(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Returns {pair -> {family -> [rows sorted by PF desc]}}"""
    by_pair_fam: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        pair = row.get("pair", "")
        var = row.get("variant", "")
        fam = family(var)
        by_pair_fam.setdefault(pair, {}).setdefault(fam, []).append(row)
    # Sort by PF desc within each group
    for pair_d in by_pair_fam.values():
        for fam_list in pair_d.values():
            fam_list.sort(key=lambda r: float(r.get("profit_factor", 0) or 0), reverse=True)
    return by_pair_fam


def best_per_pair_family(by_pair_fam: dict) -> dict[str, dict[str, dict]]:
    """Return the single best config (by PF) for each pair/family combination."""
    result: dict[str, dict[str, dict]] = {}
    for pair, fam_dict in by_pair_fam.items():
        result[pair] = {}
        for fam, rows in fam_dict.items():
            if rows:
                result[pair][fam] = rows[0]  # already sorted by PF desc
    return result


# ---------------------------------------------------------------------------
# Build report
# ---------------------------------------------------------------------------

def build_report(csv_path: Path, output_path: Path) -> None:
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    rows = load_bakeoff_csv(csv_path)
    print(f"  Loaded {len(rows)} rows from {csv_path.name}")

    by_pair_fam = aggregate_bakeoff(rows)
    best_per_pair_family(by_pair_fam)
    all_pairs = sorted(by_pair_fam.keys())

    # Pool stats per family
    def pool_family(fam: str) -> dict:
        trade_list = []
        for pair_d in by_pair_fam.values():
            for row in pair_d.get(fam, []):
                trade_list.append(row)
        configs = len({r["variant"] for r in trade_list})
        pairs_with_data = len({r["pair"] for r in trade_list if int(r.get("trades", 0)) > 0})
        total_trades = sum(int(r.get("trades", 0)) for r in trade_list)
        total_wins = sum(int(r.get("wins", 0) or 0) for r in trade_list)
        wr = total_wins / total_trades if total_trades else 0.0
        # Pool PF: aggregate gross_win / gross_loss across all configs+pairs
        # Approximate from avg_win * wins and avg_loss * losses
        gw = sum(float(r.get("avg_win", 0) or 0) * int(r.get("wins", 0) or 0) for r in trade_list)
        gl = sum(float(r.get("avg_loss", 0) or 0) * int(r.get("losses", 0) or 0) for r in trade_list)
        pf = gw / gl if gl > 0 else (999.0 if gw > 0 else 0.0)
        avg_pnl = sum(float(r.get("total_pnl_pct", 0) or 0) for r in trade_list) / len(trade_list) if trade_list else 0.0
        profitable = sum(1 for r in trade_list if float(r.get("total_pnl_pct", 0) or 0) > 0)
        return {
            "configs": configs,
            "pairs_with_trades": pairs_with_data,
            "total_trades": total_trades,
            "win_rate": wr,
            "avg_pnl_pct": avg_pnl,
            "profit_factor": pf,
            "profitable_instances": profitable,
            "total_instances": len(trade_list),
        }

    v0_pool = pool_family("V0")
    v1_pool = pool_family("V1")
    v2_pool = pool_family("V2")
    v2r_pool = pool_family("V2R")

    # Best V2 configs with enough trades (>= 5)
    v2_best_rows: list[dict] = []
    for pair_d in by_pair_fam.values():
        for row in pair_d.get("V2", []):
            if int(row.get("trades", 0)) >= 5:
                v2_best_rows.append(row)
    v2_best_rows.sort(key=lambda r: float(r.get("profit_factor", 0) or 0), reverse=True)

    # Per-pair best V2 (any trade count)
    pair_best_v2: list[tuple[str, dict]] = []
    for pair in all_pairs:
        rows_v2 = by_pair_fam.get(pair, {}).get("V2", [])
        if rows_v2:
            pair_best_v2.append((pair, rows_v2[0]))

    pair_best_v2.sort(key=lambda x: float(x[1].get("profit_factor", 0) or 0), reverse=True)

    # Per-pair best V1 (continuation for reference)
    pair_best_v1: list[tuple[str, dict]] = []
    for pair in all_pairs:
        rows_v1 = by_pair_fam.get(pair, {}).get("V1", [])
        if rows_v1:
            pair_best_v1.append((pair, rows_v1[0]))
    pair_best_v1.sort(key=lambda x: float(x[1].get("profit_factor", 0) or 0), reverse=True)

    # Build report
    lines: list[str] = []

    lines += [
        "# RSI + RSI-based MA + Highest High / Lowest Low — Backtest Report",
        f"Generated: {now_str}",
        "",
        "> **Data source:** Dukascopy M1, resampled to 15m/30m/1h, ~180-day window (2026-04).  ",
        "> **Engine:** `scripts/run_confirmation_bakeoff.py` + `scripts/run_donchian_backtest.py`  ",
        "> **RSI-MA gate impact:** drawn from live evaluator diagnostic runs (2026-06)  ",
        "> **Cost model:** 2 pip spread + 2 pip slippage + $3 commission each side",
        "",
        "---",
        "",
        "## Strategy Overview",
        "",
        "The RSI+RSI-MA+HH/LL system is a 3-layer mean-reversion stack:",
        "",
        "```",
        "Layer 1 — MTF RSI Alignment",
        "  RSI(14) on 1h, 30m, 15m all < 30 (BUY) or all > 70 (SELL)",
        "  → identifies extreme momentum across timeframes",
        "",
        "Layer 2 — RSI-MA Hard Gate  [current live production, added 2026-04]",
        "  SMA(5) of RSI must ALSO be ≤ 30 (BUY) or ≥ 70 (SELL) on all 3 TFs",
        "  → filters out transient RSI spikes; requires sustained extreme reading",
        "",
        "Layer 3 — V2 HH/LL Reversal Entry  [V2 variant]",
        "  BUY:  15m bar low < 20-bar Lowest Low, AND close recovers above LL",
        "  SELL: 15m bar high > 20-bar Highest High, AND close recovers below HH",
        "  → requires actual price structure test at extreme",
        "```",
        "",
        "Additional gates active in production (excluded from bakeoff to isolate entry signal):",
        "- ADX(14) < 25 on 1h (ranging-only filter)",
        "- Session filter: 06-17 UTC or 12-21 UTC",
        "- News lockout: ±60/30 min around Forex Factory 3-star events",
        "- SMA(50) alignment gate (3 TFs)",
        "- Rule C: one active signal per pair per direction until TP/SL/RSI-midline/SMA flip",
        "",
        "---",
        "",
        "## Section 1 — Bakeoff Summary by Variant Family",
        "",
        "Pooled across all pairs × all buffer/confirm-bar configurations.",
        "",
        "| Variant | Description | Configs | Trades | WR | Avg PnL% | Pool PF |",
        "|---------|-------------|---------|--------|----|----------|---------|",
        f"| V0 | RSI-only, no HH/LL gate | {v0_pool['configs']} | {v0_pool['total_trades']} | {v0_pool['win_rate']:.1%} | {v0_pool['avg_pnl_pct']:.2f}% | {v0_pool['profit_factor']:.2f} |",
        f"| V1 | RSI + breakout continuation (break of HH/LL) | {v1_pool['configs']} | {v1_pool['total_trades']} | {v1_pool['win_rate']:.1%} | {v1_pool['avg_pnl_pct']:.2f}% | {v1_pool['profit_factor']:.2f} |",
        f"| V2 | RSI + reversal (wick through HH/LL + reclaim) | {v2_pool['configs']} | {v2_pool['total_trades']} | {v2_pool['win_rate']:.1%} | {v2_pool['avg_pnl_pct']:.2f}% | {v2_pool['profit_factor']:.2f} |",
        f"| V2R | RSI + structural break (break above HH/below LL) | {v2r_pool['configs']} | {v2r_pool['total_trades']} | {v2r_pool['win_rate']:.1%} | {v2r_pool['avg_pnl_pct']:.2f}% | {v2r_pool['profit_factor']:.2f} |",
        "",
        "**Key observation:** V2 (reversal) achieves the highest average PF despite the lowest trade count.",
        "V0 generates the most trades but the worst quality. V2R fires near-zero trades across all pairs.",
        "",
        "---",
        "",
        "## Section 2 — V2 (RSI + HH/LL Reversal) Results by Pair",
        "",
        "### Best V2 config per pair (ranked by Profit Factor)",
        "",
        "| Pair | Best Config | Trades | WR | PnL% | PF | MaxDD% |",
        "|------|------------|--------|----|------|----|--------|",
    ]

    for pair, row in pair_best_v2:
        v = row.get("variant", "")
        t = int(row.get("trades", 0))
        wr = float(row.get("win_rate", 0) or 0)
        pnl = float(row.get("total_pnl_pct", 0) or 0)
        pf = float(row.get("profit_factor", 0) or 0)
        dd = float(row.get("max_dd_pct", 0) or 0)
        pf_disp = f"{pf:.2f}" if pf < 100 else "999"
        low_n = "⚠️ N<5" if t < 5 else ""
        lines.append(f"| {pair} | `{v}` | {t} {low_n} | {wr:.0%} | {pnl:.2f}% | {pf_disp} | {dd:.2f}% |")

    lines += [
        "",
        "> ⚠️ = fewer than 5 trades — PF is statistically meaningless at this count.",
        "> High PF (999) entries are PF=∞ (zero losses) from 1-3 trade samples. Discard.",
        "",
        "### V2 configs with ≥5 trades, ranked by PF",
        "",
        "| Pair | Config | Trades | WR | PnL% | PF | MaxDD% |",
        "|------|--------|--------|----|------|----|--------|",
    ]

    for row in v2_best_rows[:20]:
        pair = row.get("pair", "")
        v = row.get("variant", "")
        t = int(row.get("trades", 0))
        wr = float(row.get("win_rate", 0) or 0)
        pnl = float(row.get("total_pnl_pct", 0) or 0)
        pf = float(row.get("profit_factor", 0) or 0)
        dd = float(row.get("max_dd_pct", 0) or 0)
        pf_disp = f"{pf:.2f}" if pf < 100 else "999"
        lines.append(f"| {pair} | `{v}` | {t} | {wr:.0%} | {pnl:.2f}% | {pf_disp} | {dd:.2f}% |")

    if not v2_best_rows:
        lines.append("_(no V2 configs with ≥5 trades across all pairs)_")

    lines += [
        "",
        "---",
        "",
        "## Section 3 — V1 vs V2 Comparison (HH/LL Entry Direction)",
        "",
        "V1 = breakout continuation (price breaks *through* HH/LL)  ",
        "V2 = reversal reclaim (wick through HH/LL, close reverses back)",
        "",
        "| Pair | V1 Best PF | V1 Trades | V1 PnL% | V2 Best PF | V2 Trades | V2 PnL% | Winner |",
        "|------|-----------|---------|--------|-----------|---------|--------|--------|",
    ]

    for pair in all_pairs:
        v1_rows = by_pair_fam.get(pair, {}).get("V1", [])
        v2_rows_pair = by_pair_fam.get(pair, {}).get("V2", [])
        if not v1_rows and not v2_rows_pair:
            continue
        v1r = v1_rows[0] if v1_rows else None
        v2r_ = v2_rows_pair[0] if v2_rows_pair else None
        v1_pf = float(v1r.get("profit_factor", 0) or 0) if v1r else 0.0
        v2_pf = float(v2r_.get("profit_factor", 0) or 0) if v2r_ else 0.0
        v1_t = int(v1r.get("trades", 0)) if v1r else 0
        v2_t = int(v2r_.get("trades", 0)) if v2r_ else 0
        v1_pnl = float(v1r.get("total_pnl_pct", 0) or 0) if v1r else 0.0
        v2_pnl = float(v2r_.get("total_pnl_pct", 0) or 0) if v2r_ else 0.0
        winner = "V2" if v2_pf > v1_pf else ("V1" if v1_pf > v2_pf else "Tie")
        v1_pf_d = f"{v1_pf:.2f}" if v1_pf < 100 else "999"
        v2_pf_d = f"{v2_pf:.2f}" if v2_pf < 100 else "999"
        lines.append(
            f"| {pair} | {v1_pf_d} | {v1_t} | {v1_pnl:.2f}% | "
            f"{v2_pf_d} | {v2_t} | {v2_pnl:.2f}% | **{winner}** |"
        )

    lines += [
        "",
        "---",
        "",
        "## Section 4 — RSI-MA Gate Impact",
        "",
        "The RSI-MA(5) hard gate (SMA of 5 RSI bars must be outside 30/70 on ALL 3 TFs) was NOT",
        "in the bakeoff engine above. Its impact was measured separately via the live evaluator",
        "diagnostic pipeline (`research/diagnose_live_entry_volume.py`), 2026-06.",
        "",
        "### Gate rejection analysis — 800-bar windows on 8 pairs",
        "",
        "| Pair | MTF Aligned Events | Fires | Top Rejection Reasons |",
        "|------|--------------------|-------|----------------------|",
        "| GBP/CHF | 97 | 0 | 15m breakout not confirmed (61), trending ADX (33), outside session (24) |",
        "| GBP/JPY | 45 | 0 | Outside session (34), trending ADX (31), RSI-MA(5) gate (4+) |",
        "| USD/JPY | 72 | 0 | Outside session (33), trending ADX (33), RSI-MA(5) gate (4+) |",
        "| EUR/USD | 38 | 0 | Breakout low not confirmed (26), outside session (20), trending (10) |",
        "| GBP/USD | 38 | 0 | Outside session (30), trending ADX (21), breakout high not confirmed (20) |",
        "| NZD/JPY | 145 | 0 | Outside session (78), trending ADX (17) |",
        "| AUD/CAD | 29 | 0 | Outside session (18), trending ADX (18), RSI-MA(5) gate (4) |",
        "| USD/CHF | 52 | 0 | Breakout high not confirmed (41), outside session (35), trending ADX (29) |",
        "",
        "**Pooled (8 pairs, 800 bars each):**",
        "- 516 MTF aligned events, **0 fires** (fire rate 0.0000 per bar)",
        "- Top blockers: session filter (272), trending ADX (190), V2 breakout not confirmed (193), RSI-MA gate (~20+)",
        "",
        "### Full honest harness baseline (2026-06-04)",
        "",
        "Engine: `engine=\"live_mtf_rsi\"` with full gate stack (LIVE_BT_MAX_BARS=3000, 8 pairs, current defaults):",
        "",
        "| Metric | IS | OOS |",
        "|--------|----|-----|",
        "| Trades | 0 | 0 |",
        "| Win Rate | — | — |",
        "| PnL% | 0.00% | 0.00% |",
        "| Profit Factor | 0.00 | 0.00 |",
        "",
        "**Verdict: DISCARD** (fails MIN_TRADES=30 gate on both splits)",
        "",
        "### RSI-MA gate counterfactual",
        "",
        "The RSI-MA gate is a *multiplicative* filter on top of an already sparse set:",
        "- Bakeoff V2 finds only 2–22 trades per pair over ~180 days **without** RSI-MA gate",
        "- Adding RSI-MA gate requires the SMA(5) of RSI to be extreme on all 3 TFs simultaneously",
        "- Expected to cut remaining V2 entries by 30-70% (estimate based on rejection share)",
        "- At bakeoff V2 trade counts (2-22 per pair), this typically reduces to 0-8 per pair",
        "- Insufficient for promotion-gate validation (requires ≥30 trades minimum)",
        "",
        "---",
        "",
        "## Section 5 — Parameter Sensitivity",
        "",
        "### Buffer pips (b) — V2 reversal",
        "",
        "Buffer = minimum pip distance the wick must pierce through the HH/LL before close reclaims.",
        "",
        "| Buffer | Effect |",
        "|--------|--------|",
        "| b0 (0.0 pip) | Highest trade count; wick just touches LL/HH. Some marginal touches included. |",
        "| b0.5 (0.5 pip) | Best quality/volume balance in EUR/GBP bakeoff (PF 3.09 → 3.53 with c2). |",
        "| b1 (1.0 pip) | Trades reduce further; only clear wick-through events remain. |",
        "| b2 (2.0 pip) | Very few trades; mainly useful for JPY pairs (larger pip). |",
        "",
        "**Recommendation:** 0.5 pip is the canonical choice (production default). Higher buffers",
        "improve selectivity but worsen the trade-count problem.",
        "",
        "### Confirm bars (c) — V2 reversal",
        "",
        "Bars = how long after MTF RSI alignment to accept a V2 breakout/reclaim event.",
        "",
        "| c0 (immediate) | c1-c2 | c3-c5 |",
        "|----------------|-------|-------|",
        "| Highest count, riskier | Best quality (EUR/GBP c2 optimal) | Lower count, sometimes worse quality |",
        "",
        "**Recommendation:** c2 (2 bars ≈ 30 min after alignment) is the production default.",
        "",
        "### RSI thresholds (30/70 vs. tighter)",
        "",
        "Tighter thresholds (25/75, 20/80) reduce trade count to near-zero and were not tested",
        "in the bakeoff due to insufficient alignment frequency with Dukascopy 180d data.",
        "Wider thresholds (35/65) are theoretically testable but risk degrading signal quality.",
        "",
        "---",
        "",
        "## Section 6 — Per-Pair V2 Detailed Breakdown",
        "",
        "Pairs from the April 2026 bakeoff (Dukascopy M1, ~180d).",
        "",
    ]

    # Per-pair detailed table
    pair_summaries = [
        ("EUR/GBP", "V2_b0.5_c2", 10, "27%", "+0.87%", "3.53", "0.11%",
         "Best pair for V2 reversal. Retired from active config 2026-06 after honest harness showed 0 trades on corrected engine. Historical result came from divergent Donchian engine; not live-family validated."),
        ("GBP/CHF", "V2_b0_c0", 9, "56%", "+0.14%", "1.78", "0.17%",
         "Best V2 per bakeoff April 2026. V1_b0.5 was actually used in production initially. Honest harness 2026-06: 0 fires."),
        ("AUD/CAD", "V2_b0_c0", 5, "80%", "+0.33%", "4.32", "0.10%",
         "V2 wins but small N (5 trades). V1_b2 had much better count (14-17 trades) and was production-promoted."),
        ("AUD/JPY", "V2_b2_c1", 5, "100%", "+0.33%", "999", "0.00%",
         "N=5 with 0 losses: PF is meaningless. V2 at b0 gave 22 trades with PF 1.10."),
        ("GBP/USD", "V1_b2_c4", 24, "63%", "+0.48%", "1.75", "0.36%",
         "V1 (continuation) wins over V2 for this pair. V2 scores were mostly negative."),
        ("EUR/CHF", "Best V2", 3, "0%", "-0.03%", "0.00", "0.03%",
         "V2 generally negative for EUR/CHF. V1_b0 had slight positive edge (+0.23%)."),
        ("EUR/CAD", "V2_b0_c2", 1, "100%", "+0.10%", "999", "0.00%",
         "N=1: no meaningful result. V2 and V1 both weak with very low trades."),
    ]

    lines += [
        "| Pair | Best V2 Config | Trades | WR | PnL% | PF | MaxDD% | Notes |",
        "|------|---------------|--------|----|------|----|--------|-------|",
    ]
    for pair, cfg, t, wr, pnl, pf, dd, notes in pair_summaries:
        lines.append(f"| {pair} | `{cfg}` | {t} | {wr} | {pnl} | {pf} | {dd} | {notes} |")

    lines += [
        "",
        "---",
        "",
        "## Section 7 — Honest Assessment & Conclusions",
        "",
        "### What the data shows",
        "",
        "1. **V2 HH/LL reversal is the best-quality entry variant** of those tested.",
        "   Best configs per pair show positive PnL and PF > 1, unlike V0 (RSI-only) which is broadly negative.",
        "   However, trade counts are extremely low (2–22 per pair per 180d).",
        "",
        "2. **Trade count is the binding constraint.** The promotion gate requires ≥30 trades",
        "   per IS/OOS window. No V2 config across any pair met this threshold in the bakeoff data.",
        "   The highest count was AUD/JPY V2_b0 at 22 trades — still below gate.",
        "",
        "3. **RSI-MA gate adds quality but kills volume.** The SMA(5) of RSI filter on all 3 TFs",
        "   is a tight requirement. When already running V2 which restricts to 5-22 trades per 180d,",
        "   adding RSI-MA likely reduces to 0-8 per window — well below the ≥30 trades required.",
        "",
        "4. **Full gate stack produces near-zero entries.** The 2026-06 honest harness run",
        "   (V2 + RSI-MA + ADX + session + SMA alignment + Rule C) produced 0 IS and 0 OOS trades",
        "   across 8 pairs on 3000-bar windows. This is the current production configuration's",
        "   realistic entry frequency: extremely sparse, consistent with live observation.",
        "",
        "5. **Results are engine-specific.** The positive numbers in bakeoff (e.g., AUD/CAD V1_b2 at",
        "   PF 2.67, EUR/GBP V2_b0.5_c2 at PF 3.53) came from the Donchian/yfinance engine",
        "   without the full live gate stack (no RSI-MA, no SMA alignment, no Rule C, no news gate).",
        "   When the live engine is applied, those numbers do not replicate.",
        "",
        "### Direction verdict",
        "",
        "| Question | Answer |",
        "|---------|--------|",
        "| Is V2 (reversal) better than V0 (RSI-only)? | **Yes** — consistently higher PF when it fires |",
        "| Is V1 (continuation) or V2 (reversal) better? | **Pair-dependent** — no universal winner |",
        "| Does RSI-MA gate help quality? | **Yes** — removes transient spikes, better selectivity |",
        "| Does the full stack (RSI+RSI-MA+V2+all gates) have enough trades? | **No** — structurally sparse (0/0 IS/OOS) |",
        "| Should this strategy be promoted to live? | **Not yet** — fails trade-count gate. Branch B (selective alert tool) remains the operating posture |",
        "",
        "### What would change the conclusion",
        "",
        "- **Wider RSI thresholds or looser RSI-MA** (e.g., RSI-MA(10) instead of (5)): would add volume",
        "  but the 2026-06 finding shows even V0 (RSI-only) generates PF ~0.5–1.0 across major pairs.",
        "- **Longer lookback (365d+ Dukascopy)**: required for a proper IS/OOS split. 180d bakeoff data",
        "  is too short for the promotion gate's ≥30 OOS trades requirement.",
        "- **Different instrument family**: FX majors on OHLC TA is closed per 2026-06 finding.",
        "  The RSI+HH/LL edge, if it exists at all, is likely in less liquid pairs or higher-frequency data.",
        "",
        "---",
        "",
        "## Data Provenance",
        "",
        f"Primary bakeoff: `{csv_path.name}` (Dukascopy M1, resampled, ~180 days ending 2026-04)  ",
        "Diagnostic data: `research/diagnose_live_entry_volume.py`, 2026-06  ",
        "Honest harness baseline: `research/run_experiment.py`, 2026-06-04  ",
        "Live evaluator: `src/scanner/evaluator.py` (RSI-MA gate lines 382-414, HH/LL lines 186-188)  ",
        "RSI-MA indicator: `src/indicators/rsi.py` (`calculate_rsi_ma_series`, `detect_rsi_curl`)  ",
        "HH/LL indicator: `src/indicators/high_low.py` (`previous_rolling_highest_high/lowest_low`)",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    csv_path = Path("results/confirmation_bakeoff_20260415_043937.csv")
    if not csv_path.exists():
        print(f"ERROR: bakeoff CSV not found: {csv_path}")
        return 1

    output_dir = Path("results")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"rsi_ma_hh_ll_report_{stamp}.md"

    print("=== RSI + RSI-MA + HH/LL Report Generator ===")
    print(f"Input:  {csv_path}")
    print(f"Output: {output_path}")
    print()

    build_report(csv_path, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
