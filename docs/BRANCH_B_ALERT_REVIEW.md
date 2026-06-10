# Branch B Alert Review & Observability

This document defines how we evaluate the live Branch B scanner (the manual alert tool) so that it becomes a reviewable decision-support system rather than a black box.

## Purpose
The value of Branch B is **operational usefulness to a human trader**, not autonomous profitability. We measure signal quality, context quality, and human decision friction.

## Evaluation Dimensions
For each alert (or blocked event) we want to be able to answer later:

- Was the setup "good" on the 3-TF RSI + wick/reclaim + candle pattern criteria at the moment it fired?
- Was it late (price had already moved a lot)?
- Was it noisy (RSI alignment weak or ADX borderline)?
- Was it correctly blocked by news (3-star events)?
- Did it reach the TP zone (high/low of the ATR target) before the SL zone after the signal?
- How quickly did it invalidate (RSI midline cross, SMA flip, or opposite wick)?
- What was the favorable excursion (best price reached in favor) vs adverse excursion (worst price against) in the first 1h / 4h / 24h?
- Time from alert to first confirmation (price moving in the signaled direction).

## Journal Format (lightweight, append-only)

We extend the existing `logs/signal_audit.jsonl` (already used by the scanner for telemetry and outcomes).

Additional outcome records (written by the scanner when a pending trade resolves or on manual review):

```json
{
  "ts": "2026-06-07T12:34:56Z",
  "kind": "alert_outcome",
  "pair": "EURUSD",
  "direction": "BUY",
  "signal_id": "uuid-or-timestamp",
  "fired_at": "2026-06-07T08:15:00Z",
  "entry": 1.0850,
  "tp": 1.0870,
  "sl": 1.0820,
  "outcome": "tp_zone_first" | "sl_zone_first" | "invalidated_midline" | "invalidated_sma" | "manual_skip" | "still_open",
  "bars_to_outcome": 12,
  "max_favorable_pips": 28.4,
  "max_adverse_pips": 4.1,
  "news_blocked": false,
  "human_note": "would take | would skip | unclear | good context but wide spread"
}
```

The existing scan telemetry rows already capture:
- alignments, pending, entries, blockers (including "news", "active_signal", "adx_trending", etc.)
- RSI levels, ADX, spread at decision time
- 20-bar high/low references

This combination gives us the full picture without heavy infrastructure.

## Summarization Script

Run:

```bash
python -m scripts.summarize_alerts --days 30 --format table
```

It produces:

- Alerts per day/week
- % blocked by news (and whether the block was "correct" — i.e. a big move happened in the blocked direction)
- % that reached TP zone before SL zone
- % immediately invalidated (within first 4 bars)
- Average / median favorable vs adverse excursion (1h, 4h, 24h)
- Time-to-confirmation distribution
- Breakdown by pair and by blocker type
- List of recent "human_note" entries for qualitative review

The script reads `logs/signal_audit.jsonl` (and optionally a small manual review CSV if you want to add notes offline).

## Telegram Alert Improvements (decision context)

Current alerts are already rich. We ensure they always surface the fields a human needs in < 5 seconds:

- Symbol + direction + emoji
- Entry / TP (pips) / SL (pips)
- RSI(1h/30m/15m) values
- 20-bar High/Low reference (the "wick through" levels)
- ADX + DI context (with ⚠️ if opposing)
- Patterns / Divergence if present
- News status ( "News clear" or "Blocked by 3-star: <event>" for blocked cases )
- Suggested invalidation ( "Invalidate on 15m RSI cross of 50 or close back below 20-bar low" )
- Timestamp + "Rule C active — one signal per direction until TP/SL/midline/SMA"

This is already largely present; minor polish was added to make "news status" and "suggested invalidation" explicit even on firing signals.

## Observation Mode

For a fixed window (e.g. 4 weeks) we can run the scanner with extra logging:

```bash
LIVE_OBSERVE=1 python -m src.cli scan --pairs "EUR/USD,GBP/USD,..." --once
```

This forces shadow mode + full telemetry + outcome simulation even if no real position is taken. Great for collecting the journal without execution risk.

## Success Criteria for "Branch B is useful"

After 30–60 days of journaled data we look for:

- > 60% of fired signals reach the TP zone before the SL zone (or before manual invalidation).
- News filter demonstrably reduces large adverse moves (compare blocked vs would-have-fired during 3-star windows).
- Human review time per alert < 30 seconds (good message design).
- Clear clusters of "good setups" vs "noisy" that we can use for future light filters (without turning it into an autonomous system).

If the data shows the alerts are consistently late, noisy, or blocked too late, we refine the message or add cheap context filters. If they are useful, we keep the tool and document the best practices for the human.

This turns the scanner into a measurable, improvable decision-support system — exactly what Branch B is supposed to be.