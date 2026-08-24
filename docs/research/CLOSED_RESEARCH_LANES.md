# Closed Research Lanes

This document records research directions that have been terminated after honest negative results. 

**Agent Rule**: Do not reopen a closed lane for tuning, extra parameters, different lookbacks, cost tweaks, or "one more variant" unless there is a **genuinely new premise** that materially changes the instrument class, data source, edge family, or timeframe structure. Any new attempt must start in an isolated worktree or branch and must define its own gates (universe, metrics, pass/fail thresholds, stop rule) *before* writing code.

Re-opening without a new premise violates the fail-fast discipline.

## 1. FX Majors Directional TA (M15/H1 OHLC)

- **Status**: Permanently closed / locked negative result (2026-06)
- **Core Finding**: Gross PF consistently ~1.0–1.07 across ORB and trend-pullback families on full 365d Dukascopy data. No accessible edge before realistic costs. Live execution extremely sparse.
- **References**:
  - `docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md`
  - `research/program.md` (STOP banner + re-entry criteria)
  - Agent-proof guards in `research/autosearch.py` and `research/run_experiment.py`
- **Rule**: Do not restart FX-majors OHLC directional TA on M15/H1 (or similar intraday) on this data/family. Older promotion-table numbers from divergent engines are retired.

## 2. Daily Multi-Asset Time-Series Momentum (TSMOM)

- **Status**: Closed (2026-06)
- **Core Finding**: Gross-first gate on the planned diversified universe (metals XAU/XAG 2016+, 5 indices 2018+ via yfinance fallback because Dukascopy public bi5 was unavailable, core FX majors with long data). ~10-year history, 10 instruments, 252-bar lookback, inverse-vol portfolio. Result: gross PF 1.036, Sharpe 0.15, max DD ~17%. Diversification was real (mean pairwise correlation 0.19), but the TSMOM premise delivered no accessible edge before costs.
- **References**:
  - Full archive + harness + detailed report: GitHub branch `research-multiasset-momentum` (see `docs/research/multiasset/TSMOM_GROSS_GATE_RESULT_2026-06-07.md`)
  - Short summary also in main `docs/PROJECT_STATUS_2026-06.md`
- **Rule**: Do not tune lookback, vol targets, rebalance, add filters, or "more variants" on this TSMOM construction. Gross PF near 1.0 before costs is the stop signal. Realistic friction only makes it worse.

## 3. Carry / Swap / Funding (Hetzner cTrader Account)

- **Status**: CLOSED_DISCARD (2026-06-12)
- **Core Finding**: Real cTrader `ProtoOASymbolByIdReq` swap metadata returned `long=0.0` / `short=0.0` for all five resolved pairs on the Hetzner account. Gross carry falsifier net carry after entry drag was negative (-$27). The financing premise is absent on this account.
- **References**:
  - `docs/research/carry/CTRADER_CARRY_DISCARD_2026-06-12.md`
  - `docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md`
  - Ledger row: `research/new_edge/research_ledger.jsonl` (lane `carry`, status `CLOSED_DISCARD`)
- **Rule**: Do not continue carry research on this cTrader account. Reopen only with a different broker/account that provides verified nonzero long/short swaps.

## 4. Daily FX Statistical Arbitrage (Pairs Z-Score)

- **Status**: DISCARD (2026-06-18)
- **Core Finding**: Gross pass on EUR/GBP and AUD/NZD spreads, but OOS promotion gates failed. Best spread AUD/NZD OOS net PF 1.128 < 1.20; EUR/GBP OOS net PF 0.191. CAD/AUD JPY gross PF 0.601.
- **References**:
  - `docs/research/stat_arb/STAT_ARB_RESULTS_2026-06-18.md`
  - `docs/research/stat_arb/STAT_ARB_CONTRACT_2026-06-18.md`
  - Ledger row: `research/new_edge/research_ledger.jsonl` (lane `stat_arb`, status `DISCARD`)
- **Rule**: Do not retune z-score thresholds, lookbacks, spreads, or exit rules on this daily pairs prototype.

## 5. Event Surprise Drift (Post-Release)

- **Status**: DISCARD (2026-06-19)
- **Core Finding**: Pinned HF calendar data proof passed (lane `events`, status `DATA_PASS`), but the post-release surprise-drift falsifier failed net/OOS gates. Gross PF 1.200; OOS net PF 0.375 < 1.20 after widened-spread cost model.
- **References**:
  - `docs/research/events/EVENT_DRIFT_RESULTS_2026-06-19.md`
  - `docs/research/events/EVENT_DRIFT_CONTRACT_2026-06-19.md`
  - `docs/research/events/EVENT_DATA_MANIFEST_2026-06-19.md`
  - Ledger rows: `research/new_edge/research_ledger.jsonl` (lane `events`)
- **Rule**: Do not retune entry delay, hold period, surprise thresholds, or event-family filters on this prototype. Calendar data proof remains valid; the drift signal does not.

## 6. HTF Pivot/Fibonacci Directional TA (4H pivots, 15m execution)

- **Status**: DISCARD (2026-06-30; archived in branch `research/archive-htf-fib-2026-07`)
- **Core Finding**: Confirmed 4H-pivot Fibonacci setup on eight FX pairs (365d Dukascopy 15m). IS-selected grid winner (11 IS / 12 OOS trades) produced OOS net PF 0.07 and -11.53% OOS P&L. Marker baseline and hardened MTF variants also failed promotion gates. Fixed-lot account scenarios worsened under wider stops. This is **additional evidence** for the locked FX directional-TA closure — not a new edge family.
- **References**:
  - `docs/research/HTF_FIB_NEGATIVE_RESULT_2026-06.md`
  - `scripts/run_htf_fib_backtest.py`, `scripts/optimize_htf_fib_backtest.py`, `scripts/evaluate_htf_fib_accounts.py`
  - `pine_scripts/htf_pivots_fib_ema_strategy.pine`
  - Ledger row: `research/new_edge/research_ledger.jsonl` (lane `htf_fib`, status `DISCARD`)
- **Rule**: Do not retune pivots, Fibonacci levels, RSI thresholds, EMA filters, exits, or chart timeframe. Do not run further autosearch on this family. The optimizer requires `--override-negative-result` against the locked FX directional-TA report — that guard must remain.

## 7. Volatility Regime / Range Compression Breakout (H1 FX Majors)

- **Status**: DISCARD (2026-06-20; implementation merged in PR #10, commit `545fef0`)
- **Core Finding**: Fixed H1 Donchian compression breakout on seven FX majors (2016-01-01 → 2026-06-01). Gross stage passed (pooled gross PF 1.114, 3778 trades, max year concentration 12.8%), but the edge is too small to survive 6-pip round-trip costs. Pooled costed net PF 0.802; OOS net PF 0.782 < 1.20 gate.
- **References**:
  - `docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md`
  - `docs/research/vol_regime/VOL_REGIME_CONTRACT_2026-06-19.md`
  - `docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md`
  - `research/new_edge/vol_regime/range_compression_breakout_test.py`
  - Ledger row: `research/new_edge/research_ledger.jsonl` (lane `vol_regime`, status `DISCARD`)
- **Rule**: Do not retune compression percentile, persistence, entry window (07:00-17:00 UTC), 24-bar hold, pairs, or H1 timeframe. This is not permission to reopen unconditional Donchian/ORB directional TA.

## 8. CFTC COT Non-Commercial Positioning Reversal

- **Status**: RELATIONSHIP_FAIL / CLOSED (2026-06-30)
- **Core Finding**: Official CFTC data proof passed for all 23 fixed markets, but the
  preregistered 4-week reversal relationship failed on 22 markets with price coverage.
  IS slope was weakly negative (-0.00315, one-sided p=0.0506); OOS slope reversed
  positive (+0.00938, p=0.9991). The highest-positioning OOS quintile outperformed
  the lowest by 0.965%, only 50% of market slopes were negative, and the reversal
  ranked at the 1.6th percentile of within-market shuffled signals.
- **References**:
  - `docs/research/cot_positioning/COT_RELATIONSHIP_CONTRACT_2026-06.md`
  - `docs/research/cot_positioning/COT_RELATIONSHIP_RESULTS_2026-06.md`
  - `research/new_edge/cot_positioning/relationship.py`
  - Ledger row: `research/new_edge/research_ledger.jsonl` (lane `cot_positioning`,
    status `RELATIONSHIP_FAIL`)
- **Rule**: Do not tune positioning lookback, percentile thresholds, forward horizon,
  market subset, or add price filters to rescue the reversal premise. A materially
  different positioning thesis requires a new prewritten contract.

## New Edge Program Lane Scoreboard (2026-07-02)

| Lane | Status |
|---|---|
| FX directional TA | CLOSED |
| HTF pivot/Fibonacci directional TA | DISCARD |
| Daily TSMOM | CLOSED |
| Carry (Hetzner cTrader) | CLOSED_DISCARD |
| Stat-arb (daily pairs) | DISCARD |
| Event data proof (HF calendar) | DATA_PASS |
| Event surprise drift | DISCARD |
| Vol-regime compression breakout | DISCARD |
| COT positioning reversal | RELATIONSHIP_FAIL / CLOSED |
| Term-structure roll yield (commodity futures) | BLOCKED (data gate) |
| PEAD | CONTRACT_DEFINED (data proof only) |
| Zacks MCP statements / current ETF holdings | SCHEMA_PASS / ALPHA_BLOCKED (2026-08-22) |

**Deferred (not a standalone alpha lane):** Microstructure / execution-quality research should not proceed unless tied to an already-positive gross edge. It must not be used to rescue discarded signals.

## How to Record a New Closed Lane

1. Write a clear negative-result document (one paragraph core finding + data/method summary + numbers + links).
2. Append an entry here (status, one-paragraph finding, pointer).
3. Add a one-line summary to `docs/PROJECT_STATUS_2026-06.md`.
4. Leave the branch/worktree that holds the harness and data — it is the archive.
5. Strengthen any code guards if they existed.

Future agents (or humans) should consult this list before proposing work. The goal is to stop rediscovering the same dead ends.
