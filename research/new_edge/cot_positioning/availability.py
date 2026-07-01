"""No-lookahead release controls for historical CFTC COT reports."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SPECIAL_ANNOUNCEMENTS_URL = (
    "https://www.cftc.gov/MarketReports/CommitmentsofTraders/"
    "HistoricalSpecialAnnouncements/index.htm"
)
SHUTDOWN_2019_URL = "https://www.cftc.gov/PressRoom/PressReleases/7864-19"
SHUTDOWN_2025_URL = "https://www.cftc.gov/PressRoom/PressReleases/9147-25"


@dataclass(frozen=True)
class ReleaseOverride:
    """Verified report-date to publication-date mapping."""

    report_date: str
    release_date: str
    reason: str
    source: str = SPECIAL_ANNOUNCEMENTS_URL


@dataclass(frozen=True)
class ExclusionWindow:
    """Reports excluded because exact point-in-time use is unsafe."""

    start: str
    end: str
    reason: str
    source: str
    symbols: frozenset[str] | None = None


@dataclass(frozen=True)
class AvailabilityAudit:
    """Summary of applied point-in-time controls."""

    input_rows: int
    included_rows: int
    excluded_rows: int
    overridden_rows: int
    exclusion_reasons: dict[str, int]


RELEASE_OVERRIDES: tuple[ReleaseOverride, ...] = (
    ReleaseOverride("2014-12-23", "2014-12-30", "Christmas federal-holiday delay"),
    ReleaseOverride("2015-06-23", "2015-07-06", "Independence Day complete-release delay"),
    ReleaseOverride("2020-12-21", "2020-12-28", "additional federal-holiday delay"),
    ReleaseOverride("2021-06-15", "2021-06-21", "Juneteenth federal-holiday delay"),
    ReleaseOverride("2025-01-07", "2025-01-13", "National Day of Mourning delay"),
    ReleaseOverride(
        "2025-09-30", "2025-11-19", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-10-07", "2025-11-21", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-10-14", "2025-11-25", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-10-21", "2025-12-02", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-10-28", "2025-12-05", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-11-04", "2025-12-09", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-11-10", "2025-12-10", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-11-18", "2025-12-12", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-11-25", "2025-12-15", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-12-02", "2025-12-17", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-12-09", "2025-12-19", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-12-16", "2025-12-23", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
    ReleaseOverride(
        "2025-12-23", "2025-12-29", "2025 appropriations-lapse backlog", SHUTDOWN_2025_URL
    ),
)

EXCLUSION_WINDOWS: tuple[ExclusionWindow, ...] = (
    ExclusionWindow(
        "2018-12-24",
        "2019-03-05",
        "2018-2019 appropriations-lapse backlog excluded",
        SHUTDOWN_2019_URL,
    ),
    ExclusionWindow(
        "2023-01-31",
        "2023-03-14",
        "2023 ION reporting incident backlog excluded",
        SPECIAL_ANNOUNCEMENTS_URL,
    ),
    ExclusionWindow(
        "2017-03-28",
        "2017-03-28",
        "historically revised fixed-universe reports excluded",
        SPECIAL_ANNOUNCEMENTS_URL,
        frozenset({"CORN", "SOYBEANS", "SUGAR", "SP500"}),
    ),
)


def apply_release_controls(frame: pd.DataFrame) -> tuple[pd.DataFrame, AvailabilityAudit]:
    """Apply verified publication dates and exclude unsafe delayed reports."""

    required = {"symbol", "report_date", "available_date"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"COT frame missing availability columns: {missing}")

    controlled = frame.copy()
    controlled["report_date"] = pd.to_datetime(controlled["report_date"]).dt.normalize()
    controlled["effective_available_date"] = pd.to_datetime(
        controlled["available_date"]
    ).dt.normalize()
    controlled["release_control"] = "standard_conservative_monday"
    controlled["release_source"] = ""
    controlled["excluded"] = False
    controlled["exclusion_reason"] = ""

    overridden_rows = 0
    for override in RELEASE_OVERRIDES:
        mask = controlled["report_date"].eq(pd.Timestamp(override.report_date))
        matched = int(mask.sum())
        if matched:
            controlled.loc[mask, "effective_available_date"] = pd.Timestamp(override.release_date)
            controlled.loc[mask, "release_control"] = "verified_release_date"
            controlled.loc[mask, "release_source"] = override.source
            overridden_rows += matched

    exclusion_reasons: dict[str, int] = {}
    for window in EXCLUSION_WINDOWS:
        mask = controlled["report_date"].between(
            pd.Timestamp(window.start), pd.Timestamp(window.end)
        )
        if window.symbols is not None:
            mask &= controlled["symbol"].isin(window.symbols)
        matched = int(mask.sum())
        if matched:
            controlled.loc[mask, "excluded"] = True
            controlled.loc[mask, "exclusion_reason"] = window.reason
            controlled.loc[mask, "release_source"] = window.source
            exclusion_reasons[window.reason] = matched

    invalid = controlled["effective_available_date"] < controlled["report_date"]
    if invalid.any():
        raise ValueError("release controls produced availability before report date")

    included = controlled.loc[~controlled["excluded"]].copy()
    audit = AvailabilityAudit(
        input_rows=len(controlled),
        included_rows=len(included),
        excluded_rows=int(controlled["excluded"].sum()),
        overridden_rows=overridden_rows,
        exclusion_reasons=exclusion_reasons,
    )
    return included.reset_index(drop=True), audit
