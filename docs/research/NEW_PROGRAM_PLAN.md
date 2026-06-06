# New Profitability Program — Plan (2026-06)

A *new research program* to keep looking for profitability, satisfying the
falsifiable re-entry criteria from the FX negative result
([report](FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md)). Runs in an **isolated
git worktree** so it cannot disturb `main` or the deployed Branch B scanner.

> **This is a new program, not "one more strategy."** It must change at least one
> of: **instrument**, **edge source**, or **data type** — not re-run FX-majors
> OHLC directional TA (which is locked + guarded).

## 1. What carries over (the reusable asset — do NOT rebuild)
- `research/evaluate.py` — IS/OOS chronological split, **held-out OOS judge**, costed
  bar-walker driver, entry-mode seam, **gross-vs-net diagnostic**, KEEP gates.
- `src/scanner/evaluator.py` pattern — pure, side-effect-free entry functions.
- The **discipline**: gross-first read, fail-fast budget, OOS-gated KEEP, anti-overfit
  (the judge is sacred), portfolio-level metrics.

## 2. What is new (must be built)
- **Data adapter** for the new market (OHLC + timestamps), cached like the Dukascopy parquets.
- **Cost model** for that market (e.g. crypto: taker fee ~5–10 bps/side + funding; equities:
  commission + spread). Costs are decisive — model them honestly from day one.
- Possibly a **cross-sectional layer** (rank a universe; long top / short bottom) if pursuing
  relative momentum rather than absolute directional entries.
- A **new entrypoint / config namespace** so it does NOT trip the FX STOP guard. Cleanest:
  scope the existing guard to FX-majors specifically; alternatively run with
  `--override-negative-result docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`
  (legitimate, since this is a qualifying re-entry).

## 3. Candidate directions (priors + recommendation)
| # | Direction | Why it might have edge | Cost/friction | Prior |
|---|---|---|---|---|
| **1 (recommended)** | **Crypto, daily/4h, cross-sectional momentum** (rank N coins, long top/short bottom) | Relative momentum persists better than absolute directional TA; crypto has documented retail-accessible inefficiency | Large moves → friction wall avoided; model fees+funding | **Highest** |
| 2 | Crypto, daily, absolute trend-following | Simple, decent documented persistence; new instrument | Good (large ATR) | Medium-high |
| 3 | Equity index / sector-ETF momentum (daily) | Well-documented cross-sectional momentum | Low (cheap, liquid) | Medium-high |
| 4 (defer) | FX carry / calendar / stat-arb on crosses | Genuinely different edge source | n/a | Needs non-OHLC data |

**Recommendation: #1 (crypto daily/4h cross-sectional momentum).** It changes *both*
instrument and edge source, sidesteps the M15 friction wall that killed FX, uses free/
accessible data, and maximally reuses the harness. Cross-sectional (relative) momentum is
a structurally different bet than the absolute directional TA that just failed.

## 4. Methodology (carry the honest bar over verbatim)
1. **Gross-first diagnostic** on every hypothesis (costs=0 vs realistic). If gross PF ≈ 1.0 → no edge, stop that hypothesis.
2. **IS/OOS chronological split**; KEEP only on the held-out window: portfolio OOS PF ≥ 1.2–1.3, ≥100–200 pooled OOS trades, positive net PnL, IS/OOS consistency, acceptable Monte-Carlo DD.
3. **Fail-fast budget:** ~2–3 structurally different hypotheses. If all gross ≈ 1.0, conclude no accessible edge in that market and write a negative-result report (same as FX).
4. **Anti-overfit:** never tune against OOS; minimal params; beware subset/per-asset cherry-picking.

## 5. Phased execution
- **Phase 0:** worktree + data adapter (crypto OHLC daily/4h for a liquid universe, e.g. top 20–30 by liquidity) + honest cost/funding model + cache.
- **Phase 1:** implement hypothesis #1 (cross-sectional momentum) as a pure entry/ranking fn behind the seam; run the gross/net + IS/OOS diagnostic. **Decision gate:** gross PF clearly >1.1 → pursue (filter for net); gross ≈ 1.0 → next hypothesis or different market.
- **Phase 2:** iterate (one filter at a time, OOS-judged) **or** pivot per the gate. Only merge to `main` if something clears the KEEP gates and is worth operating.

## 6. Worktree setup (isolation)
```bash
# from the main repo root
./scripts/worktree-create.sh research-profitability-v2 main
cd ../manual-trading-agent-research-profitability-v2
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
# build the new program under research/<market>/ ; reuse research/evaluate.py judge
```
- Branch: `research-profitability-v2` (worktree dir `../manual-trading-agent-research-profitability-v2`).
- Base `main` so it inherits the harness + evaluator + discipline.
- Keep it isolated; the deployed Branch B scanner on `main`/prod is untouched.
- Clean up with `./scripts/worktree-cleanup.sh` if a direction dead-ends.

## 7. Success / stop criteria
- **Success:** one hypothesis clears the portfolio OOS KEEP gates with realistic costs and survives Monte-Carlo → harden, paper-shadow, then consider operating.
- **Stop:** budget of hypotheses exhausted with gross ≈ 1.0 → write that market's negative-result report, lock it, and decide whether to try another market or accept the search is done.
