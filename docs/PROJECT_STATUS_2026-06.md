# Project Status & Handoff — 2026-06

Single-page summary of the profitability investigation, what was built, the
honest conclusion, and what is reusable. For the detailed negative result see
[`docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`](research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md);
for the next search see [`docs/research/NEW_PROGRAM_PLAN.md`](research/NEW_PROGRAM_PLAN.md).

## Goal
Make the manual-trading-agent a *reliably profitable* system (originally: MTF RSI
scanner on FX majors, M15/H1, paper + Telegram alerts).

## Outcome (honest)
**No accessible directional-TA edge on FX majors at M15/H1.** Proven, not assumed —
three structurally different entries over full 365d, ~19k trades, all **gross PF ≈ 1.0**
(no edge even before costs); realistic friction makes net far worse. This line of
research is **terminated** (locked + agent-guarded). The system's honest posture is
**Branch B: a selective manual-alert / research-audit tool**, not an autonomous edge.

## What was fixed / built (the durable assets)
- **ATR bug fix** — live had silently used fixed 30/90-pip TP/SL for months; now ATR(14)-based.
- **Unified pure evaluator** (`src/scanner/evaluator.py`) — single source of truth for the
  live entry; live scanner *and* backtest harness call the same function → "live == backtest
  by construction." Eliminated the prior 3-engine divergence.
- **Honest research harness** (`research/`) — Karpathy-style autoresearch with a **held-out
  OOS judge** (can't win by overfitting), a **costed bar-walker driver**, an **entry-mode seam**
  (`mtf_rsi` / `session_orb` / `trend_pullback`), and a **gross-vs-net diagnostic**. This is the
  crown jewel and is **market-agnostic / reusable**.
- **Rule C fidelity, audit enrichment, config revert, 205 passing tests, ruff clean.**
- **Locked negative result + agent-proof STOP guard** on the research entrypoints.

## Current state
- Code: `main` @ latest (all of the above, committed + pushed).
- Prod (Hetzner, `acca11b`): deployed, healthy, running as the Branch B scanner/alerter.
- Promotion-table P&L numbers in older docs: **retired** (came from divergent engines).
- 2026-06 research (isolated worktree `research-multiasset-momentum`): daily TSMOM on metals (2016+ Dukascopy), 5 indices (2018+ via yfinance fallback), core FX majors. Gross-first gate on long data (~10y effective, 10 instruments, 252-bar lookback, inv-vol sizing): gross PF 1.036, Sharpe 0.15. No accessible edge before costs/friction. Lane closed; negative result archived on the research branch (detailed report + harness). No production promotion or further TSMOM tuning.

## Key evidence (the diagnostic table)
| Entry | TF | Trades | Gross PF | Net PF |
|---|---|---|---|---|
| ORB (reversal/breakout) | M15 | 6,535 | 1.02 | 0.34 |
| Trend-pullback (momentum) | M15 | 8,298 | 1.07 | 0.19 |
| Trend-pullback | H1 | 3,870 | 1.01 | 0.53 |

## What's reusable for the next attempt
The harness, evaluator pattern, costed driver, IS/OOS judge, gross-first diagnostic,
and the *discipline* (fail-fast, OOS-gated, anti-overfit). A new market/edge only needs
a new data adapter + cost model — see the new-program plan.

## Do NOT
Restart FX-majors OHLC directional TA (guarded). Cite retired promotion numbers.
Tune costs or parameters of a ~1.0 gross base. Run correlated 4th TA variants.
Extend or retune a daily TSMOM result that failed the gross gate at PF ≈ 1.03–1.04 / weak Sharpe (this lane is archived).
