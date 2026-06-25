# COT Positioning Data-Proof Contract — 2026-06

**Lane:** Premise #6 — broad listed-futures COT positioning reversal
**Current stage:** Official-source data proof only
**Allowed verdicts in this stage:** `DATA_PASS` or `BLOCKED`

## Scope

This contract implements Wave 1 of
[`PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md`](../PROGRAM_DECISION_MEMO_ADDENDUM_2026-06-24.md).
It verifies whether a broad, fixed listed-futures universe has enough reliable
Commitments of Traders history for later frequency and OLS relationship tests.

This stage does **not** test profitability. It does not authorize:

- a trading strategy or backtest;
- classification or any other machine-learning model;
- parameter search;
- changes to Branch B, `src/`, Telegram, Docker, or production configuration.

## Economic premise

The later hypothesis is that extreme non-commercial futures positioning can be a
contrary indicator. The information source is participant positioning reported by
the CFTC, not a transformation of OHLC prices.

This contract proves only that the required positioning data exists and can support
a no-lookahead pipeline once exceptional delayed releases are controlled.

## Official source decision

The loader MUST use the CFTC Public Reporting Environment:

- Dataset: **Legacy - Futures Only**
- Dataset ID: `6dca-aqww`
- Resource API: `https://publicreporting.cftc.gov/resource/6dca-aqww.json`
- Metadata API: `https://publicreporting.cftc.gov/api/views/6dca-aqww`

The Legacy report is selected because it supplies one consistent non-commercial /
commercial classification across physical and financial futures. The lane MUST use
the futures-only report, not futures-and-options combined data.

The `cot_reports` package was evaluated but is not required. Direct CFTC PRE access
is preferred because it exposes the official schema, stable dataset ID, query
filters, and metadata without adding a wrapper dependency or relying on historical
bulk-file URL conventions.

## Fixed universe

The universe is pre-registered before relationship testing. Markets may fail the
data gate, but failed markets MUST NOT be replaced after results are observed.

| Code | Symbol | Sector | Contract market |
| --- | --- | --- | --- |
| `002602` | CORN | grains | CORN - CHICAGO BOARD OF TRADE |
| `005602` | SOYBEANS | grains | SOYBEANS - CHICAGO BOARD OF TRADE |
| `007601` | SOYBEAN_OIL | grains | SOYBEAN OIL - CHICAGO BOARD OF TRADE |
| `026603` | SOYBEAN_MEAL | grains | SOYBEAN MEAL - CHICAGO BOARD OF TRADE |
| `039601` | ROUGH_RICE | grains | ROUGH RICE - CHICAGO BOARD OF TRADE |
| `057642` | LIVE_CATTLE | livestock | LIVE CATTLE - CHICAGO MERCANTILE EXCHANGE |
| `054642` | LEAN_HOGS | livestock | LEAN HOGS - CHICAGO MERCANTILE EXCHANGE |
| `073732` | COCOA | softs | COCOA - ICE FUTURES U.S. |
| `083731` | COFFEE | softs | COFFEE C - ICE FUTURES U.S. |
| `080732` | SUGAR | softs | SUGAR NO. 11 - ICE FUTURES U.S. |
| `088691` | GOLD | metals | GOLD - COMMODITY EXCHANGE INC. |
| `084691` | SILVER | metals | SILVER - COMMODITY EXCHANGE INC. |
| `076651` | PLATINUM | metals | PLATINUM - NEW YORK MERCANTILE EXCHANGE |
| `075651` | PALLADIUM | metals | PALLADIUM - NEW YORK MERCANTILE EXCHANGE |
| `232741` | AUD | currencies | AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE |
| `090741` | CAD | currencies | CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE |
| `099741` | EUR | currencies | EURO FX - CHICAGO MERCANTILE EXCHANGE |
| `097741` | JPY | currencies | JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE |
| `095741` | MXN | currencies | MEXICAN PESO - CHICAGO MERCANTILE EXCHANGE |
| `092741` | CHF | currencies | SWISS FRANC - CHICAGO MERCANTILE EXCHANGE |
| `1170E1` | VIX | volatility | VIX FUTURES - CBOE FUTURES EXCHANGE |
| `240743` | NIKKEI_YEN | equity_index | NIKKEI STOCK AVERAGE YEN DENOM - CHICAGO MERCANTILE EXCHANGE |
| `13874+` | SP500 | equity_index | S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE |

## Required source fields

- CFTC contract market code
- market and exchange name
- report date
- open interest
- non-commercial long positions
- non-commercial short positions
- commodity group and subgroup
- commodity name
- contract units
- futures-only / combined discriminator

The normalized data derives:

```text
net_noncommercial = noncommercial_long - noncommercial_short
net_noncommercial_pct_oi = net_noncommercial / open_interest
```

Rows with missing required values, non-positive open interest, unknown market codes,
duplicate market-date keys, or changed contract names are invalid.

## No-lookahead rule

CFTC positions are measured as of Tuesday and are generally released Friday at
3:30 p.m. U.S. Eastern Time. Holidays can shift release timing, and the CFTC states
that a complete historical release-date list is not available.

The loader therefore assigns a standard-schedule field:

```text
available_date = report_date + 6 calendar days
```

That is the following Monday. It is suitable for the normal Friday publication
schedule, but it is not a complete historical release calendar. Exceptional delayed
releases, including government disruption periods, can make the derived Monday too
early.

Before relationship testing, the next task MUST inspect the CFTC release schedules
and historical special announcements. Every delayed report week MUST either receive
a verified publication date or be excluded. The Tuesday report date MUST never be
treated as the information-availability date.

## Data gates

The fixed verification window is `2010-01-01` through `2026-06-16`.

A market passes only if all conditions hold:

- at least 15.0 years between first and last report;
- at least 720 observations, equivalent to a conservative 48 reports per year;
- no gap greater than 14 calendar days;
- latest row no more than 14 days behind the latest report in the fetched universe;
- no duplicate market-date keys;
- exact expected contract-market name throughout the fixed window;
- no availability-date violations.

The lane receives `DATA_PASS` only when at least 15 pre-registered markets pass.
Otherwise it is `BLOCKED`.

`BLOCKED` means the data premise or implementation requires repair. It is not a
negative profitability verdict and MUST NOT be appended to the closed-lane registry.

## Provenance requirements

Every live verification MUST record:

- exact query URL or URLs;
- retrieval timestamp in UTC;
- requested window and fixed market codes;
- CFTC dataset metadata and update timestamp;
- canonical SHA256 of source rows;
- SHA256 of dataset metadata;
- per-market coverage, gaps, names, and verdict.

The raw multi-market dataset is not committed. The query is fixed to a historical
end date, and the CFTC states that historical data is not updated after publication;
the query plus hashes form the pinned proof.

## Executable verification

```bash
python -m research.new_edge.cot_positioning.data.verify_cot_data \
  --start 2010-01-01 \
  --end 2026-06-16 \
  --output docs/research/cot_positioning/COT_DATA_MANIFEST_2026-06-25.md \
  --provenance research/new_edge/cot_positioning/data/provenance/cftc_legacy_futures_only_2010-01-01_2026-06-16.json
```

## Next dependency

Only after `DATA_PASS`, open a separate task for simple relationship testing:

1. fixed frequency tables by positioning-extremity bucket;
2. OLS with chronological subsample stability checks;
3. shuffled-label control and concentration checks.

That task MUST first implement the delayed-release exclusion / timestamp control
defined above.

No classifier is allowed unless a later fixed-rule strategy clears the binding gross
PF gate in the merged program governance.
