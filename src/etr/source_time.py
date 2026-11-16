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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

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
            parsed = parsed.astimezone(UTC)
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

    A naive date and an aware instant are not comparable without inventing a
    timezone; treat that as unknown instead of guessing. A date-only stamp and a
    timed stamp are compared by calendar date, the coarsest precision they share.
    """
    left = _parse_stamp(current)
    right = _parse_stamp(previous)
    if left is None or right is None:
        return False
    if (left.value.tzinfo is None) != (right.value.tzinfo is None):
        return False
    if left.has_time and right.has_time:
        return left.value < right.value
    return left.value.date() < right.value.date()


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
    if (left.value.tzinfo is None) != (right.value.tzinfo is None):
        # Cannot order them; keep what was already recorded.
        return first_raw
    if left.has_time and right.has_time:
        return second_raw if right.value > left.value else first_raw
    if left.has_time != right.has_time:
        if left.value.date() != right.value.date():
            return second_raw if right.value.date() > left.value.date() else first_raw
        return first_raw if left.has_time else second_raw
    return second_raw if right.value > left.value else first_raw
