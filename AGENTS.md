# Manual Trading Agent — AGENTS.md

This file is the **source of truth** for all AI agents operating in this repository.

## Project Overview

- **Language**: Python 3.11+
- **Purpose**: Manual forex trading assistant with RSI-based multi-timeframe strategy
- **Reference Projects**: `/home/emilio/ctrader-trading-agent/` and `/home/emilio/crypto-trading-agent/`

## Related docs

- [`docs/BACKTEST_RUNNERS.md`](docs/BACKTEST_RUNNERS.md) — offline runner inventory (next-bar fills, `CostBook`, develop-only rank). Not a live-go.
- [`docs/MATH_MODELS_ROADMAP.md`](docs/MATH_MODELS_ROADMAP.md) — which math overlays to consider next (GARCH / regime / meta-label). Docs map only; not implement authorization. Shared `src/risk/` layer; not new strategies.

## Trading Strategy Requirements

From `instruction.md`:

1. **Instruments**: All forex majors and minors
2. **RSI Filter**: RSI 14 must be >70 or <30 across THREE timeframes: 1h, 30m, and 15m
3. **Entry References**: Use highest high and lowest low for entry and trend checking
4. **Lot Size**: 3
5. **News**: Always check for 3-star news that could impact the strategy
6. **TP/SL**: TP ~$500, SL ~$1800 (high-accuracy strategy, near 100%)
7. **Lifecycle**: Research → Backtesting → Implementation

## Build / Lint / Test Commands

```bash
# Create and activate venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v --tb=short

# Run single test file
pytest tests/test_strategy.py -v

# Run tests matching pattern
pytest -k "test_rsi" -v

# Lint with ruff
ruff check src/ tests/

# Format with ruff
ruff format src/ tests/

# Type check with mypy
mypy src/

# Pre-commit hooks (if configured)
pre-commit run --all-files
```

## Code Style Guidelines

### Python Standards

- **Python**: 3.11+
- **Formatter**: ruff-format (line-length 100)
- **Linter**: ruff (E, F, W, I, B, C4, UP rules)
- **Type checker**: mypy (permissive mode)

### Type Hints

```python
from __future__ import annotations

def calculate_rsi(prices: list[float], period: int = 14) -> float | None:
    """Calculate RSI indicator."""
    ...

# Use str | None instead of Optional[str]
# Avoid Any unless absolutely necessary
```

### Imports

```python
# Order: stdlib → third-party → local
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel

from src.strategy.base import BaseStrategy
```

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Classes | PascalCase | `SessionMomentumStrategy` |
| Functions/variables | snake_case | `calculate_rsi`, `atr_value` |
| Constants | UPPER_SNAKE | `MAX_DRAWDOWN`, `RSI_PERIOD` |
| Dataclasses | PascalCase | `@dataclass class TradeSignal:` |
| Private members | _leading_underscore | `_internal_state` |

### Error Handling

```python
# Good: Specific exceptions with context
raise ValueError(f"RSI value {rsi} out of range for {symbol}")

# Good: Try/except around I/O boundaries
try:
    result = await api_client.fetch_candles(symbol, timeframe)
except httpx.HTTPError as e:
    logger.error("API request failed", symbol=symbol, error=str(e))
    raise

# Bad: Bare except, catching everything
except:
    pass

# Bad: Nested try/except around pure logic
try:
    x = a + b  # Don't wrap pure logic
except Exception:
    ...
```

### Async Patterns

```python
# Use async/await throughout
async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
    """Evaluate strategy and return signal."""
    ...

# Reuse aiohttp sessions - don't create per request
class MyClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
```

### Logging

```python
# Use structlog or standard logging
import logging
logger = logging.getLogger(__name__)

# Never use print()
# Never log secrets (API keys, tokens, passwords)
```

## Project Structure

```
src/
├── __init__.py
├── cli.py                    # CLI entry point
├── strategy/
│   ├── __init__.py
│   ├── base.py               # BaseStrategy abstract class
│   ├── signals.py            # Signal dataclasses
│   └── multi_timeframe.py    # MTF RSI strategy (main strategy)
├── indicators/
│   ├── __init__.py
│   ├── rsi.py                # RSI calculation
│   └── high_low.py           # Highest high / lowest low
├── news/
│   ├── __init__.py
│   └── news_checker.py       # News impact checker
├── execution/
│   ├── __init__.py
│   └── lot_sizing.py         # Lot size calculator
├── risk/
│   ├── __init__.py
│   └── manager.py            # Risk management
└── config/
    ├── __init__.py
    └── settings.py            # Configuration management
tests/
├── __init__.py
├── conftest.py               # Pytest fixtures
├── test_strategy.py
├── test_indicators.py
└── test_risk.py
config/
└── settings.yaml             # All tunable parameters
```

## Module Responsibilities

| Module | Purpose |
|--------|---------|
| `src/strategy/` | Strategy logic, signal generation |
| `src/indicators/` | Technical indicator calculations (RSI, high/low) |
| `src/news/` | News impact checking (3-star events) |
| `src/execution/` | Lot sizing, order management |
| `src/risk/` | Risk limits, drawdown tracking |
| `src/config/` | Settings parsing and validation |

## Test Patterns

```python
import pytest
from src.indicators.rsi import calculate_rsi

@pytest.mark.asyncio
async def test_rsi_overbought():
    """RSI above 70 indicates overbought."""
    prices = [100 + i for i in range(20)]
    result = calculate_rsi(prices, period=14)
    assert result is not None
    assert result > 70

@pytest.fixture
def sample_ohlcv():
    """Sample OHLCV data for testing."""
    return {
        "open": 1.0850,
        "high": 1.0900,
        "low": 1.0800,
        "close": 1.0880,
        "volume": 10000,
    }
```

## Safety Rules

- **Paper mode is default** — no real orders without explicit config
- **Never commit secrets** — use `.env` for API keys, `.env.example` for templates
- **Risk checks required** — validate all trades against risk limits before execution
- **No live trading without approval** — explicit human authorization needed

## Coordination Protocol

1. **Read before write** — Always read existing files before editing
2. **Test before commit** — All tests must pass
3. **Branch for non-trivial work** — `feat/<description>`
4. **Conventional commits** — `feat(<scope>): <description>`

## Reference Projects

This project references two existing trading agents:

- `/home/emilio/ctrader-trading-agent/` — Forex trading with cTrader, session momentum strategy
- `/home/emilio/crypto-trading-agent/` — Crypto trading on Binance, multi-strategy framework

Both projects follow similar patterns and can be used for implementation reference.
