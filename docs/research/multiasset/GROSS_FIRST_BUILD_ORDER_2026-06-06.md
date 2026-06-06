# Gross-First Build Order + Universe Breadth Decision (2026-06-06)

**Context**: After the Phase 0.1-0.3 metals data verification, the operator reviewed cache state and the single number that actually gates the program (gross portfolio PF/Sharpe of TSMOM).

**Locked decisions** (recorded from user):
- **Sequencing**: gross-first build order. Do **not** build costs.py (0.5), guard scoping, `engine="ts_momentum"` seam in evaluate.py, or the OOS/KEEP harness until the gross gate passes.
- **Universe for the gross test**: full intended breadth — Metals (XAU/XAG) + Indices (US500/Tech100/GER40/UK100/JP225) + FX majors (daily).

This is the cheapest trustworthy path to the kill-switch number.

## Faithful deviation from the written plan (noted here)

The original `MULTIASSET_MOMENTUM_EXECUTION_PLAN.md` listed 0.4 → 0.5 → 0.6 → Phase 1 linearly.

Gross-first discipline applied to the *build order itself* says: do not spend cycles on cost models, guard rails, and the full IS/OOS judge for a hypothesis that may have no gross edge at all.

**Current operating order** (deviation explicitly recorded):
1. Backfill full-history metals (background).
2. 0.4 indices (breadth precondition — **not** deferred).
3. FX majors daily (no inherited cache in this worktree).
4. Correlation matrix (diversification sanity before trusting any PF).
5. Minimal gross path only: `signals.py` (single-lookback ts_momentum), `portfolio.py` (inv-vol), `backtest.py` (gross, explicit signal-on-close→t+1).
6. **THE GATE** (portfolio gross PF + Sharpe over full history).
   - ≈1.0 → pivot to #2 (XSMOM) or stop (zero wasted machinery on costs/guard/OOS).
   - Clearly >1.1 → *then* build 0.5 + 0.6 + net + held-out OOS portfolio KEEP (Sharpe/MAR + net-after-swap).

All other anti-overfit rules (gross-first, judge the portfolio, minimal params, report full universe, overnight friction when we get to net) remain in force.

## Work completed in this stretch (while long backfills run)

- Metals full-history backfill kicked:
  `PYTHONPATH=. .venv/bin/python -m research.multiasset.data --symbols XAUUSD,XAGUSD --start 2016-01-01 --force`
  (still running at commit time; ~10y target)

- FX majors daily population kicked (core set for breadth):
  `... --symbols EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD --start 2018-01-01 --force`

- 0.4 structural work (instrument-aware quality gate + point value map):
  - `src/data/dukascopy_fetcher.py`: `INDEXES` set (with variants from Dukascopy range-of-markets + common forms), `INDEX_POINT_VALUES` (to be populated after successful empirical verification), relaxed `max_weekday_zero_rate` (0.18 default for indices vs 0.05), `download_dukascopy_data` + `get_multi_timeframe...` now accept the override.
  - `research/multiasset/data.py`: `_is_index`, auto-relaxed gate on fetch, yfinance fallback for indices (see below).

- Index symbol discovery: exhaustive probe (`probe_indices_v2.py`) returned only 404s across many recent weekdays for all common variants (USA500, USA500.IDXUSD, USATECH*, DEU40, GBR100, JPN225, DEU.IDX, etc.). Official Dukascopy pages confirm the instruments exist (USA500.IDX/USD etc.) with Sun-Fri hours. Public bi5 feed may be limited/sparse for these CFD indices in the current environment or require exact JForex-style naming.

  **Practical decision for breadth**: Dukascopy path remains primary (and is wired with the correct relaxed gate + point-value hook). Added a thin yfinance fallback (`_fetch_index_yf`) inside `research/multiasset/data.py` using well-known tickers (`^GSPC`, `^GDAXI`, `^FTSE`, `^N225`, `^IXIC`/`^NDX`). This lets the gross test actually exercise a diversified book even if Dukascopy bi5 for indices is not yielding bars right now. When a successful Dukascopy fetch for an index appears, it will be preferred.

- Minimal gross path (the only machinery we are allowed to build before THE GATE):
  - `research/multiasset/signals.py`: `ts_momentum(...)` (single 252-bar trailing return sign, pure) + `ts_momentum_series` + corr helper.
  - `research/multiasset/portfolio.py`: `inverse_vol_weights`, `portfolio_equity_curve`, `portfolio_metrics` (PF, Sharpe, MAR, maxDD, ... — all gross).
  - `research/multiasset/backtest.py`: `load_universe_d1`, `run_gross_tsmom_backtest` (the daily walker), **hard "signal on bar t close → position for bar t+1"** rule, `print_gate_report` that emits the exact numbers + correlation matrix + the pass/fail language.
  - `research/multiasset/run_gross_check.py`: convenient CLI driver that loads caches for a symbol list, runs the above, and prints the gate report.

- Also landed `research/multiasset/run.py` skeleton (future single entrypoint per the original plan; currently thin).

All new code passed ruff. 205 inherited tests still green.

## Immediate next commands the operator can run (as data arrives)

```bash
# Watch the backfills (they are long-running)
# When metals or FX finish you will see the "Done." line in their logs.

# Once you have reasonable caches (even partial long windows), run the gross diagnostic:
PYTHONPATH=. .venv/bin/python -m research.multiasset.run_gross_check \
  --symbols XAUUSD,XAGUSD,USA500,USATECH,DEU40,GBR100,EURUSD,GBPUSD,USDJPY \
  --lookback 252

# Or the short metals + FX we already had + yf indices for a quick smoke of the whole pipeline.
```

The correlation matrix is printed automatically by the gate report (mean pairwise + per-instrument matrix). High correlation is called out as a warning that the "portfolio" may actually be fewer independent bets.

## Status at commit

- Metals (2016+) backfill: running
- FX majors (2018+): running
- Indices: structural 0.4 + gate + yf fallback complete; actual long cache population will use whatever Dukascopy + yf gives us for the five names.
- Gross machinery + runner + correlation check: ready
- Costs, guard, evaluate seam, full OOS judge: **not started** (correct per gross-first)

Next real milestone is "THE GATE" once the long caches finish and the runner produces a stable gross PF/Sharpe on the chosen universe.

All work is isolated to this worktree. Main and the deployed Branch B scanner remain untouched.

(Deviation from linear plan numbering is recorded above and is the correct application of the gross-first principle that the whole program is built around.)
