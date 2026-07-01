"""Tests for COT release controls and the fixed relationship falsifier."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.new_edge.cot_positioning.availability import (
    AvailabilityAudit,
    apply_release_controls,
)
from research.new_edge.cot_positioning.relationship import (
    add_positioning_percentiles,
    build_observations,
    evaluate_relationship,
    fit_ols,
)


def _cot_row(symbol: str, report_date: str, value: float = 0.0) -> dict[str, object]:
    report = pd.Timestamp(report_date)
    return {
        "symbol": symbol,
        "sector": "test",
        "report_date": report,
        "available_date": report + pd.Timedelta(days=6),
        "net_noncommercial_pct_oi": value,
    }


def test_release_controls_exclude_shutdown_and_apply_verified_override() -> None:
    frame = pd.DataFrame(
        [
            _cot_row("GOLD", "2019-01-08"),
            _cot_row("GOLD", "2025-09-30"),
            _cot_row("GOLD", "2025-01-07"),
        ]
    )

    controlled, audit = apply_release_controls(frame)

    assert audit.excluded_rows == 1
    assert audit.overridden_rows == 2
    shutdown = controlled[controlled["report_date"] == pd.Timestamp("2025-09-30")].iloc[0]
    assert shutdown["effective_available_date"] == pd.Timestamp("2025-11-19")
    mourning = controlled[controlled["report_date"] == pd.Timestamp("2025-01-07")].iloc[0]
    assert mourning["effective_available_date"] == pd.Timestamp("2025-01-13")


def test_release_controls_limit_revision_exclusion_to_affected_symbols() -> None:
    frame = pd.DataFrame(
        [
            _cot_row("CORN", "2017-03-28"),
            _cot_row("GOLD", "2017-03-28"),
        ]
    )

    controlled, audit = apply_release_controls(frame)

    assert audit.excluded_rows == 1
    assert controlled["symbol"].tolist() == ["GOLD"]


def test_positioning_percentile_uses_only_trailing_values(monkeypatch) -> None:
    monkeypatch.setattr("research.new_edge.cot_positioning.relationship.MIN_ROLLING_REPORTS", 3)
    monkeypatch.setattr("research.new_edge.cot_positioning.relationship.ROLLING_REPORTS", 3)
    frame = pd.DataFrame(
        [
            _cot_row("GOLD", "2020-01-07", 1.0),
            _cot_row("GOLD", "2020-01-14", 2.0),
            _cot_row("GOLD", "2020-01-21", 3.0),
            _cot_row("GOLD", "2020-01-28", -100.0),
        ]
    )

    positioned = add_positioning_percentiles(frame)

    assert pd.isna(positioned.iloc[1]["positioning_percentile"])
    assert positioned.iloc[2]["positioning_percentile"] == pytest.approx(5 / 6)
    assert positioned.iloc[3]["positioning_percentile"] == pytest.approx(1 / 6)


def test_build_observations_enters_strictly_after_availability(monkeypatch) -> None:
    monkeypatch.setattr("research.new_edge.cot_positioning.relationship.MIN_ROLLING_REPORTS", 1)
    monkeypatch.setattr("research.new_edge.cot_positioning.relationship.ROLLING_REPORTS", 2)
    cot = pd.DataFrame([_cot_row("GOLD", "2026-01-06", 1.0)])
    price_index = pd.to_datetime(["2026-01-12", "2026-01-13", "2026-02-10", "2026-02-11"])
    prices = {"GOLD": pd.Series([100.0, 101.0, 110.0, 111.0], index=price_index)}

    observations, _ = build_observations(cot, prices)

    assert observations.iloc[0]["entry_date"] == pd.Timestamp("2026-01-13")
    assert observations.iloc[0]["exit_date"] == pd.Timestamp("2026-02-10")
    assert observations.iloc[0]["forward_log_return"] == pytest.approx(np.log(110.0 / 101.0))


def test_fit_ols_detects_negative_relationship() -> None:
    frame = pd.DataFrame(
        {
            "positioning_percentile": np.linspace(0.01, 0.99, 200),
            "forward_log_return": np.linspace(0.05, -0.05, 200),
        }
    )

    regression = fit_ols(frame)

    assert regression.slope < 0
    assert regression.one_sided_p < 0.001


def test_evaluate_relationship_passes_broad_stable_synthetic_reversal() -> None:
    rng = np.random.default_rng(7)
    rows = []
    dates = pd.date_range("2015-01-05", periods=160, freq="W-MON")
    for market_number in range(20):
        market_offset = market_number * 0.00001
        for index, signal_date in enumerate(dates):
            percentile = ((index * 17 + market_number * 11) % 100 + 0.5) / 100
            noise = rng.normal(0, 0.0003)
            rows.append(
                {
                    "symbol": f"M{market_number:02d}",
                    "effective_available_date": signal_date,
                    "positioning_percentile": percentile,
                    "forward_log_return": 0.04 - 0.08 * percentile + market_offset + noise,
                }
            )
    observations = pd.DataFrame(rows)
    availability = AvailabilityAudit(len(rows), len(rows), 0, 0, {})

    result = evaluate_relationship(
        observations,
        availability,
        missing_price_symbols=(),
        shuffles=100,
    )

    assert result.verdict == "RELATIONSHIP_PASS"
    assert not result.reasons
    assert result.negative_market_fraction == 1.0
