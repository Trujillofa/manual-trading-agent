# COT Positioning Data Manifest — 2026-06-25

## Verdict: DATA_PASS

This is a data-availability verdict only. It is not evidence of a return
relationship and does not authorize a strategy, classifier, or backtest.

## Command

```bash
python -m research.new_edge.cot_positioning.data.verify_cot_data --start 2010-01-01 --end 2026-06-16 --output docs/research/cot_positioning/COT_DATA_MANIFEST_2026-06-25.md --provenance research/new_edge/cot_positioning/data/provenance/cftc_legacy_futures_only_2010-01-01_2026-06-16.json
```

## Fixed data gate

- Requested window: `2010-01-01` through `2026-06-16`
- Source rows: 19,734
- Passing markets: 23/23 (minimum 15)
- Minimum coverage per market: 15 years
- Minimum observations per market: 720
- Maximum report gap: 14 calendar days
- Source latest report: `2026-06-16`

## Per-market verification

| Code | Symbol | Sector | Rows | First | Last | Years | Max gap | Status |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |
| `002602` | CORN | grains | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `005602` | SOYBEANS | grains | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `007601` | SOYBEAN_OIL | grains | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `026603` | SOYBEAN_MEAL | grains | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `039601` | ROUGH_RICE | grains | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `057642` | LIVE_CATTLE | livestock | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `054642` | LEAN_HOGS | livestock | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `073732` | COCOA | softs | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `083731` | COFFEE | softs | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `080732` | SUGAR | softs | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `088691` | GOLD | metals | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `084691` | SILVER | metals | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `076651` | PLATINUM | metals | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `075651` | PALLADIUM | metals | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `232741` | AUD | currencies | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `090741` | CAD | currencies | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `099741` | EUR | currencies | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `097741` | JPY | currencies | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `095741` | MXN | currencies | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `092741` | CHF | currencies | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `1170E1` | VIX | volatility | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `240743` | NIKKEI_YEN | equity_index | 859 | 2010-01-05 | 2026-06-16 | 16.44 | 8 | DATA_PASS |
| `13874+` | SP500 | equity_index | 836 | 2010-06-15 | 2026-06-16 | 16.00 | 8 | DATA_PASS |

## Exceptions

- None.

## Source and schema

- Official dataset: CFTC PRE `Legacy - Futures Only` (`6dca-aqww`)
- Resource endpoint: `https://publicreporting.cftc.gov/resource/6dca-aqww.json`
- Dataset metadata: `https://publicreporting.cftc.gov/api/views/6dca-aqww`
- CFTC COT source page: https://publicreporting.cftc.gov/stories/s/r4w3-av2u
- CFTC report description: https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm
- Report type: Legacy Futures Only (`FutOnly`)
- Position fields: non-commercial long, non-commercial short, open interest
- Stable join key: CFTC contract market code plus report date

## No-lookahead rule

- CFTC positions are measured as of Tuesday and generally released Friday afternoon.
- CFTC states that historical exact release dates are not available beyond its
  rolling release schedule and holidays can shift publication.
- Every normalized row receives a standard-schedule `available_date` equal to the
  following Monday (`report_date + 6 calendar days`).
- This derived date is not sufficient for exceptional delayed-release periods.
  Before relationship testing, delayed weeks MUST be identified from CFTC special
  announcements / available schedules and either assigned verified release dates
  or excluded.
- Relationship tests MUST NOT use the Tuesday report date as the information-
  availability date.

## Provenance

- Retrieved at UTC: `2026-06-25T15:30:00.723589+00:00`
- Canonical source-row SHA256: `2baa3384112b6c6f57efd0cfbb46432796c83dbc4fcce6adbdb796a00ae5eef6`
- Dataset-metadata SHA256: `e48ad48285bedf83b74d7ccb7923250e2dacd021ad22466a9f995679358259cb`
- Machine-readable record: `research/new_edge/cot_positioning/data/provenance/cftc_legacy_futures_only_2010-01-01_2026-06-16.json`

## Next dependency

If `DATA_PASS`: first implement the delayed-release exclusion / verified-timestamp control, then build frequency tables and fixed OLS relationship tests. Do not build a classifier or trading strategy.
If `BLOCKED`: repair only the documented data issue. Do not substitute markets after seeing relationship results.
