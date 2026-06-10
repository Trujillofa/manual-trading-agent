# Carry / Funding Data Manifest — 2026-06

This is the first deliverable for the new-edge profitability program. It defines what data must
exist before writing carry strategy logic. The goal is to test a financing edge, not another price
pattern.

## Research Question

Can an FX portfolio earn positive expected return from broker swap / funding after spread,
slippage, rollover conventions, and carry-crash drawdowns?

## Why This Lane Is Valid

This satisfies the re-entry criteria from the closed FX directional-TA lane because the primary
signal is financing, not OHLC direction:

- The signal source is broker long/short swap, interest-rate differential, or financing yield.
- OHLC data is used only for volatility, drawdown, and exit/risk simulation.
- RSI, Donchian, breakout confirmation, TSMOM lookbacks, and per-pair TA overrides are out of scope.

## Minimum Data Sources

| Dataset | Required fields | Preferred source | Fallback | Required before code? |
|---|---|---|---|---|
| Broker swap rates | pair, long_swap, short_swap, units, timestamp, broker/account type | live broker export or API | manually captured broker table | yes |
| Instrument metadata | pair, base, quote, pip size, pip value, contract size, lot constraints | broker symbols endpoint/config | checked-in YAML manifest | yes |
| Daily OHLC | pair, date, open, high, low, close | existing Dukascopy/yfinance adapter | broker candles | yes |
| Spread model | pair, median spread, p90 spread, rollover spread multiplier | live audit or broker history | conservative fixed assumptions | yes |
| Commission/slippage | pair or account-level commission, assumed slippage | broker account terms | current harness assumptions | yes |
| Policy rates | currency, central-bank rate, effective date | central-bank/FRED-style source | manual table | no, sanity check only |

## Broker Swap Normalization

Before any backtest, normalize swap into a daily account-currency value per one standard lot:

```text
daily_swap_value = broker_swap_value converted to account currency
daily_swap_bps = daily_swap_value / notional_value * 10000
```

The manifest must explicitly record the broker's units:

- points,
- pips,
- account currency per lot,
- base currency per lot,
- quote currency per lot,
- percent annualized.

If units cannot be verified, the lane is blocked. Guessing swap units invalidates the backtest.

## Rollover Rules

The cost model must document:

- normal rollover time in UTC,
- triple-swap weekday,
- whether Wednesday or Friday triple swap applies per instrument,
- whether holidays shift triple swap,
- whether long and short swaps are both negative for some pairs,
- whether swap is charged on open positions only at rollover or pro-rated.

The backtest must apply triple swap. A carry strategy that ignores triple-swap timing is not valid.

## Initial Universe

Start with the current liquid FX watchlist, but only keep pairs with verified broker swap data:

| Pair group | Examples | Initial use |
|---|---|---|
| Majors | EUR/USD, GBP/USD, USD/JPY, USD/CHF, USD/CAD, AUD/USD, NZD/USD | required |
| Liquid minors | AUD/JPY, CAD/JPY, EUR/JPY, GBP/JPY, AUD/NZD, EUR/AUD | optional if swap data is reliable |
| Exclusions | pairs with wide spreads or unreliable candles | exclude until data quality improves |

The first pass should prefer fewer pairs with trustworthy funding data over broad coverage with
unknown swap units.

## Cost Model

Every simulated trade must include:

1. Entry spread.
2. Exit spread.
3. Commission.
4. Slippage.
5. Daily swap or funding credit/debit.
6. Triple-swap adjustment.
7. Rollover spread widening if entering/exiting near rollover.

Default assumptions are allowed only as a clearly labeled conservative baseline. If the strategy
passes only under optimistic costs, the verdict is DISCARD.

## Prototype Rules To Test First

Keep the first test deliberately simple:

1. Rank pairs by verified expected daily swap after converting to account currency.
2. Long only when long swap is materially positive; short only when short swap is materially positive.
3. Skip pairs where both sides are negative after costs.
4. Vol-target position size using daily realized volatility.
5. Rebalance weekly, not intraday.
6. Risk-off filter: reduce or close exposure when portfolio realized volatility or drawdown breaches
   a pre-written threshold.

No technical-entry timing is allowed in the first test. If pure carry does not show a gross edge,
adding RSI or breakout filters is not a valid rescue.

## Required Diagnostics

The first result artifact must include:

- gross carry return before price movement,
- price return contribution,
- swap/funding contribution,
- spread/commission/slippage drag,
- net return,
- IS/OOS split,
- pair contribution table,
- monthly contribution table,
- max drawdown,
- Sharpe or Sortino,
- MAR,
- turnover,
- exposure by currency,
- carry-crash stress test,
- KEEP / DISCARD verdict.

## Pass Gate

The lane may continue only if all are true:

- Verified swap data covers the whole backtest or a clearly bounded sample.
- Gross funding contribution is positive before price movement.
- Net OOS result is positive after spread, commission, slippage, and rollover.
- Performance is not concentrated in one pair, one currency, or one month.
- Drawdown is tolerable under a carry-crash stress.
- The strategy can run in paper-shadow without changing Branch B scanner behavior.

## Stop Gate

Write `docs/research/carry/CARRY_RESULTS_YYYY-MM-DD.md` with DISCARD if any of these occur:

- Broker swap data cannot be verified.
- Swap units are ambiguous.
- Most pairs have negative carry on both sides after costs.
- Net result depends mainly on price drift, not funding.
- Gross funding edge is near zero.
- Net OOS fails after realistic costs.
- Drawdown or currency concentration dominates the return.

## First Implementation Deliverables

Only after this manifest is satisfied:

1. Add a static broker-swap sample under `research/new_edge/carry/data/` or a documented fetcher.
2. Add a normalizer that converts swap into account-currency daily value.
3. Add a portfolio simulator that separates price P&L from swap P&L.
4. Emit the required result artifact with a KEEP / DISCARD verdict.

Do not connect this to Telegram, live scanner, or paper trading until the research verdict passes.
