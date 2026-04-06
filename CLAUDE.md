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
    - Cooldown (min time between signals per pair)
    ↓
Signal output → signal_audit.jsonl + Telegram notification
    TP = 1.0 × ATR(14), SL = 3.0 × ATR(14)
```

### Key design decisions

- **config/settings.yaml** is the single source for all tunable parameters (RSI thresholds, TP/SL, news lockout, session hours, pair lists). Settings class in `src/config/settings.py` loads and validates it.
- **State files in logs/**: `cooldown_state.json`, `near_setup_state.json`, `news_cache.json` persist between scan runs. `signal_audit.jsonl` is an append-only audit trail.
- **Async throughout**: CLI uses `asyncio.run()`, data fetchers and Telegram use async HTTP clients (httpx/aiohttp). TelegramNotifier falls back to background thread if no event loop.
- **Graceful degradation**: missing news feed doesn't block scanning, missing OANDA quote skips spread check.

### Production config (validated 360-day OOS backtest, 2026-04-03)

- **Pairs**: GBP/USD (+1.45%, PF 1.54), NZD/USD (+0.62%, PF 1.24), AUD/JPY (+0.63%, PF 1.16)
- **Entry variant**: V2 reversal breakout (wick through LL/HH + close reclaim)
- **RSI thresholds**: 30/70 across all three timeframes (1h, 30m, 15m)
- **Buffer**: 2.0 pips on breakout level
- **Confirm bars**: 5 (window after RSI alignment to accept breakout)
- **TP/SL**: 1.0 ATR / 3.0 ATR (high win-rate, ~74% WR on GBP/USD)
- **ADX filter**: ADX(14) < 25 on 1h (skip trending markets)
- **Lot size**: 3.0
- **News lockout**: 3-star Forex Factory events block trading
- **Data source**: yfinance (live scanner), Dukascopy M1 (backtests)

### Backtest data

- **Dukascopy fetcher** (`src/data/dukascopy_fetcher.py`): Downloads M1 bi5 binary data, resamples to h1/m30/m15
- **Optimization script** (`scripts/run_entry_optimization.py`): Grid search over variants, RSI, buffer, confirm bars, TP/SL, ADX
- All 24 pairs screened over 360 days; only 3 profitable with current config

## Code Conventions

- Python 3.11+, `from __future__ import annotations` in all modules
- `str | None` not `Optional[str]`, `list[float]` not `List[float]`
- ruff for linting (line-length 100) and formatting
- structlog or `logging.getLogger(__name__)` — never `print()`
- Conventional commits: `feat(<scope>): description`
