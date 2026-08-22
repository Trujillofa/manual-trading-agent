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
    events = [{"asset": "btc", "entry_price": 76_500.0, "exit_price": 77_200.0}]
    polls = [{"asset": "btc", "price": 77_000.0}]
    summaries = {s.asset: s for s in summarize(events, polls)}
    assert summaries["btc"].basis_guess == "compatible_with_yf_continuous"


def test_gold_compatible_at_2026_futures_scale() -> None:
    events = [{"asset": "gold", "entry_price": 4_371.1, "exit_price": 4_362.2}]
    polls = [{"asset": "gold", "price": 4_393.0}]
    summaries = {s.asset: s for s in summarize(events, polls)}
    assert summaries["gold"].basis_guess == "compatible_with_yf_continuous"
    assert summaries["gold"].scale_ratio is not None
    assert 0.5 <= summaries["gold"].scale_ratio <= 2.0
