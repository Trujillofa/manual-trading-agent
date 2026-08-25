"""Tests for intraday EMA golden/death-cross standalone Telegram alerts."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.indicators.ema import (
    EMACrossover,
    EMACrossoverType,
    EMAPriceTouch,
    EMASlope,
    EMASlopeDirection,
)
from src.notifications.digest import EmaSignalEntry
from src.notifications.telegram import TelegramNotifier
from src.scanner.gates import _session_allowed
from src.scanner.scan_service import _ema_standalone_fingerprint, _filter_standalone_ema_signals


def _crossover(
    *,
    kind: EMACrossoverType = EMACrossoverType.GOLDEN_CROSS,
    timeframe: str = "15m",
    fast: int = 9,
    slow: int = 21,
) -> EMACrossover:
    return EMACrossover(
        crossover_type=kind,
        fast_period=fast,
        slow_period=slow,
        fast_value=1.10010,
        slow_value=1.10000,
        timeframe=timeframe,
    )


def _entry(sig_type: str, data: object, pair: str = "EUR/USD") -> EmaSignalEntry:
    return {"type": sig_type, "data": data, "pair": pair}  # type: ignore[typeddict-item]


def test_crossover_fingerprint_uses_fast_slow_periods() -> None:
    data = _crossover()
    fp = _ema_standalone_fingerprint("crossover", data, "EUR/USD")
    assert fp == "ema_crossover_15m_EUR/USD_9/21_golden_cross"
    assert "9/21" in fp
    # EMACrossover has no ema_period — empty period must not appear as trailing __.
    assert not fp.endswith("__golden_cross")


def test_price_touch_fingerprint_uses_ema_period() -> None:
    data = EMAPriceTouch(
        ema_period=50,
        ema_value=1.1,
        price=1.1001,
        direction="above",
        distance_pips=0.5,
        timeframe="1h",
    )
    fp = _ema_standalone_fingerprint("price_touch", data, "GBP/USD")
    assert fp == "ema_price_touch_1h_GBP/USD_50_above"


def test_slope_fingerprint_uses_period() -> None:
    data = EMASlope(
        period=9,
        slope_direction=EMASlopeDirection.RISING,
        current_value=1.1,
        previous_value=1.09,
        timeframe="30m",
    )
    fp = _ema_standalone_fingerprint("slope", data, "USD/JPY")
    assert fp == "ema_slope_30m_USD/JPY_9_rising"


def test_filter_keeps_crossover_on_allowed_timeframes_only() -> None:
    signals = [
        _entry("crossover", _crossover(timeframe="15m")),
        _entry("crossover", _crossover(timeframe="30m")),
        _entry("crossover", _crossover(timeframe="1h")),
        _entry(
            "price_touch",
            EMAPriceTouch(50, 1.1, 1.1, "above", 0.1, "15m"),
        ),
        _entry(
            "slope",
            EMASlope(9, EMASlopeDirection.RISING, 1.1, 1.09, "15m"),
        ),
    ]
    filtered = _filter_standalone_ema_signals(
        signals,
        allowed_types=["crossover"],
        allowed_timeframes=["15m", "30m"],
    )
    assert len(filtered) == 2
    assert all(s["type"] == "crossover" for s in filtered)
    tfs = {s["data"].timeframe for s in filtered}
    assert tfs == {"15m", "30m"}


def test_filter_respects_multiple_signal_types() -> None:
    signals = [
        _entry("crossover", _crossover(timeframe="15m")),
        _entry(
            "price_touch",
            EMAPriceTouch(50, 1.1, 1.1, "above", 0.1, "15m"),
        ),
    ]
    filtered = _filter_standalone_ema_signals(
        signals,
        allowed_types=["crossover", "price_touch"],
        allowed_timeframes=["15m"],
    )
    assert {s["type"] for s in filtered} == {"crossover", "price_touch"}


def test_session_allowed_matches_rsi_windows() -> None:
    windows = ["06-17", "12-21"]
    assert _session_allowed(datetime(2026, 6, 1, 10, 0, tzinfo=UTC), windows) is True
    assert _session_allowed(datetime(2026, 6, 1, 18, 0, tzinfo=UTC), windows) is True
    assert _session_allowed(datetime(2026, 6, 1, 3, 0, tzinfo=UTC), windows) is False
    assert _session_allowed(datetime(2026, 6, 1, 22, 0, tzinfo=UTC), windows) is False


@pytest.mark.asyncio
async def test_send_ema_crossover_golden_includes_bias_and_price() -> None:
    notifier = TelegramNotifier(bot_token="t", chat_id="1")
    sent: list[str] = []
    notifier.send = AsyncMock(side_effect=lambda msg: sent.append(msg) or True)  # type: ignore[method-assign]

    await notifier.send_ema_crossover(
        pair="EUR/USD",
        direction="bullish",
        fast_ema=1.10010,
        slow_ema=1.10000,
        fast_period=9,
        slow_period=21,
        timeframe="15m",
        price=1.10005,
    )

    assert len(sent) == 1
    message = sent[0]
    assert "Golden Cross" in message
    assert "Bullish bias" in message
    assert "Price: `1.10005`" in message
    assert "EMA(9):" in message
    assert "EMA(21):" in message
    assert "*EMA Crossover*" in message


@pytest.mark.asyncio
async def test_send_ema_crossover_death_bias_without_price() -> None:
    notifier = TelegramNotifier(bot_token="t", chat_id="1")
    sent: list[str] = []
    notifier.send = AsyncMock(side_effect=lambda msg: sent.append(msg) or True)  # type: ignore[method-assign]

    await notifier.send_ema_crossover(
        pair="GBP/USD",
        direction="bearish",
        fast_ema=1.25000,
        slow_ema=1.25100,
        fast_period=9,
        slow_period=21,
        timeframe="30m",
    )

    message = sent[0]
    assert "Death Cross" in message
    assert "Bearish bias" in message
    assert "Price:" not in message


def test_ema_alert_audit_payload_records_adx_regime() -> None:
    from src.scanner.scan_service import _ema_alert_audit_payload

    data = _crossover(kind=EMACrossoverType.DEATH_CROSS, timeframe="30m", fast=20, slow=50)
    payload = _ema_alert_audit_payload(
        ts_iso="2026-08-11T12:00:00+00:00",
        pair="NASDAQ",
        crossover=data,
        price=29749.5,
        adx_1h=17.4,
        fingerprint="ema_crossover_30m_NASDAQ_20/50_death_cross",
    )
    assert payload["kind"] == "ema_alert"
    assert payload["pair"] == "NASDAQ"
    assert payload["crossover"] == "death_cross"
    assert payload["timeframe"] == "30m"
    assert payload["fast_period"] == 20
    assert payload["slow_period"] == 50
    assert payload["adx_1h"] == 17.4
    assert payload["fingerprint"] == "ema_crossover_30m_NASDAQ_20/50_death_cross"


def test_ema_alert_audit_payload_tolerates_missing_adx() -> None:
    from src.scanner.scan_service import _ema_alert_audit_payload

    payload = _ema_alert_audit_payload(
        ts_iso="2026-08-11T12:00:00+00:00",
        pair="BTC/USD",
        crossover=_crossover(fast=20, slow=50),
        price=None,
        adx_1h=None,
        fingerprint="fp",
    )
    assert payload["adx_1h"] is None
    assert payload["price"] is None
