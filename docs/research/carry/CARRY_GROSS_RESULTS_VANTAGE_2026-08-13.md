# Carry Gross Falsifier Results

## Verdict
GROSS_PASS_REAL_DATA

## Exact command run
```bash
python -m research.new_edge.carry.gross_carry_test \
  --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \
  --start 2016-01-01 --end 2026-08-01 \
  --output docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_2026-08-13.md
```

## Branch
cursor/research-lanes-2026-08

## Data sources and assumptions
- OHLC daily closes + vol: yfinance (aligned common trading days)
- Swap file: `research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json`
  - broker=`Vantage International MT5 / live login 27496181 / VantageMarkets-Live 5`
  - source_date=`2026-08-13`
  - retrieved/source=`Mt5ArchBridge symbols.json SYMBOL_SWAP_* via mt5-arch (2026-08-13T21:51:47Z)`
  - is_real_data=True
- Rollover: ×3 on Wednesdays, ×1 otherwise (no holiday calendar)
- Sizing: static full-sample ann-vol risk split across 5 legs; target portfolio ann vol 10%
- Legs: top 2 by long_rate LONG, bottom 3 SHORT (universe n=5)
- Costs: entry drag only = 3.0 pips × pip_value per lot at t=0
- Price P&L: ignored (gross-first falsifier)
- Capital $100,000; pip_value $10.0; lot_notional $100,000
- Simulated: 2016-01-01 → 2026-07-31 (2753 trading days)

## Legs and sizing
Long: ['AUD/JPY', 'NZD/JPY']
Short: ['AUD/USD', 'NZD/USD', 'USD/ZAR']
- AUD/JPY: 0.178 lots (ann_vol=0.112)
- NZD/JPY: 0.187 lots (ann_vol=0.107)
- AUD/USD: -0.202 lots (ann_vol=0.099)
- NZD/USD: -0.197 lots (ann_vol=0.101)
- USD/ZAR: -0.099 lots (ann_vol=0.202)

## Gross carry metrics (leg-level accounting)
- Trading days: 2753
- Positive carry $: $12,738.12
- Negative carry / funding $: $1,444.23
- Gross (pos − neg) $: $11,293.89
- Initial entry drag $: $25.87
- Net after drag $: $11,268.03
- Carry PF pos/(neg+drag): 8.665
- Max DD on cum net-carry path: 0.00%

### Per-pair accumulated carry $
- USD/ZAR (SHORT): $9,637.69 (rate=+2.5320)
- AUD/JPY (LONG): $1,671.64 (rate=+0.2440)
- NZD/USD (SHORT): $1,011.77 (rate=+0.1330)
- NZD/JPY (LONG): $417.03 (rate=+0.0580)
- AUD/USD (SHORT): $-1,444.23 (rate=-0.1860)

## Failure reason / next step
N/A

Real-broker metadata present (`is_real_data=True`).
If GROSS_PASS_REAL_DATA: next is richer costs, price P&L risk, chronological IS/OOS, carry-crash stress — not LIVE promotion.
If DISCARD_REAL_DATA: close or redesign premise; do not retune to rescue.
