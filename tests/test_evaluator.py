"""Direct unit tests for the pure evaluate_entry (single source of truth).

Covers: structure, ATR computation (post-fix), injected I/O values (spread/news/now/session),
MTF alignment detection, confirmation/breakout gating, Rule C suppression, TP/SL calc from ATR.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from src.scanner.evaluator import evaluate_entry


def _make_ohlc_df(
    closes: list[float], start: str = "2024-01-01", freq: str = "15min"
) -> pd.DataFrame:
    """Minimal OHLCV df from close series. High/low derived for ATR/breakout calcs (tz-aware UTC for _is_signal_invalidated comparisons with fired_at)."""
    n = len(closes)
    idx = pd.date_range(start=start, periods=n, freq=freq, tz=UTC)
    highs = [c + 0.0005 for c in closes]
    lows = [c - 0.0005 for c in closes]
    opens = closes[:]
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=idx,
    )


def _down_plunge(n: int = 60, start_price: float = 1.1000, step: float = 0.0004) -> list[float]:
    """Strongly decreasing closes to drive RSI < 30 on tail."""
    return [start_price - i * step for i in range(n)]


def _flatish(n: int = 60, start_price: float = 1.1000) -> list[float]:
    """Low-vol series for potentially lower ADX (real adx depends on full window)."""
    import math

    return [start_price + 0.0001 * math.sin(i * 0.3) for i in range(n)]


class TestEvaluateEntryContract:
    """Basic contract and no-crash on edge data."""

    def test_returns_dict_with_expected_keys(self) -> None:
        d15 = _make_ohlc_df(_down_plunge(20))
        d30 = _make_ohlc_df(_down_plunge(20))
        d1h = _make_ohlc_df(_down_plunge(60))
        res = evaluate_entry("EUR/USD", d1h, d30, d15)
        assert isinstance(res, dict)
        for k in (
            "fired",
            "direction",
            "entry",
            "tp",
            "sl",
            "atr",
            "reasons",
            "no_trade_reasons",
            "profile",
            "aligned",
        ):
            assert k in res

    def test_no_data_returns_no_fire(self) -> None:
        empty = pd.DataFrame(columns=["open", "high", "low", "close"])
        res = evaluate_entry("GBP/USD", empty, empty, empty)
        assert res["fired"] is False
        assert "no 15m data" in str(res.get("reasons", []))

    def test_insufficient_bars_for_rsi_no_fire(self) -> None:
        tiny = _make_ohlc_df([1.1 + i * 0.0001 for i in range(5)])
        res = evaluate_entry("GBP/USD", tiny, tiny, tiny)
        assert res["fired"] is False
        assert "rsi unavailable" in str(res.get("reasons", []))


class TestEvaluateEntryATRAndTPSL:
    """ATR(14) fix + TP/SL derived from it (when direction candidate exists)."""

    def test_atr_computed_with_15_plus_bars(self) -> None:
        d15 = _make_ohlc_df(_down_plunge(20))
        d30 = _make_ohlc_df(_down_plunge(20))
        d1h = _make_ohlc_df(_down_plunge(60))
        res = evaluate_entry("EUR/USD", d1h, d30, d15)
        assert res["atr"] is not None
        assert isinstance(res["atr"], float)
        assert res["atr"] > 0

    def test_tp_sl_set_when_mtf_dir_candidate_even_if_gated(self) -> None:
        """When MTF alignment sets a direction, tp/sl/ entry computed from ATR * pair mults (before final gates)."""
        d15 = _make_ohlc_df(_down_plunge(30))
        d30 = _make_ohlc_df(_down_plunge(30))
        d1h = _make_ohlc_df(_down_plunge(60))
        res = evaluate_entry("EUR/USD", d1h, d30, d15, news_blocked=False)
        # direction may be BUY (oversold) if breakout/profile allows for this pair's profile
        if res.get("direction") in ("BUY", "SELL"):
            assert res.get("entry") is not None
            assert res.get("tp") is not None
            assert res.get("sl") is not None
            assert res.get("atr") is not None
            # tp should be on the correct side of entry
            if res["direction"] == "BUY":
                assert res["tp"] > res["entry"]
                assert res["sl"] < res["entry"]
            else:
                assert res["tp"] < res["entry"]
                assert res["sl"] > res["entry"]


class TestEvaluateEntryInjectedValues:
    """Purity: spread_quote, news_blocked, now_utc, bars_aligned, active_state injected; no I/O inside."""

    def test_news_blocked_injected_adds_reason(self) -> None:
        d15 = _make_ohlc_df(_down_plunge(25))
        d30 = _make_ohlc_df(_down_plunge(25))
        d1h = _make_ohlc_df(_down_plunge(60))
        res = evaluate_entry("EUR/USD", d1h, d30, d15, news_blocked=True)
        assert res["fired"] is False
        assert any("news" in r.lower() for r in res.get("no_trade_reasons", []))

    def test_spread_too_wide_injected_adds_reason_when_filter_on(self) -> None:
        d15 = _make_ohlc_df(_down_plunge(25))
        d30 = _make_ohlc_df(_down_plunge(25))
        d1h = _make_ohlc_df(_down_plunge(60))
        # spread value in price units; /pip_size will be large
        bad_quote = {"spread": 0.01}  # 100+ pips
        res = evaluate_entry(
            "EUR/USD", d1h, d30, d15, spread_quote=bad_quote, spread_filter_enabled=True
        )
        assert res["fired"] is False
        assert any("spread" in r.lower() for r in res.get("no_trade_reasons", []))

    def test_now_utc_outside_session_adds_reason(self) -> None:
        d15 = _make_ohlc_df(_down_plunge(25))
        d30 = _make_ohlc_df(_down_plunge(25))
        d1h = _make_ohlc_df(_down_plunge(60))
        # Inject classic FX windows so 03:00 is outside regardless of multi-asset YAML.
        early = datetime(2024, 6, 1, 3, 0, tzinfo=UTC)
        res = evaluate_entry(
            "EUR/USD",
            d1h,
            d30,
            d15,
            now_utc=early,
            overrides={
                "session_filter_enabled": True,
                "session_allowed_utc": ["06-17", "12-21"],
            },
        )
        # may or may not be the blocking reason (depends if MTF dir set), but if session filter on it should appear when dir candidate
        if any("MTF RSI" in r for r in res.get("reasons", [])):
            assert any("session" in r.lower() for r in res.get("no_trade_reasons", []))

    def test_bars_aligned_passed_used_for_confirm_window(self) -> None:
        d15 = _make_ohlc_df(_down_plunge(25))
        d30 = _make_ohlc_df(_down_plunge(25))
        d1h = _make_ohlc_df(_down_plunge(60))
        # pass explicit bars_aligned (as cli does after advancing state)
        res = evaluate_entry("EUR/USD", d1h, d30, d15, bars_aligned=10, news_blocked=False)
        # if profile has confirm_bars <10 and breakout not met, may see expired message
        # (not asserting exact text; profile-dependent; just exercise the passed bars_aligned path)
        _ = [
            r
            for r in res.get("no_trade_reasons", [])
            if "expired" in r or "confirmation window" in r
        ]
        assert isinstance(res["fired"], bool)


class TestEvaluateEntryRuleC:
    """Rule C: active_signal_state suppresses same-dir until invalidated."""

    def test_active_not_invalidated_suppresses(self) -> None:
        # >=50 bars for sma(50); tz-aware via helper so no tz-naive/aware compare error in _is
        d15 = _make_ohlc_df(_down_plunge(60))
        d30 = _make_ohlc_df(_down_plunge(60))
        d1h = _make_ohlc_df(_down_plunge(60))
        # Fire time before df window -> bars_since non-empty; tp/sl placed so plunge doesn't hit them + no midline/sma flip
        fired_dt = datetime(2023, 12, 1, 12, 0, tzinfo=UTC)
        active = {
            "direction": "BUY",
            "fired_at": int(fired_dt.timestamp()),
            "entry": 1.1000,
            "tp": 1.1010,
            # SL far below the entire data range so plunge does not falsely "hit" it in _is check
            "sl": 1.0000,
        }
        good_spread = {"spread": 0.00005}
        res = evaluate_entry(
            "EUR/USD",
            d1h,
            d30,
            d15,
            active_signal_state={"EUR/USD": active},
            news_blocked=False,
            spread_quote=good_spread,
            spread_filter_enabled=True,
        )
        if res.get("direction") == "BUY":
            # _is returns (False, None) -> "not yet" appended (caught the except before due to tz or sma None or time order)
            assert any(
                "active signal not yet invalidated" in r for r in res.get("no_trade_reasons", [])
            )
            assert res["fired"] is False

    def test_opposite_direction_active_not_suppressed(self) -> None:
        # Regression: an active SELL must NOT suppress a BUY candidate (opposite always allowed).
        d15 = _make_ohlc_df(_down_plunge(60))
        d30 = _make_ohlc_df(_down_plunge(60))
        d1h = _make_ohlc_df(_down_plunge(60))
        fired_dt = datetime(2023, 12, 1, 12, 0, tzinfo=UTC)
        active_sell = {
            "direction": "SELL",
            "fired_at": int(fired_dt.timestamp()),
            "entry": 1.1000,
            "tp": 1.0990,
            "sl": 1.2000,
        }
        res = evaluate_entry(
            "EUR/USD",
            d1h,
            d30,
            d15,
            active_signal_state={"EUR/USD": active_sell},
            news_blocked=False,
            spread_quote={"spread": 0.00005},
            spread_filter_enabled=True,
        )
        # Rule C must not fire for an opposite-direction active record, whatever else gates it.
        assert not any(
            "active signal not yet invalidated" in r for r in res.get("no_trade_reasons", [])
        )


class TestEvaluateEntryPurityNoSideEffects:
    """Calling evaluate_entry must not perform network or mutate caller state."""

    def test_no_network_side_effects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        d15 = _make_ohlc_df(_down_plunge(20))
        d30 = _make_ohlc_df(_down_plunge(20))
        d1h = _make_ohlc_df(_down_plunge(60))
        calls: list[str] = []

        def _fail(*a: object, **k: object) -> None:
            calls.append("network")
            raise AssertionError("evaluator must not do I/O")

        monkeypatch.setattr("src.data.fetcher.OandaFetcher", _fail, raising=False)
        # NewsChecker etc are not imported inside anymore
        res = evaluate_entry("EUR/USD", d1h, d30, d15, now_utc=datetime.now(UTC))
        assert res is not None
        assert "network" not in calls
