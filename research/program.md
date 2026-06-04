# autoresearch (trading) — find a robustly profitable config

Adapted from Karpathy's autoresearch. Instead of minimizing `val_bpb` on a
pinned validation shard, we **maximize a held-out-validated profitability
`score`** on Dukascopy M1 with realistic costs. The judge is out-of-sample, so
you cannot win by overfitting — that is the entire point.

## In-scope files

- `research/evaluate.py` — **FIXED metric. Never edit.** Splits each pair's
  cached 365d series into in-sample (first 65%) and out-of-sample (last 35%),
  runs the candidate on both, and only returns verdict KEEP when it is
  profitable AND has enough trades AND is consistent on the *held-out* window.
- `research/strategy_config.py` — **the file you edit.** `CONFIG` is the
  candidate; keys map 1:1 to the ground-truth backtest engine (Donchian). For the live
  entry family use `"engine": "live_mtf_rsi"` + keys like lower_bound / buffer_pips /
  confirm_bars / adx etc (forwarded via overrides to the pure evaluator).
- `research/run_experiment.py` — runs one experiment, prints the summary block.
- `research/autosearch.py` — automated loop (hands-free overnight search).
- `research/results.tsv` — experiment log (untracked).

## What you CAN do

- Edit `CONFIG` in `research/strategy_config.py`: RSI bounds, ATR TP/SL,
  lookback, confirmation, filters (DI/ADX/session), exits (BE/trailing/time).
- Add genuinely new entry/exit logic to the engine if a config plateau is hit
  (bigger change; re-validate from scratch).

## What you CANNOT do

- Edit `research/evaluate.py` (the judge), the IS/OOS split, the trade-count
  gates, or the cost model (spread/commission/slippage). Lowering costs or the
  `MIN_TRADES` gate to make a config "pass" is self-deception, not research.

## The goal

**Highest `score` with verdict KEEP.** `score` rewards out-of-sample profit and
penalizes (a) the gap between in-sample and out-of-sample edge (overfitting) and
(b) thin trade counts. A KEEP verdict requires, on BOTH windows, ≥30 trades and
positive PnL, plus out-of-sample PF ≥ 1.20.

**Reality check (baseline, today): DISCARD.** Out-of-sample yields ~9 trades
across 7 pairs over ~4 months — too few to be profitable or to validate. The
honest outcome of this search may be "no robust edge exists in this space." That
is a valid, valuable result. If so, the lever is not parameters but either
(a) a different entry that fires more often, or (b) accepting this is a
discretionary alert tool, not an autonomous edge.

## The loop

```
LOOP:
  1. Edit CONFIG in research/strategy_config.py with one experimental idea.
  2. python -m research.run_experiment > run.log 2>&1
  3. grep "^score:\|^verdict:\|^fail_reasons:" run.log
  4. If score improved over the current best AND verdict is KEEP -> keep the edit.
     Else -> revert the edit.
  5. Append a row to research/results.tsv.
```

Or run it hands-free: `python -m research.autosearch --iters 200`.

## results.tsv format (tab-separated)

```
ts	score	oos_pf	oos_pnl_pct	oos_trades	status	description
```

`status` is `baseline`, `keep`, or `discard`. Do not commit `results.tsv`.

## 2026-06 Live entry family R1 status (unified evaluator)
The live MTF RSI + V* + gates entry (the one that actually runs in `src/cli.py` scan and
writes Telegram/audit) is now first-class in the harness via `engine="live_mtf_rsi"`.
See research/evaluate.py (backtest_live_entry + evaluate_config dispatch) and
src/scanner/evaluator.py (pure evaluate_entry + overrides= for param search).

Corrected sampled baseline (rerun after restoring the configured 3-TF SMA alignment
gate, same-direction-only Rule C suppression, and costed driver P&L):
LIVE_BT_MAX_BARS=3000, current production settings.yaml, no overrides, 8 cached pairs
→ IS 0 trades / OOS 0 trades → DISCARD on the strict MIN_TRADES=30 + OOS PF>=1.20
and positive PnL gates.

Pre-fix sampled numbers are retired for quantitative claims. The archived strict
2 IS / 0 OOS and relaxed 14 IS / 10 OOS runs were generated before those parity fixes
and should be treated only as historical debugging context.

This corrected sample, plus historical volume diagnostics and prior harness runs on
the live driver, is the honest R1 evidence on the actual live entry family. Low
frequency is structural; the centralized evaluator + ATR fix + Rule C + searchable
harness are the concrete deliverables. Branch B (selective manual alert tool) is the
supported posture.

## Honesty rules

1. The out-of-sample window is sacred. Never tune against it directly; it only
   judges.
2. A config that needs <30 out-of-sample trades to look good has not been
   validated, regardless of its PF.
3. Prefer simpler configs when scores tie. Removing a filter for equal/better
   out-of-sample score is a win.
