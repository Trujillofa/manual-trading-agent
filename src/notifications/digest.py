"""Concise Telegram scan digest formatting and dedup rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NotRequired, TypedDict, cast

from src.indicators.ema import EMACrossover, EMAPriceTouch, EMASlope

EmaSignalData = EMACrossover | EMAPriceTouch | EMASlope


class EmaSignalEntry(TypedDict):
    type: str
    data: EmaSignalData
    pair: str


class EmaCandidate(TypedDict):
    pair: str
    symbol: str
    signals: list[EmaSignalEntry]
    price: NotRequired[float | None]


SetupState = Literal["breakout_pending", "aligned", "near"]
Direction = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class SetupCandidate:
    pair: str
    direction: Direction
    distance: float
    remaining: int
    missing_timeframes: list[str]
    breakout_pending: bool
    aligned: bool
    rsi_1h: float
    rsi_30m: float
    rsi_15m: float
    blockers: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, object]) -> SetupCandidate:
        direction = str(payload.get("direction", "BUY"))
        if direction not in {"BUY", "SELL"}:
            direction = "BUY"
        missing = payload.get("missing_timeframes", [])
        blockers = payload.get("no_trade_reasons", payload.get("blockers", []))
        return cls(
            pair=str(payload.get("pair", "")),
            direction=cast(Direction, direction),
            distance=float(cast(float, payload.get("distance", 999.0))),
            remaining=int(cast(int, payload.get("remaining", 99))),
            missing_timeframes=list(cast(list[str], missing)) if isinstance(missing, list) else [],
            breakout_pending=bool(payload.get("breakout_pending")),
            aligned=bool(payload.get("aligned")),
            rsi_1h=float(cast(float, payload.get("rsi_1h", 0.0))),
            rsi_30m=float(cast(float, payload.get("rsi_30m", 0.0))),
            rsi_15m=float(cast(float, payload.get("rsi_15m", 0.0))),
            blockers=list(cast(list[str], blockers)) if isinstance(blockers, list) else [],
        )

    @property
    def compact_pair(self) -> str:
        return self.pair.replace("/", "")

    @property
    def state(self) -> SetupState:
        if self.breakout_pending:
            return "breakout_pending"
        if self.aligned or self.remaining == 0:
            return "aligned"
        return "near"

    @property
    def threshold_hint(self) -> str:
        return "<30" if self.direction == "BUY" else ">70"

    @property
    def watchable(self) -> bool:
        return self.breakout_pending or self.remaining <= 1 or self.distance <= 4.0

    @property
    def status_text(self) -> str:
        if self.breakout_pending:
            return "aligned, needs 15m breakout"
        if self.state == "aligned":
            return "aligned, waiting on final gates"
        label = "TF" if self.remaining == 1 else "TFs"
        return f"{self.remaining} {label} to align, +{max(self.distance, 0.0):.1f} pts"

    @property
    def emoji(self) -> str:
        return "⏳" if self.state in {"breakout_pending", "aligned"} else "👀"

    def format_line(self, rank: int) -> str:
        return (
            f"{rank}. {self.emoji} `{self.compact_pair}` `{self.direction}` - "
            f"{self.status_text} "
            f"(RSI 1h/30m/15m: {self.rsi_1h:.1f}/{self.rsi_30m:.1f}/{self.rsi_15m:.1f} "
            f"→ {self.threshold_hint})"
        )


def digest_fingerprint(candidates: list[SetupCandidate]) -> str:
    """Fingerprint meaningful digest state, ignoring RSI/distance ticks and ordering."""
    tokens = sorted(
        f"{candidate.pair}:{candidate.direction}:{candidate.state}" for candidate in candidates[:3]
    )
    return "|".join(tokens)


def should_send_digest(
    *,
    previous_fingerprint: str,
    current_fingerprint: str,
    sent_at: int,
    now_ts: int,
    interval_seconds: int,
) -> bool:
    if not current_fingerprint:
        return False
    if previous_fingerprint != current_fingerprint:
        return True
    return now_ts - sent_at >= interval_seconds


def _digest_header(scanned_at: datetime | None) -> str:
    if scanned_at is None:
        return "*Scan Digest*"
    return f"*Scan Digest · {scanned_at.strftime('%H:%M')} UTC*"


def format_blocker_summary(reasons: list[str], *, max_items: int = 2) -> str | None:
    cleaned = [reason.strip() for reason in reasons if reason.strip()]
    if not cleaned:
        return None
    return " · ".join(cleaned[:max_items])


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    label = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {label}"


def _ema_summary_for_pairs(
    ema_candidates: list[EmaCandidate],
    pairs: set[str],
    *,
    max_ema_pairs: int,
) -> str | None:
    type_counts: dict[str, int] = {}
    pair_counts: dict[str, int] = {}
    compact_pairs = {pair.replace("/", "") for pair in pairs}

    for candidate in ema_candidates:
        pair = candidate["pair"]
        compact_pair = pair.replace("/", "")
        if pair not in pairs and compact_pair not in compact_pairs:
            continue
        signals = candidate["signals"]
        pair_counts[compact_pair] = pair_counts.get(compact_pair, 0) + len(signals)
        for signal in signals:
            sig_type = signal["type"]
            type_counts[sig_type] = type_counts.get(sig_type, 0) + 1

    total = sum(type_counts.values())
    if total == 0:
        return None

    crosses = type_counts.get("crossover", 0)
    touches = type_counts.get("price_touch", 0)
    slopes = type_counts.get("slope", 0)
    parts = []
    if crosses:
        parts.append(_pluralize(crosses, "cross", "crosses"))
    if touches:
        parts.append(_pluralize(touches, "touch", "touches"))
    if slopes:
        parts.append(_pluralize(slopes, "slope"))
    top_pairs = ", ".join(
        pair
        for pair, _ in sorted(pair_counts.items(), key=lambda item: (-item[1], item[0]))[
            :max_ema_pairs
        ]
    )
    return (
        f"EMA ({_pluralize(total, 'relevant event', 'relevant events')}): "
        f"{', '.join(parts)} · {top_pairs}"
    )


def build_setup_digest_message(
    candidates: list[SetupCandidate],
    ema_candidates: list[EmaCandidate] | None = None,
    *,
    scanned_at: datetime | None = None,
    max_setups: int = 3,
    max_ema_pairs: int = 3,
) -> str | None:
    """Build a compact scan digest from ranked MTF and EMA candidates."""
    watchable = [candidate for candidate in candidates if candidate.watchable]
    if not watchable:
        return None

    displayed = watchable[:max_setups]
    lines = [_digest_header(scanned_at), "", "No confirmed entry.", "", "*Top setups*"]
    lines.extend(candidate.format_line(idx) for idx, candidate in enumerate(displayed, start=1))

    if ema_candidates:
        ema_summary = _ema_summary_for_pairs(
            ema_candidates,
            {candidate.pair for candidate in displayed},
            max_ema_pairs=max_ema_pairs,
        )
        if ema_summary:
            lines.extend(["", ema_summary])

    leader = displayed[0]
    blocker_text = format_blocker_summary(leader.blockers)
    if blocker_text:
        lines.extend(["", f"*Blockers*: `{blocker_text}`"])
    lines.extend(["", f"`/pair {leader.compact_pair}` for full detail"])
    return "\n".join(lines)
