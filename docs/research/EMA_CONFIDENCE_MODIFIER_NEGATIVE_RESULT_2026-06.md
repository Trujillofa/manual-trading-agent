# EMA Trend-Alignment Confidence Modifier — Negative Result, 2026-06-22

**Finding:** Adding an EMA-200 trend-alignment confidence modifier to the live mean-reversion RSI strategy **does not improve performance — it degrades it.** Implemented and gated off by default (`strategy.ema.confidence_modifier_enabled: false`); **do not enable in production, and do not re-sweep it.**

This is a valid negative result. It is consistent with, and a corollary of, the locked [FX Directional TA Negative Result](FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md): there is no accessible directional edge to amplify here, and a *trend* filter is conceptually mismatched to a *mean-reversion* engine.

## Hypothesis tested

Recommendation #1 from the EMA pairing review: use EMA as a **confidence modifier, not a gate** — boost an RSI signal's confidence when price agrees with the macro EMA(200) trend, dampen it when price opposes the trend ("buy dips in uptrends"). Modifier scales confidence (`×boost` aligned, `×dampen` counter); the `confidence ≥ 0.4` entry threshold means a strong-enough dampen can also filter the weakest counter-trend signals.

## Results (enhanced backtest, 2y yfinance 1h, gross)

| Pair | Variant | Trades | WR | PnL % | PF |
|------|---------|--------|------|-------|------|
| GBP/USD | baseline | 352 | 70.5% | +44.3% | 1.17 |
| GBP/USD | soft (dampen ×0.85) | 345 | 70.1% | +38.7% | 1.15 |
| GBP/USD | aggressive (×0.6) | 219 | 66.7% | −2.2% | 0.98 |
| NZD/JPY | baseline | 316 | 72.8% | +73.5% | 1.29 |
| NZD/JPY | soft (×0.85) | 315 | 72.7% | +71.8% | 1.28 |
| NZD/JPY | aggressive (×0.6) | 195 | 73.8% | +52.5% | 1.41 |
| EUR/USD | baseline | 333 | 68.2% | +12.4% | 1.05 |
| EUR/USD | soft (×0.85) | 329 | 67.8% | +8.1% | 1.03 |
| EUR/USD | aggressive (×0.6) | 208 | 65.9% | −6.9% | 0.96 |

## Interpretation

- **Soft modifier is marginally negative on all three pairs** — it barely changes the trade set, and the few signals it dampens below threshold were slightly net-positive.
- **Aggressive filtering destroys the edge on 2 of 3 pairs** (GBP/USD PF 1.17→0.98; EUR/USD 1.05→0.96).
- **The one apparent win (NZD/JPY PF 1.29→1.41) is a trap:** it bought higher per-trade quality by discarding a third of total PnL. The existing per-pair override tuning achieves better risk-adjusted return without that sacrifice.
- **Root cause — conceptual mismatch:** the strategy is mean-reversion (RSI oversold → buy). EMA-200 "trend alignment" is a trend-following filter, so it dampens exactly the counter-trend dips/rips the system profits from. Filtering them removes the edge.

## Caveats that make this *more* damning, not less

- Numbers above are **gross**, on yfinance 1h. Per the locked finding, this family runs gross PF ~1.0–1.07 and nets to no edge after realistic spread/slippage. The modifier moves figures *within* the no-edge regime; it does not create one.
- These do not meet the promotion gate (would need 180d+ Dukascopy, ≥30 trades, PF clearly >1 net, no regime flip). The bar is documented as **unmet**.

## Decision

- **Keep the implementation, default OFF.** It mirrors the existing RSI-MA variant tooling and is a reusable evaluation harness; the `--ema-confidence[-ref|-boost|-dampen]` flags on `backtest-enhanced` reproduce the table above.
- **Do NOT enable in production.** Do NOT parameter-sweep across pairs to find a winner (overfitting; violates the STOP banner re-entry rules in `research/program.md`).
- **EMA's productive role remains context-only:** enrichment on RSI signal messages + the rare discrete crossover / price-touch alerts (slope alerts are disabled — too noisy). It is decision-support for the Branch B manual tool, not automated signal math.

## Inverted variant (mean-reversion confluence) — deliberately not pursued

Flipping the logic (favor counter-trend / stretch-from-EMA) is the conceptually correct direction, but pursuing it would be another TA tweak on FX majors — exactly what the locked finding's re-entry criteria exclude. Not done by design.
