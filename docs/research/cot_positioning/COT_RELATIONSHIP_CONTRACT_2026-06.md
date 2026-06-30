# COT Positioning Relationship-Test Contract — 2026-06

**Lane:** Premise #6 — broad listed-futures COT positioning reversal
**Stage:** fixed relationship test; no trading strategy or classifier
**Allowed verdicts:** `RELATIONSHIP_PASS` or `RELATIONSHIP_FAIL`

## Hypothesis

Extreme non-commercial positioning is a contrary indicator: markets with low
positioning percentiles should subsequently outperform markets with high
positioning percentiles.

The primary response is the four-week log return. A 13-week return may be
reported later only as a labeled diagnostic; it cannot change this verdict.

## Fixed signal

- Source: CFTC PRE `Legacy - Futures Only`, dataset `6dca-aqww`.
- Raw measure: `(noncommercial long - noncommercial short) / open interest`.
- Normalization: each market's rolling 156-report percentile, requiring at
  least 104 prior/current reports.
- Availability: the conservative following-Monday field from the data-proof
  package, amended by the controls below.
- Entry observation: the first daily futures close **strictly after** the
  effective availability date.
- Response: log return from that close to the first close at least 28 calendar
  days later.

No percentile window, response horizon, universe member, or price mapping may
be changed after seeing relationship results.

## Delayed-release and revision controls

The relationship test MUST apply `availability.py` before signal construction.
It uses verified CFTC special announcements to:

- exclude the 2018–2019 appropriations-lapse backlog;
- exclude the 2023 ION incident backlog;
- exclude the March 28, 2017 reports for specifically revised fixed-universe
  markets;
- assign verified publication dates to the 2014, 2015, 2020, 2021, January
  2025, and late-2025 delayed reports.

The first price close must be strictly later than the effective publication
date. This prevents same-day publication timing from entering the response.

Official controls:

- <https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm>
- <https://www.cftc.gov/PressRoom/PressReleases/7864-19>
- <https://www.cftc.gov/PressRoom/PressReleases/9147-25>

## Fixed price mapping

Yahoo Finance continuous front-month futures are used only for this low-cost
relationship falsifier. Twenty-two fixed markets have predeclared tickers.
`VIX` has no accepted Yahoo continuous-futures ticker and MUST fail coverage;
spot VIX must not be substituted because its term-structure return differs from
VIX futures.

Continuous-contract roll gaps are a known limitation. A relationship pass
would authorize a separate roll-aware strategy-data gate; it would not
authorize trading.

At least 15 pre-registered markets and 500 OOS observations are required.
Failed markets cannot be replaced after results are observed.

## Chronological validation

The post-warmup sample is split once by unique effective availability date:

- first 65%: in-sample;
- final 35%: untouched out-of-sample judge.

The pooled OLS is:

```text
forward_log_return = intercept + slope * positioning_percentile
```

The contrary hypothesis requires a negative slope. The reported one-sided
p-value uses the large-sample normal approximation; the deterministic
within-market shuffled-signal control is the binding distribution-free check.

## Binding pass gates

Every gate must pass:

1. at least 15 markets and 500 OOS observations;
2. IS pooled slope is negative;
3. OOS pooled slope is negative with one-sided p-value at most 0.10;
4. bottom-minus-top OOS positioning-quintile mean return is positive;
5. at least three of four adjacent OOS quintile means decrease;
6. at least 60% of market-level OOS slopes are negative;
7. observed OOS slope is more reversal-like than at least 95% of 2,000
   within-market shuffled-signal slopes, seed `20260630`;
8. at least 80% of leave-one-market-out OOS slopes remain negative.

Any failed gate produces `RELATIONSHIP_FAIL`. Do not optimize or rescue the
test. A pass authorizes one separately specified, cost-aware weekly strategy
test; it is not evidence of production readiness.
