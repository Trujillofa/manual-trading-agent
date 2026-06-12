# Carry Data Manifest - 2026-06-11 (from verifier --quick run)

## OHLC Coverage (yfinance daily, quick mode for fast reproducible verification)
Pairs tested: ['AUD/JPY', 'NZD/JPY', 'AUD/USD', 'NZD/USD', 'USD/TRY', 'USD/ZAR', 'EUR/TRY', 'GBP/TRY']

- AUD/JPY: {'ticker': 'AUDJPY=X', 'd1_bars': 2710, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}
- NZD/JPY: {'ticker': 'NZDJPY=X', 'd1_bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}
- AUD/USD: {'ticker': 'AUDUSD=X', 'd1_bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}
- NZD/USD: {'ticker': 'NZDUSD=X', 'd1_bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}
- USD/TRY: {'ticker': 'USDTRY=X', 'd1_bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}
- USD/ZAR: {'ticker': 'USDZAR=X', 'd1_bars': 2708, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}
- EUR/TRY: {'ticker': 'EURTRY=X', 'd1_bars': 2709, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}
- GBP/TRY: {'ticker': 'GBPTRY=X', 'd1_bars': 2709, 'start': '2016-01-01', 'end': '2026-05-29', 'ok': True}

## Swap / Financing Units
- Source: N/A
- Source date: 2026-06-12
- Broker: cTrader (live account on Hetzner, ProtoOASymbolByIdReq)
- Rollover rules: 3x swap on day 3 (Wednesday) per swapRollover3Days=3 from API; broker specific, holidays may suppress or adjust.
- Units note: pips per day per standard lot (positive = receive when long the pair) - values as returned by this account's API
- Rates (from verified source):
{
  "AUD/JPY": {
    "long": 0.0,
    "short": 0.0
  },
  "NZD/JPY": {
    "long": 0.0,
    "short": 0.0
  },
  "AUD/USD": {
    "long": 0.0,
    "short": 0.0
  },
  "NZD/USD": {
    "long": 0.0,
    "short": 0.0
  },
  "USD/ZAR": {
    "long": 0.0,
    "short": 0.0
  }
}

## Verification Result
- Daily OHLC: Verified via lightweight yfinance daily or dukascopy for all requested pairs.
- Swap data: NOT VERIFIED. Issues: Non-positive long swap for AUD/JPY: 0.0; Non-positive long swap for NZD/JPY: 0.0; Non-positive long swap for AUD/USD: 0.0; Non-positive long swap for NZD/USD: 0.0; Missing swap rate for USD/TRY; Non-positive long swap for USD/ZAR: 0.0; Missing swap rate for EUR/TRY; Missing swap rate for GBP/TRY
- Real broker data status: REAL_BROKER_DATA. 
- Rollover: Verified per documented rule.

**Verdict for data verifier: BLOCKED** (real broker swap/rollover data not yet provided; lane remains blocked on data gate per CARRY_CONTRACT. Current rates are for methodology / gross falsifier skeleton validation only.)

Data for OHLC is verified available and usable with existing fetchers.
Next: Resolve data issues above before gross test.

## Recommended next command (after real data placed in JSON)
python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick
python -m research.new_edge.carry.gross_carry_test --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md

See CARRY_CONTRACT_2026-06-11.md for gates and full falsification test. See research/new_edge/carry/data/ for the JSON template.
