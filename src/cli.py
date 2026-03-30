"""CLI entry point for manual trading agent."""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.backtest.engine import BacktestEngine
from src.config import get_settings
from src.data.fetcher import DataFetcher
from src.indicators.high_low import highest_high, lowest_low
from src.indicators.rsi import calculate_rsi
from src.news.news_checker import NewsChecker
from src.strategy.multi_timeframe import MTFRSIStrategy


def _parse_pairs(raw_pairs: str | None) -> list[str] | None:
    if raw_pairs is None:
        return None

    pairs = [pair.strip().upper() for pair in raw_pairs.split(",") if pair.strip()]
    return pairs or None


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manual Forex Trading Agent - RSI Multi-Timeframe Strategy"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    scan_parser = subparsers.add_parser("scan", help="Scan pairs for signals")
    scan_parser.add_argument("--pairs", help="Comma-separated pairs (default: all)")
    scan_parser.add_argument("--timeframe", default="15m", help="Execution timeframe")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze specific pair")
    analyze_parser.add_argument("pair", help="Pair to analyze (e.g., EUR/USD)")
    analyze_parser.add_argument("--timeframe", default="15m", help="Timeframe")

    news_parser = subparsers.add_parser("news", help="Check upcoming news")
    news_parser.add_argument("--hours", type=int, default=24, help="Hours ahead")

    backtest_parser = subparsers.add_parser("backtest", help="Run backtest")
    backtest_parser.add_argument("--pair", required=True, help="Pair to backtest (e.g., EUR/USD)")
    backtest_parser.add_argument("--start", help="Start date YYYY-MM-DD")
    backtest_parser.add_argument("--end", help="End date YYYY-MM-DD")

    return parser


async def run_scan(pairs: list[str] | None, timeframe: str) -> None:
    settings = get_settings()
    fetcher = DataFetcher()
    strategy = MTFRSIStrategy()

    majors = list(getattr(settings.trading, "majors", []))
    minors = list(getattr(settings.trading, "minors", []))
    selected_pairs = pairs or (majors + minors)

    print(f"\n[SCAN] Scanning {len(selected_pairs)} pairs on {timeframe}...")

    for pair in selected_pairs:
        try:
            data_15m = fetcher.fetch(pair, period="3d", interval="15m")
            if data_15m.empty:
                continue

            close = data_15m["close"].values.tolist()
            high = data_15m["high"].values.tolist()
            low = data_15m["low"].values.tolist()

            lookback = int(strategy.strategy_config.lookback_bars)
            rsi_period = int(strategy.strategy_config.rsi_period)
            rsi_15m = calculate_rsi(close[-50:], rsi_period)
            hh = highest_high(high, lookback)
            ll = lowest_low(low, lookback)
            close_price = close[-1]

            print(f"\n{pair}:")
            print(f"  Price: {close_price:.5f}")
            print(f"  RSI(14): {rsi_15m:.1f}" if rsi_15m is not None else "  RSI(14): N/A")
            print(f"  20-bar HH: {hh:.5f}" if hh is not None else "  20-bar HH: N/A")
            print(f"  20-bar LL: {ll:.5f}" if ll is not None else "  20-bar LL: N/A")
        except Exception as exc:
            print(f"  Error: {exc}")


async def run_analyze(pair: str, timeframe: str) -> None:
    _ = get_settings()
    fetcher = DataFetcher()

    print(f"\n[ANALYZE] {pair} on {timeframe}")

    try:
        mtf_data = fetcher.fetch_multi_timeframe(pair, period="7d")

        for tf_name, data in mtf_data.items():
            if data.empty:
                print(f"  {tf_name}: No data")
                continue

            close = data["close"].values.tolist()
            rsi = calculate_rsi(close[-50:], 14) if len(close) >= 50 else None
            if rsi is None:
                print(f"  {tf_name}: {len(data)} candles, RSI: N/A")
                continue

            print(f"  {tf_name}: {len(data)} candles, latest RSI(14): {rsi:.1f}")
    except Exception as exc:
        print(f"  Error: {exc}")


async def run_news(hours: int) -> None:
    checker = NewsChecker()

    print("\n[NEWS] Fetching upcoming 3-star events...")

    try:
        events = await checker.fetch_events(hours_ahead=hours)

        if not events:
            print(f"  No 3-star events in the next {hours} hours")
            return

        for event in events:
            print(f"  {event.timestamp.strftime('%Y-%m-%d %H:%M')} {event.currency}: {event.name}")
    except Exception as exc:
        print(f"  Error: {exc}")


async def run_backtest(pair: str, start: str | None, end: str | None) -> None:
    _ = get_settings()
    fetcher = DataFetcher()
    strategy = MTFRSIStrategy()
    engine = BacktestEngine(strategy)

    print(f"\n[BACKTEST] {pair}")

    try:
        mtf_data = fetcher.fetch_multi_timeframe(pair, start=start, end=end)

        result = await engine.run(
            pair,
            mtf_data["1h"],
            mtf_data["30m"],
            mtf_data["15m"],
        )

        print(f"  Period: {result.start_date.date()} to {result.end_date.date()}")
        print(f"  Total trades: {result.total_trades}")
        print(f"  Win rate: {result.win_rate:.1%}")
        print(f"  Total PnL: ${result.total_pnl:.2f}")
        print(f"  Max drawdown: {result.max_drawdown:.1%}")
    except Exception as exc:
        print(f"  Error: {exc}")


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    handlers = {
        "scan": lambda: run_scan(_parse_pairs(args.pairs), args.timeframe),
        "analyze": lambda: run_analyze(args.pair, args.timeframe),
        "news": lambda: run_news(args.hours),
        "backtest": lambda: run_backtest(args.pair, args.start, args.end),
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    asyncio.run(handler())
    return 0


if __name__ == "__main__":
    sys.exit(main())
