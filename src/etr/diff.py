"""Detect actionable changes between ETR report snapshots."""

from __future__ import annotations

from src.etr.models import EtrChange, EtrReport, EtrScenario


def _norm(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _zone_str(scenario: EtrScenario | None) -> str:
    if scenario is None or scenario.activation_zone is None:
        return "—"
    return scenario.activation_zone.format()


def diff_reports(
    previous: EtrReport | None,
    current: EtrReport,
    *,
    score_low: float = 50.0,
    score_high: float = 80.0,
    score_delta: float = 10.0,
    prev_in_zone: bool | None = None,
) -> list[EtrChange]:
    """Return actionable structural changes. Empty if only price/noise moved."""
    if previous is None:
        return []

    changes: list[EtrChange] = []

    if _norm(previous.bias) != _norm(current.bias):
        changes.append(
            EtrChange(
                field="bias",
                old=previous.bias or "—",
                new=current.bias or "—",
                severity="action",
            )
        )

    if _norm(previous.estado) != _norm(current.estado):
        changes.append(
            EtrChange(
                field="estado",
                old=previous.estado or "—",
                new=current.estado or "—",
                severity="action",
            )
        )

    prev_primary = previous.primary
    curr_primary = current.primary
    if _norm(prev_primary.direction if prev_primary else "") != _norm(
        curr_primary.direction if curr_primary else ""
    ):
        changes.append(
            EtrChange(
                field="primary_direction",
                old=(prev_primary.direction if prev_primary else "—") or "—",
                new=(curr_primary.direction if curr_primary else "—") or "—",
                severity="action",
            )
        )

    if _fmt_num(prev_primary.invalidation if prev_primary else None) != _fmt_num(
        curr_primary.invalidation if curr_primary else None
    ):
        changes.append(
            EtrChange(
                field="primary_invalidation",
                old=_fmt_num(prev_primary.invalidation if prev_primary else None),
                new=_fmt_num(curr_primary.invalidation if curr_primary else None),
                severity="action",
            )
        )

    if _zone_str(prev_primary) != _zone_str(curr_primary):
        changes.append(
            EtrChange(
                field="primary_zone",
                old=_zone_str(prev_primary),
                new=_zone_str(curr_primary),
                severity="action",
            )
        )

    if _norm(prev_primary.status if prev_primary else "") != _norm(
        curr_primary.status if curr_primary else ""
    ):
        changes.append(
            EtrChange(
                field="primary_status",
                old=(prev_primary.status if prev_primary else "—") or "—",
                new=(curr_primary.status if curr_primary else "—") or "—",
                severity="info",
            )
        )

    # Score thresholds / large delta
    prev_score = previous.context_score
    curr_score = current.context_score
    if prev_score is not None and curr_score is not None:
        crossed = False
        if prev_score > score_low >= curr_score or prev_score < score_low <= curr_score:
            crossed = True
        if prev_score < score_high <= curr_score or prev_score > score_high >= curr_score:
            crossed = True
        if abs(curr_score - prev_score) >= score_delta:
            crossed = True
        if crossed:
            changes.append(
                EtrChange(
                    field="context_score",
                    old=f"{prev_score:g}",
                    new=f"{curr_score:g}",
                    severity="info",
                )
            )

    # Price entered primary activation zone (edge into zone only)
    now_in = current.price_in_primary_zone()
    was_in = prev_in_zone
    if was_in is None:
        was_in = previous.price_in_primary_zone()
    if now_in is True and was_in is not True:
        changes.append(
            EtrChange(
                field="price_in_primary_zone",
                old="no",
                new="yes",
                severity="action",
            )
        )

    return changes
