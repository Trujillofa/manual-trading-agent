# Manual Trading Agent — Operating Instructions

**IMPORTANT: Read CLAUDE.md before making ANY changes to config or code.**

CLAUDE.md is the authoritative source for:
- Which pairs are promoted, shadow-only, or rejected
- Confirmation profiles per pair
- TP/SL, spread limits, session hours, ADX thresholds
- Promotion gate criteria

## Config change policy

1. **NEVER edit `config/settings.yaml` without reading CLAUDE.md first**
2. **NEVER add pairs to `majors:` or `minors:` unless they pass the promotion gate in CLAUDE.md**
3. **NEVER widen spread limits, relax ADX thresholds, or change session hours without backtesting evidence**
4. If CLAUDE.md and `config/settings.yaml` disagree, CLAUDE.md wins — restore config to match
5. If asked to "match documentation", the documentation is CLAUDE.md, not this file

## Current state (updated 2026-04-20)

All pairs are **shadow-only** (audit records, no Telegram alerts).
No pairs have passed the promotion gate. See CLAUDE.md for details.

## Original brief (historical, superseded by CLAUDE.md)

Multi-timeframe RSI forex scanner:
- RSI 14 alignment across 1h/30m/15m (< 30 or > 70)
- Highest high / lowest low as entry references
- News lockout on 3-star Forex Factory events
- ATR-based TP/SL (currently TP = 1.5x ATR, SL = 2.0x ATR)
- Lot size: 3
