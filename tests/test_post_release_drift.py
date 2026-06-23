"""Unit tests for post-release event drift falsifier helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from research.new_edge.events.post_release_drift_test import (
    classify_event_family,
    entry_exit_prices,
    gross_pips,
    load_drift_events,
    parse_numeric_value,
    surprise_sign,
    trade_direction,
)


def test_parse_numeric_value_suffixes():
    assert parse_numeric_value("167K") == 167_000.0
    assert parse_numeric_value("0.1%") == 0.1
    assert parse_numeric_value("51.4") == 51.4
    assert parse_numeric_value("-40K") == -40_000.0
    assert parse_numeric_value("") is None
    assert parse_numeric_value("n/a") is None


def test_surprise_sign():
    assert surprise_sign("120K", "100K") == 1
    assert surprise_sign("90K", "100K") == -1
    assert surprise_sign("100K", "100K") == 0
    assert surprise_sign("bad", "100K") == 0


def test_classify_event_family_excludes_adp_nfp():
    assert classify_event_family("Non-Farm Employment Change") == "nfp"
    assert classify_event_family("ADP Non-Farm Employment Change") is None
    assert classify_event_family("CPI m/m") == "cpi"
    assert classify_event_family("GDP q/q") == "gdp"
    assert classify_event_family("ISM Manufacturing PMI") == "pmi"
    assert classify_event_family("Official Bank Rate") == "rate_decision"
    assert classify_event_family("Bank Holiday") is None


def test_trade_direction_quote_and_base():
    assert trade_direction(1, "base") == 1
    assert trade_direction(-1, "base") == -1
    assert trade_direction(1, "quote") == -1
    assert trade_direction(-1, "quote") == 1


def test_gross_pips_buy_and_sell():
    assert gross_pips(1, 1.1000, 1.1010, "EUR/USD") == pytest.approx(10.0)
    assert gross_pips(-1, 1.1000, 1.1010, "EUR/USD") == pytest.approx(-10.0)
    assert gross_pips(1, 110.00, 110.10, "USD/JPY") == pytest.approx(10.0)


def test_entry_exit_prices_on_m15_bars():
    idx = pd.date_range("2024-01-05 13:00", periods=20, freq="15min", tz="UTC")
    bars = pd.DataFrame(
        {
            "open": [1.0 + i * 0.0001 for i in range(20)],
            "high": [1.0 + i * 0.0002 for i in range(20)],
            "low": [1.0 + i * 0.00005 for i in range(20)],
            "close": [1.0 + i * 0.00015 for i in range(20)],
        },
        index=idx,
    )
    entry_time = datetime(2024, 1, 5, 13, 37, tzinfo=UTC)
    exit_time = datetime(2024, 1, 5, 17, 37, tzinfo=UTC)
    entry, exit_price = entry_exit_prices(bars, entry_time, exit_time)
    assert entry == bars.iloc[3]["open"]
    assert exit_price == bars.iloc[18]["close"]


def test_load_drift_events_filters_high_impact_and_window(tmp_path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        "DateTime,Currency,Impact,Event,Actual,Forecast,Previous,Detail\n"
        "2018-06-01T12:30:00+00:00,USD,High Impact Expected,Non-Farm Employment Change,200K,180K,190K,\n"
        "2018-06-01T12:30:00+00:00,USD,Medium Impact Expected,ADP Non-Farm Employment Change,200K,180K,190K,\n"
        "2018-06-02T08:00:00+00:00,EUR,High Impact Expected,CPI m/m,0.2%,0.1%,0.0%,\n"
        "2015-01-01T08:00:00+00:00,GBP,High Impact Expected,GDP m/m,0.1%,0.0%,0.0%,\n",
        encoding="utf-8",
    )
    start = datetime(2016, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 1, tzinfo=UTC)
    events = load_drift_events(csv, start, end)
    assert len(events) == 2
    assert {e.family for e in events} == {"nfp", "cpi"}
    assert events[0].pair == "EUR/USD"
    assert events[0].direction == -1  # USD positive surprise on quote leg
