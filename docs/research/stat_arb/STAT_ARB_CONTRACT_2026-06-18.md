# FX Statistical Arbitrage Pairs contract — 2026-06-18

## Premise

Trade relative mispricing between economically linked FX pairs, not outright directional OHLC patterns.
The edge source is mean-reversion in a rolling hedge-ratio spread (cointegration-style residual), entered
only when the z-score is extreme and expected reversion exceeds two-leg friction.

This is structurally different from the closed FX directional TA (MTF RSI / ORB / trend-pullback) and
daily TSMOM families, which bet on absolute momentum or reversal in single instruments.

## Why this is not a closed lane

- FX directional TA (M15/H1): closed — gross PF ~1.0–1.07, no edge before costs.
- Daily multi-asset TSMOM: closed — gross PF 1.036, Sharpe 0.15, no accessible edge before costs.
- Carry / swap (Hetzner cTrader account): DISCARD — all resolved pairs returned 0.0 swap; financing premise absent.

Stat-arb trades **relative** price residuals between two legs. It does not use RSI alignment, Donchian
breakouts, ADX regime filters, or overnight financing as the primary signal.

## Candidate spreads (phase 1)

| Spread ID | Leg A | Leg B | Rationale |
|---|---|---|---|
| `eur_gbp` | EUR/USD | GBP/USD | Classic dollar-block residual; high liquidity |
| `aud_nzd` | AUD/USD | NZD/USD | Commodity-currency pair with strong correlation |
| `cad_aud_jpy` | CAD/JPY | AUD/JPY | Shared JPY leg, commodity FX linkage |

Phase 1 tests daily closes only. Intraday refinement is out of scope until gross daily edge exists.

## Data required

- Synchronized daily OHLC closes for both legs of each spread.
- Minimum 8 years aligned history (2016-01-01 → 2026-06-01) for gross-first diagnostics.
- Missing-data handling: strict `dropna` on aligned calendar; report overlap bar count.
- Two-leg spread cost model documented before any net run.

Sources: yfinance daily (lightweight, reproducible — same path as carry verifier `--quick`).
Dukascopy M1 resampled daily available for heavier verification if yfinance gaps appear.

## Cost model (for net runs only; gross-first uses zero friction)

- Entry: `2 × spread_pips` (one cost per leg) from `config/settings.yaml` spread_limits (~2.0 pips majors).
- Exit: `2 × spread_pips` per round-trip close.
- Slippage: 0.5 pip per leg (conservative) = 1.0 pip total per side.
- Round-trip all-in: ~10 pips for two-leg major pair trade (entry + exit, both legs).
- No swap modeled in phase 1 (short holding periods; daily rebalance not used).

## First falsification test

For each candidate spread, run a minimal daily pairs-trade backtest:

- Rolling OLS hedge ratio (60-day lookback on log closes).
- Z-score of spread residual (60-day rolling mean/std).
- Enter long spread when z < −2.0; enter short spread when z > +2.0.
- Exit when z crosses 0 or 20-bar time stop.
- **Gross only**: no spread, slippage, or commission deducted.
- Single parameter set; no optimization.

**Falsified if:** gross PF ≤ 1.05 across all candidate spreads, or trade count < 30 on any spread
that claims pass (too sparse to validate).

## Pass gate (gross-first)

- At least one spread shows gross PF > 1.10 with ≥ 30 round-trip events over full sample.
- Gross edge is not concentrated in a single calendar year (> 50% of gross profit from one year → stop).
- Spread half-life (ADF or AR(1) on residuals) is finite and stable in first/second half of sample.

## Pass gate (net / OOS — only after gross pass)

- Chronological 70/30 IS/OOS split; OOS gross PF > 1.05 before costs.
- Net OOS PF ≥ 1.20 after two-leg costs.
- OOS trade count ≥ 30 (or pre-written slower-strategy bar).
- No single spread contributes > 60% of net OOS profit.

## Stop gate

- Gross PF ≈ 1.0 on all spreads → DISCARD lane.
- Net edge vanishes after two-leg costs on spreads that passed gross.
- Hedge ratio unstable (rolling beta sign-flips > 30% of windows).
- Returns cluster in one historical episode (e.g. March 2020 only).

## First command

```bash
python -m research.new_edge.stat_arb.data.verify_stat_arb_data \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/stat_arb/STAT_ARB_DATA_MANIFEST_2026-06-18.md
```

Then:

```bash
python -m research.new_edge.stat_arb.gross_stat_arb_test \
  --start 2016-01-01 --end 2026-06-01 \
  --output docs/research/stat_arb/STAT_ARB_GROSS_RESULTS_2026-06-18.md
```

## Verification status

- Pending first run of data verifier and gross falsifier.