"""Minimal daily portfolio bar-walker for the gross TSMOM gate.

Gross only (costs = 0). Explicit lookahead guard:
- Signal computed using data up to and including the close of bar t.
- The position is applied to the *return of bar t+1*.

This is the single most important anti-lookahead rule for daily momentum systems.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from research.multiasset.portfolio import (
    inverse_vol_weights,
    portfolio_equity_curve,
    portfolio_metrics,
)
from research.multiasset.signals import ts_momentum_series


def load_universe_d1(
    symbols: list[str],
    cache_root: Path | str = "data/cache/multiasset",
) -> pd.DataFrame:
    """Load daily closes for a list of symbols from the multiasset cache (pickle).

    Returns a DataFrame of closes (columns = symbols, index = date).
    Missing caches are skipped with a warning printed.
    """
    cache_root = Path(cache_root)
    closes: dict[str, pd.Series] = {}
    for sym in symbols:
        p = cache_root / f"{sym}_d1.pkl"
        if not p.exists():
            # try a couple normalizations
            p2 = cache_root / f"{sym.upper().replace('/', '')}_d1.pkl"
            if not p2.exists():
                print(f"[warn] no d1 cache for {sym} (looked for {p} and {p2})")
                continue
            p = p2
        df = pd.read_pickle(p)
        if isinstance(df, pd.DataFrame) and "close" in df.columns:
            s = df["close"].copy()
        else:
            # assume the df itself is the OHLC with close as a column or the series
            s = df["close"] if "close" in getattr(df, "columns", []) else df.squeeze()
        s.name = sym
        closes[sym] = s
    if not closes:
        return pd.DataFrame()
    out = pd.concat(closes.values(), axis=1).sort_index()
    return out


def compute_daily_returns(closes: pd.DataFrame) -> pd.DataFrame:
    return closes.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)


def run_gross_tsmom_backtest(
    closes: pd.DataFrame,
    lookback: int = 252,
    vol_window: int = 63,
    vol_target: float | None = None,  # if set, could scale whole book; not required for first gate
) -> dict:
    """Run the gross (costs=0) time-series momentum portfolio backtest.

    Returns a dict with:
      - equity: the portfolio equity curve (pd.Series)
      - metrics: pf, sharpe, mar, max_dd, ...
      - signals: the raw +1/0/-1 matrix (pre-shift)
      - shifted_signals: the ones actually used for returns (signal_t for return_{t+1})
      - corr: correlation matrix of daily instrument returns (for the diversification check)
    """
    if closes.empty or len(closes) < lookback + 10:
        return {"equity": pd.Series(dtype=float), "metrics": {}, "error": "insufficient data"}

    rets = compute_daily_returns(closes)

    # Per-instrument signals: computed on close of t
    sigs = {}
    for col in closes.columns:
        sigs[col] = ts_momentum_series(closes[col], lookback=lookback)
    signals = pd.DataFrame(sigs, index=closes.index).astype(int)

    # CRITICAL: shift signals by +1 so that signal on t's close is applied to t+1's return.
    # This eliminates the most common daily-bar lookahead bug.
    shifted = signals.shift(1)

    # Weights (inverse vol on instrument returns)
    w = inverse_vol_weights(rets, window=vol_window)

    equity = portfolio_equity_curve(rets, shifted, weights=w)

    metrics = portfolio_metrics(equity)

    corr = rets.corr()

    return {
        "equity": equity,
        "metrics": metrics,
        "signals": signals,
        "shifted_signals": shifted,
        "weights": w,
        "corr": corr,
        "lookback": lookback,
    }


def print_gate_report(result: dict, universe_label: str = "universe") -> None:
    """Pretty-print the numbers that feed THE GATE."""
    if result.get("error"):
        print(f"\n=== GROSS TSMOM ({universe_label}) ===")
        print(f"Insufficient data: {result.get('error')}")
        print(
            "Need history >> lookback (e.g. 300+ days for 252-bar momentum) for a real gross PF/Sharpe."
        )
        print(
            "This is expected on short windows; the long backfills (metals 2016+, indices 2018+, FX) will provide it."
        )
        return

    m = result.get("metrics", {})
    print(f"\n=== GROSS TSMOM GATE REPORT ({universe_label}) ===")
    print(f"lookback (bars): {result.get('lookback')}")
    print(f"days:            {m.get('n_days')}")
    print(f"final equity:    {m.get('final_equity', 0):.3f}")
    print(f"gross PF:        {m.get('pf', 0):.3f}")
    print(f"Sharpe (ann):    {m.get('sharpe', 0):.3f}")
    print(f"MAR:             {m.get('mar', 0):.3f}")
    print(f"Max DD:          {m.get('max_dd', 0):.2%}")
    print(f"Ann. return:     {m.get('ann_return', 0):.2%}")

    corr = result.get("corr")
    if corr is not None and not corr.empty:
        print("\nInstrument correlation matrix (daily returns):")
        print(corr.round(2).to_string())
        # Simple cluster warning
        mean_offdiag = corr.where(~np.eye(corr.shape[0], dtype=bool)).mean().mean()
        print(f"\nMean pairwise correlation (excl. diagonal): {mean_offdiag:.2f}")
        if mean_offdiag > 0.65:
            print(
                "  [WARN] High average correlation — the 'portfolio' may be fewer independent bets than it appears."
            )

    print("\n=== GATE DECISION (gross only) ===")
    pf = m.get("pf", 0)
    sharpe = m.get("sharpe", 0)
    if pf < 1.05 or sharpe < 0.2:
        print("Gross PF ≈ 1.0 or very low Sharpe → hypothesis has no accessible gross edge.")
        print("→ PIVOT (to XSMOM) or STOP. Do not build costs/guard/OOS machinery.")
    elif pf > 1.15 and sharpe > 0.6:
        print("Gross clearly > 1.1 with respectable risk-adjusted numbers.")
        print(
            "→ Proceed to build 0.5 (costs.py with swap), 0.6 (guard + engine seam), net + held-out OOS KEEP gate."
        )
    else:
        print(
            "Marginal gross result — ITERATE one lever (lookback, vol target, universe) and re-test gross."
        )
    print()
