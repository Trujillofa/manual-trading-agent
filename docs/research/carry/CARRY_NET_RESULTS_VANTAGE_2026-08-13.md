# Carry Net Falsifier Results (price P&L + IS/OOS)

## Verdict
DISCARD_REAL_DATA

## Exact command run
```bash
python -m research.new_edge.carry.net_carry_test \
  --rates research/new_edge/carry/data/verified_swap_rates_VANTAGE_2026-08-13.json \
  --economics auto \
  --start 2016-01-01 --end 2026-08-01 --is-end 2021-12-31 \
  --spread-pips 3.0 --slippage-pips 1.0 \
  --output docs/research/carry/CARRY_NET_RESULTS_VANTAGE_2026-08-13.md
```

## Branch
cursor/research-lanes-2026-08

## Pre-committed gates
- OOS net PnL > 0
- OOS daily PF >= 1.2
- Max single-leg |PnL| share <= 0.6
- Stress DD <= 15% of capital (covid_2020, vol_2018, hike_2022)

Gate results: `{"oos_pnl_pos": true, "oos_pf": false, "concentration": true, "stress_dd": true}`

## Strategy (frozen from pip-correct gross)
- Economics: **mt5** (POINTS×tick_value; price PnL via Δprice/point×tick_value)
- Long: ['AUD/JPY', 'AUD/USD']
- Short: ['NZD/JPY', 'NZD/USD', 'USD/ZAR']
- Sizing: static full-sample ann-vol; target 10% port vol; capital $100,000
- Costs: entry once = (3.0+1.0) pips × pair pip_$ = $25.34
- No RSI/Donchian/TSMOM filters; no leg retune after gross
- IS: 2016-01-01 → 2021-12-31 (1563 days)
- OOS: after 2021-12-31 → 2026-08-01 (1190 days)
- Simulated bars: 2016-01-01 → 2026-07-31 (2753 days)

## Lots
- AUD/JPY: +0.178 lots (ann_vol=0.112, pip_usd=6.2662)
- AUD/USD: +0.202 lots (ann_vol=0.099, pip_usd=10.0000)
- NZD/JPY: -0.187 lots (ann_vol=0.107, pip_usd=6.2662)
- NZD/USD: -0.197 lots (ann_vol=0.101, pip_usd=10.0000)
- USD/ZAR: -0.099 lots (ann_vol=0.202, pip_usd=0.6172)

## Full-sample net metrics
- Net PnL $: $2,574.06 (swap $413.85 + price $2,185.55 − drag $25.34)
- Daily PF: 1.018
- Sharpe: 0.095
- Max DD (equity): 6.18%

## IS metrics
- Net PnL $: $-92.04 (swap $234.90 / price $-301.61)
- Daily PF: 0.999
- Sharpe: -0.007
- Max DD: 6.18%

## OOS metrics (binding)
- Net PnL $: $2,666.11 (swap $178.94 / price $2,487.16)
- Daily PF: 1.043 (gate >= 1.2)
- Sharpe: 0.195
- Max DD: 3.81%

## Concentration (|leg total PnL| share)
- NZD/JPY: 35.8% (leg_pnl=$-3,910.31)
- AUD/JPY: 35.2% (leg_pnl=$3,839.50)
- NZD/USD: 26.7% (leg_pnl=$2,918.92)
- AUD/USD: 1.9% (leg_pnl=$-209.43)
- USD/ZAR: 0.4% (leg_pnl=$-39.28)
- Max: NZD/JPY @ 35.8% (gate <= 60%)

## Stress windows
- covid_2020: days=43, dd=3.09%, pnl=$-1,875.26, ok=True
- vol_2018: days=65, dd=0.99%, pnl=$-457.79, ok=True
- hike_2022: days=64, dd=0.37%, pnl=$1,249.12, ok=True

## Failure reason / next step
Failed gates: oos_pf

DISCARD_REAL_DATA: close or redesign premise; do not retune legs/costs to rescue OOS.
