from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _resolve_env_placeholder(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return value
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1])
    return value


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
    shadow: list[str] = field(default_factory=list)
    lot_size: float = 3.0

    def __post_init__(self) -> None:
        if self.mode not in {"paper", "live"}:
            raise ValueError(f"trading.mode must be 'paper' or 'live', got: {self.mode}")
        if not self.majors and not self.shadow:
            raise ValueError("trading.majors and trading.shadow cannot both be empty")
        if not all(_is_non_empty_string(symbol) for symbol in self.majors):
            raise ValueError("trading.majors must contain only non-empty strings")
        if not all(_is_non_empty_string(symbol) for symbol in self.minors):
            raise ValueError("trading.minors must contain only non-empty strings")
        if not all(_is_non_empty_string(symbol) for symbol in self.shadow):
            raise ValueError("trading.shadow must contain only non-empty strings")
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
class PairOverride:
    """Per-pair parameter overrides that take precedence over global defaults."""

    sma_period: int | None = None
    tp_atr_multiplier: float | None = None
    sl_atr_multiplier: float | None = None
    adx_threshold: float | None = None

    def __post_init__(self) -> None:
        if self.sma_period is not None and self.sma_period <= 0:
            raise ValueError("pair_override sma_period must be greater than 0")
        if self.tp_atr_multiplier is not None and self.tp_atr_multiplier <= 0:
            raise ValueError("pair_override tp_atr_multiplier must be greater than 0")
        if self.sl_atr_multiplier is not None and self.sl_atr_multiplier <= 0:
            raise ValueError("pair_override sl_atr_multiplier must be greater than 0")
        if self.adx_threshold is not None and self.adx_threshold < 0:
            raise ValueError("pair_override adx_threshold must be >= 0")


@dataclass
class StrategyConfig:
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    lookback_bars: int = 20
    cooldown_minutes: int = 60
    session_filter_enabled: bool = True
    session_allowed_utc: list[str] = field(default_factory=lambda: ["06-17", "12-21"])
    breakout_buffer_pips: float = 0.0
    spread_filter_enabled: bool = False
    max_spread_pips: float = 2.0
    spread_limits_pips: dict[str, float] = field(default_factory=dict)
    sma_period: int = 50
    sma_alignment_enabled: bool = True
    rsi_ma_gate_enabled: bool = False
    rsi_ma_gate_period: int = 5
    pair_priorities: dict[str, int] = field(
        default_factory=lambda: {
            "EUR/GBP": 100,
            "USD/JPY": 80,
            "GBP/USD": 60,
            "GBP/CHF": 60,
            "EUR/USD": 40,
        }
    )
    pair_overrides: dict[str, PairOverride] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rsi_period <= 0:
            raise ValueError("strategy.rsi_period must be greater than 0")
        if not 0 < self.rsi_oversold < self.rsi_overbought < 100:
            raise ValueError(
                "strategy RSI thresholds must satisfy 0 < rsi_oversold < rsi_overbought < 100"
            )
        if self.lookback_bars <= 0:
            raise ValueError("strategy.lookback_bars must be greater than 0")
        if self.cooldown_minutes < 0:
            raise ValueError("strategy.cooldown_minutes must be >= 0")
        if self.max_spread_pips < 0:
            raise ValueError("strategy.max_spread_pips must be >= 0")
        if self.sma_period <= 0:
            raise ValueError("strategy.sma_period must be greater than 0")
        if self.rsi_ma_gate_period <= 0:
            raise ValueError("strategy.rsi_ma_gate_period must be greater than 0")
        for pair, limit in self.spread_limits_pips.items():
            if not _is_non_empty_string(pair):
                raise ValueError("strategy.spread_limits_pips keys must be non-empty strings")
            if float(limit) < 0:
                raise ValueError("strategy.spread_limits_pips values must be >= 0")


@dataclass
class RiskConfig:
    tp_atr_multiplier: float = 1.5
    sl_atr_multiplier: float = 2.0
    tp_usd_legacy: float = 300.0
    sl_usd_legacy: float = 900.0
    max_concurrent_positions: int = 1
    max_daily_loss_usd: float = 1500.0

    @property
    def tp_usd(self) -> float:
        return self.tp_usd_legacy

    @property
    def sl_usd(self) -> float:
        return self.sl_usd_legacy

    def __post_init__(self) -> None:
        if self.tp_atr_multiplier <= 0:
            raise ValueError("risk.tp_atr_multiplier must be greater than 0")
        if self.sl_atr_multiplier <= 0:
            raise ValueError("risk.sl_atr_multiplier must be greater than 0")
        if self.max_concurrent_positions <= 0:
            raise ValueError("risk.max_concurrent_positions must be greater than 0")
        if self.max_daily_loss_usd <= 0:
            raise ValueError("risk.max_daily_loss_usd must be greater than 0")


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


@dataclass
class TelegramConfig:
    bot_token: str | None = None
    chat_id: str | None = None
    enabled: bool = True

    signal_notifications: bool = True
    near_setup_notifications: bool = True
    aligned_pending_notifications: bool = True
    scan_results: bool = True

    def __post_init__(self) -> None:
        self.bot_token = _resolve_env_placeholder(self.bot_token)
        self.chat_id = _resolve_env_placeholder(self.chat_id)

        if not _is_non_empty_string(self.bot_token):
            self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not _is_non_empty_string(self.chat_id):
            self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    @property
    def is_configured(self) -> bool:
        return _is_non_empty_string(self.bot_token) and _is_non_empty_string(self.chat_id)


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
    telegram: TelegramConfig = field(default_factory=TelegramConfig)

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
            "shadow": trading_data.get("shadow", pairs_data.get("shadow", [])),
            "lot_size": trading_data.get("lot_size", 3.0),
        }

        timeframes_data = payload.get("timeframes", {})
        strategy_data = payload.get("strategy", {})
        risk_data = payload.get("risk", {})
        news_data = payload.get("news", {})
        data_data = payload.get("data", {})
        telegram_data = payload.get("telegram", {})
        oanda_data = data_data.get("oanda", {}) if isinstance(data_data.get("oanda"), dict) else {}

        for section_name, section_data in (
            ("timeframes", timeframes_data),
            ("strategy", strategy_data),
            ("risk", risk_data),
            ("news", news_data),
            ("data", data_data),
            ("telegram", telegram_data),
        ):
            if not isinstance(section_data, dict):
                raise ValueError(f"'{section_name}' must be a YAML object")

        # Build DataConfig with nested OandaConfig
        data_config = DataConfig(
            provider=data_data.get("provider", "yfinance"),
            warmup_candles=data_data.get("warmup_candles", 200),
            oanda=OandaConfig(
                api_key=oanda_data.get("api_key"),
                account_id=oanda_data.get("account_id"),
                practice=oanda_data.get("practice", True),
            ),
        )

        telegram_payload: dict[str, Any] = {
            "bot_token": telegram_data.get("bot_token"),
            "chat_id": telegram_data.get("chat_id"),
            "enabled": telegram_data.get("enabled", True),
            "signal_notifications": telegram_data.get("signal_notifications", True),
            "near_setup_notifications": telegram_data.get("near_setup_notifications", True),
            "aligned_pending_notifications": telegram_data.get(
                "aligned_pending_notifications", True
            ),
            "scan_results": telegram_data.get("scan_results", True),
        }

        # Build pair_overrides from strategy config
        raw_overrides = strategy_data.get("pair_overrides", {})
        pair_overrides_parsed: dict[str, PairOverride] = {}
        if isinstance(raw_overrides, dict):
            for pair_key, override_data in raw_overrides.items():
                if isinstance(override_data, dict):
                    pair_overrides_parsed[pair_key] = PairOverride(**override_data)

        strategy_payload = {
            k: v for k, v in strategy_data.items() if k != "pair_overrides"
        }
        strategy_payload["pair_overrides"] = pair_overrides_parsed

        return cls(
            trading=TradingConfig(**trading_payload),
            timeframes=TimeframesConfig(**timeframes_data),
            strategy=StrategyConfig(**strategy_payload),
            risk=RiskConfig(**risk_data),
            news=NewsConfig(**news_data),
            data=data_config,
            telegram=TelegramConfig(**telegram_payload),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
