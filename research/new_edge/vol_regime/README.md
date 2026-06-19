# Volatility Regime / Range Compression Breakout

Status: **PLANNED**.

Contract: `docs/research/vol_regime/VOL_REGIME_CONTRACT_2026-06-19.md`

This lane tests whether H1 FX range compression followed by breakout has a gross edge before
costs. It is a volatility-regime thesis, not a retune of closed FX directional TA.

## Fixed prototype

- Universe: EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, NZD/USD.
- Compression: 20-bar H1 Donchian range at or below the rolling 252-bar 10th percentile.
- Persistence: 3 consecutive compressed bars before arming.
- Entry: first breakout close after arming, 07:00-17:00 UTC only.
- Exit: 24 H1 bars later at close.
- Gross first; add 6-pip round-trip cost and 70/30 IS/OOS only if gross passes.

## Required commands

```bash
python -m research.new_edge.vol_regime.data.verify_vol_regime_data \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md

python -m research.new_edge.vol_regime.range_compression_breakout_test \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md
```

## Stop rule

If pooled gross PF is near 1.0, trade count is below 30, or passing requires parameter tuning,
mark the lane `DISCARD` in the results and ledger. Do not reopen closed TA/event/carry/stat-arb
lanes from this work.

