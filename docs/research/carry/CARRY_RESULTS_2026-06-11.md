# Carry / Swap-Aware FX Portfolio RESULTS - 2026-06-11

## Verdict
BLOCKED

## Exact command run
python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick

## Git commit or worktree branch
docs/profitability-plan-2026-06 (carry data unblock on verified broker swap source)

## Data sources and date ranges
- OHLC: yfinance daily via verifier --quick (reproducible fast path; existing dukascopy_fetcher also available for full runs). 2016-01-01 to 2026-06-01 (yf sample end ~2026-05-29).
- Swap: VERIFIED static table from checked-in broker statement sample: research/new_edge/carry/data/verified_swap_rates_2026-06.json (rates + source + rollover_rule). Loaded and validated by verify_carry_data (positive long carry on target pairs).
- Rollover: 3x on Wednesdays per verified table (exceptions for holidays, broker specific).

## Gross result before costs
N/A (data verified; gross carry backtest / first falsification test per contract not yet implemented).

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
Gross carry test (first falsification per CARRY_CONTRACT) not yet implemented/run. Data verification PASSED in fresh --quick run (all 8 pairs OHLC ok via yf; swap rates VERIFIED from checked-in table with positive long for carry pairs; rollover documented). No strategy code written.

## Next action
Implement the smallest gross-only carry backtest (static positive-carry long/short portfolio, vol target, swap income net of entry spread/slippage only, ignore price P&L) per contract. Run it. Update manifest/results/ledger with gross metrics. Only then add full costs + IS/OOS if gross passes.

This is the first BLOCKED/KEEP/DISCARD-ready artifact per the Grok loop (contract + manifest + ledger entry + this results doc). No strategy logic written.

Ledger entry appended to research/new_edge/research_ledger.jsonl .