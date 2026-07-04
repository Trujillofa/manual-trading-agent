# PEAD Relationship Contract — 2026-07

## Decision

Post-earnings announcement drift (PEAD) is the next data-proof lane. This
contract authorizes a source audit and, only after `DATA_PASS`, one fixed
relationship falsifier. It does not authorize a trading strategy, parameter
search, paid-data purchase, paper trading, or production integration.

## Why this is a new lane

PEAD studies firm-specific earnings surprises in US equities. The closed event
lane studied macroeconomic surprises in FX. PEAD therefore changes the
instrument class, event class, data source, and holding horizon. It does not
reopen FX event drift or price-only momentum.

## Fixed hypothesis

Stocks with positive earnings surprises have positive market- and
sector-adjusted returns after the announcement; stocks with negative surprises
have negative adjusted returns. The top-minus-bottom surprise-quintile spread
should be positive from day 5 through day 21 after realistic costs.

## Data gate

The verifier must prove all of the following before relationship code is
authorized:

- At least 10 years of history and 500 eligible US stocks.
- Point-in-time announcement date, time, and timezone.
- Point-in-time consensus estimate captured before the announcement.
- Reported actual earnings and a reproducible surprise value.
- Daily adjusted and unadjusted OHLC, volume, splits, dividends, and delisting
  returns or an explicit delisting treatment.
- Point-in-time sector classification and security identifiers that survive
  ticker changes.
- A survivorship-safe universe, including names that delisted or left major
  indexes.
- Source license and redistribution terms compatible with local research.

Missing or ambiguous point-in-time estimates, announcement timestamps,
survivorship controls, or minimum coverage produces `BLOCKED`. Free-tier
availability alone is not evidence that the gate passes.

## Point-in-time universe

For each announcement date:

1. Start with US common stocks available in the source's historical security
   master.
2. Exclude ETFs, funds, preferred shares, ADRs, OTC securities, and securities
   below $5 at the prior close.
3. Rank eligible stocks by trailing 60-session median daily dollar volume,
   calculated only from information available before the announcement.
4. Retain the 1,000 most liquid stocks. A date is valid when at least 500 remain.

The universe and thresholds are fixed before data inspection. Rejected stocks
are not replaced after results are known.

## Event timing

- Before-open announcement: the first tradable price is that session's regular
  market open.
- During-session announcement: the first tradable price is the next regular
  market open.
- After-close announcement: the first tradable price is the next regular market
  open.
- Date-only or timezone-ambiguous announcements are ineligible.
- The estimate must carry an observation timestamp earlier than the public
  announcement. Later revisions and restated histories are forbidden.

## Fixed relationship test

- Define standardized surprise as `(actual - estimate) / abs(estimate)`.
  Estimates equal to zero are ineligible.
- Winsorize surprise cross-sectionally at the 1st and 99th percentiles using
  in-sample data only, then freeze those bounds.
- Form surprise quintiles independently each calendar quarter.
- Measure open-to-close market- and sector-adjusted returns from the first
  tradable open through 1, 5, 21, and 60 sessions.
- The primary relationship is the equal-weight top-minus-bottom quintile return.
  Days 5 and 21 are binding; days 1 and 60 describe the decay curve.
- Split events chronologically 65% in-sample and 35% out-of-sample. OOS cannot
  influence eligibility, winsorization, quintiles, costs, or horizons.

This is a relationship falsifier, not a portfolio backtest. If the relationship
fails, no entry/exit strategy may be built to rescue it.

## Cost and bias controls

- Charge 10 basis points per side (20 basis points round trip) as the binding
  implementation allowance.
- Start returns at the first tradable open. Report the pre-entry announcement
  gap separately; do not count it as attainable strategy return.
- Report zero-cost and costed results separately.
- Include delisting returns where available; otherwise apply the source's
  documented conservative delisting convention.
- Report results by sector, calendar period, market-cap bucket, and surprise
  sign.
- No single sector may contribute more than 35% of OOS spread P&L.
- Benchmark adjustment must use data available on the event date.

## Binding verdicts

`RELATIONSHIP_PASS` requires all of:

- OOS costed profit factor at least 1.20.
- At least 30 OOS events in both the top and bottom quintiles.
- Positive OOS top-minus-bottom returns at days 5 and 21.
- Same spread direction in at least 60% of sectors with eligible observations.
- No sector contributes more than 35% of OOS spread P&L.
- The effect remains positive in the post-2012 OOS subset.

`RELATIONSHIP_FAIL` is binding if any of:

- OOS costed profit factor is below 1.00.
- Day-5 or day-21 OOS spread is non-positive.
- Realistic costs make the OOS spread non-positive.
- The effect exists only before 2012.
- Results require stocks outside the fixed liquid universe.
- Results reverse sign or depend on one sector.

`BLOCKED` applies when the data gate cannot be verified. `BLOCKED` is not a
negative PEAD result; it means the premise was not tested.

## Stop rules

Do not change the universe, surprise formula, horizons, split, costs, or gates
after seeing results. Do not add price momentum, RSI, technical filters,
microcaps, options, or alternate earnings definitions to rescue a failure. A
new attempt requires a materially different written premise and owner approval.

## Authorized next command

After this contract is merged, the next task may implement:

```bash
python -m research.new_edge.pead.data.verify_pead_data \
  --input <source-snapshot> \
  --provenance <provenance-json> \
  --start 2016-01-01 \
  --end 2026-01-01 \
  --output docs/research/pead/PEAD_DATA_MANIFEST_2026-07.md
```

The verifier must be read-only with respect to source data. Relationship code
remains unauthorized until the ledger records `DATA_PASS`.
