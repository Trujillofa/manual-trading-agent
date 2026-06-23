# Volatility Regime / Range Compression Breakout

Status: **DISCARD** (closed 2026-06-20).

Contract: `docs/research/vol_regime/VOL_REGIME_CONTRACT_2026-06-19.md`
Results: `docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md`
Closure record: `docs/research/CLOSED_RESEARCH_LANES.md` (section 6)

This lane tested whether H1 FX range compression followed by breakout has a gross edge before
costs. It is a volatility-regime thesis, not a retune of closed FX directional TA. The lane is
closed; the harness remains as an archive only.

## Final verdict (2016-01-01 → 2026-06-01)

| Metric | Value |
|---|---:|
| Pooled trades | 3778 |
| Gross PF | 1.114 |
| Pooled costed net PF (6-pip RT) | 0.802 |
| OOS net PF | 0.782 |
| Gross stage | GROSS_PASS |
| Final status | DISCARD |

**Reason:** Gross edge too small to survive 6-pip round-trip costs and OOS net gate.

**Do not retune:** compression percentile (10th), persistence (3 bars), entry window
(07:00-17:00 UTC), 24-bar hold, pairs, or H1 timeframe.

## Fixed prototype (archived)

- Universe: EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, NZD/USD.
- Compression: 20-bar H1 Donchian range at or below the rolling 252-bar 10th percentile.
- Persistence: 3 consecutive compressed bars before arming.
- Entry: first breakout close after arming, 07:00-17:00 UTC only.
- Exit: 24 H1 bars later at close.
- Gross first; add 6-pip round-trip cost and 70/30 IS/OOS only if gross passes.

## Reproduce (archive only — lane closed)

```bash
python -m research.new_edge.vol_regime.data.verify_vol_regime_data \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/vol_regime/VOL_REGIME_DATA_MANIFEST_2026-06-19.md

python -m research.new_edge.vol_regime.range_compression_breakout_test \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/vol_regime/VOL_REGIME_RESULTS_2026-06-19.md
```

## Stop rule (satisfied — lane closed)

Pooled gross PF passed (>1.10) but net/OOS failed. Lane marked `DISCARD` in results and ledger.
Do not reopen closed TA, event, carry, or stat-arb lanes from this work. Microstructure /
execution-quality research is deferred unless tied to a future gross-positive edge.