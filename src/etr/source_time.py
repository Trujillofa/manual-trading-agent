"""Reading ETR's own "Actualizado" timestamp for monotonicity checks.

ETR renders a calendar date such as ``11/08/2026`` (DD/MM/YYYY), sometimes with
a wall-clock time and no timezone. ISO values are accepted too because older
snapshots and tests use them.

Only the ordering matters here: an older render must not replace newer state.
Values that cannot be parsed yield ``None`` and callers deliberately fail open
(accept the render) rather than inventing a timestamp. Fail-closed freshness
policy is a separate decision and is not made in this module.

Ordering compares at the coarsest precision both values carry. A date-only stamp
is not the same instant as midnight: ETR publishes "12/08/2026" for a render made
at any hour of that day, so comparing it as 00:00 made every later same-day
update look older and the guard silently dropped it for the rest of the day. When
one side has no time, only calendar dates are compared.

Every stamp is normalised to **naive UTC** on parse. A timezone-aware value used
to make ``source_is_older`` fail open and made ``source_timestamp_max`` refuse to
replace it, so a single legacy aware ``updated_at`` disabled both halves of the
guard permanently. Normalising before comparison makes that state self-heal: the
legacy value stays comparable and an honest newer render replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# A source stamp this far in the future is a misparse or a bad clock, not a
# fresh render. ``08/12/2026`` read as DD/MM (8 December) instead of the US
# MM/DD it was taken from sits months ahead; kept as the high-water mark it
# rejects every honest render from then on, and because the *stored report*
# still looks plausible nothing points at it.
MAX_SOURCE_FUTURE_HOURS = 48.0

_FORMATS = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y, %H:%M:%S",
    "%d/%m/%Y, %H:%M",
    "%d/%m/%Y %I:%M %p",
    "%d/%m/%Y, %I:%M %p",
    "%d/%m/%Y",
)
# Formats that carry a clock time. Everything else is date-exact only.
_TIMED_FORMATS = frozenset(_FORMATS[:-1])


@dataclass(frozen=True)
class _Stamp:
    value: datetime
    has_time: bool


def _normalise(text: str) -> str:
    # ETR renders "11/08/2026, 2:30 p. m."; strptime only knows AM/PM.
    cleaned = text.replace("\u00a0", " ")
    for source, target in (
        ("a. m.", "AM"),
        ("p. m.", "PM"),
        ("a.m.", "AM"),
        ("p.m.", "PM"),
    ):
        cleaned = cleaned.replace(source, target)
    return cleaned.replace(" hs", "").replace("hs", "").strip()


def _parse_stamp(raw: object) -> _Stamp | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        parsed = None
    if parsed is not None:
        # An ISO value with a clock (or a zeroed one) still carries an explicit
        # time; ``2026-08-11`` alone is date-exact.
        has_time = "T" in text or " " in text
        if parsed.tzinfo is not None:
            # Naive UTC, so an aware legacy value cannot make the guard
            # incomparable with a naive ETR stamp and silently abandon it.
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return _Stamp(parsed, has_time)
    normalised = _normalise(text)
    for fmt in _FORMATS:
        try:
            return _Stamp(datetime.strptime(normalised, fmt), fmt in _TIMED_FORMATS)
        except ValueError:
            continue
    return None


def parse_source_timestamp(raw: object) -> datetime | None:
    stamp = _parse_stamp(raw)
    return stamp.value if stamp is not None else None


def source_is_older(current: object, previous: object) -> bool:
    """True only when both timestamps are comparable and ``current`` is older.

    A date-only stamp and a timed stamp are compared by calendar date, the
    coarsest precision they share. Both sides are naive UTC by construction (see
    ``_parse_stamp``), so an aware legacy value can never disable the guard by
    being incomparable.
    """
    left = _parse_stamp(current)
    right = _parse_stamp(previous)
    if left is None or right is None:
        return False
    if left.has_time and right.has_time:
        return left.value < right.value
    return left.value.date() < right.value.date()


def source_latest_instant(*raws: object) -> datetime | None:
    """Latest naive-UTC instant among the parseable stamps, or ``None``.

    Used as the reference clock for freshness checks: the frontier we have
    reason to trust is the poll clock or the asset's own accepted watermark,
    whichever is later.
    """
    best: datetime | None = None
    for raw in raws:
        stamp = _parse_stamp(raw)
        if stamp is not None and (best is None or stamp.value > best):
            best = stamp.value
    return best


def source_timestamp_in_future(
    raw: object, *, now: datetime | None = None, horizon_hours: float = MAX_SOURCE_FUTURE_HOURS
) -> bool:
    """True when a stamp is further ahead than any plausible render clock.

    Both sides are naive UTC. An unparseable value is not "in the future": it is
    unknown, and the caller keeps its existing fail-open policy for it.
    """
    stamp = _parse_stamp(raw)
    if stamp is None:
        return False
    current = now or datetime.now(UTC).replace(tzinfo=None)
    if current.tzinfo is not None:
        current = current.astimezone(UTC).replace(tzinfo=None)
    try:
        ahead = (stamp.value - current).total_seconds()
    except TypeError:
        # Only reachable if a caller passes a naive ``now`` that is really aware
        # of a different zone; treat it as unknowable rather than poisoning.
        return False
    return ahead > horizon_hours * 3600


def source_timestamp_max(first: object, second: object) -> str | None:
    """The raw stamp representing the latest source time seen for an asset.

    The last committed render is not a high-water mark: once a date-only render
    is accepted it would erase the clock time an earlier render carried, and a
    genuinely older render of that same day would then compare equal and be
    committed. Callers keep this value alongside the snapshot and compare new
    renders against it.

    Unparseable values never replace a parseable one. On a tie at the coarsest
    common precision the value that carries a time wins, for the same reason.
    """
    first_raw = first.strip() if isinstance(first, str) and first.strip() else None
    second_raw = second.strip() if isinstance(second, str) and second.strip() else None
    left = _parse_stamp(first_raw)
    right = _parse_stamp(second_raw)
    if left is None:
        return second_raw
    if right is None:
        return first_raw
    if left.has_time and right.has_time:
        return second_raw if right.value > left.value else first_raw
    if left.has_time != right.has_time:
        if left.value.date() != right.value.date():
            return second_raw if right.value.date() > left.value.date() else first_raw
        return first_raw if left.has_time else second_raw
    return second_raw if right.value > left.value else first_raw
