"""Backtest module - strategy validation tooling.

Offline only. Nothing in this package sends broker orders or promotes a
strategy to live. See ``docs/BACKTEST_RUNNERS.md``.
"""

from __future__ import annotations

from src.backtest.cost_book import CostBook, pip_size_for_pair
from src.backtest.exits import same_bar_exit
from src.backtest.windows import (
    DEVELOP_FRAC,
    WindowMetrics,
    cutoff_at,
    split_trade_metrics,
)

__all__ = [
    "CostBook",
    "DEVELOP_FRAC",
    "WindowMetrics",
    "cutoff_at",
    "pip_size_for_pair",
    "same_bar_exit",
    "split_trade_metrics",
]
