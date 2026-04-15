# V2R Variant Rejection Note — 2026-04-15

## Decision

**Reject the `V2R` entry variant in its current form.**
Do not advance to 365-day validation. Do not assign to any pair.

## Why revisited

V2R was introduced as a candidate reversal-variant refinement and screened on the 180-day Dukascopy window across the active research universe (EUR/GBP, GBP/CHF, AUD/CAD, GBP/USD, EUR/CAD, AUD/JPY, EUR/CHF) alongside V0 / V1 / V2.

## Evidence

### Artifacts
- `results/confirmation_bakeoff_20260415_043937.md` / `.csv` — 180d bakeoff, 7 pairs, all four variant families
- `results/v2r_screen_180.log` — run log

### Result
Across all 7 pairs and every `V2R_b{0,0.5,1,2}_c{0..5}` combination, **V2R produced exactly 0 trades**. No exceptions.

| Pair | V2R rows | V2R trades (any row) |
|---|---|---|
| EUR/GBP | 24 | 0 |
| GBP/CHF | 24 | 0 |
| AUD/CAD | 24 | 0 |
| GBP/USD | 24 | 0 |
| EUR/CAD | 24 | 0 |
| AUD/JPY | 24 | 0 |
| EUR/CHF | 24 | 0 |

Best non-V2R variants from the same run (for context, not a promotion action):

| Pair | Best | PnL | PF |
|---|---|---:|---:|
| EUR/GBP | V2_b1_c0 | +0.05% | 1.31 |
| GBP/CHF | V2_b0_c0 | +0.14% | 1.78 |
| AUD/CAD | V0_b0_c0 | +0.89% | 2.95 |
| GBP/USD | V1_b2_c4 | +0.48% | 1.75 |
| EUR/CAD | V2_b0_c2 | +0.10% | (N small — PF inflated) |
| AUD/JPY | V2_b2_c1 | +0.33% | (N small — PF inflated) |
| EUR/CHF | V2R_b0_c0 | 0.00% | 0.00 (degenerate — no trades) |

Note: the `*_043937.md` "Best" line for EUR/CHF points at a V2R_ row with zero trades, which is a reporting artifact of sorting with all-zero V2R values present — not a result.

## Why this fails the promotion gate instantly

Against the promotion gate established in `WATCHLIST_EXPANSION_2026-04-14.md`:

1. **Trades ≥ 30** — fails, N=0 everywhere.
2. **Positive PnL** — no sample to measure.
3. **PF clearly > 1** — no sample to measure.
4. **No regime flip across windows** — not applicable without a sample.

This is not an underperformance. It is a signal-definition that admits no trades on this data. Either the trigger is too strict, or the implementation is gated out upstream (e.g. by another filter interacting with V2R's extra conditions). Either way, V2R in its current form cannot be validated on this dataset.

## Recommendation

- Treat V2R as **rejected** in current form.
- Do not schedule 365-day runs for V2R.
- If V2R is revisited, the entry logic must first be audited to confirm the trigger can fire on this data at all (unit test or trace a known RSI-aligned bar and check whether V2R accepts it) before any further backtesting cost is spent.

## References

- Promotion gate: `docs/reports/WATCHLIST_EXPANSION_2026-04-14.md`
- Variant naming: `CLAUDE.md` → "Entry-variant naming"
