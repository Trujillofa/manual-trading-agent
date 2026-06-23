# Event / Calendar Data Manifest — 2026-06-18

## Verdict: **BLOCKED**

## Command
```bash
python -m research.new_edge.events.data.verify_event_data --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-18.md
```

## XML source: offline_sample (live status: rate_limited_429; using offline sample)

## Historical feed probe

- `https://nfs.faireconomy.media/ff_calendar_lastweek.xml` → HTTP 404 (available=False)
- `https://nfs.faireconomy.media/ff_calendar_nextweek.xml` → HTTP 404 (available=False)
- `https://nfs.faireconomy.media/ff_calendar_lastmonth.xml` → HTTP 404 (available=False)

## Field coverage (live/sample XML)

- Total events: 3
- High impact: 3
- Has title: 3/3
- Has country (currency code): 3/3
- Has currency tag (parser expects): 0/3
- Has date: 3/3
- Has time: 3/3
- Has forecast: 2/3
- Has previous: 2/3
- Has actual: 0/3
- NewsChecker parse success: 0/3 (0.0%)

### Parser failure reasons

- missing_currency_tag_uses_country_instead: 3

## Look-ahead audit

### forecast_previous
Available pre-event in live XML. Safe for scheduled lockout / avoidance only. Using forecast as a live surprise signal without timestamp discipline risks look-ahead if values are revised between scrape and release.

### actual
NOT present in live faireconomy thisweek XML sample. Surprise-based lanes require a historical source with timestamped actual publication times. Scraping actual after the fact without release-time metadata is look-ahead leakage.

### production_parser
NewsChecker._parse_event_node requires <currency> and YYYY-MM-DD dates. Live feed uses <country> for currency codes and MM-DD-YYYY dates. Production live feed parsing is currently incompatible with the published XML schema.

## Spread widening model (conservative)

- base_spread_pips_majors: 2.0
- release_window_minutes: 15
- release_spread_multiplier: 3.0
- release_spread_pips: 6.0
- release_slippage_pips_per_side: 1.0
- round_trip_cost_pips_conservative: 14.0
- note: Conservative default for data-proof phase. Any post-event drift lane must show expected move exceeds this round-trip on median high-impact events.

## Blocking issues

- No historical calendar feed available from faireconomy URLs (thisweek only; lastweek/nextweek/lastmonth return 404).
- NewsChecker parser success rate 0.0% < 95% on feed XML (top failure: missing_currency_tag_uses_country_instead).
- No <actual> field in feed XML; surprise lanes blocked without external historical source.
- Feed uses <country> for currency codes; production parser expects <currency> tag.

## Next step

Obtain a verified **historical** economic calendar (≥5 years, timestamped actual/forecast) from a documented third-party archive (e.g. checked-in CSV with provenance). Fix or bypass NewsChecker schema mismatch. Re-run this verifier. **Do not write event strategy or backtest until manifest passes.**