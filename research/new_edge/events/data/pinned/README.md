# Pinned event calendar snapshots

Large CSV snapshots are **gitignored** (65MB+). Provenance JSON is checked in.

## Re-download HF snapshot

```bash
python -m research.new_edge.events.data.pin_hf_calendar --tag 2026-06-18
```

## Verify data proof

```bash
python -m research.new_edge.events.data.verify_event_data \
  --input research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv \
  --provenance research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.provenance.json \
  --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-19.md
```

Expected SHA256 for the 2026-06-18 pin: `7e0a247e1a9a23316f775c433a8bb31129a891b6f4813231e485f15d38526a23`