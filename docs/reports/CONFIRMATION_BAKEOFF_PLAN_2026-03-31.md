# Confirmation Bake-off Plan — 2026-03-31

## Goal
Resolve the ambiguity around **entry confirmation after MTF RSI alignment** by backtesting multiple confirmation variants on the same data and ranking them by risk-adjusted performance.

The key question is not whether a confirmation rule sounds reasonable, but **which confirmation rule produces the best live-like outcomes**.

---

## Strategy Core (fixed across all tests)
These rules stay constant in every variant:

- **BUY setup:**
  - RSI 1h < 30
  - RSI 30m < 30
  - RSI 15m < 30
- **SELL setup:**
  - RSI 1h > 70
  - RSI 30m > 70
  - RSI 15m > 70
- Same pair universe
- Same data source per experiment batch
- Same TP/SL framework
- Same cooldown/news/session rules for all variants in the same run

Only the **confirmation rule** changes.

---

## Pairs to Test
### Tier 1 (must test)
- EUR/GBP
- USD/JPY
- EUR/CAD
- EUR/CHF
- GBP/CHF

### Tier 2 (nice to include)
- GBP/USD
- EUR/USD
- GBP/CAD
- AUD/NZD
- USD/CHF

Reason:
- Tier 1 contains the strongest observed live/watchlist behavior so far.
- Tier 2 helps catch overfitting and pair-specific quirks.

---

## Data Windows
## Primary Window
- **In-sample / dev:** last 60 days intraday (current reliable MTF window)

## Secondary Window
- Rolling windows if available from improved data provider:
  - Window A
  - Window B
  - Window C

If limited by data history, prefer **multiple shorter rolling windows** over one single cherry-picked period.

---

## Confirmation Variants to Test

### V0 — Baseline: No confirmation
Enter immediately when MTF RSI alignment is complete.

**Purpose:** establishes whether confirmation adds value at all.

---

### V1 — Current breakout logic
- BUY: aligned oversold + 15m close breaks **below** 20-bar low
- SELL: aligned overbought + 15m close breaks **above** 20-bar high

**Purpose:** tests the current implementation exactly as coded.

---

### V2 — Reversal breakout logic
- BUY: aligned oversold + 15m close breaks **above** prior 20-bar low reclaim threshold / reversal trigger
- SELL: aligned overbought + 15m close breaks **below** prior 20-bar high rejection threshold / reversal trigger

**Purpose:** tests whether the strategy is actually mean-reversion/reversal oriented rather than continuation oriented.

---

### V3 — Buffered current breakout
Same as V1, but require a pip buffer before confirmation.

Suggested buffers:
- 0.0 pips
- 0.5 pips
- 1.0 pips
- 2.0 pips

**Purpose:** reduce false triggers from tiny wick noise.

---

### V4 — Buffered reversal confirmation
Same as V2, but require a reclaim/rejection buffer.

Suggested buffers:
- 0.0 pips
- 0.5 pips
- 1.0 pips
- 2.0 pips

**Purpose:** test whether cleaner reversal structure improves expectancy.

---

### V5 — Delayed confirmation
After MTF alignment, require confirmation within the next N entry bars.

Suggested values:
- 1 bar
- 2 bars
- 3 bars
- 4 bars

Apply separately to V1 and V2 style logic.

**Purpose:** determine whether setups decay quickly or remain valid for a short window.

---

### V6 — Micro execution refinement only
- Signal still triggered from 15m framework
- Execution price refined using 5m/1m after confirmation

Suggested modes:
- immediate market entry
- wait for 5m pullback
- wait for 1m pullback

**Purpose:** isolate whether micro-timing improves fills without changing strategy logic.

---

## Optional Confirmation Add-ons
These should be tested only after the base breakout comparison is clear.

### A1 — Divergence filter
Require matching divergence for entry:
- BUY needs bullish divergence
- SELL needs bearish divergence

### A2 — Candlestick filter
Require supportive candle pattern:
- BUY: bullish pattern
- SELL: bearish pattern

### A3 — Soft score model
Instead of hard requirement:
- MTF alignment = base score
- breakout = +score
- divergence = +score
- pattern = +score
- spread/session/news blockers = subtract / veto

---

## Metrics to Rank Variants
Do **not** optimize on win rate alone.

### Primary metrics
- Net PnL
- Profit factor
- Max drawdown
- Expectancy per trade
- Trade count

### Secondary metrics
- Win rate
- Average win / average loss
- Median hold time
- Time in market
- Consecutive losses

### Robustness metrics
- Performance by pair
- Performance by rolling window
- Sensitivity to buffer changes
- Sensitivity to TP/SL changes

---

## Recommended Ranking Logic
A confirmation variant is promotable only if it meets all of these:

1. Profit factor clearly above baseline (V0)
2. Lower or comparable drawdown
3. Reasonable trade count (not 2 lucky trades)
4. Works across more than one pair/window
5. Behavior is explainable

Reject if:
- only wins on one pair
- collapses with tiny parameter shifts
- produces too few trades to trust
- drawdown is unacceptable relative to edge

---

## Test Matrix (practical first pass)

### Phase 1 — Core direction test
Run these first:
- V0 baseline
- V1 current breakout
- V2 reversal breakout

Across:
- EUR/GBP
- USD/JPY
- EUR/CAD
- EUR/CHF
- GBP/CHF

This answers the main question fast:
> is the current breakout direction correct, or is reversal confirmation better?

---

### Phase 2 — Buffer sensitivity
For the winning direction from Phase 1:
- test 0.0 / 0.5 / 1.0 / 2.0 pips

---

### Phase 3 — Time decay
For the best buffered version:
- confirmation must occur within 1 / 2 / 3 / 4 bars

---

### Phase 4 — Add-on filters
Only then test:
- divergence required
- pattern required
- divergence OR pattern
- score-based model

---

## Expected Deliverables
For each phase, output:

1. **CSV results table**
   - pair
   - variant
   - trades
   - pnl
   - pf
   - dd
   - expectancy

2. **Ranked summary markdown**
   - best variant overall
   - best by pair
   - losers / discarded logic

3. **Promotion decision**
   - keep current logic
   - switch to reversal confirmation
   - remove breakout entirely
   - use pair-specific confirmation rules if justified

---

## Concrete First Recommendation
Start with this exact first batch:

- Pairs:
  - EUR/GBP
  - USD/JPY
  - EUR/CAD
  - EUR/CHF
  - GBP/CHF

- Variants:
  - V0 no confirmation
  - V1 current breakout
  - V2 reversal breakout

- Window:
  - last 60 days intraday MTF data

- Execution:
  - same TP/SL and cooldown for all
  - news/session rules either on for all or off for all

This is the shortest path to answering the real strategy question.

---

## Decision Rule
If V2 beats V1 consistently, then the current live breakout confirmation is likely directionally wrong for the strategy intent.

If V1 beats V2 and V0, keep it.

If V0 beats both, then breakout confirmation is likely harming the system and should be simplified or removed.
