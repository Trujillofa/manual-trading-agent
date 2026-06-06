# Multi-Asset Momentum — Execution Plan (worktree `research-multiasset-momentum`)

Concrete, step-by-step build plan for the new program defined in
[`NEW_PROGRAM_PLAN.md`](NEW_PROGRAM_PLAN.md). That doc is the *why* and the gates;
this doc is the *how* — file layout, real APIs to reuse, decision gates per phase,
and the fail-fast budget. Runs entirely in this isolated worktree; `main` and the
deployed Branch B scanner are untouched.

**Re-entry justification (recap):** changes instrument (metals/indices, not FX
majors), edge (portfolio time-series/cross-sectional momentum, not single-pair
intraday directional TA), and timeframe (daily/H4, not M15/H1). Satisfies the
falsifiable re-entry criteria in
[`FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`](FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md).

---

## Reuse map (verified against the code, do NOT rebuild)

| Need | Reuse | Notes |
|---|---|---|
| IS/OOS split + held-out OOS judge + KEEP gates | `research/evaluate.py` (`evaluate_config`, `WindowStats`, `EvalResult`, `MIN_TRADES=30`) | Add a new `engine="ts_momentum"` branch in `evaluate_config` (mirrors the `engine="live_mtf_rsi"` seam at `evaluate.py:158`). |
| Costed bar-walker pattern | `backtest_live_entry` (`evaluate.py:249`) | Reference only; daily portfolio backtest is a new walker (`research/multiasset/backtest.py`). |
| bi5 download + resample + cache | `src/data/dukascopy_fetcher.py` (`download_dukascopy_data`, `_point_value`, quality gate at `:433`) | **Extend**, don't fork. |
| Pure entry/signal pattern | `src/scanner/evaluator.py` (side-effect-free) | New signal fns under `research/multiasset/signals.py` follow this purity. |
| Discipline | gross-first, fail-fast budget, OOS-gated KEEP, portfolio metrics | Carry verbatim. |

**Key code facts to respect**
- `dukascopy_fetcher.POINT_VALUES` is currently an empty `{}` (line 35); the live
  resolver is `_point_value()` (line 61): JPY → `1000`, everything else → `100000`.
  Metals/indices need their own point values — **verify empirically** (see Phase 0.2),
  do not assume.
- The quality gate raises `DukascopyDataQualityError` when `>5%` of weekdays have
  zero bars (`download_dukascopy_data(..., strict=...)`, line 433). Indices/metals
  have different trading calendars → this gate must be **relaxed or made
  instrument-aware** before fetching them (Phase 0.4). Metals trade ~FX hours, so
  XAU/XAG may pass as-is — test gold first to de-risk.
- `evaluate.py` gates are FX-trade-count tuned (`MIN_TRADES=30` per window). Daily
  portfolio momentum produces *far fewer, larger* trades → Phase 1 adds a
  **portfolio-appropriate gate** (risk-adjusted: OOS Sharpe/MAR + net PnL after
  swap), keeping `MIN_TRADES` only as a sanity floor, not the primary judge.

---

## Target file layout (new code, isolated under `research/multiasset/`)

```
research/multiasset/
  __init__.py
  run.py            # entrypoint (sidesteps the FX STOP guard cleanly)
  universe.py       # instrument list + Dukascopy codes + point values + calendars
  data.py           # thin wrapper over dukascopy_fetcher: fetch+cache daily/H4 parquet
  costs.py          # honest daily cost model: spread + overnight swap + commission
  signals.py        # pure signal fns: ts_momentum(), xs_momentum() (Phase 2)
  portfolio.py      # vol targeting / inverse-vol sizing; aggregate equity curve
  backtest.py       # daily portfolio bar-walker; gross-vs-net; per-inst + portfolio metrics
  metrics.py        # Sharpe, MAR, max DD, PF, Monte-Carlo DD
data/cache/multiasset/   # parquet cache, mirrors existing layout
docs/research/multiasset/  # diagnostic outputs + decision-gate writeups
```

Why a new entrypoint (`research/multiasset/run.py`) instead of `autosearch.py`:
the FX STOP guard blocks `research/{autosearch,run_experiment}` unconditionally.
Cleanest is to **scope that guard to FX-majors-M15/H1** (Phase 0.5) AND run via the
new entrypoint, so neither the guard nor the FX harness is disturbed.

---

## Phase 0 — Foundations (data + costs + isolation)

**0.1 venv** — fresh worktree has no `.venv` (gitignored, per-dir):
`python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.
Smoke: `pytest tests/ -q` (inherited suite must pass before we add anything).

**0.2 Metals data (highest-confidence first).** Add `XAUUSD`, `XAGUSD` to a metals
point-value map and wire `_point_value()` to consult it. Fetch a small recent window
(e.g. 30 days M1) and **assert decoded prices are sane** (gold ≈ $1,800–2,700,
silver ≈ $20–35) to confirm the point value empirically. This is the cheap, trivial
win — do it and verify before touching indices.

**0.3 Resample + cache.** Add `data.py` to resample M1 → daily + H4 and cache as
parquet under `data/cache/multiasset/`. Idempotent (skip-if-cached). Fetch full
history available for XAU/XAG (target ≥ 8–10 years for a real IS/OOS split).

**0.4 Indices data + calendar gate.** Add Dukascopy index codes (start with what
serves cleanly: `USA500.IDXUSD`/`US500`, `USATECH100`, `DEU40`, `GBR100`, `JPN225`
— confirm exact symbols against the feed). Make the weekday-zero-bar quality gate
**instrument-aware** (per-instrument expected-session calendar, or `strict=False`
+ explicit logged rate) so index holidays don't trip the FX 5% gate. Verify decoded
prices per index.

**0.5 Honest daily cost model (`costs.py`).** Per-instrument: spread (price units),
**overnight swap/financing** (long & short, applied per calendar day held — this is
the one that matters at daily horizon and is easy to understate), commission.
Conservative defaults sourced from a real retail CFD/MT5 spec; cite the source in a
comment. Expose `cost_per_trade(symbol, side, bars_held)` so the backtest can take
gross (costs=0) vs net in one switch.

**0.6 Guard scoping.** Narrow the FX STOP guard in `research/{autosearch,run_experiment}`
to FX-majors-M15/H1 only (so the new program isn't blocked), and route the new
program through `research/multiasset/run.py`. Add a test asserting the guard still
fires for the FX intraday case and does NOT fire for `ts_momentum`.

**Phase 0 exit:** metals (and ≥1 index) cached as clean daily/H4 parquet with
verified prices; cost model callable; guard scoped; inherited tests green.

---

## Phase 1 — Hypothesis #1: time-series (absolute) momentum, daily, vol-targeted

**1.1 Signal (`signals.py`).** Pure `ts_momentum(prices, lookback) -> {-1,0,+1}`:
sign of trailing return (or fast/slow MA cross) over a daily lookback (start with a
single sensible value, e.g. ~12-month / 252-bar, **no sweep yet**). Minimal params —
anti-overfit.

**1.2 Sizing + portfolio (`portfolio.py`).** Inverse-vol / vol-target each
instrument to equal risk; aggregate to one portfolio equity curve. The edge is
portfolio-level — judge the portfolio, not the best symbol.

**1.3 Backtest (`backtest.py`) + engine seam.** Daily bar-walker producing per-trade
and daily-equity records. Add `engine="ts_momentum"` to `evaluate_config`
(`evaluate.py`) mirroring the `live_mtf_rsi` branch, so the existing IS/OOS split +
held-out OOS judge are reused unchanged.

**1.4 Gross-first diagnostic (the decision gate).** Run **gross (costs=0)** across
the universe and full history.
- **Gross portfolio PF ≈ 1.0 (≲ ~1.1) → NO EDGE.** Stop hypothesis #1 immediately.
  Do not spend budget chasing net survival on a no-edge gross. Go to Phase 2 (#2).
- **Gross clearly > 1.1 and positive → proceed:** run **net** (spread + swap +
  commission) on IS, then judge on **held-out OOS** with the portfolio gate:
  - OOS net PnL positive after swap+spread,
  - OOS PF ≥ ~1.2–1.3 **and** risk-adjusted bar (Sharpe/MAR) clears,
  - IS/OOS consistency (same sign, no regime flip),
  - acceptable Monte-Carlo max DD,
  - `MIN_TRADES` as a sanity floor only.

Write the gross/net numbers + the gate verdict to
`docs/research/multiasset/HYP1_TSMOM_<date>.md` (KEEP / ITERATE / DROP).

---

## Phase 2 — Iterate or pivot (one lever at a time, OOS-judged)

Decision tree off the Phase 1 gate:
- **KEEP:** harden → Monte-Carlo → paper-shadow → only then consider operating /
  merging to `main`. No live money on backtest alone.
- **ITERATE** (gross had edge, net marginal): change **one** lever, re-judge on OOS
  only — never tune against OOS. Candidate levers (priority): vol-target level,
  lookback, H4 vs daily, trade-frequency/turnover (swap drag), universe membership.
- **PIVOT** (gross ≈ 1.0): move to **Hypothesis #2 — cross-sectional momentum**
  (`xs_momentum()` in `signals.py`: rank universe, long top / short bottom; reuse
  portfolio + cost + judge layers). Then #3 (Donchian breakout + vol filter) if budgeted.

**Fail-fast budget:** ~2–3 structurally different hypotheses (#1 TSMOM, #2 XSMOM,
#3 Donchian). If all show gross ≈ 1.0 across the universe → write
`docs/research/multiasset/MULTIASSET_MOMENTUM_NEGATIVE_RESULT_<date>.md` (mirror the
FX negative-result report), lock the line, and stop — same discipline that closed FX.

---

## Anti-overfit guardrails (non-negotiable, carried from FX)

- Gross-first: never debug net costs on a no-edge gross.
- Judge the **portfolio** on **held-out OOS**; never select params on OOS.
- Minimal params; no per-instrument cherry-picking; report the full universe.
- Costs include **overnight swap** — the FX line died at the friction wall; do not
  understate the daily-hold equivalent.
- Every decision gate produces a dated writeup under `docs/research/multiasset/`.

---

## Immediate next action

Phase 0.1 → 0.3: venv in the worktree, extend the fetcher for **metals only**,
verify decoded gold/silver prices, cache daily/H4. Stop and report the verified
price sanity-check before moving to indices (0.4).
