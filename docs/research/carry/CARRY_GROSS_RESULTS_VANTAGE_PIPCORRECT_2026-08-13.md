# Carry Gross Falsifier Results

## Verdict
GROSS_PASS_REAL_DATA

## Exact command run
```bash
python -m research.new_edge.carry.gross_carry_test \
  --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \
  --economics auto \
  --start 2016-01-01 --end 2026-08-01 \
  --output docs/research/carry/CARRY_GROSS_RESULTS_VANTAGE_PIPCORRECT_2026-08-13.md
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
- Economics mode: **mt5** (requested=auto) — MT5 POINTS×tick_value account-currency $/lot/day; pip_$ = tick_value×points_per_pip
- Ranking key: long $/day/lot (account currency), not raw pip rates
- Rollover: ×3 on Wednesdays, ×1 otherwise (no holiday calendar)
- Sizing: static full-sample ann-vol risk split across 5 legs; target portfolio ann vol 10%
- Legs: top 2 by long_$ LONG, bottom 3 SHORT (universe n=5)
- Costs: entry drag only = 3.0 pips × pair pip_$ at t=0
- Price P&L: ignored (gross-first falsifier)
- Capital $100,000; lot_notional $100,000
- Simulated: 2016-01-01 → 2026-07-31 (2753 trading days)

## Per-pair economics ($/day/lot and pip_$)
- AUD/JPY: long_usd/day=+1.5289, short_usd/day=-7.6322, pip_usd=6.2662
- AUD/USD: long_usd/day=+0.3700, short_usd/day=-1.8600, pip_usd=10.0000
- NZD/JPY: long_usd/day=+0.3634, short_usd/day=-3.5153, pip_usd=6.2662
- NZD/USD: long_usd/day=-3.2100, short_usd/day=+1.3300, pip_usd=10.0000
- USD/ZAR: long_usd/day=-14.1013, short_usd/day=+1.5628, pip_usd=0.6172

## Legs and sizing
Long: ['AUD/JPY', 'AUD/USD']
Short: ['NZD/JPY', 'NZD/USD', 'USD/ZAR']
- AUD/JPY: 0.178 lots (ann_vol=0.112)
- AUD/USD: 0.202 lots (ann_vol=0.099)
- NZD/JPY: -0.187 lots (ann_vol=0.107)
- NZD/USD: -0.197 lots (ann_vol=0.101)
- USD/ZAR: -0.099 lots (ann_vol=0.202)

## Gross carry metrics (leg-level accounting)
- Trading days: 2753
- Positive carry $: $2,941.41
- Negative carry / funding $: $2,527.56
- Gross (pos − neg) $: $413.85
- Initial entry drag $: $19.00
- Net after drag $: $394.85
- Carry PF pos/(neg+drag): 1.155
- Max DD on cum net-carry path: 0.00%

### Per-pair accumulated carry $
- AUD/JPY (LONG): $1,047.48 (usd/day/lot=+1.5289)
- NZD/USD (SHORT): $1,011.77 (usd/day/lot=+1.3300)
- USD/ZAR (SHORT): $594.87 (usd/day/lot=+1.5628)
- AUD/USD (LONG): $287.29 (usd/day/lot=+0.3700)
- NZD/JPY (SHORT): $-2,527.56 (usd/day/lot=-3.5153)

## Failure reason / next step
N/A

Real-broker metadata present (`is_real_data=True`).
Economics=mt5. Next: richer costs, price P&L risk, chronological IS/OOS, carry-crash stress — not LIVE promotion.
If DISCARD_REAL_DATA: close or redesign premise; do not retune to rescue.
