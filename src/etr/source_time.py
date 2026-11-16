"""Reading ETR's own "Actualizado" timestamp for monotonicity checks.

ETR renders a calendar date such as ``11/08/2026`` (DD/MM/YYYY), sometimes with
a wall-clock time and no timezone. ISO values are accepted too because older
snapshots and tests use them.

Only the ordering matters here: an older render must not replace newer state.
Values that cannot be parsed yield ``None`` and callers deliberately fail open
(accept the render) rather than inventing a timestamp. Fail-closed freshness
policy is a separate decision and is not made in this module.
"""

from __future__ import annotations

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


def parse_source_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        parsed = None
    if parsed is not None:
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed
    normalised = _normalise(text)
    for fmt in _FORMATS:
        try:
            return datetime.strptime(normalised, fmt)
        except ValueError:
            continue
    return None


def source_is_older(current: object, previous: object) -> bool:
    """True only when both timestamps are comparable and ``current`` is older.

    A naive date and an aware instant are not comparable without inventing a
    timezone; treat that as unknown instead of guessing.
    """
    left = parse_source_timestamp(current)
    right = parse_source_timestamp(previous)
    if left is None or right is None:
        return False
    if (left.tzinfo is None) != (right.tzinfo is None):
        return False
    return left < right
