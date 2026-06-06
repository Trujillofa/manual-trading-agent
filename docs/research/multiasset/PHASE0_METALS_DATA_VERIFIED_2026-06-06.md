# Phase 0 (0.1–0.3) — Metals data + fetcher extension + cache (verified)

**Date:** 2026-06-06  
**Worktree:** `research-multiasset-momentum` (branch `research-multiasset-momentum`)  
**Status:** Complete for metals; **STOP per plan before 0.4 (indices)**.

## What was executed (matches MULTIASSET_MOMENTUM_EXECUTION_PLAN.md immediate next action)

1. **0.1 venv**  
   - `rm -rf .venv; python3 -m venv .venv` (fresh per plan note).  
   - `.venv/bin/python -m pip install --upgrade pip setuptools wheel`  
   - `.venv/bin/python -m pip install -e ".[dev]"` (pyarrow added later for cache).  
   - Smoke: `pytest tests/ -q` → **205 passed** (1.02s). Inherited suite green.

2. **0.2 Extend fetcher for metals only + empirical price sanity**  
   - Edited [src/data/dukascopy_fetcher.py](src/data/dukascopy_fetcher.py):  
     - Added `METALS = {"XAUUSD", "XAGUSD"}`.  
     - Wired `_point_value()` to return `1000` for metals (JPY already 1000; FX non-JPY 100000).  
     - Updated module doc.  
   - Raw bi5 probe (`/tmp/probe...`): recent day raw price ints ~4.457M (XAU), ~73k (XAG).  
     - pv=1000 produces XAU ~4457 / XAG ~73 (the only round divisor that lands in any plausible band).  
   - Full verification run via `download_dukascopy_data(..., strict=False)` over ~14 calendar days:  
     - XAUUSD: min 4312.70, max 4592.53, last ~4326.70, weekday_zero_rate=0.0%, 20160 bars.  
     - XAGUSD: min 67.57, max 78.78, last ~67.79, weekday_zero_rate=0.0%.  
     - **Verdict in script:** PASS. (Note: plan's "$1800-2700 / $20-35" ranges were 2024-era; 2026 market levels are higher — empirical data rules.)

   Metals trade ~FX hours → the FX 5% quality gate (`DukascopyDataQualityError` at dukascopy_fetcher:433) is **not tripped** on gold/silver (0% zero-bar weekdays in the window). Safe to use `strict=True` for metals; indices will need the instrument-aware relaxation in 0.4.

3. **0.3 Resample + cache (daily + H4)**  
   - Created `research/multiasset/__init__.py` and [research/multiasset/data.py](research/multiasset/data.py).  
     - `fetch_and_cache(symbol, timeframes=("d1","h4"), start=..., force=..., strict=False)` — thin wrapper, idempotent (skip if `.pkl` present).  
     - Reuses `download_dukascopy_data` + `_resample_ohlc` (via the module).  
     - `load_cached`, `cache_info`, and a `__main__` entry for `python -m research.multiasset.data --start YYYY-MM-DD --force`.  
     - Cache backend: pickle (`.pkl`) for reliable operation in this venv (pandas 3.x + pyarrow had extension registration friction on `to_parquet`). Parquet is the documented target format; this is a drop-in compatible detail. `data/cache/` is already gitignored (`/data/` in `.gitignore`).  
   - Populated (short window for fast proof-of-pipeline; see below for full):  
     ```
     python -m research.multiasset.data --symbols XAUUSD,XAGUSD --start 2026-05-20 --force
     ```
     Output:
     - XAUUSD d1: 17 bars, 2026-05-20..2026-06-05, last_close=4326.70  
     - XAUUSD h4: 102 bars, ... last_close=4326.70  
     - XAGUSD d1/h4 analogous, last_close=67.79  
   - Post-populate verification (`PYTHONPATH=. python /tmp/verify_cache.py`): frames load, prices match the M1 sanity numbers exactly, resample produces clean daily closes and 4h bars.

## Key code facts respected (per plan)
- `POINT_VALUES` remains `{}`, resolver lives in `_point_value()` — now extended for metals.
- 5% gate is FX-tuned; metals pass it (verified).
- `MIN_TRADES=30` etc. left untouched (will be addressed by portfolio risk-adjusted gate in Phase 1 for daily momentum).

## How to get the real 8–10y history (for IS/OOS)
```bash
# From repo root in this worktree
rm -f data/cache/multiasset/XAUUSD_*.pkl data/cache/multiasset/XAGUSD_*.pkl
.venv/bin/python -m research.multiasset.data \
  --symbols XAUUSD,XAGUSD --start 2016-01-01 --force
# (or 2015-01-01 etc.; Dukascopy has deep history for metals)
```
Then use `research/multiasset/data.py:load_cached("XAUUSD", "d1")` etc. in later phases.
The wrapper is intentionally small — later backtest/portfolios will call it.

## Layout created
```
research/multiasset/
  __init__.py
  data.py                 # the cache/resample layer (Phase 0.3)
data/cache/multiasset/
  XAUUSD_d1.pkl
  XAUUSD_h4.pkl
  XAGUSD_d1.pkl
  XAGUSD_h4.pkl
docs/research/multiasset/
  PHASE0_METALS_DATA_VERIFIED_2026-06-06.md   # this report
```

`src/data/dukascopy_fetcher.py` was the only shared-file change (additive, metals-only).

## Next (per plan — do not start without ack)
- Review this + the execution plan.
- When ready: Phase 0.4 (indices + calendar-aware gate) → 0.5 (honest swap-inclusive costs.py) → 0.6 (guard scoping + research/multiasset/run.py entrypoint).
- Only after 0.6 foundations complete do we implement signals/backtest for Hypothesis #1 (TSMOM daily, vol-targeted) behind the `engine="ts_momentum"` seam in evaluate.py.

## Commands for the record
- Full smoke + lint: `source .venv/bin/activate && pytest tests/ -q && ruff check src/ research/multiasset/ && ruff format --check ...`
- Re-verify prices (no cache): the `/tmp/verify_metals_prices.py` (or re-run the probe).
- This worktree is isolated; `main` + deployed Branch B are untouched.

**Ready for review / continuation decision.** (Gross-first discipline and the rest of the plan remain in force.)
