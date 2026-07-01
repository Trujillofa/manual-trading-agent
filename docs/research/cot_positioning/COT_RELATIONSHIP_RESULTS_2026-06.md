# COT Positioning Relationship Test

## Verdict: **RELATIONSHIP_FAIL**

This is a relationship falsifier, not a strategy backtest or trading authorization.

## Sample

- Markets: 22
- Observations: 16143
- OOS observations: 5654
- Chronological cutoff: 2021-05-10
- Missing price markets: VIX
- Delayed/revised rows excluded: 418
- Verified release dates applied: 414

## Regression

| Window | N | Slope | SE | t | One-sided p |
| --- | ---: | ---: | ---: | ---: | ---: |
| IS | 10489 | -0.003152 | 0.001923 | -1.639 | 0.0506 |
| OOS | 5654 | 0.009375 | 0.003006 | 3.119 | 0.9991 |

## OOS stability

- Bottom-minus-top quintile mean return: -0.9648%
- Adjacent quintile decreases: 1/4
- Markets with negative slope: 50.0%
- Shuffled-signal reversal percentile: 1.6%
- Negative leave-one-market-out slopes: 0.0%

| Bucket | Mean four-week log return |
| --- | ---: |
| Q1_low | -0.1371% |
| Q2 | 0.1311% |
| Q3 | 0.2509% |
| Q4 | 0.2434% |
| Q5_high | 0.8277% |

## Gate failures

- OOS slope 0.009375 is not negative
- OOS one-sided p 0.9991 > 0.10
- OOS bottom-minus-top return -0.009648 is not positive
- OOS adjacent bucket decreases 1 < 3
- negative market slope fraction 50.0% < 60%
- shuffle reversal percentile 1.6% < 95%
- negative leave-one-market-out slope fraction 0.0% < 80%

## Limits

- Yahoo continuous front-month futures can contain roll discontinuities.
- A pass authorizes a separate roll-aware, cost-aware strategy data gate only.
- A fail closes this fixed COT reversal relationship test without parameter rescue.
