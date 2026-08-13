# Carry Data Manifest — verifier run

## Rates file
`research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json`

## OHLC Coverage (yfinance daily --quick)
Pairs tested: ['AUD/USD', 'NZD/USD', 'AUD/JPY', 'NZD/JPY', 'USD/ZAR']

- AUD/USD: {'ticker': 'AUDUSD=X', 'd1_bars': 2753, 'start': '2016-01-01', 'end': '2026-07-31', 'ok': True}
- NZD/USD: {'ticker': 'NZDUSD=X', 'd1_bars': 2753, 'start': '2016-01-01', 'end': '2026-07-31', 'ok': True}
- AUD/JPY: {'ticker': 'AUDJPY=X', 'd1_bars': 2755, 'start': '2016-01-01', 'end': '2026-07-31', 'ok': True}
- NZD/JPY: {'ticker': 'NZDJPY=X', 'd1_bars': 2753, 'start': '2016-01-01', 'end': '2026-07-31', 'ok': True}
- USD/ZAR: {'ticker': 'USDZAR=X', 'd1_bars': 2753, 'start': '2016-01-01', 'end': '2026-07-31', 'ok': True}

## Swap / Financing Units
- Broker: Vantage International MT5 / live login 27496181 / VantageMarkets-Live 5
- Source date: 2026-08-13
- Retrieved: Mt5ArchBridge symbols.json SYMBOL_SWAP_* via mt5-arch (2026-08-13T21:51:47Z)
- Rollover rules: 3x swap on weekday(s) = Wed (SYMBOL_SWAP_ROLLOVER3DAYS)
- Units note: pips per day per standard lot when swap_mode=POINTS (positive = receive when long); otherwise raw MT5 swap_long/short
- Pairs present / nonzero: 5 / 5
- Rates:
{
  "AUD/USD": {
    "long": 0.037,
    "short": -0.186
  },
  "NZD/USD": {
    "long": -0.321,
    "short": 0.133
  },
  "AUD/JPY": {
    "long": 0.244,
    "short": -1.218
  },
  "NZD/JPY": {
    "long": 0.057999999999999996,
    "short": -0.561
  },
  "USD/ZAR": {
    "long": -22.846,
    "short": 2.532
  }
}

## Verification Result
- Daily OHLC: Verified via lightweight yfinance daily or dukascopy for all requested pairs.
- Swap data: VERIFIED.
- Rollover: documented in rates file.

**Verdict for data verifier: DATA_PASS**

Data for OHLC is verified available and usable with existing fetchers.
Next: run gross carry falsifier with the same --rates file.

## Recommended next command
```bash
.venv/bin/python -m research.new_edge.carry.gross_carry_test \
  --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \
  --start 2016-01-01 --end 2026-08-01 \
  --output docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_2026-08-13.md
```

See CARRY_CONTRACT_2026-06-11.md for gates and full falsification test.
