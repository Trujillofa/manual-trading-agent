# Carry / Swap-Aware FX Portfolio lane (new_edge)

This is the first lane in the Grok-driven new edge program per GROK_RESEARCH_LOOP_ENGINEERING.md and PROFITABILITY_PLAN_2026-06.md.

## Status
Contract written 2026-06-11. Data verification in progress. No strategy logic yet.

## Structure
- docs/research/carry/ : contracts, results, manifests
- research/new_edge/carry/ : code (data/ only for now)
- research/new_edge/research_ledger.jsonl : machine readable memory

## Current focus
Verify broker swap units, rollover rules, daily OHLC coverage before any backtest or strategy code.

See CARRY_CONTRACT_2026-06-11.md for full details and first command.

## How to run verification
(After data verifier implemented)

## Notes
- Uses existing dukascopy_fetcher and yfinance for OHLC.
- Swap data will require new source (broker API or static verified table).
- All results will be gross-first, then net, with IS/OOS.
- Lane will be marked KEEP / DISCARD / BLOCKED with ledger entry before next lane.