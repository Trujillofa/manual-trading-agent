"""Technical snapshot from existing OHLC + indicator stack.

Not a strategy family and not an entry signal. Graceful if a TF is missing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from src.briefing.models import Pillar
from src.indicators.adx import calculate_adx
from src.indicators.atr import calculate_atr
from src.indicators.ema import calculate_ema_last
from src.indicators.high_low import highest_high, lowest_low
from src.indicators.rsi import calculate_rsi
from src.indicators.sma import calculate_sma

logger = logging.getLogger(__name__)


def _closes(frame: pd.DataFrame) -> list[float]:
    return [float(value) for value in frame["close"].tolist()]


def _highs(frame: pd.DataFrame) -> list[float]:
    return [float(value) for value in frame["high"].tolist()]


def _lows(frame: pd.DataFrame) -> list[float]:
    return [float(value) for value in frame["low"].tolist()]


def _clean(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None
    required = [column for column in ("open", "high", "low", "close") if column in frame.columns]
    if len(required) < 4:
        return None
    cleaned = frame.dropna(subset=required)
    return None if cleaned.empty else cleaned


def _fmt_price(value: float, point_size: float) -> str:
    if point_size >= 1:
        return f"{value:,.0f}"
    if point_size >= 0.1:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def _pct(current: float, previous: float) -> str:
    if previous == 0:
        return "—"
    change = (current - previous) / previous * 100.0
    return f"{change:+.2f}%"


def _last_bar_time(frame: pd.DataFrame) -> datetime | None:
    try:
        raw = frame.index[-1]
        parsed = pd.Timestamp(raw)
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize(UTC)
        else:
            parsed = parsed.tz_convert(UTC)
        result = parsed.to_pydatetime()
        return result if isinstance(result, datetime) else None
    except Exception:
        return None


def build_technical_pillar(
    frames: dict[str, Any],
    *,
    point_size: float,
    rsi_period: int = 14,
    sma_period: int = 50,
    ema_fast: int = 20,
    ema_slow: int = 50,
    lookback: int = 20,
    adx_period: int = 14,
    atr_period: int = 14,
    adx_range_threshold: float = 25.0,
    extra_lines: tuple[str, ...] = (),
) -> tuple[Pillar, datetime | None]:
    """Build a TA snapshot. Never raises to the caller."""
    try:
        h1 = _clean(frames.get("1h"))
        m30 = _clean(frames.get("30m"))
        m15 = _clean(frames.get("15m"))
        if h1 is None and m30 is None and m15 is None:
            return (
                Pillar(
                    name="technical",
                    available=False,
                    lines=extra_lines,
                    unavailable_reason="OHLC vacío (yfinance)",
                    source="yfinance",
                ),
                None,
            )

        lines: list[str] = []
        as_of = None
        for frame in (m15, m30, h1):
            if frame is not None:
                as_of = _last_bar_time(frame)
                if as_of is not None:
                    break

        if h1 is not None:
            closes = _closes(h1)
            last = closes[-1]
            prev = closes[-2] if len(closes) >= 2 else None
            change = _pct(last, prev) if prev is not None else "—"
            lines.append(f"Precio 1h: `{_fmt_price(last, point_size)}` · var. `{change}`")
            sma = calculate_sma(closes, sma_period)
            if sma is not None:
                side = "por encima" if last > sma else "por debajo"
                lines.append(f"SMA{sma_period} 1h: precio {side} (`{_fmt_price(sma, point_size)}`)")
            atr = calculate_atr(_highs(h1), _lows(h1), closes, atr_period)
            if atr is not None:
                lines.append(f"ATR{atr_period} 1h: `{_fmt_price(atr, point_size)}`")
            adx = calculate_adx(_highs(h1), _lows(h1), closes, adx_period)
            if adx is not None:
                regime = "rango" if adx < adx_range_threshold else "tendencia"
                lines.append(f"ADX{adx_period} 1h: `{adx:.0f}` ({regime})")

        rsi_bits: list[str] = []
        for _label, frame in (("1h", h1), ("30m", m30), ("15m", m15)):
            if frame is None:
                rsi_bits.append("—")
                continue
            rsi = calculate_rsi(_closes(frame), rsi_period)
            rsi_bits.append(f"{rsi:.1f}" if rsi is not None else "—")
        lines.append(f"RSI{rsi_period} 1h/30m/15m: `{' / '.join(rsi_bits)}`")

        structure_frame = m15 if m15 is not None else h1
        if structure_frame is not None:
            closes_s = _closes(structure_frame)
            fast = calculate_ema_last(closes_s, ema_fast)
            slow = calculate_ema_last(closes_s, ema_slow)
            tf_label = "15m" if m15 is not None else "1h"
            if fast is not None and slow is not None:
                bias = "alcista" if fast > slow else "bajista"
                lines.append(
                    f"EMA{ema_fast}/{ema_slow} {tf_label}: "
                    f"{'20>50' if fast > slow else '20<50'} ({bias})"
                )
            hh = highest_high(_highs(structure_frame), lookback)
            ll = lowest_low(_lows(structure_frame), lookback)
            last_s = closes_s[-1]
            if hh is not None and ll is not None:
                span = hh - ll
                loc = ((last_s - ll) / span * 100.0) if span > 0 else None
                loc_txt = f" · en el {loc:.0f}%" if loc is not None else ""
                lines.append(
                    f"Rango {lookback}×{tf_label}: "
                    f"`{_fmt_price(ll, point_size)}`–`{_fmt_price(hh, point_size)}`{loc_txt}"
                )

        lines.extend(extra_lines)
        lines.append("Snapshot técnico · no es señal de entrada")
        return (
            Pillar(name="technical", available=True, lines=tuple(lines), source="yfinance"),
            as_of,
        )
    except Exception as exc:
        logger.warning("technical pillar failed: %s", exc)
        return (
            Pillar(
                name="technical",
                available=False,
                unavailable_reason=str(exc),
                source="yfinance",
            ),
            None,
        )
