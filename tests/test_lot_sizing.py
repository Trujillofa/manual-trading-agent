"""Tests for lot sizing calculator."""

from __future__ import annotations

import pytest

from src.execution.lot_sizing import LotSizer, SymbolInfo


class TestLotSizer:
    """LotSizer tests."""

    @pytest.fixture
    def sizer(self):
        """Create LotSizer instance."""
        return LotSizer(default_lot=3.0)

    @pytest.fixture
    def standard_symbol(self):
        """Standard 5-digit forex symbol (EUR/USD)."""
        return SymbolInfo(
            symbol="EUR/USD",
            digits=5,
            pip_position=-4,
            lot_step=0.01,
            min_lots=0.01,
            max_lots=100.0,
        )

    @pytest.fixture
    def jpy_symbol(self):
        """JPY forex symbol (USD/JPY)."""
        return SymbolInfo(
            symbol="USD/JPY",
            digits=3,
            pip_position=-2,
            lot_step=0.01,
            min_lots=0.01,
            max_lots=100.0,
        )

    def test_calculate_basic_buy(self, sizer, standard_symbol):
        """Should calculate lot size for basic buy trade."""
        result = sizer.calculate(
            entry_price=1.0890,
            sl_price=1.0850,
            symbol_info=standard_symbol,
        )
        assert result.accepted is True
        assert result.lots > 0
        assert result.sl_distance_pips > 0

    def test_calculate_basic_sell(self, sizer, standard_symbol):
        """Should calculate lot size for sell trade."""
        result = sizer.calculate(
            entry_price=1.0850,
            sl_price=1.0890,
            symbol_info=standard_symbol,
        )
        assert result.accepted is True
        assert result.lots > 0

    def test_calculate_zero_sl_distance(self, sizer, standard_symbol):
        """Should reject when SL distance is zero."""
        result = sizer.calculate(
            entry_price=1.0890,
            sl_price=1.0890,
            symbol_info=standard_symbol,
        )
        assert result.accepted is False
        assert "zero" in result.rejection_reason.lower()

    def test_calculate_jpy_pair(self, sizer, jpy_symbol):
        """Should handle JPY pair correctly."""
        result = sizer.calculate(
            entry_price=149.50,
            sl_price=148.50,
            symbol_info=jpy_symbol,
        )
        assert result.accepted is True
        assert result.lots > 0

    def test_price_to_pips_standard(self, sizer, standard_symbol):
        """Should convert price to pips correctly for standard pairs."""
        pips = sizer._price_to_pips(0.0040, standard_symbol)
        assert pips == 40.0

    def test_price_to_pips_jpy(self, sizer, jpy_symbol):
        """Should convert price to pips correctly for JPY pairs."""
        pips = sizer._price_to_pips(1.00, jpy_symbol)
        assert pips == 100.0

    def test_lot_step_rounding(self, sizer, standard_symbol):
        """Should round to lot step."""
        result = sizer.calculate(
            entry_price=1.0890,
            sl_price=1.0885,
            symbol_info=standard_symbol,
        )
        # Lots should be rounded to 0.01 step
        rounded = round(result.lots / 0.01) * 0.01
        assert abs(result.lots - rounded) < 0.001

    def test_min_lot_enforcement(self, sizer, standard_symbol):
        """Should enforce minimum lot size."""
        result = sizer.calculate(
            entry_price=1.0890,
            sl_price=1.0000,  # Huge SL distance -> tiny lots
            symbol_info=standard_symbol,
        )
        assert result.lots >= standard_symbol.min_lots

    def test_can_open_with_margin_true(self, sizer, standard_symbol):
        """Should return True when sufficient margin."""
        result = sizer.can_open_with_margin(
            available_margin=10000.0,
            symbol_info=standard_symbol,
            lots=3.0,
            entry_price=1.0890,
        )
        assert result is True

    def test_can_open_with_margin_false(self, sizer, standard_symbol):
        """Should return False when insufficient margin."""
        result = sizer.can_open_with_margin(
            available_margin=100.0,  # Too low
            symbol_info=standard_symbol,
            lots=3.0,
            entry_price=1.0890,
        )
        assert result is False
