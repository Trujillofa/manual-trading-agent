# Term-Structure Tier-A Data Pipeline

This package implements the owner-selected free CME path from the roll-yield data manifest.
It is research-only and does not touch the live forex scanner.

## Normalize one settlement archive

Run:

```bash
python -m research.new_edge.term_structure.data.cme_span \
  --date 2025-09-12 \
  --output-dir /tmp/cme-settlements
```

## Audit the free source before bulk downloading

Run:

```bash
python -m research.new_edge.term_structure.data.verify_term_structure_data \
  --sample-archive /tmp/cme.20250912.s.pa2.zip \
  --sample-date 2025-09-12 \
  --output docs/research/term_structure/CME_FREE_DATA_AUDIT_2026-06-30.md \
  --provenance research/new_edge/term_structure/data/provenance/cme_free_data_audit_2026-06-30.json
```

The command exits with status 2 and records `BLOCKED`. Public PA2 archives contain contract
settlements but not daily OHLC or contract-month open interest, and the observed public archive
is shorter than the required 15 complete years.

Keep Tier B stopped. Don't relax the history or open-interest gates.
