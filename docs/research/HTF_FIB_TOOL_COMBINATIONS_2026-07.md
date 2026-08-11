# HTF Fib tool combinations (recommended stacks)

**Date:** 2026-07-10  
**Base:** confirmed HTF-pivot Fib + EMA/RSI (`pine_scripts/htf_pivots_fib_ema_strategy.pine`, `scripts/run_htf_fib_backtest.py`)  
**Lane status:** FX majors directional TA remains a locked negative result. This note preregisters **partial** multi-tool stacks for an override-guarded re-rank only. A KEEP is not expected; an honest all-DISCARD ranking is a valid outcome.

## Tools considered

| Tool | In searchable space? | Role |
|------|----------------------|------|
| Liquidity sweep + reclaim | **Yes** | Entry confirmation: wick through swing origin, close back inside |
| Anchored VWAP (tick-volume) | **Yes** | Value/bias from swing origin (FX: tick volume or unit weight if volume is 0) |
| Order flow / CVD / footprint | **No** | No real bid-ask tape on this FX OHLC path — demoted entirely |
| Full volume profile (VPOC/VAH/VAL) | **Deferred** | Not a first-class hard filter in this search (manual eyes-only only) |
| Session VWAP | **No** | Weak on 24h FX; not in PARAM_SPACE |

## Invalidation rule (required for sweep)

- **Wick invalidation** (legacy hardened): any pierce of swing origin kills the Fib — **blocks** classic origin sweeps.
- **Close invalidation** (sweep stacks): Fib dies only on **close** beyond origin; a wick-through + close reclaim is a valid sweep signal.

## Recommended combinations (partial stacks only)

### C0 — `hardened_mtf` (control)

- **In:** confirmed 4H Fib golden zone, MTF RSI, EMA50/200 stack, candle confirm, wick invalidation, one entry per swing  
- **Out:** sweep, AVWAP, order flow, VP  
- **Why:** locked baseline from the HTF Fib archive; control for ranking deltas.

### C1 — `hardened_sweep`

- **In:** C0 filters **except** close-based invalidation + **hard liquidity sweep** (wick beyond swing low/high, close reclaims origin, still in golden zone)  
- **Out:** AVWAP, order flow, VP  
- **Why:** highest conceptual fit — Fib fades often want stop-runs at structure.

### C2 — `hardened_avwap`

- **In:** C0 filters + **anchored tick-volume VWAP** bias (long only if close ≥ AVWAP from swing low; short only if close ≤ AVWAP from swing high). Wick invalidation retained.  
- **Out:** sweep, order flow, VP  
- **Why:** tests value-filter alone without coupling to sweep semantics.

### C3 — `hardened_sweep_avwap`

- **In:** C1 (sweep + close invalidation) **and** anchored VWAP bias  
- **Out:** order flow, VP  
- **Why:** structure + stop-run + value; still only two new tools, not the kitchen sink.

### Soft tier (for ranking when hardened is sparse)

Same tool axes as C0–C3, but RSI 45/55, no MTF RSI, no EMA50/200 stack, candle confirm on, exits near the archived IS grid (TP/SL 2 ATR, hold 64, left/right 5/2). Used so sweep/AVWAP gates can be ranked on non-zero trade samples; **not** a promotion claim.

| Combo id | Tools |
|----------|--------|
| `soft_baseline` | none (executable marker-style control) |
| `soft_sweep` | liquidity sweep |
| `soft_avwap` | anchored tick-volume VWAP |
| `soft_sweep_avwap` | sweep + AVWAP |

### Explicitly not recommended as search variants

- **All four tools at once** (sweep + OF + VWAP + VP) — over-constrained, unfalsifiable sparsity.  
- **Hard order-flow gate on FX spot** — fake tape.  
- **Hard fixed-range VP** — deferred; cost/complexity without data edge.

## Search discipline

1. Preregistered combos C0–C3 are always evaluated first (fixed list).  
2. Light PARAM_SPACE perturbations (RSI bounds, ATR TP/SL, hold bars, pivot left/right) may ride on top of a chosen combo id.  
3. Judge: locked gates (`MIN_WINDOW_TRADES=30`, OOS net PF ≥ 1.20, positive IS/OOS net PnL).  
4. Requires `--override-negative-result docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md` (and respects HTF Fib negative archive).  
5. Selection never invents cost/gate changes. Winner = best score under gates, or **all-DISCARD**.

## Executable mapping

| Combo id | `require_liquidity_sweep` | `require_anchored_vwap` | `invalidate_swing` | Notes |
|----------|---------------------------|------------------------|--------------------|-------|
| hardened_mtf | false | false | true (wick) | control |
| hardened_sweep | true | false | true (close) | sweep forces close-invalidation |
| hardened_avwap | false | true | true (wick) | |
| hardened_sweep_avwap | true | true | true (close) | |

See `research/htf_fib_config.py` (`COMBOS`, `PARAM_SPACE`) and `research/htf_fib_autosearch.py`.
