"""Tests for multi-asset instrument registry and data-map guards."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.instruments import (
    apply_yaml_instruments,
    get_instrument,
    get_instrument_optional,
    point_size,
    require_backtest_supported,
    reset_registry_to_defaults,
    session_windows,
    yfinance_symbol_map,
)
from src.config.settings import Settings
from src.data.fetcher import DataFetcher
from src.news.news_checker import NewsChecker
from src.scanner.gates import _session_allowed


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_registry_to_defaults()
    yield
    reset_registry_to_defaults()


def test_default_four_instruments_mapped() -> None:
    assert get_instrument("XAU/USD").yf_symbol == "GC=F"
    assert get_instrument("BTC/USD").yf_symbol == "BTC-USD"
    assert get_instrument("OIL").yf_symbol == "CL=F"
    assert get_instrument("NASDAQ").yf_symbol == "NQ=F"


def test_point_sizes() -> None:
    assert point_size("XAU/USD") == 0.1
    assert point_size("BTC/USD") == 1.0
    assert point_size("OIL") == 0.01
    assert point_size("NASDAQ") == 0.25


def test_compact_id_alias_for_display_symbols() -> None:
    # Telegram historically passes EURUSD-style compact symbols.
    assert get_instrument("XAUUSD").id == "XAU/USD"
    assert get_instrument_optional("BTCUSD") is not None


def test_news_currencies_registry_driven() -> None:
    assert set(get_instrument("NASDAQ").currencies) == {"USD"}
    assert set(get_instrument("OIL").currencies) == set()
    assert "USD" in get_instrument("XAU/USD").currencies
    assert NewsChecker._extract_currencies("NASDAQ") == {"USD"}
    assert NewsChecker._extract_currencies("OIL") == set()
    assert "USD" in NewsChecker._extract_currencies("XAU/USD")
    # FX fallback still works
    assert NewsChecker._extract_currencies("EUR/USD") == {"EUR", "USD"}


def test_session_two_window_cme_gap() -> None:
    windows = session_windows("XAU/USD")
    assert "00-21" in windows and "22-24" in windows
    # Maintenance hour 21 UTC blocked; 10 UTC and 23 UTC allowed
    assert _session_allowed(datetime(2026, 6, 1, 10, 0, tzinfo=UTC), windows) is True
    assert _session_allowed(datetime(2026, 6, 1, 21, 30, tzinfo=UTC), windows) is False
    assert _session_allowed(datetime(2026, 6, 1, 23, 0, tzinfo=UTC), windows) is True
    # Wrap-around single window must not be used — document regression
    assert _session_allowed(datetime(2026, 6, 1, 23, 0, tzinfo=UTC), ["22-06"]) is False


def test_btc_always_in_session() -> None:
    windows = session_windows("BTC/USD")
    assert _session_allowed(datetime(2026, 6, 1, 3, 0, tzinfo=UTC), windows) is True


def test_yfinance_map_never_appends_eq_x_for_registry() -> None:
    fetcher = DataFetcher()
    assert fetcher._to_yfinance_symbol("NASDAQ") == "NQ=F"
    assert fetcher._to_yfinance_symbol("OIL") == "CL=F"
    assert fetcher._to_yfinance_symbol("BTC/USD") == "BTC-USD"
    assert fetcher._to_yfinance_symbol("XAU/USD") == "GC=F"
    assert not fetcher._to_yfinance_symbol("NASDAQ").endswith("=X")


def test_twelve_data_skipped_for_registry_instruments() -> None:
    fetcher = DataFetcher()
    assert fetcher._to_td_symbol("BTC/USD") is None
    assert fetcher._to_td_symbol("GC=F") is None or fetcher._to_td_symbol("XAU/USD") is None
    assert fetcher._to_td_symbol("XAU/USD") is None
    # Legacy FX still formats
    assert fetcher._to_td_symbol("EUR/USD") == "EUR/USD"


def test_backtest_rejected_for_multi_asset() -> None:
    with pytest.raises(ValueError, match="not supported for backtest"):
        require_backtest_supported("BTC/USD")
    # Unregistered FX still allowed
    require_backtest_supported("EUR/USD")


def test_repo_settings_yaml_multi_asset_watchlist() -> None:
    repo_yaml = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    settings = Settings.load(repo_yaml)
    assert settings.trading.majors == ["XAU/USD", "BTC/USD", "OIL", "NASDAQ"]
    assert settings.trading.minors == []
    assert settings.strategy.ema.fast_period == 20
    assert settings.strategy.ema.slow_period == 50
    assert settings.strategy.ema.medium_period == 100
    assert settings.strategy.spread_filter_enabled is False
    assert settings.strategy.breakout_buffer_atr_frac == 0.05
    assert get_instrument("NASDAQ").yf_symbol == "NQ=F"


def test_yaml_overlay_updates_point_size() -> None:
    apply_yaml_instruments(
        {
            "OIL": {
                "yf_symbol": "CL=F",
                "point_size": 0.05,
                "currencies": [],
                "session_allowed_utc": ["00-24"],
            }
        }
    )
    assert point_size("OIL") == 0.05


def test_symbol_map_overlay_keys() -> None:
    m = yfinance_symbol_map()
    assert m["XAU/USD"] == "GC=F"
    assert "NASDAQ" in m


def test_fetch_uses_mapped_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = DataFetcher()
    seen: list[str] = []

    class FakeTicker:
        def __init__(self, sym: str) -> None:
            seen.append(sym)

        def history(self, **_kwargs: object) -> MagicMock:
            import pandas as pd

            return pd.DataFrame(
                {
                    "Open": [1.0],
                    "High": [1.0],
                    "Low": [1.0],
                    "Close": [1.0],
                    "Volume": [0],
                }
            )

    monkeypatch.setattr("src.data.fetcher.yf.Ticker", FakeTicker)
    fetcher._td_api_key = ""
    df = fetcher.fetch("NASDAQ", period="5d", interval="15m")
    assert seen == ["NQ=F"]
    assert not df.empty
