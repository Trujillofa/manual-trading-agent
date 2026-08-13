"""Unit tests for ETR shadow price-basis heuristics."""

from __future__ import annotations

from research.new_edge.etr_shadow.audit_price_basis import summarize


def test_nasdaq_scale_flagged_as_terminal_native() -> None:
    events = [
        {
            "asset": "nasdaq",
            "entry_price": 726.0,
            "exit_price": 724.5,
            "invalidation": 724.9,
        }
    ]
    polls = [{"asset": "nasdaq", "price": 725.5}]
    summaries = {s.asset: s for s in summarize(events, polls)}
    assert summaries["nasdaq"].basis_guess.startswith("etr_terminal")
    assert summaries["nasdaq"].scale_ratio is not None
    assert summaries["nasdaq"].scale_ratio < 0.1


def test_btc_compatible_band_when_near_spot() -> None:
    events = [{"asset": "btc", "entry_price": 61_000.0, "exit_price": 60_500.0}]
    polls = [{"asset": "btc", "price": 60_800.0}]
    summaries = {s.asset: s for s in summarize(events, polls)}
    assert summaries["btc"].basis_guess == "compatible_with_yf_continuous"
