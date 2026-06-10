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

## How to Record a New Closed Lane

1. Write a clear negative-result document (one paragraph core finding + data/method summary + numbers + links).
2. Append an entry here (status, one-paragraph finding, pointer).
3. Add a one-line summary to `docs/PROJECT_STATUS_2026-06.md`.
4. Leave the branch/worktree that holds the harness and data — it is the archive.
5. Strengthen any code guards if they existed.

Future agents (or humans) should consult this list before proposing work. The goal is to stop rediscovering the same dead ends.