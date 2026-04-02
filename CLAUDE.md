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
python -m src.cli scan --pairs EUR/USD GBP/USD    # scan pairs for signals
python -m src.cli analyze EUR/USD                  # deep single-pair analysis
python -m src.cli news --hours 24                  # upcoming high-impact news
python -m src.cli backtest --pair EUR/USD --start 2024-01-01 --end 2024-06-01
python -m src.cli backtest-enhanced --pair EUR/USD  # enhanced with TP/SL simulation
python -m src.cli telegram-poll                    # long-running Telegram command listener

# Docker (production on Hetzner)
docker compose up -d                 # runs scan every 15min + telegram-poll
```

## Architecture

**Multi-timeframe RSI forex scanner** that alerts via Telegram when RSI aligns across 1h/30m/15m timeframes.

### Signal Pipeline (scan command)

```
DataFetcher (yfinance/OANDA) → fetch 1h, 30m, 15m OHLCV per pair
    ↓
MTFRSIStrategy → RSI alignment check (>70 or <30 on all 3 TFs)
    + CandlePattern detection (hammer, shooting star, doji)
    + RSI divergence detection (bullish/bearish)
    + HH/LL breakout confirmation
    ↓
Validation gates:
    - NewsChecker (Forex Factory 3-star events → lockout window)
    - Session filter (configurable UTC hours)
    - Cooldown (min time between signals per pair)
    - Spread filter (if OANDA quote available)
    ↓
Signal output → signal_audit.jsonl + Telegram notification
```

### Key design decisions

- **config/settings.yaml** is the single source for all tunable parameters (RSI thresholds, TP/SL, news lockout, session hours, pair lists). Settings class in `src/config/settings.py` loads and validates it.
- **State files in logs/**: `cooldown_state.json`, `near_setup_state.json`, `news_cache.json` persist between scan runs. `signal_audit.jsonl` is an append-only audit trail.
- **Async throughout**: CLI uses `asyncio.run()`, data fetchers and Telegram use async HTTP clients (httpx/aiohttp). TelegramNotifier falls back to background thread if no event loop.
- **Graceful degradation**: missing news feed doesn't block scanning, missing OANDA quote skips spread check.

### Strategy rules (from instruction.md)

- RSI 14 must be >70 or <30 across all three timeframes (1h, 30m, 15m)
- Entry references: highest high / lowest low over 20-bar lookback
- Default lot size: 3.0
- TP ~$500, SL ~$1800 (high-accuracy strategy)
- 3-star Forex Factory news blocks trading within lockout window

## Code Conventions

- Python 3.11+, `from __future__ import annotations` in all modules
- `str | None` not `Optional[str]`, `list[float]` not `List[float]`
- ruff for linting (line-length 100) and formatting
- structlog or `logging.getLogger(__name__)` — never `print()`
- Conventional commits: `feat(<scope>): description`
