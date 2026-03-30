"""Pytest fixtures for manual trading agent tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_prices():
    """Sample price list for RSI testing."""
    return [
        44.0,
        44.5,
        45.0,
        44.8,
        44.2,
        43.5,
        44.0,
        44.5,
        45.5,
        46.0,
        45.8,
        46.5,
        47.0,
        46.8,
        47.2,
        48.0,
        47.5,
        47.8,
        48.5,
        49.0,
    ]


@pytest.fixture
def uptrend_prices():
    """Uptrend prices for RSI testing."""
    return [100.0 + i * 0.5 for i in range(30)]


@pytest.fixture
def downtrend_prices():
    """Downtrend prices for RSI testing."""
    return [100.0 - i * 0.5 for i in range(30)]


@pytest.fixture
def sample_highs():
    """Sample high prices for high/low testing."""
    return [
        1.0900,
        1.0910,
        1.0895,
        1.0920,
        1.0935,
        1.0915,
        1.0940,
        1.0950,
        1.0930,
        1.0960,
        1.0970,
        1.0955,
        1.0980,
        1.0990,
        1.0975,
    ]


@pytest.fixture
def sample_lows():
    """Sample low prices for high/low testing."""
    return [
        1.0800,
        1.0810,
        1.0795,
        1.0820,
        1.0835,
        1.0815,
        1.0840,
        1.0850,
        1.0830,
        1.0860,
        1.0870,
        1.0855,
        1.0880,
        1.0890,
        1.0875,
    ]
