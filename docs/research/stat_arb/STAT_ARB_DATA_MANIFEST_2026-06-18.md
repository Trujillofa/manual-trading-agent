# Stat-Arb Data Manifest — 2026-06-18

## Verdict: PASS

## Command
```bash
python -m research.new_edge.stat_arb.data.verify_stat_arb_data --start 2016-01-01 --end 2026-06-01 --output docs/research/stat_arb/STAT_ARB_DATA_MANIFEST_2026-06-18.md
```

## Window requested: 2016-01-01 → 2026-06-01

## Per-spread verification

### eur_gbp (EUR/USD vs GBP/USD)
- Leg A: {'ok': True, 'bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29'}
- Leg B: {'ok': True, 'bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29'}
- Aligned bars: 2708
- Aligned range: 2016-01-01 → 2026-05-29
- OK: True
- Two-leg round-trip cost estimate: 9.0 pips (spread 1.5+2.0 per side)

### aud_nzd (AUD/USD vs NZD/USD)
- Leg A: {'ok': True, 'bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29'}
- Leg B: {'ok': True, 'bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29'}
- Aligned bars: 2708
- Aligned range: 2016-01-01 → 2026-05-29
- OK: True
- Two-leg round-trip cost estimate: 11.0 pips (spread 2.0+2.5 per side)

### cad_aud_jpy (CAD/JPY vs AUD/JPY)
- Leg A: {'ok': True, 'bars': 2709, 'start': '2016-01-01', 'end': '2026-05-29'}
- Leg B: {'ok': True, 'bars': 2710, 'start': '2016-01-01', 'end': '2026-05-29'}
- Aligned bars: 2709
- Aligned range: 2016-01-01 → 2026-05-29
- OK: True
- Two-leg round-trip cost estimate: 12.0 pips (spread 2.5+2.5 per side)

## Data source
- yfinance daily closes (`PAIR=X` tickers)
- Strict inner-join alignment (common trading days only)

## Next step
If PASS: run gross_stat_arb_test (gross-first falsifier, zero friction).
If BLOCKED: fix data gaps before any backtest.
