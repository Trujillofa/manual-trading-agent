"""Shared live-scan overrides for evaluate_entry.

The 15-minute scanner and Plan NY must inject the same session windows and
ATR-scaled breakout buffer. evaluate_entry stays pure (no registry I/O).
"""

from __future__ import annotations

from typing import Any

from src.config.instruments import get_instrument_optional
from src.config.instruments import session_windows as instrument_session_windows
from src.indicators.atr import calculate_atr


def atr_from_15m(data_15m: Any) -> float | None:
    if data_15m is None or getattr(data_15m, "empty", True):
        return None
    high = data_15m["high"].values.tolist()
    low = data_15m["low"].values.tolist()
    close = data_15m["close"].values.tolist()
    if len(high) < 15:
        return None
    return calculate_atr(high[-(14 + 1) :], low[-(14 + 1) :], close[-(14 + 1) :])


def build_live_entry_overrides(
    pair: str,
    *,
    atr: float | None,
    settings: Any,
) -> dict[str, Any]:
    """Session + ATR buffer the live scan injects into evaluate_entry."""
    inst = get_instrument_optional(pair)
    if inst is None:
        return {}
    overrides: dict[str, Any] = {
        "session_allowed_utc": instrument_session_windows(
            pair, list(settings.strategy.session_allowed_utc)
        ),
        "spread_filter_enabled": bool(inst.spread_filter_enabled),
    }
    atr_frac = float(getattr(settings.strategy, "breakout_buffer_atr_frac", 0.05))
    if atr is not None and atr > 0 and atr_frac > 0:
        overrides["pip_size"] = 1.0
        overrides["buffer_pips"] = float(atr) * atr_frac
    return overrides
