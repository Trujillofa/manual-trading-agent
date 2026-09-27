# PEAD Source Audit Results — 2026-07

## Verdict: BLOCKED

Source inventory and field-matrix audit only. No licensed snapshot was purchased,
no bulk download was performed, and relationship code remains unauthorized.

## Command

```bash
python -m research.new_edge.pead.data.audit_pead_sources --output docs/research/pead/PEAD_SOURCE_AUDIT_RESULTS_2026-07.md --provenance research/new_edge/pead/data/provenance/pead_source_audit_inventory_2026-07.json
```

## Audit order summary

| Step | Scope | Result |
|---|---|---|
| 1 | Local / institutional snapshots | 0 non-synthetic dirs in repo; prod ssh `skipped` |
| 2 | Free official / exchange-hosted | SEC EDGAR probed — INSUFFICIENT; IEX — INSUFFICIENT |
| 3 | Reproducible free-tier APIs | Alpha Vantage + yfinance + Twelve Data probed — INSUFFICIENT; FMP — INSUFFICIENT |
| 4 | Low-cost commercial | EODHD probed — INSUFFICIENT (free EOD-only key); Zacks — UNVERIFIED |

## Binding blocker

no free or probed source exposes estimate observation timestamps.

The PEAD contract requires `estimate_observed_ts` strictly before `announcement_ts`.
None of the probed free sources expose that field. Vendor-supplied `surprise_pct`
or restated consensus values cannot satisfy the gate.

## Provider comparison

| Source | Tier | Verdict | Cost | Key gaps |
|---|---:|---|---|---|
| SEC EDGAR company facts (XBRL) | 2 | INSUFFICIENT | free | no consensus EPS |
| IEX Cloud / exchange-hosted earnings | 2 | INSUFFICIENT | n/a | platform not viable without alternate vendor contract |
| Alpha Vantage EARNINGS + EARNINGS_ESTIMATES | 3 | INSUFFICIENT | free tier | no estimate observation timestamp |
| yfinance / Yahoo Finance | 3 | INSUFFICIENT | free | no announcement timestamp with timezone |
| Twelve Data earnings + estimates | 3 | INSUFFICIENT | free tier + premium calendar add-on | no estimate observation timestamp |
| Financial Modeling Prep stable earnings | 3 | INSUFFICIENT | free/stable tier + premium for historical calendar windows | no estimate observation timestamp |
| EOD Historical Data calendar/earnings | 4 | INSUFFICIENT | free EOD tier + paid Corporate Events / Fundamentals plans | no estimate observation timestamp in public field matrix |
| Zacks Data consensus + reference feeds | 4 | UNVERIFIED | institutional / enterprise (Nasdaq Data Link premium or WRDS / direct) | free trial sample verification required before DATA_PASS attempt (paid license needs contract amendment) |

## Field matrix notes

### SEC EDGAR company facts (XBRL)

- Probe: `probed`
- License: Public SEC data; redistribution subject to SEC terms.
- Coverage claim: Reported EPS for filers; no consensus history.
- Reference: https://www.sec.gov/edgar/sec-api-documentation

| Required domain | Present | Evidence |
|---|---|---|
| security_master_survivorship_safe | no | No exchange security master or delisting feed. |
| announcement_timestamp_tz | no | XBRL provides filing date, not earnings call timestamp. |
| estimate_observation_timestamp | no | SEC filings contain reported EPS only. |
| actual_eps | yes | EarningsPerShareDiluted available with fiscal period metadata. |
| consensus_eps | no | No analyst consensus in EDGAR company facts. |
| daily_ohlcv_adjusted | no | Prices not in company facts API. |
| delisting_treatment | no | No delisting return series. |
| point_in_time_sector | no | No GICS/sector history in this endpoint. |
| local_research_license | yes | Public EDGAR; local research permitted. |

Blocking gaps:
- no consensus EPS
- no estimate observation timestamp
- no announcement timestamp with timezone
- no survivorship-safe security master
- no price or sector bundles

### IEX Cloud / exchange-hosted earnings

- Probe: `desk_review`
- License: IEX Cloud no longer a viable free research path.
- Coverage claim: Not available for PEAD gate under current public offering.
- Reference: https://iexcloud.io/

| Required domain | Present | Evidence |
|---|---|---|
| security_master_survivorship_safe | marketed_unverified | IEX Cloud migrated/sunset; status unclear. |
| announcement_timestamp_tz | marketed_unverified | Not evaluated. |
| estimate_observation_timestamp | marketed_unverified | Not evaluated. |
| actual_eps | marketed_unverified | Not evaluated. |
| consensus_eps | marketed_unverified | Not evaluated. |
| daily_ohlcv_adjusted | marketed_unverified | Not evaluated. |
| delisting_treatment | marketed_unverified | Not evaluated. |
| point_in_time_sector | marketed_unverified | Not evaluated. |
| local_research_license | no | Legacy IEX Cloud wound down; not a current free path. |

Blocking gaps:
- platform not viable without alternate vendor contract

### Alpha Vantage EARNINGS + EARNINGS_ESTIMATES

- Probe: `probed`
- License: Free API key; 25 requests/day; premium for bulk history.
- Coverage claim: EARNINGS per-symbol history (121 AAPL rows); EARNINGS_ESTIMATES 40 rows; EARNINGS_CALENDAR 0 rows.
- Reference: https://www.alphavantage.co/documentation/#earnings

| Required domain | Present | Evidence |
|---|---|---|
| security_master_survivorship_safe | no | LISTING_STATUS active-only probe returned 0 symbols; not a point-in-time survivorship-safe master. |
| announcement_timestamp_tz | no | reportedDate is date-only; reportTime is pre/post-market label only. |
| estimate_observation_timestamp | no | EARNINGS_ESTIMATES exposes revision trails, not a point-in-time observation timestamp predating each announcement. |
| actual_eps | yes | quarterlyEarnings.reportedEPS present on AAPL probe. |
| consensus_eps | yes | quarterlyEarnings.estimatedEPS present; restated-at-report risk. |
| daily_ohlcv_adjusted | unknown | Separate TIME_SERIES_DAILY_ADJUSTED endpoint; not bundled. |
| delisting_treatment | no | No delisting return feed confirmed. |
| point_in_time_sector | no | OVERVIEW sector is current-state only. |
| local_research_license | yes | ALPHA_VANTAGE_API_KEY works on EARNINGS; free tier is rate-limited (25 req/day). |

Blocking gaps:
- no estimate observation timestamp
- announcement timing is date-only / pre-post label, not timezone timestamp
- no delisting treatment
- no point-in-time sector history
- no survivorship-safe security master in probe
- revision trails are not auditable point-in-time consensus snapshots
- per-symbol EARNINGS only (AAPL sample 121 rows, 1996-04-17 -> 2026-04-30); not a 500+ cross-section archive
- live probe hit free-tier daily limit; field evidence merged from cached probe artifact

### yfinance / Yahoo Finance

- Probe: `probed`
- License: Unofficial API; Yahoo terms prohibit redistribution and bulk storage.
- Coverage claim: Convenience wrapper; not a research-grade point-in-time archive.
- Reference: https://github.com/ranaroussi/yfinance

| Required domain | Present | Evidence |
|---|---|---|
| security_master_survivorship_safe | no | No historical security master in yfinance. |
| announcement_timestamp_tz | no | earnings_history index is fiscal quarter end, not announcement timestamp. |
| estimate_observation_timestamp | no | No estimate observation timestamp field. |
| actual_eps | yes | epsActual present on earnings_history probe. |
| consensus_eps | yes | epsEstimate present; likely restated current consensus. |
| daily_ohlcv_adjusted | unknown | Available via history(); separate from earnings. |
| delisting_treatment | no | No delisting return handling. |
| point_in_time_sector | no | Current sector in info; not point-in-time. |
| local_research_license | unknown | Unofficial Yahoo scraper; redistribution prohibited. |

Blocking gaps:
- no announcement timestamp with timezone
- no estimate observation timestamp
- consensus likely restated rather than point-in-time
- license incompatible with pinned local validation
- no survivorship-safe universe

### Twelve Data earnings + estimates

- Probe: `probed`
- License: Commercial API; rate-limited free tier; calendar is premium.
- Coverage claim: Per-symbol earnings history (66 AAPL rows in probe); cross-section calendar gated.
- Reference: https://twelvedata.com/docs/fundamentals/earnings

| Required domain | Present | Evidence |
|---|---|---|
| security_master_survivorship_safe | no | stocks catalog lists active symbols; no list/delist dates in earnings bundle. |
| announcement_timestamp_tz | no | earnings.date is YYYY-MM-DD; earnings.time empty on all 66 AAPL rows probed. |
| estimate_observation_timestamp | no | eps_estimate present without observation timestamp; eps_trend exposes 7/30/60/90-day revision windows only. |
| actual_eps | yes | earnings.eps_actual present in probe. |
| consensus_eps | yes | earnings.eps_estimate present; restated-at-report risk. |
| daily_ohlcv_adjusted | unknown | time_series endpoint separate; not bundled with earnings. |
| delisting_treatment | no | No delisting return feed on earnings endpoints. |
| point_in_time_sector | no | profile sector is current-state; no historical sector series. |
| local_research_license | yes | API key in .env; earnings_calendar requires grow/pro plan (forbidden); user earnings probe rate_limited. Daily credit limit blocks bulk cross-section builds. |

Blocking gaps:
- no estimate observation timestamp
- announcement time missing or empty on all probed earnings rows
- per-symbol earnings endpoint; no survivorship-safe cross-section archive
- earnings_calendar requires grow/pro plan (user probe: forbidden)
- vendor surprise_prc cannot substitute for point-in-time consensus proof
- user key exhausted daily API credits during probe; field shape confirmed via demo fallback
- demo/user sample oldest AAPL earnings row: 2010-10-17 (per-symbol only)

### Financial Modeling Prep stable earnings

- Probe: `probed`
- License: Commercial API; legacy v3 blocked; ranged calendar and quarter estimates premium.
- Coverage claim: stable/earnings per-symbol history (164 AAPL rows); default calendar 76 symbols.
- Reference: https://site.financialmodelingprep.com/developer/docs/stable-earnings-calendar

| Required domain | Present | Evidence |
|---|---|---|
| security_master_survivorship_safe | no | /api/v3/stock/list legacy-forbidden on current key; no delisting master in earnings bundle. |
| announcement_timestamp_tz | no | stable earnings uses date (YYYY-MM-DD) only; no announcement time or timezone. |
| estimate_observation_timestamp | no | lastUpdated is record refresh metadata, not a pre-announcement estimate snapshot. |
| actual_eps | yes | stable/earnings.epsActual present on AAPL probe. |
| consensus_eps | yes | stable/earnings.epsEstimated present; restated-at-report risk. |
| daily_ohlcv_adjusted | unknown | legacy /api/v3/historical-price-full forbidden on current key. |
| delisting_treatment | no | No delisting return feed on earnings endpoints. |
| point_in_time_sector | no | /stable/profile sector is current-state only. |
| local_research_license | yes | FMP_API_KEY in .env works on stable endpoints; legacy v3 and ranged calendar need upgrade. |

Blocking gaps:
- no estimate observation timestamp
- announcement timing is date-only (no timezone timestamp)
- lastUpdated is not a point-in-time consensus observation timestamp
- historical earnings_calendar date windows require premium plan (402)
- default earnings_calendar window has 76 symbols, not 500+ cross-section
- per-symbol stable/earnings only (AAPL sample 164 rows, 1985-09-30 -> 2026-07-30); not a survivorship-safe 500+ archive
- vendor fields cannot prove estimate predates announcement

### Zacks Data consensus + reference feeds

- Probe: `desk_review`
- License: Request-access licensing; redistribution terms negotiated per contract.
- Coverage claim: ZEEH consensus revisions from 1979 (obs_date); ZES surprises from 2000 (17k+ US/CA incl. delisted); 23k+ in EEH coverage.
- Reference: https://zacksdata.com/datasets/consensus-data/

| Required domain | Present | Evidence |
|---|---|---|
| security_master_survivorship_safe | marketed_unverified | Nasdaq ZACKS/MT master table: m_ticker, active_ticker_flag, asset_type; 17k+ active and delisted names in ZES coverage list. |
| announcement_timestamp_tz | marketed_unverified | Nasdaq ZACKS/ES: act_rpt_date, act_rpt_time (HH:MI America/New_York), act_rpt_code (BTO/DTM/AMC). Supports PEAD before-open / during / after-close mapping. |
| estimate_observation_timestamp | marketed_unverified | Nasdaq ZACKS/EEH obs_date labels revision receipt date (vendor column def); EEH published D+1 at 10:00 UTC — 1-day lag reduces look-ahead risk. NY vs UTC calendar date for obs_date still unspecified; strict same-day join attrition unquantified (likely BTO/DTM-heavy). |
| actual_eps | marketed_unverified | ZACKS/ES eps_act (BNRI-adjusted, comparable to eps_mean_est). |
| consensus_eps | marketed_unverified | ZACKS/EEH eps_mean_est by obs_date; ZACKS/ES eps_mean_est is pre-announcement consensus on surprise row but vendor surprise fields are informational only. |
| daily_ohlcv_adjusted | marketed_unverified | Zacks Prices & Returns + corporate actions feeds advertised. |
| delisting_treatment | marketed_unverified | ZES covers active and delisted; corporate-actions feed includes delistings. |
| point_in_time_sector | marketed_unverified | ZACKS/MT zacks_x_sector_code/desc; sector history not probed. |
| local_research_license | marketed_unverified | Direct Zacks Data or Nasdaq Data Link / WRDS premium; terms require sales contact. |

Blocking gaps:
- free trial sample verification required before DATA_PASS attempt (paid license needs contract amendment)
- table codes ZEEH/ZACKS/EEH + ZES/ZACKS/ES verified on Nasdaq URLs 2026-07-07; ZEE/ZEA are not substitutes
- residual obs_date calendar timezone (NY vs UTC) unspecified; intraday ordering on one obs_date unknown
- quantify same-day obs_date == act_rpt_date collisions — likely BTO/DTM-heavy; strict join may fail 30-OOS-events gate
- do not use ZACKS/ES eps_pct_diff_surp; recompute surprise from joined EEH eps_mean_est
- BNRI methodology must be pinned; ZEE snapshot and ZEA forward calendar are insufficient
- enterprise license terms unverified for local pin + verify_pead_data workflow

## Owner decision required

To move from BLOCKED toward `DATA_PASS`, the owner must authorize **one** paid
source sample or trial so the project can:

1. Pin a non-synthetic snapshot under `research/new_edge/pead/data/pinned/`.
2. Run `verify_pead_data` on the pinned snapshot.
3. Confirm estimate observation timestamps, survivorship-safe master, and coverage gates.

**Leading paid candidates (desk order):**

1. **Zacks Data** — explicitly markets point-in-time consensus, reference entity data,
   delistings, and prices. Requires request-access / enterprise quote.
2. **EODHD extended fundamentals + calendar** — lower cost; public docs still lack
   estimate observation timestamps; verify before purchase.

Do not purchase both. Pick one vendor, obtain a sample, then append a new ledger row.

## Issues

- no licensed local or institutional PEAD snapshot found in repo
- no earnings/pead/ibes/zacks files found on prod via ssh search
- no free or probed source exposes estimate observation timestamps
- owner approval required before paid trial: Zacks (point-in-time marketed) or EODHD extended fundamentals/calendar bundle

## Next authorized command

After a pinned licensed snapshot exists:

```bash
python -m research.new_edge.pead.data.verify_pead_data \
  --input research/new_edge/pead/data/pinned/<snapshot> \
  --provenance research/new_edge/pead/data/provenance/<snapshot>.json \
  --start 2016-01-01 --end 2026-01-01 \
  --output docs/research/pead/PEAD_DATA_MANIFEST_2026-07.md
```

Machine-readable inventory: `research/new_edge/pead/data/provenance/pead_source_audit_inventory_2026-07.json`
