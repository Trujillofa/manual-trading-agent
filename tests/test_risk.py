"""Tests for risk manager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.risk.manager import DailyStats, Position, RiskLevel, RiskManager


class TestRiskManager:
    """RiskManager tests."""

    @pytest.fixture
    def manager(self):
        """Create RiskManager with test limits."""
        return RiskManager(
            max_concurrent_positions=2,
            max_daily_loss_usd=500.0,
            max_drawdown_pct=0.10,
        )

    @pytest.fixture
    def open_position(self):
        """Create sample open position."""
        return Position(
            symbol="EUR/USD",
            side="buy",
            entry_price=1.0890,
            lots=3.0,
            open_time=datetime.now(UTC),
        )

    @pytest.mark.asyncio
    async def test_pre_trade_check_allowed(self, manager):
        """Should allow trade when within limits."""
        result = await manager.pre_trade_check("EUR/USD", "buy", 3.0)
        assert result.allowed is True
        assert result.level == RiskLevel.ALLOWED

    @pytest.mark.asyncio
    async def test_pre_trade_check_max_positions(self, manager, open_position):
        """Should block when max positions reached."""
        manager._positions["EUR/USD"] = open_position
        manager._positions["GBP/USD"] = open_position

        result = await manager.pre_trade_check("USD/JPY", "buy", 3.0)
        assert result.allowed is False
        assert "max" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_pre_trade_check_duplicate_symbol(self, manager, open_position):
        """Should block if symbol already has position."""
        manager._positions["EUR/USD"] = open_position

        result = await manager.pre_trade_check("EUR/USD", "buy", 3.0)
        assert result.allowed is False
        assert "already open" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_record_trade(self, manager):
        """Should record new trade."""
        position = await manager.record_trade("EUR/USD", "buy", 1.0890, 3.0)

        assert position.symbol == "EUR/USD"
        assert position.side == "buy"
        assert position.entry_price == 1.0890
        assert position.lots == 3.0
        assert "EUR/USD" in manager._positions

    @pytest.mark.asyncio
    async def test_close_position(self, manager):
        """Should close position and update stats."""
        await manager.record_trade("EUR/USD", "buy", 1.0890, 3.0)

        await manager.close_position("EUR/USD", 1.0920, 100.0)

        assert "EUR/USD" not in manager._positions
        stats = manager.get_daily_stats()
        assert stats.trades == 1
        assert stats.wins == 1
        assert stats.losses == 0
        assert stats.pnl == 100.0

    @pytest.mark.asyncio
    async def test_close_position_loss(self, manager):
        """Should record loss when PnL negative."""
        await manager.record_trade("EUR/USD", "buy", 1.0890, 3.0)

        await manager.close_position("EUR/USD", 1.0850, -100.0)

        stats = manager.get_daily_stats()
        assert stats.losses == 1
        assert stats.pnl == -100.0

    @pytest.mark.asyncio
    async def test_daily_loss_limit(self, manager):
        """Should block when daily loss limit reached."""
        manager._daily_stats.pnl = -500.0

        result = await manager.pre_trade_check("EUR/USD", "buy", 3.0)
        assert result.allowed is False
        assert "daily loss" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_drawdown_limit(self, manager):
        """Should block when drawdown limit reached."""
        manager._peak_equity = 10000.0
        manager._total_equity = 8999.0

        result = await manager.pre_trade_check("EUR/USD", "buy", 3.0)
        assert result.allowed is False
        assert "drawdown" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_update_equity(self, manager):
        """Should update total equity."""
        manager.update_equity(10500.0)
        assert manager._total_equity == 10500.0
        assert manager._peak_equity == 10500.0

    @pytest.mark.asyncio
    async def test_update_equity_new_peak(self, manager):
        """Should update peak equity when new high."""
        manager._total_equity = 8000.0
        manager._peak_equity = 10000.0

        manager.update_equity(11000.0)

        assert manager._total_equity == 11000.0
        assert manager._peak_equity == 11000.0

    def test_get_open_positions(self, manager, open_position):
        """Should return copy of open positions."""
        manager._positions["EUR/USD"] = open_position

        positions = manager.get_open_positions()
        assert "EUR/USD" in positions
        assert len(positions) == 1

    def test_get_daily_stats(self, manager):
        """Should return daily stats."""
        stats = manager.get_daily_stats()
        assert isinstance(stats, DailyStats)
        assert stats.date == manager._today()

    def test_drawdown_calculation(self, manager):
        """Should calculate drawdown correctly."""
        manager._peak_equity = 10000.0
        manager._total_equity = 9500.0

        dd = manager._drawdown_pct()
        assert dd == 0.05

    def test_drawdown_calculation_no_drawdown(self, manager):
        """Should return 0 when no drawdown."""
        manager._peak_equity = 10000.0
        manager._total_equity = 11000.0

        dd = manager._drawdown_pct()
        assert dd == 0.0

    def test_today_format(self, manager):
        """Should return date in YYYY-MM-DD format."""
        today = manager._today()
        assert len(today) == 10
        assert today[4] == "-"
        assert today[7] == "-"
