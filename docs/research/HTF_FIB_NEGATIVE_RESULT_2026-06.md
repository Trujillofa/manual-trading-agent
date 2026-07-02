# Confirmed HTF Pivot/Fibonacci Strategy — Negative Result (2026-06)

## Verdict

**DISCARD.** The confirmed 4H-pivot Fibonacci setup did not produce a stable,
cost-aware out-of-sample edge. This experiment is additional evidence for the
locked FX directional-TA closure; it does not reopen that lane.

## Test design

- Data: cached Dukascopy 15-minute OHLC for eight FX pairs over 365 days.
- Split: chronological 65% in-sample / 35% out-of-sample.
- Execution: signal at bar close, entry at the next bar open, stop-first when
  target and stop are touched within the same bar.
- Costs: 2-pip spread, 2-pip adverse slippage per fill, and commission.
- Minimum promotion gates: at least 30 trades in each window, OOS net profit
  factor at least 1.20, and positive IS and OOS net P&L.
- Optimization discipline: 128 entry configurations, 135 exit configurations,
  and 12 hardening configurations were ranked on IS only. Exactly one winner
  was evaluated on the untouched OOS window.

The original TradingView file is an indicator without entry/exit orders. The
Python baseline is therefore an explicit executable interpretation of its
markers, not a claim of exact TradingView Strategy Tester parity.

## Results

| Test | IS trades | IS net PF | IS net P&L | OOS trades | OOS net PF | OOS net P&L | OOS max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Marker baseline | 2 | 0.00 | -2.87% | 1 | 0.00 | -1.23% | 1.23% |
| Hardened MTF | 0 | 0.00 | 0.00% | 0 | 0.00 | 0.00% | 0.00% |
| IS-selected grid winner | 11 | 1.30 | +1.36% | 12 | 0.07 | -11.53% | 11.53% |

The selected configuration was:

```text
4H pivots, left=5, right=2
chart RSI long<=45 / short>=55
EMA200 directional filter
candle confirmation
2 ATR target / 2 ATR stop
64-bar maximum hold
```

It failed every binding OOS promotion gate and was profitable on only one of
eight pairs.

## Requested fixed-lot account scenarios

The account profiles have approximately equal per-position base-notional
leverage (about 16.1x):

| Stop policy | Account | Lots | OOS trades | OOS net PF | OOS net P&L | Ending balance | OOS max DD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 ATR | $6,500.68 | 1.05 | 10 | 0.08 | -22.81% | $5,017.86 | 22.81% |
| 2 ATR | $116,502.53 | 18.78 | 10 | 0.08 | -22.76% | $89,981.24 | 22.76% |
| 80% starting-capital stop | $6,500.68 | 1.05 | 9 | 0.21 | -25.97% | $4,812.62 | 28.55% |
| 80% starting-capital stop | $116,502.53 | 18.78 | 9 | 0.21 | -25.92% | $86,310.35 | 28.50% |

The 80%-capital stop was not hit in this sample; exits were 11 targets and
eight time stops. Widening the stop nevertheless prolonged adverse exposure and
worsened OOS loss and drawdown versus the 2-ATR stop.

The fixed-lot simulations do not model broker margin calls, stop-out
liquidation, financing/swap, or point-in-time news. Peak concurrency was two
positions, approximately 32.3x aggregate base notional. Those omissions make
the scenarios less conservative operationally, not candidates for promotion.

## Reproduction

```bash
python scripts/run_htf_fib_backtest.py

python scripts/optimize_htf_fib_backtest.py \
  --override-negative-result \
  docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md

python scripts/evaluate_htf_fib_accounts.py \
  --config-json results/htf_fib_optimization_<timestamp>.json

python scripts/evaluate_htf_fib_accounts.py \
  --config-json results/htf_fib_optimization_<timestamp>.json \
  --stop-capital-fraction 0.80
```

## Decision

Do not tune pivots, Fibonacci levels, RSI thresholds, EMA filters, or exits
again on this data family. Continue only with a materially different premise
and data source. The next authorized research action is the **term-structure
roll-yield source gate** (individual contract-month data for the fixed
12-market commodity universe; see
`docs/research/term_structure/ROLL_YIELD_DATA_MANIFEST_2026-06.md`). No
strategy logic until that gate records `DATA_PASS` or `BLOCKED`.
