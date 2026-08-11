# HTF Fib tool-combo search result (2026-07)

## Verdict

**all-DISCARD.** No preregistered tool combination (liquidity sweep and/or anchored tick-volume VWAP on top of confirmed HTF Fib) cleared the locked promotion gates on 8 FX pairs × 365d Dukascopy 15m with costed IS/OOS.

This does **not** reopen the FX directional-TA lane. It ranks partial multi-tool stacks under override discipline and confirms that adding sweep/AVWAP does not create a KEEP edge.

## Combinations note

See `docs/research/HTF_FIB_TOOL_COMBINATIONS_2026-07.md`.

- **In space:** liquidity sweep, anchored tick-volume VWAP (partial stacks).  
- **Out:** order flow / CVD / footprint; full volume profile as hard filter.  
- **Tiers:** hardened MTF (C0–C3) + soft marker-style (soft_*).

## Search entry point

```bash
python -m research.htf_fib_autosearch \
  --iters 12 --seed 1 --days 365 \
  --override-negative-result docs/research/FX_DIRECTIONAL_TA_NEGATIVE_RESULT_2026-06.md
```

Artifacts:

- `results/htf_fib_combo_ranking.md` — ranked table + single best / all-DISCARD  
- `research/htf_fib_results.tsv` — trial log  
- No `research/htf_fib_best_config.json` (no KEEP)

## Ranking snapshot (8 pairs, 365d, 65/35 IS/OOS)

| Combo | IS n | OOS n | OOS net PF | OOS PnL% | Score | Verdict |
|-------|-----:|------:|-----------:|---------:|------:|---------|
| hardened_mtf | 0 | 0 | 0.00 | 0.00 | -15.00 | DISCARD |
| hardened_sweep | 0 | 0 | 0.00 | 0.00 | -15.00 | DISCARD |
| hardened_avwap | 0 | 0 | 0.00 | 0.00 | -15.00 | DISCARD |
| hardened_sweep_avwap | 0 | 0 | 0.00 | 0.00 | -15.00 | DISCARD |
| soft_sweep | 0 | 0 | 0.00 | 0.00 | -15.00 | DISCARD |
| soft_sweep_avwap | 0 | 0 | 0.00 | 0.00 | -15.00 | DISCARD |
| soft_avwap | 1 | 1 | 0.00 | -1.21 | -16.72 | DISCARD |
| soft_baseline | 11 | 12 | 0.07 | -11.99 | -27.94 | DISCARD |

### Single best under fixed score

- **Winner by score:** `hardened_mtf` (score −15.0, 0 IS / 0 OOS trades).  
- Among all-DISCARD stacks, zero-trade configs outscore the soft baseline that loses ~12% OOS net (not trading beats realizing a negative edge).  
- **Highest-activity informative stack:** `soft_baseline` (11 IS / 12 OOS) — matches the archived IS-selected grid family (OOS net PF ~0.07); still fails every gate.

### Tool effects (qualitative)

- **Liquidity sweep (hard):** drove trade count to **zero** on the soft base as well (origin-sweep + reclaim is rare under one-entry/invalidation).  
- **Anchored VWAP:** cut soft_baseline activity to a single IS and OOS trade; still DISCARD.  
- **Sweep + AVWAP:** zero trades.  
- Hardened MTF remains structurally sparse (0/0), consistent with `docs/research/HTF_FIB_NEGATIVE_RESULT_2026-06.md`.

## Gates (unchanged)

- IS and OOS trades ≥ 30  
- OOS net PF ≥ 1.20  
- Positive IS and OOS net PnL  
- Costs: 2 pip spread, 2 pip slippage/fill, $3/order  

## Engine changes shipped

- `StrategyConfig.require_liquidity_sweep` / `require_anchored_vwap` in `scripts/run_htf_fib_backtest.py`  
- Close-based invalidation when sweep is required (enables wick-through reclaim)  
- Tick-volume anchored VWAP from swing origin  
- Preregistered `CONFIGS` include the four hardened tool stacks  
- `research/htf_fib_config.py` + `research/htf_fib_autosearch.py` restored with combo ranking  
- **Config resolve fix:** `resolve_config` / `dict_to_strategy` let free PARAM_SPACE knobs
  (rsi, tp/sl, hold, pivots, atr) override combo defaults; only combo **identity** flags
  are forced. `_perturb` no longer re-applies full `COMBOS` after sampling.  
- Unit tests: combo gates change trade counts; free-param non-clobber
  (`tests/test_htf_fib_backtest.py`)
