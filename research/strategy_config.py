"""The MUTABLE strategy config — this is the file you (or the searcher) edit.

Analog of autoresearch's `train.py`. Everything here is fair game; the only
constraint is that `evaluate.evaluate_config(CONFIG)` runs without crashing.
Lower-risk edits are single-parameter nudges; bigger ones change the entry
character (RSI bounds, lookback, filters, exit logic). Keep a change only if
`research/run_experiment.py` reports verdict KEEP *and* an improved score.

CONFIG keys map 1:1 to scripts.run_donchian_backtest.run_config kwargs (for Donchian engine).
For live engine (engine="live_mtf_rsi"), many keys are forwarded as overrides to evaluate_entry
(e.g. lower_bound/upper_bound -> rsi_oversold/overbought, max_adx -> adx_threshold, buffer_pips,
confirm_bars, tp_atr_mult etc) so autosearch can explore the *live entry family* params.
See research/evaluate.py and src/scanner/evaluator.py (overrides=).
"""

from __future__ import annotations

# Current candidate. Starts from a production-leaning, realistic baseline.
CONFIG: dict = {
    "upper_bound": 70.0,        # RSI overbought (sell setups)
    "lower_bound": 30.0,        # RSI oversold (buy setups)
    "use_fixed_pip": False,     # False = ATR-scaled TP/SL (recommended)
    "tp_atr_mult": 1.0,         # take-profit = tp_atr_mult * ATR
    "sl_atr_mult": 3.0,         # stop-loss = sl_atr_mult * ATR
    "lookback": 20,             # Donchian HH/LL lookback
    "confirm_bars": 8,          # bars allowed for reclaim confirmation
    "buffer_pips": 0.0,         # breakout buffer
    "use_di_filter": False,     # +DI/-DI opposition filter
    "di_ratio": 1.65,
    "use_adx_filter": True,     # only trade when ADX < max_adx (ranging)
    "max_adx": 25.0,
    "use_session": False,       # restrict to a UTC session window
    "session_start": 6,
    "session_end": 21,
    "use_mom_fade": False,
    "mom_fade_bars": 3,
    "use_breakeven": False,     # move stop to entry once in profit
    "be_trigger_pct": 50.0,
    "use_trailing": False,
    "trail_atr_mult": 2.0,
    "use_time_exit": True,
    "max_bars_exit": 192,
    "spread_pips": 2.0,         # cost model (do not lower to flatter results)
    "commission_per_order": 3.0,
    "slippage_pips": 2.0,
}

# Search space for research/autosearch.py. Each key -> list of candidate values.
# Cost params are intentionally NOT searched (they are real-world constants).
PARAM_SPACE: dict[str, list] = {
    "upper_bound": [65.0, 70.0, 75.0, 80.0],
    "lower_bound": [20.0, 25.0, 30.0, 35.0],
    "tp_atr_mult": [1.0, 1.5, 2.0, 2.5],
    "sl_atr_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
    "lookback": [10, 15, 20, 30, 40],
    "confirm_bars": [2, 4, 6, 8, 12],
    "buffer_pips": [0.0, 0.5, 1.0],
    "use_di_filter": [False, True],
    "use_adx_filter": [False, True],
    "max_adx": [20.0, 25.0, 30.0],
    "use_session": [False, True],
    "use_breakeven": [False, True],
    "be_trigger_pct": [33.0, 50.0, 66.0],
    "use_trailing": [False, True],
    "trail_atr_mult": [1.5, 2.0, 3.0],
    "use_time_exit": [True, False],
    "max_bars_exit": [48, 96, 192],
}
