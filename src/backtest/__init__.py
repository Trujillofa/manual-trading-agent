"""Backtest module - strategy validation tooling.

Offline only. Nothing in this package sends broker orders or promotes a
strategy to live. See ``docs/BACKTEST_RUNNERS.md``.
"""

from __future__ import annotations

from src.backtest.cost_book import CostBook, pip_size_for_pair

__all__ = ["CostBook", "pip_size_for_pair"]
