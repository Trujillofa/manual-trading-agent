from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass
class TradingConfig:
    mode: Literal["paper", "live"] = "paper"
    majors: list[str] = field(
        default_factory=lambda: [
            "EUR/USD",
            "GBP/USD",
            "USD/JPY",
            "USD/CHF",
            "USD/CAD",
            "AUD/USD",
            "NZD/USD",
        ]
    )
    minors: list[str] = field(default_factory=list)
    lot_size: float = 3.0

    def __post_init__(self) -> None:
        if self.mode not in {"paper", "live"}:
            raise ValueError(f"trading.mode must be 'paper' or 'live', got: {self.mode}")
        if not self.majors:
            raise ValueError("trading.majors must contain at least one symbol")
        if not all(_is_non_empty_string(symbol) for symbol in self.majors):
            raise ValueError("trading.majors must contain only non-empty strings")
        if not all(_is_non_empty_string(symbol) for symbol in self.minors):
            raise ValueError("trading.minors must contain only non-empty strings")
        if self.lot_size <= 0:
            raise ValueError("trading.lot_size must be greater than 0")


@dataclass
class TimeframesConfig:
    regime: str = "1h"
    momentum: str = "30m"
    entry: str = "15m"

    def __post_init__(self) -> None:
        if not _is_non_empty_string(self.regime):
            raise ValueError("timeframes.regime must be a non-empty string")
        if not _is_non_empty_string(self.momentum):
            raise ValueError("timeframes.momentum must be a non-empty string")
        if not _is_non_empty_string(self.entry):
            raise ValueError("timeframes.entry must be a non-empty string")


@dataclass
class StrategyConfig:
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    lookback_bars: int = 20

    def __post_init__(self) -> None:
        if self.rsi_period <= 0:
            raise ValueError("strategy.rsi_period must be greater than 0")
        if not 0 < self.rsi_oversold < self.rsi_overbought < 100:
            raise ValueError(
                "strategy RSI thresholds must satisfy 0 < rsi_oversold < rsi_overbought < 100"
            )
        if self.lookback_bars <= 0:
            raise ValueError("strategy.lookback_bars must be greater than 0")


@dataclass
class RiskConfig:
    tp_usd: float = 500.0
    sl_usd: float = 1800.0
    max_concurrent_positions: int = 1

    def __post_init__(self) -> None:
        if self.tp_usd <= 0:
            raise ValueError("risk.tp_usd must be greater than 0")
        if self.sl_usd <= 0:
            raise ValueError("risk.sl_usd must be greater than 0")
        if self.max_concurrent_positions <= 0:
            raise ValueError("risk.max_concurrent_positions must be greater than 0")


@dataclass
class NewsConfig:
    enabled: bool = True
    lockout_minutes_before: int = 60
    lockout_minutes_after: int = 30
    importance_threshold: int = 3

    def __post_init__(self) -> None:
        if self.lockout_minutes_before < 0:
            raise ValueError("news.lockout_minutes_before must be >= 0")
        if self.lockout_minutes_after < 0:
            raise ValueError("news.lockout_minutes_after must be >= 0")
        if not 1 <= self.importance_threshold <= 3:
            raise ValueError("news.importance_threshold must be between 1 and 3")


@dataclass
class OandaConfig:
    api_key: str | None = None
    account_id: str | None = None
    practice: bool = True

    def __post_init__(self) -> None:
        # Allow env var substitution like "${OANDA_API_KEY}"
        if self.api_key and self.api_key.startswith("${"):
            self.api_key = None
        if self.account_id and self.account_id.startswith("${"):
            self.account_id = None


@dataclass
class DataConfig:
    provider: str = "yfinance"
    warmup_candles: int = 200
    oanda: OandaConfig = field(default_factory=OandaConfig)

    def __post_init__(self) -> None:
        if not _is_non_empty_string(self.provider):
            raise ValueError("data.provider must be a non-empty string")
        if self.warmup_candles <= 0:
            raise ValueError("data.warmup_candles must be greater than 0")


@dataclass
class Settings:
    trading: TradingConfig = field(default_factory=TradingConfig)
    timeframes: TimeframesConfig = field(default_factory=TimeframesConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    data: DataConfig = field(default_factory=DataConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Settings:
        if path is None:
            path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        config_path = Path(path)

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with config_path.open(encoding="utf-8") as file:
            payload = yaml.safe_load(file)

        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("Config file must define a YAML object")

        trading_data = payload.get("trading", {})
        if not isinstance(trading_data, dict):
            raise ValueError("'trading' must be a YAML object")

        pairs_data = trading_data.get("pairs", {})
        if pairs_data is None:
            pairs_data = {}
        if not isinstance(pairs_data, dict):
            raise ValueError("'trading.pairs' must be a YAML object")

        trading_payload: dict[str, Any] = {
            "mode": trading_data.get("mode", "paper"),
            "majors": trading_data.get("majors", pairs_data.get("majors", [])),
            "minors": trading_data.get("minors", pairs_data.get("minors", [])),
            "lot_size": trading_data.get("lot_size", 3.0),
        }

        timeframes_data = payload.get("timeframes", {})
        strategy_data = payload.get("strategy", {})
        risk_data = payload.get("risk", {})
        news_data = payload.get("news", {})
        data_data = payload.get("data", {})
        oanda_data = data_data.get("oanda", {}) if isinstance(data_data.get("oanda"), dict) else {}

        for section_name, section_data in (
            ("timeframes", timeframes_data),
            ("strategy", strategy_data),
            ("risk", risk_data),
            ("news", news_data),
            ("data", data_data),
        ):
            if not isinstance(section_data, dict):
                raise ValueError(f"'{section_name}' must be a YAML object")

        # Build DataConfig with nested OandaConfig
        data_payload = {
            "provider": data_data.get("provider", "yfinance"),
            "warmup_candles": data_data.get("warmup_candles", 200),
            "oanda": OandaConfig(
                api_key=oanda_data.get("api_key"),
                account_id=oanda_data.get("account_id"),
                practice=oanda_data.get("practice", True),
            ),
        }

        return cls(
            trading=TradingConfig(**trading_payload),
            timeframes=TimeframesConfig(**timeframes_data),
            strategy=StrategyConfig(**strategy_data),
            risk=RiskConfig(**risk_data),
            news=NewsConfig(**news_data),
            data=DataConfig(**data_payload),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
