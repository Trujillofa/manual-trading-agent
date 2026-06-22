# Post-Release Event Drift Results — 2026-06-19

## Lane verdict: **GROSS_PASS**

Reason: N/A

## Command
```bash
python -m research.new_edge.events.post_release_drift_test --start 2016-01-01 --end 2025-04-07 --calendar research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv --output docs/research/events/EVENT_DRIFT_RESULTS_2026-06-19.md
```

## Window: 2016-01-01 → 2025-04-07
- Calendar: `research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv`
- Eligible events (surprise ≠ 0, filters applied): 1655
- Filled trades: 1647
- Skipped (missing OHLC): 8

## Parameters (fixed, no optimization)
- Entry delay: 30 minutes after `datetime_utc`
- Hold: 4 hours
- Signal: sign(Actual − Forecast) at/after release; Actual is post-release label only
- Costs (gross run): **zero**
- Net cost model (if gross passes): 14.0 pips round-trip

## Pooled gross-first stats
- Trades: 1647
- Gross PF: 1.200
- Win rate: 52.3%
- Total gross pips: 2700.5
- Avg gross pips/trade: 1.64

## Net + IS/OOS (after gross pass)
- Split time (70% events): 2023-06-01T13:00:00+00:00
- IS: 1152 trades, gross PF 1.053, net PF 0.256
- OOS: 495 trades, gross PF 1.675, net PF 0.375
- Net stage verdict: **DISCARD** — OOS net PF 0.375 < 1.2

- Max year concentration: 21.9% (2023)

## Per-family breakdown

- cpi: 566 trades, gross PF 1.218, avg 1.67 pips
- gdp: 264 trades, gross PF 1.143, avg 1.52 pips
- nfp: 111 trades, gross PF 1.191, avg 0.56 pips
- pmi: 686 trades, gross PF 1.207, avg 1.76 pips
- rate_decision: 20 trades, gross PF 1.382, avg 4.22 pips

## Sample trades (first 10 filled)

- 2016-01-04 GBP pmi → SELL GBP/USD | gross +40.8 pips
- 2016-01-04 USD pmi → BUY EUR/USD | gross -1.9 pips
- 2016-01-05 GBP pmi → BUY GBP/USD | gross +5.7 pips
- 2016-01-06 GBP pmi → SELL GBP/USD | gross +8.5 pips
- 2016-01-06 USD pmi → BUY EUR/USD | gross +38.9 pips
- 2016-01-07 USD nfp → SELL EUR/USD | gross +24.2 pips
- 2016-01-19 GBP cpi → BUY GBP/USD | gross -93.5 pips
- 2016-01-19 USD cpi → BUY EUR/USD | gross +11.0 pips
- 2016-01-19 USD cpi → BUY EUR/USD | gross +11.0 pips
- 2016-01-19 USD cpi → BUY EUR/USD | gross +11.0 pips

## Accounting notes
- Entry: M15 open at or after entry time (Dukascopy M1 resampled).
- Exit: M15 close at or before exit time.
- Production NewsChecker / live faireconomy parser not used.
- No parameter sweeps; single contract parameter set.

## Next step
Gross passed but net/OOS failed. Lane falsified after costs or OOS gates.