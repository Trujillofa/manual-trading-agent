# Stat-Arb Lane Results — 2026-06-18

## Verdict: DISCARD (failed net/OOS promotion gates)

Gross-first falsification passed on two of three candidate spreads, but neither surviving spread
clears the pre-written net/OOS promotion bar (OOS net PF ≥ 1.20, OOS trades ≥ 30).

This is not a KEEP candidate. The lane is closed at the daily pairs-trade prototype stage unless
a genuinely new premise emerges (e.g. intraday data with verified two-leg execution costs on a
different broker feed).

## Evidence summary

| Spread | Gross PF | Net PF | OOS gross PF | OOS net PF | OOS trades | Spread verdict |
|---|---:|---:|---:|---:|---:|---|
| EUR/USD vs GBP/USD | 1.349 | 1.159 | 0.271 | 0.191 | 14 | GROSS_PASS → DISCARD at OOS |
| AUD/USD vs NZD/USD | 1.645 | 1.384 | 1.409 | 1.128 | 13 | GROSS_PASS → DISCARD at OOS |
| CAD/JPY vs AUD/JPY | 0.601 | 0.497 | 0.348 | 0.250 | 10 | DISCARD at gross |

## Why DISCARD (not KEEP)

1. **EUR/GBP spread:** Full-sample gross edge exists, but OOS window (2023-05-31 → 2026-05-29)
   collapses (OOS net PF 0.191, 14 trades). Regime instability — edge does not survive chronological
   holdout.

2. **AUD/NZD spread:** Strongest full-sample performer (gross PF 1.645, net PF 1.384), but OOS net
   PF 1.128 < 1.20 gate and OOS trades 13 < 30. Too sparse and too weak after costs on holdout.

3. **CAD/AUD JPY:** Negative gross edge; correctly discarded at falsifier stage.

4. **Concentration:** No single-year profit dominance (> 50%) on passing spreads — concentration
   gate passed, but insufficient to override OOS failure.

## Commands run

```bash
python -m research.new_edge.stat_arb.data.verify_stat_arb_data \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/stat_arb/STAT_ARB_DATA_MANIFEST_2026-06-18.md

python -m research.new_edge.stat_arb.gross_stat_arb_test \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/stat_arb/STAT_ARB_GROSS_RESULTS_2026-06-18.md
```

## Data and cost assumptions

- Daily closes via yfinance (`PAIR=X` tickers), strict inner-join alignment.
- Two-leg round-trip costs: EUR/GBP ~90 USD/trade, AUD/NZD ~110 USD/trade (spread + slippage).
- Fixed parameters: 60-day lookback, z-entry ±2.0, z-exit 0, 20-bar time stop.
- No optimization performed.

## Next action

Move to the next open lane in `PROFITABILITY_PLAN_2026-06.md`: **Event / Calendar** (Phase 4),
subject to historical calendar data availability proof.

Do not tune z-thresholds, lookbacks, or spread selection on these results — that would violate
the closed-lane discipline for a DISCARD verdict.

## References

- Contract: `docs/research/stat_arb/STAT_ARB_CONTRACT_2026-06-18.md`
- Data manifest: `docs/research/stat_arb/STAT_ARB_DATA_MANIFEST_2026-06-18.md`
- Gross diagnostics: `docs/research/stat_arb/STAT_ARB_GROSS_RESULTS_2026-06-18.md`
- Ledger: `research/new_edge/research_ledger.jsonl`