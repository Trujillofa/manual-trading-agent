"""Tests for concise Telegram scan digests."""

from __future__ import annotations

from datetime import UTC, datetime

from src.notifications.digest import (
    SetupCandidate,
    build_setup_digest_message,
    digest_fingerprint,
    format_blocker_summary,
    should_send_digest,
)


def test_build_setup_digest_message_ranks_mtf_and_compresses_ema_context() -> None:
    message = build_setup_digest_message(
        [
            _candidate(
                pair="USD/JPY",
                direction="SELL",
                distance=-1.2,
                remaining=0,
                breakout_pending=True,
                aligned=True,
                rsi_1h=75.1,
                rsi_30m=73.8,
                rsi_15m=71.2,
            ),
            _candidate(
                pair="EUR/CAD",
                direction="BUY",
                distance=3.5,
                remaining=1,
                missing_timeframes=["1h"],
                rsi_1h=37.5,
                rsi_30m=28.1,
                rsi_15m=27.2,
            ),
        ],
        [
            {
                "pair": "NZD/JPY",
                "signals": [
                    {"type": "crossover"},
                    {"type": "price_touch"},
                    {"type": "price_touch"},
                ],
            },
            {"pair": "USD/JPY", "signals": [{"type": "price_touch"}]},
        ],
        scanned_at=datetime(2026, 6, 22, 10, 49, tzinfo=UTC),
    )

    assert message is not None
    assert "*Scan Digest · 10:49 UTC*" in message
    assert "`USDJPY` `SELL` - aligned, needs 15m breakout" in message
    assert "`EURCAD` `BUY` - 1 TF to align, +3.5 pts" in message
    assert "RSI 1h/30m/15m: 75.1/73.8/71.2" in message
    assert "gap `-" not in message
    assert "missing `-`" not in message
    assert "EMA (1 relevant event): 1 touch · USDJPY" in message
    assert "EMA Price Touch" not in message
    assert "Setup Invalidated" not in message


def test_build_setup_digest_message_returns_none_without_useful_context() -> None:
    message = build_setup_digest_message(
        [
            _candidate(
                pair="GBP/USD",
                direction="SELL",
                distance=15.0,
                remaining=3,
                missing_timeframes=["1h", "30m", "15m"],
                rsi_1h=55.0,
                rsi_30m=58.0,
                rsi_15m=60.0,
            )
        ],
        [],
    )

    assert message is None


def test_digest_fingerprint_ignores_distance_only_drift() -> None:
    before = [
        _candidate(pair="GBP/NZD", direction="SELL", distance=3.1, remaining=1),
        _candidate(pair="NZD/JPY", direction="BUY", distance=5.5, remaining=3),
    ]
    after = [
        _candidate(pair="GBP/NZD", direction="SELL", distance=2.9, remaining=1),
        _candidate(pair="NZD/JPY", direction="BUY", distance=5.2, remaining=3),
    ]

    assert digest_fingerprint(before) == digest_fingerprint(after)


def test_digest_fingerprint_changes_when_state_changes() -> None:
    near = [_candidate(pair="GBP/NZD", direction="SELL", distance=2.9, remaining=1)]
    breakout = [
        _candidate(
            pair="GBP/NZD",
            direction="SELL",
            distance=-0.2,
            remaining=0,
            breakout_pending=True,
            aligned=True,
        )
    ]

    assert digest_fingerprint(near) != digest_fingerprint(breakout)


def test_should_send_digest_suppresses_same_state_before_interval() -> None:
    assert (
        should_send_digest(
            previous_fingerprint="GBP/NZD:SELL:near",
            current_fingerprint="GBP/NZD:SELL:near",
            sent_at=1_000,
            now_ts=1_120,
            interval_seconds=3_600,
        )
        is False
    )


def test_should_send_digest_sends_on_state_change_before_interval() -> None:
    assert (
        should_send_digest(
            previous_fingerprint="GBP/NZD:SELL:near",
            current_fingerprint="GBP/NZD:SELL:breakout_pending",
            sent_at=1_000,
            now_ts=1_120,
            interval_seconds=3_600,
        )
        is True
    )


def test_should_send_digest_sends_after_interval() -> None:
    assert (
        should_send_digest(
            previous_fingerprint="GBP/NZD:SELL:near",
            current_fingerprint="GBP/NZD:SELL:near",
            sent_at=1_000,
            now_ts=4_700,
            interval_seconds=3_600,
        )
        is True
    )


def test_digest_includes_top_blockers_for_leader() -> None:
    message = build_setup_digest_message(
        [
            _candidate(
                pair="GBP/NZD",
                direction="SELL",
                distance=-0.2,
                remaining=0,
                breakout_pending=True,
                aligned=True,
                no_trade_reasons=[
                    "15m breakout high not confirmed",
                    "trending market (ADX 31 >= 25.0)",
                    "outside allowed session",
                ],
            )
        ],
    )

    assert message is not None
    assert (
        "*Blockers*: `15m breakout high not confirmed · trending market (ADX 31 >= 25.0)`"
        in message
    )
    assert "outside allowed session" not in message
    assert "`/pair GBPNZD` for full detail" in message


def test_format_blocker_summary_returns_none_when_empty() -> None:
    assert format_blocker_summary([]) is None


def test_ema_section_suppressed_when_not_relevant_to_displayed_setups() -> None:
    message = build_setup_digest_message(
        [_candidate(pair="GBP/NZD", direction="SELL", distance=2.0, remaining=1)],
        [{"pair": "AUD/CAD", "signals": [{"type": "price_touch"}]}],
    )

    assert message is not None
    assert "EMA (" not in message


def _candidate(
    *,
    pair: str,
    direction: str,
    distance: float,
    remaining: int,
    missing_timeframes: list[str] | None = None,
    breakout_pending: bool = False,
    aligned: bool = False,
    rsi_1h: float = 35.0,
    rsi_30m: float = 34.0,
    rsi_15m: float = 33.0,
    no_trade_reasons: list[str] | None = None,
) -> SetupCandidate:
    return SetupCandidate.from_mapping(
        {
            "pair": pair,
            "direction": direction,
            "distance": distance,
            "remaining": remaining,
            "missing_timeframes": missing_timeframes or [],
            "breakout_pending": breakout_pending,
            "aligned": aligned,
            "rsi_1h": rsi_1h,
            "rsi_30m": rsi_30m,
            "rsi_15m": rsi_15m,
            "no_trade_reasons": no_trade_reasons or [],
        }
    )
