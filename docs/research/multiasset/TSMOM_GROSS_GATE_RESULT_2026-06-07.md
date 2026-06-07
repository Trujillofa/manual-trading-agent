# Time-Series Momentum (TSMOM) Gross-First Gate — Negative Result (2026-06)

**Date:** 2026-06-07  
**Worktree:** `research-multiasset-momentum`  
**Status:** Gross gate failed. TSMOM hypothesis on metals + indices + FX majors (daily) has no accessible edge.

## Universe & Data (full intended breadth per locked plan)
- **Metals:** XAUUSD, XAGUSD — Dukascopy bi5, long backfill from 2016-01-01 (~3780–3790 trading days to 2026-06).
- **Indices (the 5 targeted for diversification):** USA500, USATECH, DEU40, GBR100, JPN225 — 2018-01-01 to 2026-06-05 via yfinance fallback (Dukascopy public bi5 feed returned 404 for all variants despite confirmed trading hours on Dukascopy site; ~2054–2138 daily bars / ~8.4 years).
- **FX majors (daily):** EURUSD + GBPUSD full long d1+h4 (2018-01-01 → 2026-06, ~3065 trading days). USDJPY d1 only (2018-01-01 → 2026-06, 3068 bars); no h4. AUDUSD/USDCAD/USDCHF/NZDUSD: not populated (the dedicated long FX backfill task was terminated by the 10-hour max_runtime limit after partially writing USDJPY d1).
- **Total for this gate run:** 10 instruments, 3810 trading days overlap (driven by 2016 metals start), using daily closes.

**Note on data source for indices:** Public Dukascopy bi5 M1 (the high-quality path used for FX/metals) was unavailable for these CFD indices in exhaustive probes (all 404). yfinance daily closes were used as the fallback to satisfy the "full breadth" requirement for the gross test. This is lower-frequency but appropriate and high-quality for daily momentum evaluation.

## Method (minimal, per Phase 1 gross path)
- Signal: `ts_momentum` — sign of trailing total return over fixed 252-bar (~1 year) lookback. Single sensible parameter, no sweep, no per-instrument tuning (anti-overfit).
- Sizing: Inverse-vol weights (63-day trailing vol, floor 5%), renormalized daily to sum ~1. Portfolio-level risk targeting via diversification rather than explicit vol target for this gate.
- Backtest: Pure daily bar-walker on d1 closes. **Signal computed on close of bar t → position applied to return of bar t+1** (lookahead guard enforced in `backtest.py`).
- Costs: **Gross only (0)** — the kill-switch test. (Net/costs deferred until gross clearly >1.1 + positive.)
- Rebalance: Daily.
- Equity: Starts at 1.0; cumulative product of (1 + weighted signed instrument returns).
- Metrics: Gross PF (sum pos / |sum neg| daily), annualized Sharpe, MAR (CAGR / maxDD), max drawdown, etc. (see `portfolio.py`).
- Judge: Portfolio-level only (not per-instrument). Full universe reported. Correlation matrix computed and inspected first (diversification sanity).

This matches the "minimal gross path" in the execution plan and the "gross-first decision gate" for Hypothesis #1.

## Results
**Loaded:** 3810 days across 10 instruments (XAUUSD, XAGUSD, USA500, USATECH, DEU40, GBR100, JPN225, EURUSD, GBPUSD, USDJPY).

### Portfolio Gross Metrics (252-bar lookback)
- Days (after returns): 3809
- Final equity: 1.113
- Gross PF: **1.036**
- Sharpe (annualized): **0.150**
- MAR: **0.061**
- Max DD: 16.89%
- Ann. return (approx CAGR): 1.03%

### Correlation Matrix (daily instrument returns)
```
         XAUUSD  XAGUSD  USA500  USATECH  DEU40  GBR100  JPN225  EURUSD  GBPUSD  USDJPY
XAUUSD     1.00    0.78    0.10     0.10   0.08    0.07    0.05    0.37    0.33   -0.34
XAGUSD     0.78    1.00    0.19     0.20   0.13    0.12    0.06    0.33    0.32   -0.21
USA500     0.10    0.19    1.00     0.95   0.52    0.47    0.13    0.16    0.25    0.08
USATECH    0.10    0.20    0.95     1.00   0.47    0.37    0.10    0.15    0.24    0.08
DEU40      0.08    0.13    0.52     0.47   1.00    0.80    0.33    0.13    0.22    0.03
GBR100     0.07    0.12    0.47     0.37   0.80    1.00    0.31    0.09    0.08    0.06
JPN225     0.05    0.06    0.13     0.10   0.33    0.31    1.00    0.07    0.07    0.01
EURUSD     0.37    0.33    0.16     0.15   0.13    0.09    0.07    1.00    0.70   -0.45
GBPUSD     0.33    0.32    0.25     0.24   0.22    0.08    0.07    0.70    1.00   -0.38
USDJPY    -0.34   -0.21    0.08     0.08   0.03    0.06    0.01   -0.45   -0.38    1.00
```

**Mean pairwise correlation (excl. diagonal):** **0.19**

- Metals cluster (XAU/XAG): 0.78 (expected).
- US Tech/500: 0.95.
- European indices: 0.80.
- Nikkei low cross-corr.
- FX majors correlated to each other (0.70) and modestly to metals (~0.33), low to indices.
- Overall low average correlation → the "portfolio of many weak bets" diversification thesis was genuinely tested, not inflated by a few tight clusters.

## Gate Decision (gross-first, as specified)
**Gross PF ≈ 1.0 (1.042) with very low Sharpe (0.176) → no accessible gross edge before costs/friction.**

Per the execution plan (Phase 1 gross-first diagnostic and the decision tree in "THE GATE"):

- Gross clearly > ~1.1 + positive risk-adjusted → proceed to net (build 0.5 costs.py with realistic overnight swap/financing, 0.6 guard scoping + `engine="ts_momentum"` seam in evaluate.py, then held-out OOS portfolio KEEP gates with Sharpe/MAR + net-after-swap, MIN_TRADES as sanity floor only).
- Gross ≈ 1.0 (≲1.1) or negligible Sharpe → **hypothesis #1 fails**. Stop investing in costs, guards, OOS harness, or further TSMOM tuning on this universe/timeframe. Pivot to Hypothesis #2 (cross-sectional momentum / XSMOM) or write the locked negative result and terminate the line (fail-fast budget ~2-3 hyps).

**Verdict: PIVOT or STOP for daily TSMOM.**

This is the honest gross-first outcome on ~8–10+ years across a diversified (low-corr) 10-instrument book (metals + 5 indices + 3 FX majors with available long data). Even before any swap/spread/commission, the strategy delivers essentially flat-to-modest positive PnL with high drawdown risk and no meaningful edge.

## Anti-Overfit / Plan Fidelity Notes
- Gross-first strictly observed (no costs model built or tuned against).
- 0.5 (costs) and 0.6 (guard + evaluate seam) **not built** — correctly deferred until after this gate (per the gross-first build-order deviation we locked and documented).
- Minimal params (one lookback, no sweeps, no cherry-picking).
- Portfolio-level judge only.
- Full universe + corr matrix reported first.
- Data quality: yf daily for indices is a limitation vs ideal Dukascopy bi5, but sufficient for this test and the only path that delivered the required breadth. Dukascopy public feed unavailability for indices was exhaustively probed and documented.
- Short-window smokes earlier (toy 10–20 bar lookbacks on recent data) were for pipeline validation only; this run uses the long spans.

## Next (per plan & locked decisions)
- **Pivot:** Implement Hypothesis #2 (cross-sectional momentum: rank the universe, long top / short bottom, reuse portfolio sizing + gross walker + same gate).
- Or: Full negative-result report for this TSMOM line (mirror style of `FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`), lock it, and decide whether to try the remaining budgeted hyp or accept the search on this instrument set/timeframe/edge family is exhausted.
- The durable artifacts remain: the evaluator harness reuse, pure signal/portfolio/backtest layers, instrument-aware data layer (with yf fallback), corr/gate reporting discipline, and the honest gross test on real history.

**This worktree remains isolated.** Main and the deployed Branch B scanner untouched.

Caches (parquet/pickle under `data/cache/multiasset/`) are gitignored and were produced by the long backfills + yf fallback.

---

*Gross PF 1.036 / Sharpe 0.150 on 8–10y diversified book (metals + 5 indices + available FX majors) is the kill switch. TSMOM daily does not clear it.*