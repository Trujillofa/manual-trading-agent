# FX Directional TA Negative Result — 2026-06

**Finding (locked):** After three rigorous full-365d gross-vs-net diagnostics on two structurally different entry families (plus H1 re-test of the stronger one) using the honest IS/OOS harness with costed P&L simulation on Dukascopy M1 resampled data for the 8 FX major/minor pairs, **no accessible gross edge was found** for directional TA-based mean-reversion or trend-pullback strategies on M15 (or H1) for these instruments.

Gross PF hovered at ~1.0–1.07 across thousands of trades (coin-flip or marginal before any costs). Net results were destroyed by realistic friction. Low frequency in the live strict MTF variant is structural, not a parameter issue. The live alerts are from the same no-edge family (just rarer).

This is a **valid, valuable negative result**. It terminates further investment in FX-majors directional OHLC TA (M15/H1) under the current approach.

## The Three Diagnostics

| Diagnostic | Bars (per pair) | Gross trades (pooled) | Gross PF | Gross pooled PnL | Net PF | Key observation |
|------------|-----------------|-----------------------|----------|------------------|--------|-----------------|
| ORB (M15 reversal breakout after London/NY opens) | ~35k 15m | ~6535 | ~1.02 | near 0 / small | ~0.3x | Coin-flip before costs; no regime selectivity |
| Trend-pullback momentum (ADX>25 + EMA pullback/reclaim) on M15 | ~35k 15m | 8298 | 1.07 | +363 | 0.19 | Slightly better gross on some pairs, but portfolio ~1.0; friction dominant |
| Trend-pullback on H1 (same logic, decisions on 1h bars) | ~8.7k 1h | 3870 | 1.01 | +30 | 0.53 | Lower relative friction helped winners but did not lift overall gross above ~1.0 |

**Live strict MTF RSI (the production scanner at acca11b):** Extremely sparse (~36 fires ever in prod audit over months; 0 trades in recent 30d+ harness samples on 8 pairs). Too few to validate statistically. From the same family.

**HTF pivot/Fibonacci directional TA (2026-06-30 archive):** A separate 4H-pivot / Fibonacci / EMA200 / RSI confirmation family on the same eight FX pairs (365d Dukascopy 15m, costed IS/OOS harness). IS-selected grid winner: OOS net PF 0.07, -11.53% OOS P&L, 12 OOS trades. Marker baseline and hardened MTF variants also failed. This does **not** reopen the lane — it is corroborating evidence that OHLC directional TA on FX majors lacks accessible edge even with a different timeframe structure (4H pivots, 15m execution). See `docs/research/HTF_FIB_NEGATIVE_RESULT_2026-06.md`.

## Gross-vs-Net Friction Analysis

- M15 ATR(14) for EUR/USD ~11 pips. Round-trip costs in the driver (~1.5 spread + 2 slip + commission) ≈3.5 pips adverse + fixed. This is ~30% tax on the stop distance.
- Breakeven WR for 1.5:1 RR (typical target) is ~40%. Realized WRs were 34–40% with no edge after costs.
- H1 ATR ~4× larger → relative friction drops sharply, yet gross PF remained ~1.01 portfolio-wide. The base signal (not costs) lacks sufficient edge.
- "Too consistent to be a tuning problem": three different theses (reversal-ORB, trend-pullback), two timeframes, full history, costed, held-out judge — all returned the same verdict. Correlated family of OHLC directional TA on FX majors at these frequencies.

## Why Donchian / Similar Baselines Are Skipped

The Donchian (volatility breakout) engine exists in the harness as a research baseline. It is a member of the same broad family (directional TA on OHLC for FX majors). Given the consistent ~1.0 gross across the two tested families and the explicit negative finding, running additional correlated variants (Donchian on M15/H1, more V* profiles, etc.) would be self-deception, not research. The harness is sacred; lowering gates or costs to "pass" is forbidden.

## Re-Entry Criteria (Falsifiable)

The negative result stands until **one** of the following occurs (documented with new full diagnostics under the same harness/judge):

1. **Different instrument class**: e.g., crypto majors, equity indices, or non-FX crosses with materially different microstructure.
2. **Different edge source**: carry, calendar effects, volatility regime, statistical arbitrage / cointegration, or order-flow / tick data features (not derived from standard OHLC TA).
3. **Non-OHLC / higher-fidelity data**: real dealable spreads, limit-order-book imbalance, or other microstructure signals that change the cost model or signal generation fundamentally.

"More parameters", "different confirmation", "cost tuning", or "optimizing on the 3 pairs that happened to print >1.1" do **not** reopen the question. Subset overfitting or correlated re-derivations are explicitly out of scope.

## Conclusion

After exhaustive, honest testing with a reusable overfit-proof harness, **no accessible profitable edge was found** for this class of strategy on these instruments and timeframes. The work produced:
- A correct, unified pure evaluator (ATR fixed, live == backtest by construction).
- A portable, disciplined research harness + costed driver + IS/OOS judge.
- Explainable audit/scanner.
- The discipline to accept and lock a rigorous negative.

**Branch B posture (current operating mode):** The system (deployed at acca11b) functions as a structured scanner / research instrument and selective manual-alert aid. The alerts themselves are from the no-edge family and should not be treated as having validated positive expectancy. Invest in observability/UX only to the degree it supports operating a watchlist or enables future (re-entry-criteria-satisfying) research — do not gold-plate a no-edge alert stream.

**Profitability goal:** Remains valid, but requires a new program with fresh assumptions (instrument, edge source, or data). This repo's durable contribution is the machine (harness + evaluator + process) that lets you test the next idea cheaply and honestly without lying to yourself.

See also: research/program.md (STOP section), CLAUDE.md, and the operator guide for pointers.

**Date locked:** 2026-06. Re-evaluate only on explicit re-entry criteria above.