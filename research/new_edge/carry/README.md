# Carry / Swap-Aware FX Portfolio lane (new_edge)

This is the first lane in the Grok-driven new edge program per GROK_RESEARCH_LOOP_ENGINEERING.md and PROFITABILITY_PLAN_2026-06.md.

## Status
Contract written 2026-06-11. Data verified (fresh --quick + checked-in sample swap rates). Gross falsifier run 2026-06-12: GROSS_PASS (sample data only) with very large positive carry PF on illustration rates (net ~$81.8k after $22 drag over ~2708 days). Real broker statement/API data still required to unblock beyond sample. No strategy logic yet.

## Structure
- docs/research/carry/ : contracts, results, manifests
- research/new_edge/carry/ : code (data/ only for now)
- research/new_edge/research_ledger.jsonl : machine readable memory

## Current focus
Gross carry falsifier (first falsification test per contract) complete on sample data. Next real unblock requires actual (non-illustration) broker swap/rollover data + re-run of gross test + IS/OOS etc.

See CARRY_CONTRACT_2026-06-11.md and CARRY_GROSS_RESULTS_2026-06-12.md .

## How to run data verification
python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick

## How to run gross carry falsifier (smallest, sample data)
python -m research.new_edge.carry.gross_carry_test --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_GROSS_RESULTS_2026-06-12.md
(Produces metrics, PF on carry financing net of entry drag, per-leg contrib, carry equity DD. Ignores price P&L. Rollover *3 Wed applied.)

## Notes
- Uses existing dukascopy_fetcher and yfinance for OHLC.
- Swap data will require new source (broker API or static verified table).
- All results will be gross-first, then net, with IS/OOS.
- Lane will be marked KEEP / DISCARD / BLOCKED with ledger entry before next lane.