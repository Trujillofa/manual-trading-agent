# Carry Gross Falsifier Results - 2026-06-12 (sample data)

## Verdict
GROSS_PASS (sample data only)

## Exact command run
python -m research.new_edge.carry.gross_carry_test --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md

## Git branch
docs/profitability-plan-2026-06

## Data sources and assumptions
- OHLC daily closes + vol: yfinance (lightweight daily, same as data verifier --quick). Aligned common trading days.
- Swap/financing + rollover: checked-in research/new_edge/carry/data/verified_swap_rates_2026-06.json ("Verified from broker statement sample... For illustration only; in real use, replace with actual broker API or statement data and re-verify.")
- Rollover applied: rate * 3 on Wednesdays (date.weekday()==2), *1 otherwise. (No holiday exceptions in this skeleton.)
- Sizing: static (full-history ann vol from price returns), risk-parity-ish per leg (target 10% portfolio ann vol / 8 legs), constant lots (minimal turnover = initial entry only).
- Universe & legs: top 4 by long_rate to LONG, bottom 4 to SHORT.
- Costs (gross carry net of...): entry/turnover drag only = 3.0 pips (spread+slippage) * pip_value per lot changed at start (per CARRY_CONTRACT cost model + settings spread_limits ~2-3 pips).
- Price P&L: deliberately IGNORED (first falsifier scope).
- Capital ref: $100,000; pip value ref: $10.0; lot notional ref: $100,000.
- Period actually simulated: 2016-01-01 to 2026-05-29 (2708 trading days).

**Sample data caveat (per source JSON and user review):** This is a sample-data gross carry falsifier only. The rates produce a "plausible positive carry" shape but are not live broker verified for this run. Any GROSS_PASS is illustrative of the method and the premise on these numbers; real rates from broker statements or API must replace the JSON and the test re-run before the lane can advance past sample.

## Legs and sizing (constant lots from full-sample vol)
Long legs (top by long carry): ['USD/TRY', 'EUR/TRY', 'GBP/TRY', 'USD/ZAR']
Short legs (bottom by long carry): ['AUD/JPY', 'NZD/JPY', 'AUD/USD', 'NZD/USD']

Lots (signed for short legs):
- USD/TRY: 0.071 lots (ann_vol=0.176)
- EUR/TRY: 0.069 lots (ann_vol=0.181)
- GBP/TRY: 0.066 lots (ann_vol=0.189)
- USD/ZAR: 0.061 lots (ann_vol=0.204)
- AUD/JPY: -0.111 lots (ann_vol=0.113)
- NZD/JPY: -0.116 lots (ann_vol=0.108)
- AUD/USD: -0.125 lots (ann_vol=0.100)
- NZD/USD: -0.123 lots (ann_vol=0.102)


## Gross carry metrics (financing only + entry drag) - LEG-LEVEL ACCOUNTING
- Trading days: 2708
- Total positive carry $ (income from legs with positive daily swap): $111,607.47
- Total negative carry $ (funding costs from legs with negative daily swap): $29,702.71
- Gross carry (pos - neg): $81,904.76
- Initial entry drag $: $22.27
- Net carry after drag (and after all leg-level funding costs): $81,882.49
- Carry gross PF (pos / (neg + drag)): 3.755
- Max DD on cumulative carry equity (price risk not included; net daily carry): 0.00%

**Accounting note:** Positive and negative are now summed from *individual leg/day* contributions (long legs produce positive carry income; short legs produce negative carry = funding cost). This is the correct gross carry falsifier view even when net daily portfolio carry is always positive due to swamping. The equity curve / DD still reflect the net carry P&L to the book.

## Per-pair exact carry contribution (accumulated leg-by-leg with daily rollover applied)
- USD/TRY (LONG): $41,608.17 (rate=+15.5)
- EUR/TRY (LONG): $31,462.95 (rate=+12.0)
- GBP/TRY (LONG): $28,763.60 (rate=+11.5)
- USD/ZAR (LONG): $9,772.75 (rate=+4.2)
- NZD/USD (SHORT): $-4,896.84 (rate=+-1.05)
- AUD/USD (SHORT): $-5,940.54 (rate=+-1.25)
- NZD/JPY (SHORT): $-9,020.26 (rate=+-2.05)
- AUD/JPY (SHORT): $-9,845.07 (rate=+-2.35)


## Notes on implementation (smallest per scope)
- Daily loop over aligned trading days applies rollover and accrues carry $ *per leg* for separate pos/neg tracking.
- Ranks and lots fixed (static portfolio) => turnover drag only at t=0.
- If daily vol targeting + rebalance were used, turnover drag would be higher (future refinement after this falsifier).
- No optimization, no filters, no OOS split (gross-first diagnostic only).
- Next if GROSS_PASS on real data: add realistic costs beyond entry, chronological IS/OOS split, robustness (carry crash periods), concentration, then net + full gates.

## Verdict after gross falsifier
GROSS_PASS (sample data only)

Gross positive on sample verified rates (illustration only). Real broker statement/API rates + re-verify required before any IS/OOS or promotion consideration.

This run used the checked-in sample swap table as input source (per explicit scope). Do not start stat-arb. Real broker data is the next unblock if this step passes on sample.
