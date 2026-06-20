# Vol-Regime Range Compression Breakout Results — 2026-06-19

## Lane verdict: **DISCARD** (closed 2026-06-20)

Reason: Gross edge too small to survive 6-pip round-trip costs and OOS net gate (OOS net PF 0.782 < 1.20).

- Gross stage: **GROSS_PASS**
- Final lane status: **DISCARD** — do not retune compression percentile, persistence, entry window, hold, pairs, or timeframe.

## Command
```bash
python -m research.new_edge.vol_regime.range_compression_breakout_test --start 2016-01-01 --end 2026-06-01 --output docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md
```

## Window: 2016-01-01 → 2026-06-01
- Universe: EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, NZD/USD
- Pooled trades: 3778

## Parameters (fixed, no optimization)
- Donchian window: 20 H1 bars
- Compression threshold: 10th percentile of prior 252 H1 ranges
- Compression persistence: 3 consecutive bars
- Entry window: 07:00-17:00 UTC
- Time stop: 24 H1 bars
- Costs (gross run): **zero**
- Net cost model (if gross passes): 6.0 pips round-trip

## Pooled gross-first stats
- Trades: 3778
- Gross PF: 1.114
- Win rate: 51.3%
- Total gross pips: 7437.9
- Avg gross pips/trade: 1.97

## Pooled net stats (6.0 pip round-trip)
- Net PF: 0.802
- Total net pips: -15230.1

## Net + IS/OOS (after gross pass)
- Split time (70% entries): 2023-04-17T12:00:00+00:00
- IS: 2642 trades, gross PF 1.118, net PF 0.810
- OOS: 1136 trades, gross PF 1.104, net PF 0.782
- Net stage verdict: **DISCARD** — OOS net PF 0.782 < 1.2

- Max year concentration: 12.8% (2020)

## Per-pair breakdown

- AUD/USD: 541 trades, gross PF 0.975, avg -0.38 pips
- EUR/USD: 535 trades, gross PF 1.234, avg 3.70 pips
- GBP/USD: 534 trades, gross PF 1.049, avg 1.17 pips
- NZD/USD: 540 trades, gross PF 1.262, avg 3.33 pips
- USD/CAD: 544 trades, gross PF 1.189, avg 3.22 pips
- USD/CHF: 544 trades, gross PF 1.024, avg 0.36 pips
- USD/JPY: 540 trades, gross PF 1.114, avg 2.40 pips

## Sample trades (first 10)

- 2016-01-18 SELL USD/CAD | gross +30.5 pips
- 2016-01-18 SELL EUR/USD | gross -24.6 pips
- 2016-01-19 BUY USD/JPY | gross -113.0 pips
- 2016-01-19 BUY AUD/USD | gross -85.5 pips
- 2016-01-19 BUY NZD/USD | gross -133.3 pips
- 2016-01-19 SELL USD/CHF | gross +16.3 pips
- 2016-01-25 BUY USD/CAD | gross +121.0 pips
- 2016-01-25 BUY EUR/USD | gross +18.3 pips
- 2016-01-25 SELL USD/JPY | gross +30.7 pips
- 2016-01-25 SELL AUD/USD | gross +28.1 pips

## Accounting notes
- Entry: H1 close on first breakout bar after compression arms (07:00-17:00 UTC).
- Exit: H1 close after 24 bars.
- Dukascopy M1 resampled to H1; per-pair consolidated parquet cache.
- No parameter sweeps; single contract parameter set.
- Closed lanes (TA, TSMOM, carry, stat-arb, event drift) not reopened.

## Closure

Lane closed after PR #10 merge (`545fef0`). Recorded in `docs/research/CLOSED_RESEARCH_LANES.md`.
Do not retune compression percentile, persistence, entry window, 24-bar hold, universe, or H1 timeframe.
Microstructure / execution-quality research is deferred and must not be used to rescue this signal.