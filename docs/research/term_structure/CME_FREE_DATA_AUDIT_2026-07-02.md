# CME Free Settlement Data Audit — 2026-06-30

## Verdict: BLOCKED

This is a Tier-A data verdict only. It does not test roll yield and does not
authorize strategy simulation, parameter changes, or live trading.

## Command

```bash
python -m research.new_edge.term_structure.data.verify_term_structure_data --output docs/research/term_structure/CME_FREE_DATA_AUDIT_2026-07-02.md --provenance research/new_edge/term_structure/data/provenance/term_structure_source_gate_2026-07.json
```

## What works

- Anonymous CME FTP access works without credentials.
- The adapter downloads final expanded PA2 files and parses type-P plus paired
  type-81/type-82 records into decimal contract-month settlements.
- Positive and negative settlements are preserved without ratio adjustment.
- The normalized CSV includes source file and SHA256 provenance per row.
- Parser smoke: 0 settlements across 0/12 fixed-universe markets.

## Hard-gate result

- First public archive date: `2014-01-02`
- Last public archive date: `2025-09-12`
- Observed archive files: 3,043
- Observed coverage: 11.69 years
- Required coverage: 15 complete years per market
- Passing markets: 0/12 (minimum 10)
- Required daily fields: open, high, low, settle, open_interest
- PA2 fields available: settle

## Blocking evidence

- public SPAN archive coverage is 11.69y; 15 complete years required
- expanded PA2 settlement files do not contain required fields: high, low, open, open_interest
- contract-month open interest is unavailable, so the OI-confirmed roll calendar cannot be derived

## Source

- Public FTP root: `ftp://ftp.cmegroup.com/span/archive/cme/`
- CME SPAN page: https://www.cmegroup.com/clearing/risk-management/span-overview.html
- Official expanded PA2 layout: https://cmegroupclientsite.atlassian.net/wiki/spaces/pubsub/pages/457083445/Risk+Parameter+File+Layouts+for+the+Positional+Formats
- CME type-81 records carry high-precision settlement prices.
- CME type-82 records carry the settlement sign used for negative prices.
- CME type-P records carry decimal locators and contract value factors.

## Decision

The free CME PA2 path is implemented, but it cannot produce `DATA_PASS` under
the existing lane contract. Bulk downloading the archive would consume substantial
bandwidth and storage without repairing either hard blocker, so the verifier stops
at source inventory plus parser smoke.

Do not start Tier B. Continue only if a free official source is found for
contract-month OHLC/open interest with at least 15 complete years, or if the owner
authorizes a paid individual-contract source. Do not relax the 15-year or OI gates.

Machine-readable provenance: `research/new_edge/term_structure/data/provenance/term_structure_source_gate_2026-07.json`
