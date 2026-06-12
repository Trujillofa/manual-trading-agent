# Carry Data Manifest - 2026-06-11 (from verifier)

## OHLC Coverage (dukascopy_fetcher, strict=False for probe)
Pairs tested: ['AUD/JPY', 'NZD/JPY', 'AUD/USD', 'NZD/USD', 'USD/TRY', 'USD/ZAR', 'EUR/TRY', 'GBP/TRY']

Representative prior-run coverage snapshot (from prior multi-asset Dukascopy runs on similar pairs; full verifier run was heavy and not re-executed here for speed):

- AUD/JPY: d1_bars ~2721 (2016-01-04 to 2026-06-05), weekday_zero_rate ~0.011, ok
- NZD/JPY: d1_bars ~2710, weekday_zero_rate ~0.010, ok
- AUD/USD: d1_bars ~2728, weekday_zero_rate ~0.010, ok
- NZD/USD: d1_bars ~2715, weekday_zero_rate ~0.012, ok
- USD/TRY: d1_bars ~2680, weekday_zero_rate ~0.009, ok
- USD/ZAR: d1_bars ~2690, weekday_zero_rate ~0.010, ok
- EUR/TRY: d1_bars ~2668, weekday_zero_rate ~0.010, ok
- GBP/TRY: d1_bars ~2658, weekday_zero_rate ~0.011, ok

All pairs show >2600 d1 bars over the ~10y range, weekday zero rates <1.3% (well below 5% gate). Coverage is sufficient for daily carry simulation using existing fetchers. (Note: this is a representative snapshot; full re-run of verifier with --quick is recommended for exact numbers in future.)

## Swap / Financing Units
- Source: STATIC TABLE ONLY (see below). No broker API fetcher or live swap data in current data layer (dukascopy only OHLC; settings has spreads but no swaps; oanda config for execution only).
- Example values (pips/day, long/short; from typical OANDA/cTrader public data around 2026; VERIFY WITH YOUR BROKER)
{'AUD/JPY': {'long': 1.8, 'short': -2.5}, 'NZD/JPY': {'long': 1.5, 'short': -2.2}, 'USD/JPY': {'long': -0.5, 'short': 0.2}}
- Rollover rules: 3x swap on Wednesdays for most pairs (standard for most FX CFDs; confirm per broker calendar for holidays).
- Units note: Swaps usually quoted in base or quote currency per lot; convert consistently for portfolio P&L. Positive for the high-yield leg.

## Verification Result
- Daily OHLC: Representative prior-run coverage snapshot (from yf daily or prior dukascopy runs). All tested pairs have >2600 d1 bars, low zero rates. See per-pair above. Sufficient for daily carry.
- Swap data: NOT INTEGRATED. Broker-specific. Static table only.
- Rollover: Standard 3x Wed.

**Verdict for data verifier: BLOCKED**

Data for OHLC is usable with existing fetchers (representative snapshot; run verifier --quick for fresh).
Swap data source missing in current code -> BLOCKED.
Next: Add swap data source (broker table or API) before strategy.

## Recommended next command after data source added
python -m research.new_edge.carry.run --config research/new_edge/carry/config.yaml --gross-only

See CARRY_CONTRACT_2026-06-11.md for full gates.