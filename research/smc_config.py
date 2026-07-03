"""Mutable SMC strategy config for autosearch.

Maps to ``scripts.run_smc_backtest.StrategyConfig``. Keep a change only when
``research.smc_autosearch.evaluate_smc_config`` reports an improved score.
"""

from __future__ import annotations

CONFIG: dict = {
    "entry_mode": "ob_retest",
    "tag_filter": "all",
    "swing_length": 50,
    "structure_timeframe": "1h",
    "require_zone": False,
    "ob_retest_bars": 16,
    "atr_period": 14,
    "tp_atr": 2.0,
    "sl_atr": 1.5,
    "max_hold_bars": 32,
}

PARAM_SPACE: dict[str, list] = {
    "entry_mode": ["immediate", "ob_retest", "htf_swing_map"],
    "tag_filter": ["all", "bos", "choch"],
    "swing_length": [20, 30, 50, 70],
    "structure_timeframe": ["15m", "1h", "4h"],
    "ob_retest_bars": [8, 16, 24, 32],
    "atr_period": [10, 14, 21],
    "tp_atr": [1.0, 1.5, 2.0, 2.5, 3.0],
    "sl_atr": [1.0, 1.5, 2.0, 2.5],
    "max_hold_bars": [16, 32, 48, 64],
}
