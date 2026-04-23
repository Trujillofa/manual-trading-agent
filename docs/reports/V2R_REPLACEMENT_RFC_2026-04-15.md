# V2R Replacement RFC — Failed-Break / Reclaim State Machine — 2026-04-15

## Decision

If `V2R` is revisited, replace it with a **two-stage failed-break / reclaim state machine**.

Do **not** continue screening the current `V2R` definition.

## Why this RFC exists

The current `V2R` variant was implemented as:

- BUY: all 1h / 30m / 15m RSI values oversold **and** close above recent 20-bar HH
- SELL: all 1h / 30m / 15m RSI values overbought **and** close below recent 20-bar LL

That definition was screened on the 180-day Dukascopy window and produced **0 trades across all 168 tested combinations × 7 pairs**. See `V2R_REJECTION_2026-04-15.md`.

This is now understood as a **specification defect**, not a tuning problem. The trigger asks for an extreme multi-timeframe mean-reversion setup and an opposite-side structural break on the same active confirmation window. That is too restrictive to fire on real FX data.

## Proposed replacement

Replace `V2R` with a failed-break / reclaim model that separates:

1. **Setup arming** — identify exhaustion
2. **Entry trigger** — confirm reversal later

### BUY setup

Arm a bullish setup when:

- 1h RSI < oversold threshold
- 30m RSI < oversold threshold
- 15m RSI < oversold threshold

Then require a failed bearish break:

- 15m price trades below the previous 20-bar LL (or buffered LL)
- price later reclaims back above the previous LL, or above a smaller local swing high

### SELL setup

Arm a bearish setup when:

- 1h RSI > overbought threshold
- 30m RSI > overbought threshold
- 15m RSI > overbought threshold

Then require a failed bullish break:

- 15m price trades above the previous 20-bar HH (or buffered HH)
- price later rejects back below the previous HH, or below a smaller local swing low

## State machine

### State 1 — Armed

Create a per-pair setup record when MTF RSI alignment first appears:

- `armed_at_bar`
- `armed_direction` (`BUY` or `SELL`)
- `armed_hh`
- `armed_ll`
- `expires_after_bars`

This differs from the current confirmation model because the setup survives after the exact alignment bar and allows the reversal confirmation to happen later.

### State 2 — Failed break observed

Mark the setup as progressed when price first breaks the expected exhaustion level:

- BUY setup: wick or close below armed LL
- SELL setup: wick or close above armed HH

This establishes the "failure" part of the pattern.

### State 3 — Reclaim / rejection trigger

Trigger entry only if the price then reverses back through the reclaim threshold before setup expiry:

- BUY setup: close back above reclaim level
- SELL setup: close back below reclaim level

The reclaim level should be explicitly parameterized.

## Initial parameterization

Start with the smallest variant family that is distinct from current `V2`.

### Required parameters

- `break_buffer_pips`
- `reclaim_buffer_pips`
- `max_setup_bars`
- `confirm_bars` or `max_reclaim_bars`

### First recommended defaults

- `break_buffer_pips = 0.0`
- `reclaim_buffer_pips = 0.0`
- `max_setup_bars = 4`
- `max_reclaim_bars = 2`

These are starting values for research only, not production defaults.

## Non-goals

This RFC does **not** include:

- RSI crossback as a requirement
- divergence as a hard gate
- ATR TP/SL changes
- watchlist expansion
- pair promotion

Divergence may remain a confidence weight later, but it should not be required in the first replacement experiment.

## Why this is better than the rejected V2R

- It separates exhaustion detection from reversal confirmation.
- It tests a real failed-break behavior rather than asking the same bar to be both extreme and opposite-side breakout.
- It is meaningfully different from current `V2`.
- It can be backtested with a finite state machine and explicit expiry.

## Alternatives considered

### A. Arm on MTF alignment, then trigger later on structural break

Rejected for now because it is too broad and underspecified. Without the failed-break step, it risks becoming a generic delayed breakout family rather than a reversal family.

### C. Decouple trigger from RSI by allowing RSI normalization before entry

This may be useful later, but on its own it does not define the reversal structure clearly enough. It is better treated as an optional rule within the failed-break / reclaim state machine, not as the primary definition.

### Parameterize current `V2` instead of adding a new family

Rejected for now because the intended behavior is materially different from wick-through + close reclaim at the same reference level. If future testing shows the new design collapses to a `V2` threshold tweak, that can be revisited.

## Preconditions before implementation

Do not implement this RFC until the live scanner and research paths use the same HH/LL semantics.

Specifically:

- `src/cli.py` must use previous-bar HH/LL references consistent with bakeoff and optimization
- `src/strategy/multi_timeframe.py` fallback must be fixed or constrained the same way

Without that alignment, live-vs-research comparability is not trustworthy.

## Validation plan

After HH/LL alignment is fixed:

1. Add the replacement variant to the same three places used by current profile families:
   - `src/cli.py`
   - `scripts/run_confirmation_bakeoff.py`
   - `scripts/run_entry_optimization.py`
2. Keep it **opt-in only**.
3. Run a 180-day screen on the current research universe.
4. Only advance if the replacement variant produces nontrivial trades and is not dominated by `V1` / `V2` everywhere.
5. If it survives 180d, run 365d validation on the best candidate pairs.

## Success criteria

The replacement variant is worth keeping only if it:

- produces a real sample size
- remains positive on at least one serious candidate pair
- does not collapse to a tiny-trade artifact
- offers behavior distinct from current `V2`

## References

- Rejection note: `docs/reports/V2R_REJECTION_2026-04-15.md`
- Promotion gate: `docs/reports/WATCHLIST_EXPANSION_2026-04-14.md`
- Prior confirmation bakeoff: `docs/reports/CONFIRMATION_BAKEOFF_FULL_REPORT_2026-03-31.md`
