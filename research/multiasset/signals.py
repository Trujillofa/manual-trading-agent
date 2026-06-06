"""Pure signal functions for the multi-asset momentum program.

All functions are side-effect free (take prices, return signals or diagnostics).
This mirrors the purity discipline of src/scanner/evaluator.py.

For the gross-first gate we use a minimal time-series momentum rule with a
single sensible lookback (no sweeps, no per-instrument tuning).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

Signal = Literal[-1, 0, 1]


def ts_momentum(
    prices: pd.Series | list[float] | np.ndarray,
    lookback: int = 252,
    min_periods: int | None = None,
) -> int:
    """Time-series (absolute) momentum signal.

    Returns:
        +1 if trailing return over `lookback` bars is positive (long),
        -1 if negative (short),
         0 if insufficient data or zero return.

    The signal is computed on the *close of bar t* and is intended to be
    applied to the *next* bar (t+1) in the backtest to eliminate lookahead.
    """
    if min_periods is None:
        min_periods = max(20, lookback // 2)

    s = pd.Series(prices, dtype="float64") if not isinstance(prices, pd.Series) else prices
    if len(s) < min_periods:
        return 0

    # Use simple trailing total return (close_t / close_{t-lookback} - 1)
    # A more robust variant could use log returns or MA cross; keep minimal for gate.
    past = s.iloc[-lookback] if len(s) >= lookback else s.iloc[0]
    curr = s.iloc[-1]
    if past <= 0 or curr <= 0:
        return 0

    ret = (curr / past) - 1.0
    if ret > 0:
        return 1
    if ret < 0:
        return -1
    return 0


def ts_momentum_series(
    prices: pd.Series,
    lookback: int = 252,
) -> pd.Series:
    """Vectorized version returning a Series of signals aligned to the input index.

    Signal at position i is based on data up to i (close at i), for use on i+1.
    """
    rets = prices.pct_change(lookback)
    sig = rets.apply(lambda r: 1 if r > 0 else (-1 if r < 0 else 0))
    return sig.astype(int)


def correlation_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    """Pearson correlation of daily returns (for the diversification sanity check)."""
    return returns.corr()
