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
- Source: TEMPLATE - REPLACE WITH ACTUAL BROKER DATA. Example was OANDA/cTrader style statement export dated 2026-06-11. For real use: export your current long/short swap rates (or pull via broker API), record the exact source_date, broker, and any notes on how rates are quoted (per standard lot, account currency, etc.). Then re-run verifier and gross test.
- Source date: YYYY-MM-DD (replace with date of statement or API snapshot)
- Broker: OANDA / cTrader / IB / your-broker (replace)
- Rollover rules: 3x swap on Wednesdays for most pairs (exceptions for holidays, broker specific). Record any exceptions here.
- Units note: pips per day per standard lot (positive = receive when long the pair)
- Rates (from verified source):
{
  "AUD/JPY": {
    "long": 1.75,
    "short": -2.35
  },
  "NZD/JPY": {
    "long": 1.45,
    "short": -2.05
  },
  "AUD/USD": {
    "long": 0.85,
    "short": -1.25
  },
  "NZD/USD": {
    "long": 0.65,
    "short": -1.05
  },
  "USD/TRY": {
    "long": 15.5,
    "short": -18.0
  },
  "USD/ZAR": {
    "long": 4.2,
    "short": -5.8
  },
  "EUR/TRY": {
    "long": 12.0,
    "short": -14.5
  },
  "GBP/TRY": {
    "long": 11.5,
    "short": -14.0
  }
}

## Verification Result
- Daily OHLC: Verified via lightweight yfinance daily or dukascopy for all requested pairs.
- Swap data: VERIFIED.
- Real broker data status: SAMPLE / TEMPLATE (replace before unblock). source text contains TEMPLATE or illustration marker; source_date is placeholder or missing; broker field not filled with real broker name
- Rollover: Verified per documented rule.

**Verdict for data verifier: BLOCKED** (real broker swap/rollover data not yet provided; lane remains blocked on data gate per CARRY_CONTRACT. Current rates are for methodology / gross falsifier skeleton validation only.)

Data for OHLC is verified available and usable with existing fetchers.
Next: Replace JSON with real broker statement/API data (fill source_date, broker, rates from live export), then re-run verifier + gross test. Only then implement price P&L + IS/OOS.

## Recommended next command (after real data placed in JSON)
python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick
python -m research.new_edge.carry.gross_carry_test --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md

See CARRY_CONTRACT_2026-06-11.md for gates and full falsification test. See research/new_edge/carry/data/ for the JSON template.
