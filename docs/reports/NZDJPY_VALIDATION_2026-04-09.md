# NZD/JPY Validation Note — 2026-04-09

## Decision
- **Do not keep `NZD/JPY` in the watchlist.**
- **Do not add a pair-specific confirmation profile for `NZD/JPY`.**

The pair was tested because it showed a live triple-RTSI extreme, but the broader backtesting evidence did not support promoting it into the production watchlist.

## Why this was revisited
- Live check showed `NZD/JPY` with aligned overbought RSI readings on `1h`, `30m`, and `15m`.
- The pair was not excluded by a prior negative decision. It simply had not been included in the earlier bakeoff shortlist.
- We temporarily added it to `config/settings.yaml` to evaluate it, then removed it after the validation completed.

## Final backtest evidence

### Local 365-day Dukascopy bakeoff
Saved report:
- `results/confirmation_bakeoff_20260409_204828.md`
- `results/confirmation_bakeoff_20260409_204828.csv`

Best result for `NZD/JPY`:
- **Variant:** `V2_b0_c2`
- **Trades:** `32`
- **Total PnL:** `-0.12%`
- **Profit factor:** `0.92`

This was the most important result. Over a longer sample, `NZD/JPY` stayed negative even in its best configuration.

### Local 180-day comparison against active watchlist candidates
Saved report:
- `results/confirmation_bakeoff_20260409_210545.md`
- `results/confirmation_bakeoff_20260409_210545.csv`

Best pair result on the same 180-day window:
- **EUR/GBP:** `+0.02%`, PF `1.12`
- **GBP/CHF:** `+0.08%`, PF `2.10`
- **AUD/CAD:** `+0.12%`, PF `1.26`
- **NZD/JPY:** `+0.10%`, PF `1.14`

`NZD/JPY` was not the weakest pair on the 180-day window, but it still lagged stronger alternatives and did not hold up over 365 days.

## Recommendation for future decisions
- Keep the production watchlist focused on pairs with both:
  - positive medium-window behavior, and
  - acceptable longer-window stability
- Treat `NZD/JPY` as **not validated** unless future testing materially improves.
- If the pair is revisited, require a stronger long-window result before re-adding it.

## Workflow note: run heavy backtests locally
For future reference, heavy validation runs are better done **locally**, not on Hetzner.

Reasons:
- long Dukascopy downloads can exceed remote command timeouts
- local runs make it easier to inspect logs and keep the session alive
- local saved outputs are easier to retain in `results/`

Recommended pattern:

```bash
.venv/bin/python scripts/run_confirmation_bakeoff.py \
  --source dukascopy \
  --pairs "NZD/JPY" \
  --variants V0,V1,V2 \
  --buffers 0.0,0.5,1.0,2.0 \
  --confirm-bars 0,1,2 \
  --days 365 \
  --output-dir results
```

For multi-pair comparison:

```bash
.venv/bin/python scripts/run_confirmation_bakeoff.py \
  --source dukascopy \
  --pairs "EUR/GBP,GBP/CHF,AUD/CAD,NZD/JPY" \
  --variants V0,V1,V2 \
  --buffers 0.0,0.5,1.0,2.0 \
  --confirm-bars 0,1,2 \
  --days 180 \
  --output-dir results
```

## Config cleanup outcome
After the decision, `NZD/JPY` was removed again from:
- `trading.pairs.majors`
- `strategy.spread_limits_pips`

That keeps the watchlist aligned with the current evidence.
