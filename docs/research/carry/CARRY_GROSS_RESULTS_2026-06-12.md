# Carry Gross Falsifier Results - 2026-06-12 (sample data)

## Verdict
DISCARD_REAL_DATA

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

**Real data gate (per CARRY_CONTRACT and user guidance):** Current run uses the template/placeholder rates in the JSON. The lane remains blocked on data until the JSON is replaced with actual broker statement or API export (filled source_date, broker, retrieved, notes, and accurate rates). Only a subsequent run with is_real_data=True will produce GROSS_PASS_REAL_DATA (or DISCARD). Any GROSS_PASS (sample) is purely for validating the gross falsifier skeleton and leg-level accounting.

## Legs and sizing (constant lots from full-sample vol)
Long legs (top by long carry): ['AUD/JPY', 'NZD/JPY', 'AUD/USD', 'NZD/USD']
Short legs (bottom by long carry): ['NZD/JPY', 'AUD/USD', 'NZD/USD', 'USD/ZAR']

Lots (signed for short legs):
- AUD/JPY: 0.111 lots (ann_vol=0.113)
- NZD/JPY: -0.116 lots (ann_vol=0.108)
- AUD/USD: -0.125 lots (ann_vol=0.100)
- NZD/USD: -0.123 lots (ann_vol=0.102)
- NZD/JPY: -0.116 lots (ann_vol=0.108)
- AUD/USD: -0.125 lots (ann_vol=0.100)
- NZD/USD: -0.123 lots (ann_vol=0.102)
- USD/ZAR: -0.061 lots (ann_vol=0.204)


## Gross carry metrics (financing only + entry drag) - LEG-LEVEL ACCOUNTING
- Trading days: 2708
- Total positive carry $ (income from legs with positive daily swap): $0.00
- Total negative carry $ (funding costs from legs with negative daily swap): $0.00
- Gross carry (pos - neg): $0.00
- Initial entry drag $: $27.03
- Net carry after drag (and after all leg-level funding costs): $-27.03
- Carry gross PF (pos / (neg + drag)): 0.000
- Max DD on cumulative carry equity (price risk not included; net daily carry): 0.00%

**Accounting note:** Positive and negative are now summed from *individual leg/day* contributions (long legs produce positive carry income; short legs produce negative carry = funding cost). This is the correct gross carry falsifier view even when net daily portfolio carry is always positive due to swamping. The equity curve / DD still reflect the net carry P&L to the book.

## Per-pair exact carry contribution (accumulated leg-by-leg with daily rollover applied)
- AUD/JPY (LONG): $0.00 (rate=0.0)
- NZD/JPY (LONG): $0.00 (rate=0.0)
- AUD/USD (LONG): $0.00 (rate=0.0)
- NZD/USD (LONG): $0.00 (rate=0.0)
- USD/ZAR (SHORT): $0.00 (rate=0.0)


## Notes on implementation (smallest per scope)
- Daily loop over aligned trading days applies rollover and accrues carry $ *per leg* for separate pos/neg tracking.
- Ranks and lots fixed (static portfolio) => turnover drag only at t=0.
- If daily vol targeting + rebalance were used, turnover drag would be higher (future refinement after this falsifier).
- No optimization, no filters, no OOS split (gross-first diagnostic only).
- Next if GROSS_PASS on real data: add realistic costs beyond entry, chronological IS/OOS split, robustness (carry crash periods), concentration, then net + full gates.

## Verdict after gross falsifier
DISCARD_REAL_DATA

Gross carry (net of leg-level funding + drag) <=0 or PF<=1.0 even with real broker data.

This run used the *template* swap rates. Replace the JSON with real broker data and re-run both verifier and gross test to obtain GROSS_PASS_REAL_DATA (or DISCARD). Only then consider price P&L, additional costs, chronological IS/OOS, or carry-crash stress tests.
