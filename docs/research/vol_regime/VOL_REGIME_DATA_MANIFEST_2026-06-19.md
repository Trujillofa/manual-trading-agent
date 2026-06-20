# Vol-Regime Data Manifest — 2026-06-19

## Verdict: PASS

## Command
```bash
python -m research.new_edge.vol_regime.data.verify_vol_regime_data --start 2016-01-01 --end 2026-06-01 --output docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md
```

## Window requested: 2016-01-01 → 2026-06-01

## Universe (fixed)

- EUR/USD
- GBP/USD
- USD/JPY
- AUD/USD
- USD/CAD
- USD/CHF
- NZD/USD

## Minimum H1 bars required: 299

## Per-pair verification

### EUR/USD
- Bars in window: 88776
- Range: 2016-01-01 00:00:00+00:00 → 2026-05-31 23:00:00+00:00
- OK: True

### GBP/USD
- Bars in window: 87840
- Range: 2016-01-02 00:00:00+00:00 → 2026-05-31 23:00:00+00:00
- OK: True

### USD/JPY
- Bars in window: 91056
- Range: 2016-01-01 00:00:00+00:00 → 2026-05-31 23:00:00+00:00
- OK: True

### AUD/USD
- Bars in window: 90768
- Range: 2016-01-01 00:00:00+00:00 → 2026-05-31 23:00:00+00:00
- OK: True

### USD/CAD
- Bars in window: 90768
- Range: 2016-01-01 00:00:00+00:00 → 2026-05-31 23:00:00+00:00
- OK: True

### USD/CHF
- Bars in window: 91032
- Range: 2016-01-01 00:00:00+00:00 → 2026-05-31 23:00:00+00:00
- OK: True

### NZD/USD
- Bars in window: 91296
- Range: 2016-01-01 00:00:00+00:00 → 2026-05-31 23:00:00+00:00
- OK: True

## Data source
- Dukascopy M1 BID candles resampled to H1 (`src.data.dukascopy_fetcher`)
- Per-pair consolidated H1 parquet cache under `research/new_edge/vol_regime/data/cache/`

## Cost model (documented, not optimized)
- Gross run: zero friction
- Net stage (if gross passes): 6.0 pips round-trip (2.0 spread + 1.0 slippage per side)

## Next step
If PASS: run range_compression_breakout_test (gross-first falsifier).
If BLOCKED: fix data gaps before any backtest.
