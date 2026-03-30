"""Base class for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from src.strategy.signals import Signal


class BaseStrategy(ABC):
    """Abstract base class for trading strategies.

    All strategies must implement:
    - evaluate(): Generate trading signals based on indicators
    - get_name(): Return strategy name for logging
    """

    REQUIRED_TIMEFRAMES: dict[str, str] = {
        "regime": "1h",
        "momentum": "30m",
        "entry": "15m",
    }

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        self._config = config or {}

    @abstractmethod
    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal | None:
        """Evaluate indicators and generate trading signal.

        Args:
            symbol: Trading pair symbol
            indicators: Dictionary of latest indicator values

        Returns:
            Signal or None if no signal generated
        """
        pass

    def get_name(self) -> str:
        return self.__class__.__name__

    def get_config(self) -> Mapping[str, object]:
        return self._config
