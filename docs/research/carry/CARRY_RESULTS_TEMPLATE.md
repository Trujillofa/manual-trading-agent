# Carry / Funding Results Template

Copy this file to `CARRY_RESULTS_YYYY-MM-DD.md` for each carry/funding run. Fill every section before
assigning a KEEP / DISCARD verdict.

## Verdict

**Verdict:** KEEP / DISCARD / BLOCKED

**Reason:** One paragraph explaining the decisive evidence.

## Run Metadata

| Field | Value |
|---|---|
| Run date | YYYY-MM-DD |
| Research branch / commit | |
| Data window | |
| IS window | |
| OOS window | |
| Account currency | |
| Broker / account type | |
| Swap source | |
| OHLC source | |
| Cost model version | |

## Data Quality Checklist

| Requirement | Status | Evidence / notes |
|---|---|---|
| Broker swap units verified | PASS / FAIL | |
| Long and short swap captured separately | PASS / FAIL | |
| Triple-swap weekday documented | PASS / FAIL | |
| Rollover time documented in UTC | PASS / FAIL | |
| Instrument contract size / pip value verified | PASS / FAIL | |
| Daily OHLC coverage sufficient | PASS / FAIL | |
| Spread assumptions documented | PASS / FAIL | |
| Commission/slippage assumptions documented | PASS / FAIL | |
| Missing data handling documented | PASS / FAIL | |

If any required data quality item fails, the verdict is BLOCKED or DISCARD.

## Strategy Definition

State the exact rules tested:

- universe:
- rebalance frequency:
- long eligibility:
- short eligibility:
- position sizing:
- volatility targeting:
- risk-off rule:
- max exposure:
- exclusions:

Confirm explicitly: no RSI, Donchian, breakout, TSMOM, or other directional OHLC TA filters were used
in this first carry test.

## Gross vs Net Summary

| Split | Gross funding PnL | Price PnL | Spread cost | Commission | Slippage | Net PnL | Net PF | Sharpe/Sortino | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| IS | | | | | | | | | |
| OOS | | | | | | | | | |
| Full | | | | | | | | | |

## Funding Contribution

| Split | Funding contribution | Price contribution | Cost drag | Net result | Notes |
|---|---:|---:|---:|---:|---|
| IS | | | | | |
| OOS | | | | | |
| Full | | | | | |

The lane should not continue if net profit is mostly unexplained price drift rather than verified
funding/carry.

## Pair Contribution

| Pair | Direction(s) used | Trades/rebalances | Funding PnL | Price PnL | Costs | Net PnL | Max DD | Keep in universe? |
|---|---|---:|---:|---:|---:|---:|---:|---|
| | | | | | | | | |

## Monthly Contribution

| Month | Funding PnL | Price PnL | Costs | Net PnL | Drawdown | Notes |
|---|---:|---:|---:|---:|---:|---|
| | | | | | | |

## Currency Exposure

| Currency | Average exposure | Max exposure | Net PnL contribution | Risk note |
|---|---:|---:|---:|---|
| USD | | | | |
| EUR | | | | |
| GBP | | | | |
| JPY | | | | |
| CHF | | | | |
| CAD | | | | |
| AUD | | | | |
| NZD | | | | |

## Stress Tests

| Stress | Result | Pass? | Notes |
|---|---:|---|---|
| Carry-crash shock | | PASS / FAIL | |
| Spread x2 | | PASS / FAIL | |
| Slippage x2 | | PASS / FAIL | |
| Worst month removed | | PASS / FAIL | |
| Best month removed | | PASS / FAIL | |
| Largest pair contribution removed | | PASS / FAIL | |

## Gate Evaluation

| Gate | Pass? | Evidence |
|---|---|---|
| Verified swap data covers the tested sample | YES / NO | |
| Gross funding contribution is positive before price movement | YES / NO | |
| Net OOS positive after all costs and rollover | YES / NO | |
| Result not concentrated in one pair/currency/month | YES / NO | |
| Drawdown tolerable under carry-crash stress | YES / NO | |
| Can run in paper-shadow without changing Branch B | YES / NO | |

## Decision

Choose exactly one:

- KEEP: build the next research iteration or paper-shadow design.
- DISCARD: close this carry/funding lane and document why.
- BLOCKED: collect missing data before any strategy implementation.

## Follow-Up

List only actions justified by the evidence above:

1.
2.
3.
