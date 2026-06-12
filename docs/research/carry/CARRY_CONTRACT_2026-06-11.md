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
- Daily OHLC: Representative prior-run coverage snapshot is recorded in CARRY_DATA_MANIFEST_2026-06-11.md. All 8 tested carry-positive pairs show 2658-2728 d1 bars (2016-01-04 to 2026-06-05), weekday zero rates 0.87-1.21% (well under 5% gate). Fresh exact numbers should be produced by rerunning the verifier with `--quick` when network access is available, or without `--quick` when cached Dukascopy data is available.
- Swap data: NOT INTEGRATED. Broker-specific, changes over time with rate decisions. Static table is for initial contract only. No fetcher or live data in dukascopy/settings/oanda.
- Rollover: Documented as standard 3x Wed rule; no code yet to apply 3x or holiday exceptions.

**Verdict for data verifier: BLOCKED**

Data for OHLC appears available and usable based on prior coverage snapshots, but the carry lane remains blocked until a fresh verifier run and broker swap source exist.
Swap units and exact broker rollover not present in code/config -> cannot run even gross carry backtest without adding data source.
Next: Add swap data fetcher or verified static table + rollover calendar before strategy code.

No strategy code written.
