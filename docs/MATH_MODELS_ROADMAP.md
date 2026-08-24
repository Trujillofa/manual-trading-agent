# Math models roadmap

Which models to add next on this HITL agent, and how. **Docs only — not a
live-go, not a KEEP claim, not a retune of V2 / RSI / Fib / SMC.**

Paper/Telegram stay in `src.cli scan`. Offline walks stay on the #49 contract
in [`BACKTEST_RUNNERS.md`](BACKTEST_RUNNERS.md): next-bar open, frozen
`CostBook`, develop-only rank. Shared risk/meta layer — **not** five new
strategy folders.

## Current stack

Human-in-the-loop forex / multi-asset agent:

| Surface | What it is |
|---|---|
| Live scanner | `evaluate_entry` (MTF RSI + V2 + gates + Rule C + ATR TP/SL) → Telegram / `signal_audit.jsonl` |
| Branch B watchlist | XAU/USD, BTC/USD, OIL, NASDAQ — decision support only |
| Offline runners | RSI+MA+HH/LL, Donchian, pivot, HTF Fib, SMC, enhanced (`src.cli backtest-enhanced`) |
| Chart | `pine_scripts/htf_pivots_fib_ema_strategy.pine` |

#49 (merged) moved RSI / Donchian / pivot / enhanced to next-bar fills +
`CostBook` + develop-only sweep rank. HTF Fib was already honest. SMC now
re-ranks on IS only.

Existing `src/risk/manager.py` is concurrent-position / daily-loss gates.
It is not vol or regime. ATR(14) stays the TP/SL unit in `evaluate_entry`.

Closed research lanes stay closed: FX directional-TA retunes, daily
stat-arb z-score, H1 compression-breakout, HTF Fib autosearch. See
[`docs/research/CLOSED_RESEARCH_LANES.md`](research/CLOSED_RESEARCH_LANES.md).

## Principles

1. **Shared overlay, not new engines.** One `src/risk/` package consumed by
   every runner and (optionally) the scanner. Do not add `src/strategy/garch/`
   or a sixth `scripts/run_*_backtest.py`.
2. **Classical first.** GARCH + a cheap regime switch before LightGBM.
   Skip deep RL — this is a HITL alert agent.
3. **Replay the #49 baselines before trusting new math.** Re-run RSI,
   Donchian, pivot, and enhanced on the current CostBook. HTF Fib can stay.
   SMC: re-rank only; do not change fill/cost.
4. **Develop-only selection, frozen costs, holdout is judge.** Same split
   discipline as the runners. Not a live-go and not a promote path.

## Before new math (precondition)

#49 is on `main`. Replay, do not retune:

```bash
python scripts/run_rsi_ma_hh_ll_backtest.py --pairs EUR/USD --days 58
python scripts/run_donchian_backtest.py --pairs EUR/USD --days 365 --sweep baseline
python scripts/run_pivot_backtest.py --pairs EUR/USD --days 365 --entry-types WEEKLY
python -m src.cli backtest-enhanced --pair EUR/USD
```

Record develop/holdout counts. Those numbers are the baseline any GARCH or
regime overlay must beat on the **same** book (2/2 pip + $3/side). HTF Fib
needs no fill rewrite. SMC: IS `selection_score` only.

## Now

This “Now” section is a map, not an implement authorization. Do not add
`src/risk/vol.py` (or `regime.py`) until a one-page overlay contract exists
**and** the #49 replay numbers above are recorded. This GARCH size/skip
overlay is **not** a reopen of the discarded H1 vol-regime
compression-breakout lane.

### 1. Shared GARCH vol overlay — `src/risk/vol.py`

GARCH(1,1) on the same UTC bars the runner already walks (15m preferred;
1h if the walk is hourly). EWMA fallback when the fit fails.

Return a tiny frozen state, e.g. `sigma`, `percentile`, `size_mult`, `skip`.
Callers may scale lots or skip the bar. **Do not change the entry rule.**

- Overlay every runner in [`BACKTEST_RUNNERS.md`](BACKTEST_RUNNERS.md).
- Scanner: annotate first (`signal_audit.jsonl` + Telegram note). No
  `OrderSend`. Default **off** in `config/settings.yaml`.
- ATR stays TP/SL. GARCH is the risk overlay, not a second stop engine.
- Causality: fit on data strictly before the signal bar (same peek bar as
  `tests/test_backtest_quality.py`).

### 2. Trend vs range — Kalman or EMA-of-regime

Same package (`src/risk/vol.py` or a sibling `src/risk/regime.py` — not a
new strategy tree).

Pick one cheap filter and stick to it for the first pass:

- Kalman on close (level + slope), **or**
- EMA of a regime proxy already in-repo (ADX from `evaluate_entry`,
  Donchian width from HH/LL, or GARCH percentile).

Output: `trend` | `range`.

| Regime | Prefer (existing runners) | De-emphasize |
|---|---|---|
| Range | RSI+MA+HH/LL, Donchian | HTF Fib, SMC |
| Trend | HTF Fib, SMC | RSI / Donchian mean-reversion |

This is a **router**, not a new entry. Scanner may print the label next to
`is_ranging` (today that flag is ADX < threshold only).

## Next

### One LightGBM meta-model — `src/risk/meta_label.py`

One model over **shared** features, answering only **“take this alert?”**
(binary). Not direction, not a new signal, not per-runner models.

Start with features this repo already computes:

- HTF bias — 1h SMA/EMA side (`evaluate_entry` SMA gate, Branch B EMA 20/50)
- RSI — 15m / 30m / 1h
- Donchian width — 20-bar HH−LL / ATR
- Session — bar UTC hour (`session_allowed_utc`, pivot SESSION 07–17 / 13–22)
- Optional: GARCH percentile, regime label

Label from the #49 **runner** replay only (RSI / Donchian / pivot /
enhanced), not from live `evaluate_entry` / MTF+V2 scanner fires — that
family is structurally near zero trades. Did the alert hit TP before SL
under next-bar `CostBook`? Train on develop only; freeze; holdout judges.
Require develop N ≥ 30 before the model is allowed; if N is below that,
skip LightGBM. The model must not become a second entry rule. Default
**off** in scan.

### Short cointegration scan

A script-sized scan for pairs you actually review by hand — Branch B
names plus the FX pairs still walked in the runners (e.g. EUR/USD,
GBP/USD). Residual z as HITL context (skip a second alert on a tightly
bound pair).

This is **not** a reopen of daily stat-arb
([`STAT_ARB_RESULTS_2026-06-18.md`](research/stat_arb/STAT_ARB_RESULTS_2026-06-18.md)).
No new z-entry strategy, no lookback/threshold search, no KEEP path.

## Later

HMM session regimes for the scanner — hidden states on session returns
(London / NY vs dead hours). Sits beside or later replaces the static
`session_allowed_utc` windows in `evaluate_entry`. Only after GARCH +
regime + the meta-label have a develop-only replay.

## Skip

| Idea | Why not here |
|---|---|
| GBM / Black–Scholes toys | FX/CFD alerts, not option pricing |
| Jump-diffusion | No tick/jump book; overfit theater |
| Markowitz as the main loop | One-lot HITL agent; `CostBook.lot_size` is not a portfolio optimizer |
| PPO / deep RL | Autonomous policy vs Branch B HITL. Out of scope. |

Also skip: new live CLI commands, broker adapters, and retuning RSI 30/70,
V2 buffer/confirm, ADX 25, or Fib levels to “make the model look good.”

## Implementation shape

```
src/risk/
  manager.py      # exists — position / daily-loss gates
  vol.py          # NOW — GARCH + size/skip (+ regime if kept here)
  regime.py       # NOW — only if Kalman/EMA does not fit in vol.py
  meta_label.py   # NEXT — LightGBM “take this alert?”
```

**Scanner / CLI** (`src/cli.py` `run_scan`):

1. Keep the single `evaluate_entry(...)` call. Do not fork entry logic.
2. If `decision["fired"]` and the overlay is enabled: call `vol` / regime /
   `meta_label` on bars already in memory.
3. Write fields onto the audit / Branch B decision-signal payload
   (`src/evaluation/branch_b_decision_signal.py`). Telegram: extra line,
   not a second alert type.
4. Default off. No lot change in paper until a written replay says so.
5. No new subcommand. `scan` / `telegram-poll` / `backtest-enhanced` stay
   as they are.

**Runners:** import the same functions inside the existing walk. Do not
clone CostBook. Rank overlays on develop only.

**Tests (when code lands):** freeze + no-peek, same spirit as
`tests/test_backtest_quality.py`. No strategy-grid tests.

## Non-goals

- Live-go, `OrderSend`, or promoting Branch B to KEEP
- New strategy packages or a seventh backtest engine
- Reopening closed lanes (FX directional TA, daily pairs z-score, H1
  vol-regime breakout, HTF Fib autosearch)
- Cost or fill “improvements” after #49
- Per-pair LightGBM, deep models, or RL
- Changing Pine scripts except a later, optional display of the same
  overlay labels
