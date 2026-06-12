# Carry / Swap-Aware FX Portfolio RESULTS - 2026-06-11

## Verdict
BLOCKED

## Exact command run
python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md

## Git commit or worktree branch
main (docs-only scaffolding + contract + verifier; no worktree created yet per plan to use isolated for full runs)

## Data sources and date ranges
- OHLC: dukascopy_fetcher (existing, supports pairs in settings.yaml majors/minors). 2016-01-01 to 2026-06-01.
- Swap: STATIC TABLE ONLY (typical broker values). No integration.
- Rollover: documented standard 3x Wed rule.

## Gross result before costs
N/A (data for swap not present; OHLC coverage verified sufficient).

## Realistic net result after costs
N/A.

## Chronological IS/OOS result
N/A.

## Trade count or event count
N/A (no backtest run).

## Pair, instrument, or event contribution table
N/A.

## Drawdown and concentration checks
N/A.

## Failure reason if discarded
Data verification: swap data source not integrated (no broker fetcher or verified table in dukascopy/settings or costs). OHLC coverage ok via existing fetchers (consistent with prior multiasset work). Rollover rules standard but not coded.

## Next action
Add swap data source (e.g. OANDA API wrapper or verified static table + rollover calendar) to data layer. Re-run verifier. Only then implement gross carry backtest per contract.

This is the first BLOCKED/KEEP/DISCARD-ready artifact per the Grok loop (contract + manifest + ledger entry + this results doc). No strategy logic written.

Ledger entry appended to research/new_edge/research_ledger.jsonl .