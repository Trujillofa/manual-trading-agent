"""Portfolio construction for gross TSMOM diagnostic.

- Per-instrument vol targeting (or inverse-vol weights).
- Aggregate portfolio equity curve (gross, costs=0).
- Basic metrics (used for the gross gate).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def inverse_vol_weights(
    returns: pd.DataFrame,
    window: int = 63,  # ~3 months of daily data for vol estimate
    min_periods: int = 20,
    floor_vol: float = 0.05,  # avoid exploding weights on very low-vol regimes
) -> pd.DataFrame:
    """Compute inverse-vol weights (rebalanced daily, using trailing window vol).

    Weights sum to 1 each day. Higher vol instruments get lower weight.
    """
    vol = returns.rolling(window=window, min_periods=min_periods).std().clip(lower=floor_vol)
    inv_vol = 1.0 / vol
    w = inv_vol.div(inv_vol.sum(axis=1), axis=0)
    return w.fillna(0.0)


def vol_target_weights(
    returns: pd.DataFrame,
    target_vol: float = 0.10,  # 10% annualized portfolio vol target (rough)
    window: int = 63,
) -> pd.DataFrame:
    """Constant vol targeting at the portfolio level (simpler for gross diagnostic).

    Returns per-instrument weights such that the *portfolio* realized vol
    (ex-ante, using trailing cov) is close to target_vol.
    For the initial gross gate we often just use equal-risk (inverse-vol).
    """
    # For minimal implementation we fall back to inverse vol; a full vol-target
    # version would scale the whole book each day. Keep simple.
    return inverse_vol_weights(returns, window=window)


def portfolio_equity_curve(
    instrument_returns: pd.DataFrame,
    signals: pd.DataFrame,
    weights: pd.DataFrame | None = None,
    start_equity: float = 1.0,
) -> pd.Series:
    """Build gross portfolio equity curve.

    Args:
        instrument_returns: daily returns, columns = instruments, index = dates
        signals: +1/0/-1 per instrument per day (aligned to the *close* of that day;
                 the backtest must shift so that signal_t is applied on t+1)
        weights: per-day per-instrument allocation (sums to ~1). If None, equal weight.

    Returns:
        Equity curve (cumprod of (1 + port_ret)), starting at start_equity.
    """
    if weights is None:
        n = instrument_returns.shape[1]
        weights = pd.DataFrame(
            1.0 / n, index=instrument_returns.index, columns=instrument_returns.columns
        )

    # Align everything
    idx = instrument_returns.index.intersection(signals.index).intersection(weights.index)
    rets = instrument_returns.loc[idx]
    sigs = signals.loc[idx].astype(float)
    w = weights.loc[idx]

    # Position for "next bar" is already encoded by the caller shifting signals.
    # Here we just do position * return.
    strat_rets = (w * sigs * rets).sum(axis=1)

    equity = (1.0 + strat_rets).cumprod() * start_equity
    equity.name = "equity"
    return equity


def portfolio_metrics(equity: pd.Series) -> dict[str, float]:
    """Gross portfolio metrics for the gate (PF, Sharpe, MAR, maxDD, etc.)."""
    rets = equity.pct_change().dropna()
    if len(rets) < 10:
        return {
            "pf": 0.0,
            "sharpe": 0.0,
            "mar": 0.0,
            "max_dd": 0.0,
            "ann_return": 0.0,
            "n_days": len(rets),
        }

    # Simple profit factor (gross): sum of positive daily returns / abs(sum of negative)
    pos = rets[rets > 0].sum()
    neg = rets[rets < 0].sum()
    pf = (pos / (-neg)) if neg != 0 else (float("inf") if pos > 0 else 0.0)

    # Annualized Sharpe (daily, ~252)
    mu = rets.mean() * 252
    sigma = rets.std() * np.sqrt(252)
    sharpe = mu / sigma if sigma > 0 else 0.0

    # Max drawdown
    roll_max = equity.cummax()
    dd = (equity / roll_max - 1.0).min()
    max_dd = abs(dd)

    # MAR = (CAGR approx) / maxDD
    years = max(1e-9, (equity.index[-1] - equity.index[0]).days / 365.25)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0 if equity.iloc[0] > 0 else 0.0
    mar = cagr / max_dd if max_dd > 0 else (float("inf") if cagr > 0 else 0.0)

    return {
        "pf": float(pf),
        "sharpe": float(sharpe),
        "mar": float(mar),
        "max_dd": float(max_dd),
        "ann_return": float(cagr),
        "n_days": int(len(rets)),
        "final_equity": float(equity.iloc[-1]),
    }
