# PEAD Data-Source Audit Specification — 2026-07

## Audit outcome

No source has passed yet. This document defines the evidence required to record
`DATA_PASS` or `BLOCKED`. It does not approve a vendor or authorize a purchase.

## Audit order

Evaluate sources in this order:

1. Existing local or institutionally licensed snapshots.
2. Free official or exchange-hosted data.
3. Reproducible free-tier APIs.
4. Low-cost commercial sources such as EOD Historical Data, Zacks, or
   IEX-compatible providers.

Stop before a trial requiring payment details, accepting restrictive
redistribution terms, or purchasing a subscription. Present price, license,
coverage, and deficiencies for separate owner approval.

## Required field matrix

| Domain | Required fields | Acceptance evidence |
|---|---|---|
| Security master | Stable security ID, ticker history, exchange, security type, listing and delisting dates | Historical lookup includes inactive names and ticker changes |
| Earnings event | Fiscal period, announcement timestamp, timezone, actual EPS, consensus EPS, estimate observation timestamp | Snapshot proves estimate predates announcement |
| Prices | Unadjusted OHLCV, adjusted close, split and dividend factors | Corporate-action reconciliation passes sampled events |
| Delistings | Delisting date and return, or documented conservative substitute | Inactive securities remain in the event universe |
| Classification | Point-in-time sector | Historical sector value predates each event |
| Provenance | Source URL/API, retrieval time, license, request parameters, hashes | A pinned snapshot can be reproduced and authenticated |

Vendor-supplied `surprise_pct` is informational. The verifier must recompute
surprise from the point-in-time actual and estimate.

## Point-in-time tests

The verifier must:

- Reject estimates without an observation timestamp.
- Reject estimates observed at or after the announcement.
- Reject date-only or timezone-ambiguous announcement records.
- Detect revised or backfilled consensus histories by comparing repeated
  snapshots when the source supports them.
- Map events to the first tradable regular-session open using an exchange
  calendar, including holidays and early closes.
- Confirm that corporate actions after an event do not alter the stored
  point-in-time surprise.

## Coverage tests

`DATA_PASS` requires:

- Coverage from at least 2016-01-01 through 2026-01-01.
- At least 500 eligible stocks after applying the fixed liquidity universe.
- At least 80% complete actual, estimate, announcement timestamp, price, and
  sector fields in every calendar year.
- At least 95% of retained events mapped unambiguously to a tradable session.
- At least 90% stable-ID coverage across ticker changes and delistings.
- At least 30 prospective OOS events in each extreme surprise quintile.

Coverage must be reported by year, sector, and active/delisted status. Aggregate
coverage cannot hide a weak early period or missing delisted names.

## Provenance manifest

The source audit must pin:

- Source and dataset identifiers.
- Retrieval timestamp and request parameters.
- License and redistribution summary.
- Raw file names, byte sizes, row counts, and SHA-256 hashes.
- Field mapping, units, timezone assumptions, and null counts.
- Coverage bounds and rejection counts by reason.
- Verifier commit SHA and Python environment.

Raw licensed data must not be committed. Commit only non-sensitive provenance,
small synthetic fixtures, and aggregate verification results permitted by the
license.

## Terminal decisions

Record `DATA_PASS` only when every binding test passes. Record `BLOCKED` when:

- The source is survivorship-biased.
- Consensus estimates are restated or lack point-in-time timestamps.
- Announcement timing is ambiguous.
- Delisted securities cannot be represented.
- Coverage falls below the contract gate.
- The license prevents the required local validation.

Do not merge partial sources to manufacture coverage without a pre-written,
stable identifier and deduplication policy. Do not weaken the universe or
history requirement after the audit.

## Deliverables for the verifier PR

- A read-only `verify_pead_data` CLI.
- Synthetic fixtures for before-open, during-session, after-close, ticker-change,
  split, and delisting cases.
- A JSON provenance schema and one permitted pinned provenance record.
- A generated Markdown data manifest.
- Focused tests and a single `DATA_PASS` or `BLOCKED` ledger row.

Relationship logic remains out of scope for that PR.
