# Event / Calendar Research Lane

Macro event timing edge — data proof phase only.

## Contract

`docs/research/events/EVENT_CONTRACT_2026-06-18.md`

## Commands

```bash
# Pin HF snapshot (once; CSV is gitignored)
python -m research.new_edge.events.data.pin_hf_calendar --tag 2026-06-18

# Data proof (pinned historical snapshot — primary path)
python -m research.new_edge.events.data.verify_event_data \
  --input research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv \
  --provenance research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.provenance.json \
  --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-19.md
```

## Status

**DATA_PASS** (2026-06-19). See ledger + `docs/research/events/EVENT_DATA_MANIFEST_2026-06-19.md`.
Next: gross-first falsifier only (no optimization yet).