# Event / Calendar Data Manifest — HF snapshot (2026-06-19)

## Verdict: **DATA_PASS**

## Command
```bash
python -m research.new_edge.events.data.verify_event_data --input research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv --provenance research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.provenance.json --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-19.md
```

## Historical snapshot

- Input: `research/new_edge/events/data/pinned/forex_factory_calendar_hf_2026-06-18.csv`
- Rows: 83,427
- SHA256 (computed): `7e0a247e1a9a23316f775c433a8bb31129a891b6f4813231e485f15d38526a23`
- SHA256 (recorded): `7e0a247e1a9a23316f775c433a8bb31129a891b6f4813231e485f15d38526a23`
- SHA256 match: True
- Source: https://huggingface.co/datasets/Ehsanrs2/Forex_Factory_Calendar
- Pinned at: 2026-06-18T19:28:27.321185+00:00

## Coverage gates

- Date range (UTC): 2007-01-01 01:00:00+00:00 → 2025-04-07 18:00:00+00:00
- Years coverage: 18.26 (gate ≥ 5)
- High-impact events: 17,842 (gate ≥ 200)
- High-impact non-economic events: 17,842
- Indicator-class high-impact events (coverage gate): 9,806

## Timezone audit

- Timestamp parse OK: 83,427/83,427 (100.0%; gate ≥ 95%)
- Parse failures: 0
- Missing timestamp rows: 0

### Original offset distribution (pre-UTC normalization)

- `+03:30`: 44,385
- `+04:30`: 39,042

## Field coverage (indicator-class high-impact events — surprise-lane population)

- actual: 99.8% (gate ≥ 80%)
- forecast: 96.8% (gate ≥ 80%)
- previous: 99.8% (gate ≥ 80%)

## Field coverage (all high-impact non-economic — informational)

- actual: 73.3%
- forecast: 70.3%
- previous: 73.1%

## Currency audit

- Valid currency rows: 83,427/83,427
- Invalid currency rows: 0

## Duplicate / integrity checks

- Duplicate keys (datetime+currency+event): 12
- Rows involved in duplicates: 21

- Informational: 12 duplicate keys (21 rows); review before deduplication in research adapter.

## Event family counts (indicative)

- pmi: 7,556
- rate_decision: 5,751
- cpi: 5,031
- gdp: 2,346
- nfp: 489

## Look-ahead audit

### forecast_previous
Forecast and Previous are treated as pre-release scheduled values in the HF archive. Safe for scheduled lockout / avoidance research when aligned to event datetime_utc. Revisions between scrape and release are possible; live use needs scrape-time discipline.

### actual
Actual is post-release historical truth in this archive. Valid for backtest outcome labels and surprise measurement only when release-time discipline is defined: compare Actual to Forecast at or after datetime_utc, never before. Do not use Actual for pre-release decision logic unless publication timing is independently auditable.

### production_parser
Live faireconomy XML remains incompatible with NewsChecker (<country> vs <currency>, date format mismatch, no <actual>). Historical research uses the pinned snapshot; production lockout still needs a separate live-feed fix.

### provenance
Third-party community scrape (HuggingFace Ehsanrs2/Forex_Factory_Calendar). Pinned locally with SHA256; not an official Forex Factory API. Re-verify checksum before any research run.

## Spread widening model (conservative)

- base_spread_pips_majors: 2.0
- release_window_minutes: 15
- release_spread_multiplier: 3.0
- release_spread_pips: 6.0
- release_slippage_pips_per_side: 1.0
- round_trip_cost_pips_conservative: 14.0
- note: Conservative default for data-proof phase. Any post-event drift lane must show expected move exceeds this round-trip on median high-impact events.

## Next step

Historical data proof passed. Proceed to smallest gross-first event falsifier (avoidance or post-release drift) per EVENT_CONTRACT. Still no optimization. Actual is label-only after release-time discipline.