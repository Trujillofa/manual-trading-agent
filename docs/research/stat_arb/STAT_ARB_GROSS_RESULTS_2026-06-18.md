# Stat-Arb Gross Falsifier Results — 2026-06-18

## Lane verdict: GROSS_PASS

## Command
```bash
python -m research.new_edge.stat_arb.gross_stat_arb_test --start 2016-01-01 --end 2026-06-01 --output docs/research/stat_arb/STAT_ARB_GROSS_RESULTS_2026-06-18.md
```

## Window: 2016-01-01 → 2026-06-01

## Parameters (fixed, no optimization)
- Lookback: 60 days (hedge ratio + z-score)
- Entry z: ±2.0
- Exit z: cross 0.0
- Time stop: 20 bars
- Notional leg A: $100,000; leg B sized by rolling beta
- Costs: **zero** (gross-first)

## Per-spread results

### eur_gbp (EUR/USD vs GBP/USD)
- Period: 2016-06-15 → 2026-05-29 (2590 bars)
- Trades: 44
- Gross PF: 1.349
- Net PF (after $90/trade two-leg costs): 1.159
- Win rate: 52.3%
- Total gross P&L: $7,877.91
- Total net P&L: $3,917.91
- Avg P&L/trade: $179.04
- IS/OOS split at 2023-05-31 (70/30 chronological)
  - IS: 30 trades, gross PF 1.926, net PF 1.685
  - OOS: 14 trades, gross PF 0.271, net PF 0.191
- Max year concentration: 28.7% (2020)
- Verdict: **GROSS_PASS** — N/A

### aud_nzd (AUD/USD vs NZD/USD)
- Period: 2016-06-15 → 2026-05-29 (2590 bars)
- Trades: 41
- Gross PF: 1.645
- Net PF (after $110/trade two-leg costs): 1.384
- Win rate: 63.4%
- Total gross P&L: $12,742.32
- Total net P&L: $8,232.32
- Avg P&L/trade: $310.79
- IS/OOS split at 2023-05-31 (70/30 chronological)
  - IS: 28 trades, gross PF 1.733, net PF 1.485
  - OOS: 13 trades, gross PF 1.409, net PF 1.128
- Max year concentration: 21.4% (2021)
- Verdict: **GROSS_PASS** — N/A

### cad_aud_jpy (CAD/JPY vs AUD/JPY)
- Period: 2016-06-15 → 2026-05-29 (2591 bars)
- Trades: 38
- Gross PF: 0.601
- Net PF (after $120/trade two-leg costs): 0.497
- Win rate: 31.6%
- Total gross P&L: $-11,381.12
- Total net P&L: $-15,941.12
- Avg P&L/trade: $-299.50
- IS/OOS split at 2023-05-31 (70/30 chronological)
  - IS: 28 trades, gross PF 0.666, net PF 0.562
  - OOS: 10 trades, gross PF 0.348, net PF 0.250
- Max year concentration: 28.2% (2020)
- Verdict: **DISCARD** — gross PF 0.601 <= 1.05

## Next steps
At least one spread passed gross-first. Next: add two-leg costs, chronological IS/OOS, half-life stability.

## Accounting notes
- P&L from actual leg price moves with beta-sized leg B notional at entry.
- No spread, slippage, or commission in this gross run.
- Round-trip two-leg costs (~10 pips majors) will be applied only if gross passes.