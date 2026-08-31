# CostBook replay — 2026-08-24

Replay of the #49 runners on the frozen `CostBook` (2 pip spread, 2 pip
slip, $3/side). **Not a live-go, not a KEEP, not an overlay authorization.**

Recorded so any later GARCH / regime / LightGBM work has a same-book
baseline. See [`docs/MATH_MODELS_ROADMAP.md`](../MATH_MODELS_ROADMAP.md).

Pair: EUR/USD. Clock: UTC. Execution: close signal, next-bar open,
stop-first. Rank / selection uses develop (first 65%) only.

## Counts

| Runner | Command / window | Develop N | Holdout N | Notes |
|---|---|---|---|---|
| RSI+MA+HH/LL | `run_rsi_ma_hh_ll_backtest.py --pairs EUR/USD --days 58` · yfinance 15m **2026-06-28 – 2026-08-21** (39d delivered, not 58) | V0 best config 6–8; V2 / V0_MA / V2_MA **<5** (no ranked row) | Not printed per config. All-period pooled: V0 80, V2 12, V0_MA 0, V2_MA 0 across 12 configs each | Production-like **V2_MA: 0 trades**. Best develop V0 PF 0.35–0.42 |
| Donchian baseline | `run_donchian_backtest.py --pairs EUR/USD --days 365 --sweep baseline` · cached yfinance 15m **2025-05-23 – 2026-05-22** | **<5** (top-10 develop empty) | Unused; all-period **1** trade (WR 100%, +0.19%, PF undefined/999) | Cache hit `results/cache/EURUSD_365d.parquet` (May 2026 vintage), not a fresh 365d pull |
| Pivot WEEKLY | Roadmap command is `--days 365` (script default `dukascopy`). No local M1 pin; a live 365d Dukascopy pull is ~1.5h uncached. yfinance `--days 365` 15m is rejected (60d cap). Ran `--days 58 --source yfinance` · 15m **57d / 5451 bars** · 180 configs | **<10** (top-10 develop empty) | Unused; all-period max **8** trades / config, PF 0.00 on the high-N rows | Not a Dukascopy 365d pin |
| Enhanced | `python -m src.cli backtest-enhanced --pair EUR/USD` · yfinance **2024-08-26 – 2026-08-24** | **226** | **104** | Total 330, WR 62.4%, **PF 0.35**, PnL **−87.39%**, max DD 87.39%. Only runner with N≥30 on both windows |

## What this authorizes

Nothing to implement. The overlay contract still does not exist.

- Sparse runners (RSI V2_MA, Donchian baseline, WEEKLY pivot) do not have
  develop N ≥ 30. LightGBM is forbidden on those labels.
- Enhanced has enough N and **fails** the same-book PF / PnL bar. All-period
  PF is 0.35 on 330 trades (−87.39%); holdout N is 104. The 2026-08-24 CLI
  did not print holdout PF separately; later `backtest-enhanced` prints
  per-window WR/PnL/PF. These recorded all-period numbers stay the book.
  An overlay must beat that same book — and still is not a promote path.
- Do not start `src/risk/vol.py`. Do not retune RSI 30/70, V2, ADX, or Fib
  to fatten N.

## Artifacts (local, gitignored `results/`)

Worktree `research/costbook-replay-2026-08`:

- `results/costbook_replay_2026-08-24/rsi_ma_hh_ll_backtest_20260824_154159.md`
- `results/costbook_replay_2026-08-24/donchian_backtest_20260824_154207.md`
- `results/costbook_replay_2026-08-24/pivot_v2_backtest_20260824_154328.md`
- `results/costbook_replay_2026-08-24/backtest_enhanced.log`
