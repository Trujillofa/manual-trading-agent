# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Tests
pytest tests/ -v --tb=short          # all tests
pytest tests/test_strategy.py -v     # single file
pytest -k "test_rsi" -v              # pattern match

# Lint & format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/

# CLI usage (all commands use: python -m src.cli <command>)
python -m src.cli scan --pairs GBP/USD             # scan pairs for signals
python -m src.cli analyze GBP/USD                  # deep single-pair analysis
python -m src.cli news --hours 24                  # upcoming high-impact news
python -m src.cli dashboard --days 30              # signal dashboard + paper P&L
python -m src.cli backtest --pair GBP/USD --start 2024-01-01 --end 2024-06-01
python -m src.cli backtest-enhanced --pair GBP/USD  # enhanced with TP/SL simulation
python -m src.cli telegram-poll                    # long-running Telegram command listener

# Backtest optimization (Dukascopy M1 data, runs on Hetzner)
python scripts/run_entry_optimization.py \
  --pairs "GBP/USD,NZD/USD,AUD/JPY" --days 360 --source dukascopy \
  --variants V2 --rsi-thresholds 30/70 --buffers 2.0 \
  --confirm-bars 5 --tp-sl-ratios 1.0:3.0 --adx-threshold 25

# Docker (production on Hetzner)
docker compose up -d                 # runs scan every 15min + telegram-poll
```

## Architecture

**Multi-timeframe RSI forex scanner** that alerts via Telegram when RSI aligns across 1h/30m/15m timeframes.

### Signal Pipeline (scan command)

```
DataFetcher (yfinance) → fetch 1h, 30m, 15m OHLCV per pair
    ↓
V2 Reversal Breakout Check:
    - RSI 14 alignment (all 3 TFs < 30 or > 70)
    - Wick through 20-bar LL/HH + close reclaim (buffer 2.0 pips)
    - Confirmation window: 5 bars after alignment
    + CandlePattern detection (hammer, shooting star, doji)
    + RSI divergence detection (bullish/bearish)
    ↓
Validation gates:
    - ADX trend filter (ADX < 25 = ranging, safe for mean-reversion)
    - NewsChecker (Forex Factory 3-star events → lockout window)
    - Session filter (configurable UTC hours)
    - Active-signal suppression (Rule C): one signal per pair per direction
      until invalidated by TP hit, SL hit, RSI(15m) midline cross, or SMA flip
    ↓
Signal output → signal_audit.jsonl + Telegram notification
    TP = 1.0 × ATR(14), SL = 3.0 × ATR(14)
    V0 (RSI-only): fires on alignment alone, no breakout gate
```

### Key design decisions

- **config/settings.yaml** is the single source for all tunable parameters (RSI thresholds, TP/SL, news lockout, session hours, pair lists). Settings class in `src/config/settings.py` loads and validates it.
- **State files in logs/**: `active_signal_state.json` (Rule C: one record per pair while a signal is live), `near_setup_state.json`, `news_cache.json` persist between scan runs. `signal_audit.jsonl` is an append-only audit trail.
- **Async throughout**: CLI uses `asyncio.run()`, data fetchers and Telegram use async HTTP clients (httpx/aiohttp). TelegramNotifier falls back to background thread if no event loop.
- **Graceful degradation**: missing news feed doesn't block scanning, missing OANDA quote skips spread check.

### Production config (current state, 2026-05-05)

**27 pairs enabled** (all majors and minors except EUR/GBP). The promotion gate is intentionally relaxed in favour of broad coverage with the Rule C invalidation rule (see below). Five pairs retain backtest-validated per-pair overrides via `strategy.pair_overrides`; the other 22 use defaults (SMA 50, TP 1.0×ATR, SL 3.0×ATR).

Tuned (per-pair overrides):

| Pair | Config | SMA | TP/SL (ATR) | Trades (2y) | PnL % | PF | WR | Promotion date |
|---|---|---|---|---|---|---|---|---|
| GBP/CHF | default | 50 | 1.0/3.0 | 290 | +88% | 1.30 | 64% | 2026-04-27 |
| NZD/JPY | override | 20 | 2.5/2.5 | 269 | +126% | 1.29 | 52% | 2026-04-27 |
| GBP/JPY | override | 20 | 1.5/2.5 | 319 | +97% | 1.27 | 64% | 2026-04-27 |
| USD/JPY | override | 40 | 2.0/2.5 | 300 | +46% | 1.13 | 54% | 2026-04-27 |
| AUD/CAD | default | 50 | 1.0/3.0 | 264 | +13% | 1.05 | 58% | 2026-04-20 |

Default config (no override, scout-quality until backtested):
EUR/USD, GBP/USD, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/JPY, EUR/CHF, EUR/AUD, EUR/CAD, EUR/NZD, GBP/AUD, GBP/CAD, GBP/NZD, AUD/JPY, AUD/CHF, AUD/NZD, NZD/CAD, NZD/CHF, CAD/JPY, CAD/CHF, CHF/JPY.

Rejected (excluded from config):
EUR/GBP — negative PnL in all 48 configs tested (best: -0.30%, PF 0.66). 2026-04-20 sweep.

**Rule C — one signal per pair per direction, until invalidated.**
After a signal fires, same-direction signals for that pair are suppressed until **any** of:
1. **TP hit** — 15m high (BUY) / low (SELL) reaches the original TP since fire time.
2. **SL hit** — 15m low (BUY) / high (SELL) reaches the original SL since fire time.
3. **RSI(15m) midline cross** — RSI ≥ 50 (BUY) or ≤ 50 (SELL) on any closed 15m bar since fire.
4. **SMA flip** — current 15m close on the opposite side of pair-SMA vs. signal direction.

Opposite-direction signals are always allowed and re-arm both sides. State persists in `logs/active_signal_state.json`. The legacy `strategy.cooldown_minutes` setting is retained for backwards compatibility but no longer gates anything.

Shared parameters:
- **RSI thresholds**: 30/70 on 1h, 30m, 15m (per `config/settings.yaml`)
- **TP/SL**: ATR-based — TP = 1.0 × ATR(14), SL = 3.0 × ATR(14)
- **ADX filter**: ADX(14) < 25 on 1h (mean-reversion only in ranging regime)
- **Session filter**: 06–17 UTC, 12–21 UTC
- **News lockout**: 3-star Forex Factory events; 60 min before / 30 min after
- **Lot size**: 3.0
- **Data source**: yfinance (live scanner), Dukascopy M1 (backtests)

Entry-variant naming (used in confirmation profiles and report tables):
- `V0` — RSI-only; fires on MTF alignment alone, no breakout gate
- `V1` — breakout continuation; BUY breaks below LL, SELL breaks above HH
- `V2` — reversal; wick through LL/HH + close reclaim
- `b{N}` — buffer in pips; `c{N}` — confirmation lifetime in bars after RSI alignment

### Promotion gate for per-pair overrides

As of 2026-05-05 the watchlist is broad (27 pairs); this gate now applies to **adding a per-pair override** (custom SMA/TP/SL via `strategy.pair_overrides`), not to whether a pair is scanned at all.

1. On the shortest validation window tested (currently 180d Dukascopy): **≥ 30 trades**
2. **Positive total PnL** on that window
3. **Profit factor clearly > 1** (treat PF with N < 30 as unreliable regardless of value)
4. **No regime flip across windows** — winning variant family (V1 vs V2) must be the same on 180d and 365d, and signs must agree

Failing any gate → keep on default params. Low trade count is the dominant failure mode in this dataset.

### Backtest data

- **Dukascopy fetcher** (`src/data/dukascopy_fetcher.py`): Downloads M1 bi5 binary data, resamples to h1/m30/m15
- **Bake-off script** (`scripts/run_confirmation_bakeoff.py`): Sweeps variants × buffers × confirm-bars per pair; artifacts under `results/`
- **Optimization script** (`scripts/run_entry_optimization.py`): Broader grid including RSI, TP/SL, ADX
- Latest validation: see `docs/reports/WATCHLIST_EXPANSION_2026-04-14.md`

## Code Conventions

- Python 3.11+, `from __future__ import annotations` in all modules
- `str | None` not `Optional[str]`, `list[float]` not `List[float]`
- ruff for linting (line-length 100) and formatting
- structlog or `logging.getLogger(__name__)` — never `print()`
- Conventional commits: `feat(<scope>): description`
