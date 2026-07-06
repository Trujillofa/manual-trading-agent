# PEAD Data Manifest — 2026-07

## Verdict: BLOCKED

Data-proof only. This manifest does not authorize relationship code, strategy
simulation, parameter search, or production integration.

## Command

```bash
python -m research.new_edge.pead.data.verify_pead_data --input research/new_edge/pead/data/fixtures/synthetic_minimal --provenance research/new_edge/pead/data/provenance/pead_source_audit_2026-07.json --start 2016-01-01 --end 2026-01-01 --output docs/research/pead/PEAD_DATA_MANIFEST_2026-07.md
```

## Snapshot summary

- Source label: `synthetic_minimal_fixture`
- Requested window: `2016-01-01` → `2026-01-01`
- Total events in window: 4
- Eligible events: 4
- Peak eligible stocks: 3
- Years covered: 5.54
- Minimum yearly field completeness: 100.0%
- Tradable session mapping rate: 100.0%
- Stable security-id rate: 100.0%
- Prospective OOS quintile minimum: 0

## Gate thresholds

- Minimum history: 10 years
- Minimum eligible stocks: 500
- Minimum yearly field completeness: 80%
- Minimum tradable session mapping: 95%
- Minimum stable security-id coverage: 90%
- Minimum prospective OOS quintile events: 30

## Issues

- coverage window is 5.54y; at least 10y required
- eligible stock count peaks at 3; at least 500 required
- prospective OOS extreme-quintile events min 0; at least 30 required

## Decision

Relationship logic remains unauthorized until the research ledger records `DATA_PASS` for this lane.

Machine-readable provenance: `research/new_edge/pead/data/provenance/pead_source_audit_2026-07.json`
