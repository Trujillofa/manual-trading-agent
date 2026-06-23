# Event / Calendar Strategy contract — 2026-06-18

## Premise

Use scheduled macroeconomic events as the edge source: pre-event risk avoidance, post-event drift,
or volatility expansion after releases. This uses information timing, not chart patterns or
directional TA.

## Why this is not a closed lane

- FX directional TA: CLOSED (gross PF ~1.0–1.07).
- Daily TSMOM: CLOSED (gross PF 1.036).
- Carry (Hetzner cTrader): DISCARD (zero swaps).
- Stat-arb daily pairs: DISCARD (OOS gates failed).

Event/calendar uses release timing and surprise (actual vs forecast) as the edge family.

## Data required (before any strategy code)

| Requirement | Minimum bar | Notes |
|---|---|---|
| Historical calendar depth | ≥ 5 years, ideally 2016+ | For IS/OOS event-family counts |
| Timestamp reliability | UTC or documented TZ + conversion | Must match decision clock |
| Impact classification | 3-tier (high/medium/low) | Align with Branch B 3-star filter |
| Actual / forecast / previous | Required for surprise lanes | Must be available at decision time without look-ahead |
| Intraday OHLC around windows | M15 or M1 for ±60 min | For post-event drift / avoidance quant |
| Spread widening model | Documented per event family | Conservative multiplier on release |

## Cost model (for net runs only)

- Base spread: `config/settings.yaml` spread_limits (~1.5–3.0 pips majors).
- Release window widening: **3× base spread** for 15 minutes around timestamp (conservative default).
- Slippage: +1 pip per leg during release window.
- No strategy/backtest until data manifest passes.

## First falsification test (data proof only — this phase)

Verify whether a reproducible historical economic calendar exists with:

1. Sufficient depth and event-family counts (NFP, CPI, rate decisions, etc.).
2. Parseable timestamps without systematic timezone errors.
3. Actual/forecast fields usable without look-ahead leakage.
4. Documented spread-widening assumptions.

**No event strategy or backtest in this phase.**

## Pass gate (data manifest)

- Historical source identified with ≥ 5 years coverage and ≥ 200 high-impact events.
- Timestamp audit passes (≥ 95% parse rate, timezone documented).
- Actual/forecast/previous field coverage ≥ 80% on numeric releases.
- Look-ahead audit documents when each field becomes available.
- Spread widening model written and tied to settings.yaml base spreads.

## Stop gate

- Only live/current-week feed available (no historical).
- Production parser cannot read the live feed schema (currency/timestamp mismatch).
- Actual values unavailable historically or only with look-ahead.
- Spread widening consumes any plausible post-event move.

## First command

```bash
python -m research.new_edge.events.data.verify_event_data \
  --output docs/research/events/EVENT_DATA_MANIFEST_2026-06-18.md
```

## Verification status

**DATA_PASS** (2026-06-19). See `docs/research/events/EVENT_DATA_MANIFEST_2026-06-19.md`.

- Pinned HF snapshot: 83,427 rows, 2007-01-01 → 2025-04-07 UTC, SHA256 verified.
- Indicator-class high-impact events: 9,806 (actual/previous 99.8%, forecast 96.8%).
- Live faireconomy XML still incompatible with `NewsChecker` (production lockout separate fix).
- **Actual is label-only** after `datetime_utc`; never for pre-release decision logic.

Next: smallest gross-first event falsifier (avoidance or post-release drift). Still no optimization.