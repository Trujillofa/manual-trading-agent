# Carry / Swap-Aware FX Portfolio contract - 2026-06-11

## Premise
The edge comes from the interest rate differential / broker overnight financing (swap) paid or received for holding FX positions, not from predicting price direction or momentum via OHLC patterns. Long pairs with positive expected net carry (after broker swap), short negative carry pairs, sized by volatility, with filters for extreme volatility or risk-off regimes. This is a structurally different edge source from the closed directional OHLC TA and daily TSMOM families.

## Why this is not a closed lane
- FX directional TA (M15/H1): closed because gross PF ~1.0-1.07 with no edge before costs on price patterns.
- Daily multi-asset TSMOM: closed because gross PF 1.036 / Sharpe 0.15 on long history with real diversification but no accessible edge before costs.
This lane uses financing rates as the signal (carry trade), daily horizon, portfolio construction based on relative carry, not absolute price trend or MTF alignment.

## Data required
- Broker long/short swap rates or financing charges per pair (in account currency per standard lot or per pip, updated daily or from statements).
- Central bank policy rates (for sanity, not primary).
- Daily OHLC (open, high, low, close) for risk management, volatility targeting, drawdown simulation, and entry/exit prices. Minimum 5-10 years for IS/OOS.
- Rollover calendar (typically 3x swap on Wednesdays for most pairs; exceptions for holidays).

Sources: Broker API (OANDA/cTrader from existing config), static table from broker statements for verification, Dukascopy or yfinance for daily OHLC (existing fetchers).

## Cost model
- Spread: per-pair limits from settings (e.g. 2.0-3.0 pips for majors).
- Commission: broker specific (often included in spread for retail CFD/FX).
- Slippage: conservative 0.5-1 pip on entry for backtest.
- Swap/financing: the primary "return" (positive or negative daily).
- Rollover: 3x on Wed (or as per broker calendar).
- Two-leg for pairs trades if used.
- No borrow for FX.

## First falsification test
Check if a static positive-carry portfolio (long top carry pairs, short bottom, vol-targeted, daily rebalance) produces positive gross carry return (swap income minus spread/slippage) over a multi-year period before any price P&L. If net carry after costs is negative or near zero on average, the premise is falsified.

## Pass gate
- Gross carry (swap income net of spread/slippage on entries) positive over the test period.
- Net OOS (after all costs) positive or portfolio Sharpe/MAR meets pre-defined bar (e.g. Sharpe > 0.5 or PF >=1.20 for the carry component).
- Acceptable drawdown under carry-crash stress (e.g. 2008-style or 2015 CHF event simulation).
- Not concentrated in 1-2 pairs.

## Stop gate
- Broker swap advantage disappears or reverses after realistic rollover/spread/slippage.
- Returns come only from one regime, one pair, or one episode.
- Tail drawdowns (carry unwinds) dominate the average carry.
- Data for swap units or rollover is missing or unverified.

## First command
python -m research.new_edge.carry.data.verify_carry_data --start 2016-01-01 --end 2026-06-01 --output docs/research/carry/CARRY_DATA_MANIFEST_2026-06-11.md --quick

This will check daily OHLC coverage via yfinance daily (lightweight --quick mode) for carry pairs (default or --pairs) and document swap/rollover assumptions (static table + note on broker source needed). Use --quick for proof runs to avoid heavy M1 downloads.

## Verification status
- Daily OHLC: Fresh run via `python -m research.new_edge.carry.data.verify_carry_data ... --quick` on 2026-06-12 captured real yfinance daily coverage. All 8 pairs: 2708-2710 d1_bars (2016-01-01 to ~2026-05-29), ok=True. See CARRY_DATA_MANIFEST_2026-06-11.md for exact per-pair output from this run. (Dukascopy M1 path remains available for heavier verification if needed.)
- Swap data: VERIFIED. Loaded from checked-in broker statement sample `research/new_edge/carry/data/verified_swap_rates_2026-06.json`. Source note and rollover rule included in the json. `verify_swap_data` confirms presence + positive long rates for all CARRY_POSITIVE_PAIRS. Units: pips/day/standard lot.
- Rollover: 3x on Wednesdays (most pairs) per the verified table (holiday exceptions broker-specific, to be handled in future gross test harness).

**Verdict for data verifier: BLOCKED** (real broker swap/rollover data not yet provided).

Current `research/new_edge/carry/data/verified_swap_rates_2026-06.json` is a template with illustrative rates (explicitly marked TEMPLATE / illustration). See the new `REAL_BROKER_DATA_INSTRUCTIONS.md` next to the JSON for exact fields required (source_date, broker, retrieved, notes on holidays/rollover, actual rates).

The gross falsifier harness (leg-level accounting, rollover, vol targeting, entry drag) has been implemented and passes on the sample for methodology validation (GROSS_PASS (sample data only), PF 3.755, large net positive carry after funding costs + drag).

Next gated step (per user guidance): Replace the JSON with real broker statement or API export, then re-run:
1. verify_carry_data.py --quick
2. gross_carry_test.py
3. New ledger row with GROSS_PASS_REAL_DATA or DISCARD

Only after real data still produces a clean gross pass may we add price P&L, more complete costs, chronological IS/OOS, or carry-crash stress.

No strategy code written.
