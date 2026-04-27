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
### Promoted now
#### EUR/GBP
- Profile: **V2_b0.5_c2**
- Meaning:
  - reversal confirmation
  - 0.5 pip buffer
  - confirmation valid for 2 bars after alignment

#### GBP/CHF
- Profile: **V1_b0.5_c0**
- Meaning:
  - breakout continuation
  - 0.5 pip buffer
  - immediate confirmation only

#### AUD/CAD
- Profile: **V1_b2_c0**
- Meaning:
  - breakout continuation
  - 2.0 pip buffer
  - immediate confirmation only

### Tentative
#### EUR/CHF
- Profile: **V1_b0_c0**
- Use as observational / lower confidence until more live evidence exists

### Not promoted
- EUR/CAD
- USD/JPY
- GBP/CAD
- AUD/NZD
- USD/CHF

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
- EUR/GBP
- GBP/CHF
- AUD/CAD

### Secondary observation only
- EUR/CHF

### Ignore for now
- everything else until more research is done

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

| Repo | Path | Branch | Commit |
|---|---|---|---|
| manual-trading-agent | /home/yderf/manual-trading-agent | main | 18bd2b9 |
| ctrader-trading-agent | /home/yderf/ctrader-trading-agent | main | 0348802 |
| depositotrujillo.co | /home/yderf/depositotrujillo.co | main | 3008c56 |
| hermes-agent | /home/yderf/hermes-agent | main | b7e71fb7 |
| mt5-trading-agent | /home/yderf/mt5-trading-agent | docs/mt5-implementation-plan | (active branch) |

### Hetzner (SSH: `crypto-agent` via Tailscale 100.103.209.50)

| Repo | Path | Branch | Commit |
|---|---|---|---|
| crypto-agent | /opt/crypto-agent | main | b327366 |
| ctrader-trading-agent | /opt/ctrader-trading-agent | main | 386f0e5 |
| manual-trading-agent | /home/emilio/manual-trading-agent | main | f432b48 |

### GitHub (Trujillofa)

Repos: manual-trading-agent, crypto-trading-agent, ctrader-trading-agent, depotru_database, depositotrujillo.co, Algorithmic_Trading_Machine_Learning, algorithmic-trading-python-master, Trujillo-s, finnhub-python, skills-introduction-to-github

### Branch inventory

| Repo | Local branches | Remote branches |
|---|---|---|
| manual-trading-agent | main, feat/deployment-and-tp-sl-fix | main, feat/deployment-and-tp-sl-fix, hetzner-merge, copilot/analyze-test-coverage |
| ctrader-trading-agent | main, stable, fix/backtest-same-bar-bias, fix/session-reporting | main, stable |
| mt5-trading-agent | docs/mt5-implementation-plan | main, docs/mt5-implementation-plan |

### Operating notes

- No git worktrees exist anywhere; all repos use single-checkout clones.
- manual-trading-agent has 3 copies (local, Hetzner, GitHub), so commits may diverge.
- ctrader-trading-agent has 2 copies (local, Hetzner), so commits may diverge.
- crypto-agent exists only on Hetzner; there is no local copy.
- SSH to Hetzner uses `ssh crypto-agent` (Tailscale IP) or `ssh crypto-agent-public` (public IP 46.225.119.221), both using key `~/.ssh/hetzner_deploy`.
- GitHub auth uses the Trujillofa account over HTTPS.
