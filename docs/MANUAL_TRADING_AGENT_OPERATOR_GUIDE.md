# Manual Trading Agent — Operator Guide

## Purpose
This bot is a **manual trading decision assistant** for forex pairs. It is not meant to blindly auto-execute based on one indicator. It scans, classifies, ranks, and explains setups so the operator can understand what is happening.

---

## Strategy core
### Base setup
A setup exists only when RSI aligns across 3 timeframes.

#### BUY setup
- RSI 1h < 30
- RSI 30m < 30
- RSI 15m < 30

#### SELL setup
- RSI 1h > 70
- RSI 30m > 70
- RSI 15m > 70

This is the **setup layer**, not always the final entry.

---

## State meanings
### 1) Near setup
- MTF alignment is **not complete yet**
- one or more timeframes are still missing
- bot should show which timeframe(s) are missing

### 2) Aligned / breakout pending
- MTF RSI alignment is **complete**
- but the pair-specific confirmation breakout has **not happened yet**
- this is the strongest pre-entry state

### 3) Confirmed entry
- MTF RSI aligned
- confirmation logic satisfied
- blocker layer clear

### 4) Blocked / no trade
A setup can still be blocked by:
- breakout not confirmed
- session filter
- news block
- cooldown
- spread filter (if a real bid/ask source is configured)

---

## Current promoted pair profiles

CLAUDE.md is the authoritative source. See `config/settings.yaml` for
per-pair confirmation profiles under `strategy.confirmation_profiles`.

### Promoted (tuned with per-pair overrides)

| Pair | Profile | SMA | TP/SL (ATR) | Status |
|------|---------|-----|-------------|--------|
| GBP/CHF | V2_b0_c0 | 50 | 1.0/3.0 | Shadow-only (audit records, no Telegram alerts) |
| NZD/JPY | V0_b0_c0 | 20 | 2.5/2.5 | Live Telegram alerts |
| GBP/JPY | V0_b0_c0 | 20 | 1.5/2.5 | Live Telegram alerts |
| USD/JPY | V0_b0_c0 | 40 | 2.0/2.5 | Live Telegram alerts |
| AUD/CAD | V0_b0_c0 | 50 | 1.0/3.0 | Live Telegram alerts |

### Scout (default config, no per-pair overrides)

EUR/USD, GBP/USD, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/JPY, EUR/CHF,
EUR/AUD, EUR/CAD, EUR/NZD, GBP/AUD, GBP/CAD, GBP/NZD, AUD/JPY, AUD/CHF,
AUD/NZD, NZD/CAD, NZD/CHF, CAD/JPY, CAD/CHF, CHF/JPY.

### Rejected (excluded from config)

**EUR/GBP** — negative PnL in all 48 configs tested (best: -0.30%, PF 0.66).
2026-04-20 sweep. Despite two contradictory backtest results (Dukascopy PF 3.53
and enhanced backtest PF 1.23), the pair remains rejected pending proper 180d+
Dukascopy validation.

---

## Telegram commands
### /watchlist
Shows the latest ranked MTF candidates.

### /signal
Shows the latest confirmed entry if one exists.

### /status
Shows:
- bot mode
- scanner status
- news source
- blocked currencies
- top setup

### /news
Shows:
- current news source
- blocked currencies
- cached upcoming high-impact events

### /pairs
Lists tracked pairs.

### /pair GBP/USD
Runs a fresh single-pair review.

### /scan
Runs a fresh scan immediately.

### /help
Lists commands.

---

## How to interpret the bot
### Best current candidate
The best current candidate is the one highest in `/watchlist`, but **do not assume it is a trade**.

You must distinguish:
- near only
- aligned / breakout pending
- confirmed entry

### Strongest pre-entry state
`aligned / breakout pending`

That means:
- the setup is real
- confirmation is the only missing piece
- operator should pay attention

### When not to trade
Do not override the blocker layer casually.
If the bot says:
- breakout not confirmed
- news blocked
- cooldown active
- spread unavailable/too wide (once live spread source exists)

then that should be respected.

---

## Current technical realities
### Data provider reality
- Runtime config still says **yfinance**
- Twelve Data exists in codebase but is **not truthfully the active default provider** yet

### News layer
Currently supports:
- Forex Factory XML
- cache/backoff
- Grok fallback code path

### Spread layer
Currently **not active in a real sense** because:
- no OANDA credentials are configured
- true bid/ask spread cannot be enforced without a real quote source

---

## Recommended operating policy
### Active focus
Focus live attention on:
- AUD/CAD (promoted, V0_b0_c0, live Telegram alerts)
- NZD/JPY, GBP/JPY, USD/JPY (promoted with per-pair overrides)

### Shadow-only (audit records, no alerts)
- GBP/CHF

### Ignore for now
- EUR/GBP (rejected, negative PnL)
- Everything else until more research is done

---

## Recommended operator workflow
1. Check `/status`
2. Check `/watchlist`
3. If a promoted pair is near or aligned-pending, use `/pair <symbol>`
4. If aligned-pending, wait for confirmation rather than forcing entry
5. If confirmed entry appears, review micro context and blockers
6. Log important observations / outcomes for future bake-offs

---

## Research reference
See:
- `docs/reports/CONFIRMATION_BAKEOFF_PLAN_2026-03-31.md`
- `docs/reports/CONFIRMATION_BAKEOFF_FULL_REPORT_2026-03-31.md`

These documents are the source of truth for why current promoted pair profiles were chosen.

## Infrastructure & Repositories

### Local (`/home/yderf/`)
- manual-trading-agent: `/home/yderf/Projects/trading/manual-trading-agent`
- Deploy via `git archive` → rsync → `docker compose build` on Hetzner

### Hetzner (SSH: `crypto-agent`)
- manual-trading-agent: `/home/emilio/manual-trading-agent`
- Container: `manual-trading-agent` (scans every 15min + Telegram polling)

### GitHub (Trujillofa)
- https://github.com/Trujillofa/manual-trading-agent

### Module structure (as of 2026-05-28)
```
src/
├── cli.py              # CLI entry (1,379 lines)
├── scanner/
│   ├── gates.py        # Confirmation profiles, breakout, session, ADX, spread, signal invalidation
│   ├── state.py        # JSON persistence, trade outcome tracking, path helpers
│   └── telemetry.py    # Audit log building, scan telemetry aggregation
├── dashboard/
│   └── report.py       # Healthcheck, signal dashboard, paper P&L
├── indicators/
│   ├── adx.py, atr.py, rsi.py, sma.py, high_low.py, candlestick.py, pivot_points.py
├── backtest/
│   └── enhanced_engine.py  # Realistic TP/SL backtest simulation
├── config/settings.py
├── data/fetcher.py     # yfinance + Twelve Data + OANDA
├── news/news_checker.py
├── notifications/
│   ├── telegram.py
│   └── telegram_commands.py
├── risk/manager.py
└── strategy/multi_timeframe.py
```
