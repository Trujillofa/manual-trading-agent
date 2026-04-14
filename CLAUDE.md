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

### Production config (current live watchlist, 2026-04-14)

Promoted pairs actively scanned and allowed to fire Telegram signals (`config/settings.yaml`):

| Pair | Confirmation profile | Source |
|---|---|---|
| EUR/GBP | V2_b0.5_c2 | `docs/reports/CONFIRMATION_BAKEOFF_FULL_REPORT_2026-03-31.md` |
| GBP/CHF | V1_b0.5_c0 | same |
| AUD/CAD | V1_b2_c0 | same |

Shadow-run only (not in `config/settings.yaml`; do not alert, evaluate separately):

| Pair | Candidate profile | Status |
|---|---|---|
| GBP/USD | V2_b1_c2 (compromise across 365d/180d) | not promoted — 365d and 180d disagree on winner and regime family |

Rejected (explicit, do not re-add without new evidence): AUD/JPY, NZD/USD, NZD/JPY, EUR/CHF, EUR/CAD, USD/JPY, USD/CHF, GBP/CAD, AUD/NZD.

Shared parameters across promoted set:
- **RSI thresholds**: 30/70 on 1h, 30m, 15m (per `config/settings.yaml`)
- **TP/SL**: ATR-based — TP = 1.5 × ATR(14), SL = 2.0 × ATR(14)
- **ADX filter**: ADX(14) < 25 on 1h (mean-reversion only in ranging regime)
- **Session filter**: 06–17 UTC, 12–21 UTC
- **News lockout**: 3-star Forex Factory events; 60 min before / 30 min after
- **Lot size**: 3.0
- **Data source**: yfinance (live scanner), Dukascopy M1 (backtests)

Entry-variant naming (used in confirmation profiles and report tables):
- `V1` — breakout continuation; BUY breaks below LL, SELL breaks above HH
- `V2` — reversal; wick through LL/HH + close reclaim
- `b{N}` — buffer in pips; `c{N}` — confirmation lifetime in bars after RSI alignment

### Promotion gate for new pairs

Before a shadow-run pair is promoted into `config/settings.yaml`:

1. On the shortest validation window tested (currently 180d Dukascopy): **≥ 30 trades**
2. **Positive total PnL** on that window
3. **Profit factor clearly > 1** (treat PF with N < 30 as unreliable regardless of value)
4. **No regime flip across windows** — winning variant family (V1 vs V2) must be the same on 180d and 365d, and signs must agree

Failing any gate → keep shadow-run or reject. Low trade count is the dominant failure mode in this dataset.

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
