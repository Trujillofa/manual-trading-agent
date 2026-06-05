# research/ — autoresearch for trading

A trading adaptation of [Karpathy's autoresearch](https://github.com/karpathy/nanochat):
an autonomous loop that mutates a strategy config, evaluates it, and keeps only
improvements — but judged on a **held-out out-of-sample window** so it cannot win
by overfitting.

## Files

| file | role | edit? |
|---|---|---|
| `evaluate.py` | the judge: IS/OOS split, costs, trade-count gates, `score` | **never** |
| `strategy_config.py` | `CONFIG` candidate + `PARAM_SPACE` | yes — this is the knob |
| `run_experiment.py` | run one config, print summary block | rarely |
| `autosearch.py` | automated hands-free search loop | rarely |
| `program.md` | the loop instructions (for an agent) | yes (it's the "skill") |
| `results.tsv` | experiment log (gitignored) | auto |
| `best_config.json` | best OOS-confirmed config found (gitignored) | auto |

## Quick start

```bash
# one experiment (reads research/strategy_config.CONFIG)
python -m research.run_experiment

# automated overnight search
python -m research.autosearch --iters 200 --seed 0
```

## The metric

Each cached 365d Dukascopy pair is split chronologically: first 65% in-sample
(optimize here), last 35% out-of-sample (held-out judge). A config earns verdict
**KEEP** only if, on *both* windows, it has ≥30 trades and positive PnL, and the
out-of-sample profit factor is ≥1.20. `score` rewards out-of-sample profit and
penalizes the in-sample-vs-out-of-sample gap (overfitting) and thin samples.

Ground-truth engine: `scripts/run_donchian_backtest.run_config` (realistic costs:
spread + commission + slippage; ATR sizing; breakeven/trailing/time exits).

Live entry engine: set `"engine": "live_mtf_rsi"` (or "unified") in CONFIG. This drives the
exact production logic from `src/scanner/evaluator.evaluate_entry` (the unified pure source)
+ Rule C state + TP/SL sim. You can pass live-family params (lower_bound, buffer_pips, adx_threshold
etc) and they are forwarded as overrides (see evaluate_config + backtest_live_entry). This makes
R1 search judge the *actual live scanner entry family*.

## Honest baseline (2026-06-03)

The current `CONFIG` scores **DISCARD**: out-of-sample produces ~9 trades across
7 pairs in ~4 months — far below the 30-trade gate — and loses money. This is the
expected result given the strategy's low signal frequency. The harness exists to
find a config that genuinely clears the held-out gates, or to prove honestly that
none does in the searched space. See `../docs/` and project memory for context.

## Adding pairs / windows

`evaluate.PAIRS` uses the 7 pairs with cached 365d Dukascopy data
(`results/cache/*_365d.parquet`). To widen coverage, fetch more via
`scripts/run_donchian_backtest.fetch_pair(pair, 365)` first, then add to `PAIRS`.
More pairs = more out-of-sample trades = more trustworthy verdicts.
