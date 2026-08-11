# HTF Fib iterative refinement roadmap (2026-07)

Finite program: **≥3 distinct rounds** under locked gates. Finish with KEEP or honest all-DISCARD (profit not found). Do not weaken costs, MIN_TRADES, or OOS PF.

## Baseline (prior all-DISCARD)

From `docs/research/HTF_FIB_COMBO_SEARCH_RESULT_2026-07.md`:

- Best by score: `hardened_mtf` (0 IS / 0 OOS)
- Highest activity: `soft_baseline` (11 IS / 12 OOS, OOS net PF 0.07)

## Rounds

| Round | Id | Distinct change |
|-------|-----|-----------------|
| 1 | `R1_zone_volume` | New `fib_zone` (golden/mid/wide/shallow); soft seed with no one-entry / no candle; search zones + RSI + pivots |
| 2 | `R2_exits` | Exit/hold grid (tp/sl ATR, max hold, atr period) on soft mid/wide family |
| 3 | `R3_structure` | `invalidate_mode` none/wick/close + one_entry + require_ema_stack + soft tool combos |

## Entry point

```bash
python -m research.htf_fib_iterative_search \
  --override-negative-result docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md
```

Artifacts: `results/htf_fib_refinement/R*.md`, `docs/research/HTF_FIB_ITERATIVE_REFINEMENT_RESULT_2026-07.md`.
