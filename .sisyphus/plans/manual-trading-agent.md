# Manual Trading Agent — Implementation Plan

## Context

**Goal**: Build a manual forex trading agent with RSI-based multi-timeframe strategy.

**Requirements** (from `instruction.md`):
1. All forex majors and minors
2. RSI 14 >70 or <30 across 3 timeframes: 1h, 30m, 15m
3. Highest high / lowest low for entry and trend checking
4. Lot size: 3
5. Check 3-star news before entries
6. TP ~$500, SL ~$1800 (high-accuracy strategy)
7. Lifecycle: Research → Backtesting → Implementation

**Reference Projects**:
- `/home/emilio/ctrader-trading-agent/` — Forex with cTrader, session momentum
- `/home/emilio/crypto-trading-agent/` — Crypto with Binance, MTF strategies

---

## Phase 1: Project Scaffolding

### 1.1 Core Files

| File | Purpose | Priority |
|------|---------|----------|
| `pyproject.toml` | Dependencies, ruff, mypy, pytest config | P0 |
| `config/settings.yaml` | All tunable parameters | P0 |
| `src/__init__.py` | Package init | P0 |
| `src/cli.py` | CLI entry point | P0 |
| `src/config/__init__.py` | Config loading | P1 |
| `src/config/settings.py` | Settings dataclass | P1 |

### 1.2 Dependencies

```
# Core
pydantic>=2.0
PyYAML>=6.0
structlog>=24.0

# HTTP client
httpx>=0.27.0
aiohttp>=3.9.0

# Data (forex)
yfinance>=0.2.36  # Research/backtesting
pandas>=2.0  # Data manipulation

# Risk/indicators
numpy>=1.26

# Testing
pytest>=8.0
pytest-asyncio>=0.23
pytest-cov>=4.1

# Dev
ruff>=0.3
mypy>=1.8
```

### 1.3 Config Structure

```yaml
# config/settings.yaml
trading:
  pairs:  # All majors + minors
    majors: [EUR/USD, GBP/USD, USD/JPY, USD/CHF, USD/CAD, AUD/USD, NZD/USD]
    minors: [EUR/GBP, EUR/JPY, EUR/CHF, EUR/AUD, EUR/CAD, ...]
  lot_size: 3

timeframes:
  regime: "1h"      # Trend/regime filter
  momentum: "30m"    # Momentum confirmation  
  entry: "15m"       # Execution trigger

strategy:
  rsi_period: 14
  rsi_overbought: 70
  rsi_oversold: 30
  lookback_high_low: 20  # bars for highest_high/lowest_low

risk:
  tp_usd: 500
  sl_usd: 1800
  max_positions: 1

news:
  enabled: true
  lockout_minutes_before: 60
  lockout_minutes_after: 30
  importance_threshold: 3  # 3-star only

data:
  provider: "yfinance"  # yfinance, alpha_vantage, oanda
  warmup_candles: 200
```

---

## Phase 2: Indicators

### 2.1 RSI Calculation

```python
# src/indicators/rsi.py
def calculate_rsi(prices: list[float], period: int = 14) -> float | None:
    """Standard RSI-14 calculation."""
    # Wilder's smoothing
    # Returns None if insufficient data
```

**Reference**: ctrader `src/strategy/__init__.py` has RSI implementation patterns.

### 2.2 Highest High / Lowest Low

```python
# src/indicators/high_low.py
def highest_high(prices: list[float], lookback: int) -> float | None:
    """Rolling highest high."""

def lowest_low(prices: list[float], lookback: int) -> float | None:
    """Rolling lowest low."""

def is_breakout_high(price: float, high_low: float) -> bool:
    """Check if price breaks highest high."""

def is_breakout_low(price: float, low: float) -> bool:
    """Check if price breaks lowest low."""
```

**Reference**: Donchian channel pattern in QuantConnect/Lean.

### 2.3 Indicator Dataclass

```python
# src/indicators/base.py
@dataclass
class MultiTimeframeIndicators:
    """Container for MTF indicator values."""
    # Timeframe-aligned indicators
    rsi_1h: float | None
    rsi_30m: float | None
    rsi_15m: float | None
    
    hh_15m: float | None  # Highest high 15m
    ll_15m: float | None  # Lowest low 15m
    
    trend: Literal["bullish", "bearish", "neutral"]
```

---

## Phase 3: Multi-Timeframe Strategy

### 3.1 Strategy Pattern

```python
# src/strategy/base.py
class BaseStrategy(ABC):
    REQUIRED_TIMEFRAMES = {
        "regime": "1h",
        "momentum": "30m", 
        "entry": "15m",
    }
    
    @abstractmethod
    async def evaluate(
        self, 
        symbol: str, 
        indicators: dict[str, float]
    ) -> Signal | None:
        pass
```

**Reference**: `crypto-trading-agent/src/strategy/base.py`

### 3.2 Signal Dataclass

```python
# src/strategy/signals.py
@dataclass
class Signal:
    symbol: str
    side: Literal["buy", "sell"]
    confidence: float  # 0-1
    entry_price: float | None
    tp_price: float | None
    sl_price: float | None
    lot_size: float
    reason: str
    timestamp: datetime
```

### 3.3 MTF RSI Strategy Logic

```
Entry conditions (ALL must be true):
1. RSI(14) on 1h > 70 OR < 30
2. RSI(14) on 30m > 70 OR < 30  
3. RSI(14) on 15m > 70 OR < 30
4. RSI direction aligned across all 3 timeframes
5. 15m price breaks highest_high OR lowest_low (lookback=20)
6. No 3-star news blocking

Entry:
- BUY if all RSI > 70 and price breaks hh_15m
- SELL if all RSI < 30 and price breaks ll_15m

Risk:
- TP: ~$500 (configurable)
- SL: ~$1800 (configurable)
- Lot: 3 fixed
```

**Reference**: `crypto-trading-agent/src/strategy/mtf_template.py` for MTF join pattern with no lookahead.

### 3.4 Data Joining Pattern

```python
# Pattern from crypto-agent (no lookahead):
# Regime (1h) bar must be CLOSED before joining to 30m/15m
# 15m is the execution/entry timeframe

def join_timeframes(entry_df, regime_df):
    """Join higher TF data without lookahead."""
    # Shift regime time forward to align with entry bars
    # Forward-fill missing regime values
```

---

## Phase 4: News Checker

### 4.1 News Checker Interface

```python
# src/news/news_checker.py
class NewsChecker:
    def __init__(
        self,
        lockout_before: int = 60,  # minutes
        lockout_after: int = 30,
        importance_threshold: int = 3,
    ):
        ...
    
    async def fetch_upcoming(self, hours_ahead: int = 24) -> list[NewsEvent]:
        """Fetch upcoming high-impact news."""
        
    def is_blocked(self, symbol: str, timestamp: datetime) -> bool:
        """Check if symbol is blocked due to news."""
        
    def get_resume_time(self, symbol: str) -> datetime | None:
        """Get next allowed trading time."""
```

### 4.2 Data Sources

| Provider | Pros | Cons |
|----------|------|------|
| Forex Factory (XML) | Free, widely used | No API, XML parsing |
| TradingEconomics | Official API, importance filter | Paid tiers |
| Investing.com | Good UI/alerts | No free API |

**Initial implementation**: Forex Factory XML feed parsing.

### 4.3 News Event Model

```python
@dataclass
class NewsEvent:
    timestamp: datetime
    currency: str  # EUR, USD, GBP, JPY, etc.
    name: str  # "Non-Farm Employment Change"
    importance: int  # 1, 2, or 3
    country: str
```

---

## Phase 5: Risk & Lot Sizing

### 5.1 Lot Sizing

```python
# src/execution/lot_sizing.py
class LotSizer:
    def __init__(self, config: TradingConfig):
        self.lot_size = config.lot_size  # Fixed at 3
        
    def calculate(self, account, symbol_info, entry, sl) -> LotResult:
        """Calculate lot size and verify risk bounds."""
        # Fixed lot = 3 (as per requirements)
        # Verify SL distance gives ~$1800 loss if hit
```

**Reference**: `ctrader-trading-agent/src/execution/lot_sizing.py`

### 5.2 Risk Manager

```python
# src/risk/manager.py
class RiskManager:
    """Centralized risk gates."""
    
    async def pre_trade_check(self, signal: Signal) -> RiskResult:
        """Validate trade against risk limits."""
        
    async def record_trade(self, trade: Trade) -> None:
        """Update PnL, drawdown."""
```

**Reference**: `crypto-trading-agent/src/risk/manager.py` and `ctrader-trading-agent/src/risk/manager.py`

---

## Phase 6: Backtesting

### 6.1 Backtest Engine

```python
# src/backtest/engine.py
class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        ...
    
    async def run(
        self,
        strategy: BaseStrategy,
        pairs: list[str],
        start: datetime,
        end: datetime,
    ) -> BacktestResult:
        """Run backtest across pairs and date range."""
```

### 6.2 Backtest Metrics

- Total return
- Win rate
- Max drawdown
- Profit factor
- Sharpe ratio
- Trade count
- Accuracy (wins / total)
- Average win/loss

**Reference**: `crypto-trading-agent/src/backtest/engine.py`

---

## Phase 7: CLI & Manual Operation

### 7.1 CLI Commands

```bash
# Manual trading agent CLI
python -m src.cli run          # Run in manual mode
python -m src.cli scan        # Scan for signals across all pairs
python -m src.cli analyze EUR/USD  # Analyze specific pair
python -m src.cli news         # Check upcoming news events
python -m src.cli backtest    # Run backtest

# Options
--pairs EUR/USD,GBP/USD       # Filter by pairs
--timeframe 15m               # Execution timeframe
--paper                        # Paper mode (default)
```

### 7.2 Output Format

```
[SCAN] EUR/USD
  RSI(1h)=75.2, RSI(30m)=78.1, RSI(15m)=71.4 ✓
  Price broke 20-bar high: 1.0850 > 1.0832
  Trend: BULLISH (aligned RSI > 70)
  News: No 3-star blocking
  → SIGNAL: BUY @ 1.0850, SL: 1.0650, TP: 1.0950
  Confidence: 0.85
```

---

## Implementation Order

| Phase | Task | Files | Priority |
|-------|------|-------|----------|
| 1 | Project scaffold | pyproject.toml, config/, src/ init | P0 |
| 2 | Indicators | src/indicators/rsi.py, high_low.py | P0 |
| 3 | Strategy base | src/strategy/base.py, signals.py | P0 |
| 4 | MTF RSI strategy | src/strategy/multi_timeframe.py | P0 |
| 5 | News checker | src/news/news_checker.py | P1 |
| 6 | Risk & lot sizing | src/risk/, src/execution/ | P1 |
| 7 | Backtesting | src/backtest/engine.py | P2 |
| 8 | CLI | src/cli.py | P2 |
| 9 | Tests | tests/ | P0 |
| 10 | Integration | End-to-end test | P1 |

---

## File Structure (Final)

```
manual-trading-agent/
├── AGENTS.md
├── instruction.md
├── pyproject.toml
├── config/
│   └── settings.yaml
├── src/
│   ├── __init__.py
│   ├── cli.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── indicators/
│   │   ├── __init__.py
│   │   ├── rsi.py
│   │   └── high_low.py
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── signals.py
│   │   └── multi_timeframe.py
│   ├── news/
│   │   ├── __init__.py
│   │   └── news_checker.py
│   ├── execution/
│   │   ├── __init__.py
│   │   └── lot_sizing.py
│   ├── risk/
│   │   ├── __init__.py
│   │   └── manager.py
│   └── backtest/
│       ├── __init__.py
│       └── engine.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_rsi.py
    ├── test_high_low.py
    ├── test_news_checker.py
    └── test_strategy.py
```

---

## Risks & Open Questions

| Risk | Mitigation |
|------|------------|
| yfinance data quality for live trading | Use OANDA for live, yfinance for backtest |
| News API reliability | Cache results, fallback to manual check |
| RSI false signals in ranging markets | Add ADX or trend filter |
| SL ~$1800 may be too wide for some pairs | Make SL configurable per pair |

---

## Next Steps

1. **Start with Phase 1** — Create pyproject.toml and project structure
2. **Then Phase 2** — Implement RSI and high/low indicators
3. **Then Phase 3** — Build MTF strategy logic
4. **Parallel Phase 4** — News checker can be built alongside strategy
5. **Phase 5-7** — Risk, backtest, CLI in sequence

Ready to begin implementation?
