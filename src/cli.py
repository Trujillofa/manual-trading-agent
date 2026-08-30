"""CLI entry point for manual trading agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from src.config import get_settings
from src.config.instruments import require_backtest_supported
from src.dashboard.log_status import run_logs_status as _logs_status_run
from src.dashboard.report import run_dashboard as _dashboard_run
from src.dashboard.report import run_healthcheck as _healthcheck_run
from src.data.fetcher import DataFetcher
from src.news.news_checker import NewsChecker, format_cli_news_report
from src.scanner.gates import _get_pair_param
from src.scanner.scan_service import run_scan


def _parse_pairs(raw_pairs: str | None) -> list[str] | None:
    if raw_pairs is None:
        return None

    pairs = [pair.strip().upper() for pair in raw_pairs.split(",") if pair.strip()]
    return pairs or None


def _parse_etr_assets(raw_assets: str | None) -> list[str] | None:
    if raw_assets is None:
        return None
    assets = [a.strip().lower() for a in raw_assets.split(",") if a.strip()]
    return assets or None


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manual Forex Trading Agent - RSI Multi-Timeframe Strategy"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    scan_parser = subparsers.add_parser("scan", help="Scan pairs for signals")
    scan_parser.add_argument("--pairs", help="Comma-separated pairs (default: all)")
    scan_parser.add_argument("--timeframe", default="15m", help="Execution timeframe")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze specific pair")
    analyze_parser.add_argument("pair", help="Instrument to analyze (e.g., XAU/USD)")
    analyze_parser.add_argument(
        "--timeframe", default="15m", help="Unused; card is always 1h/30m/15m"
    )

    news_parser = subparsers.add_parser("news", help="Check upcoming news")
    news_parser.add_argument("--hours", type=int, default=24, help="Hours ahead")

    enhanced_backtest_parser = subparsers.add_parser(
        "backtest-enhanced", help="Run enhanced backtest with TP/SL"
    )
    enhanced_backtest_parser.add_argument(
        "--pair", required=True, help="Pair to backtest (e.g., EUR/USD)"
    )
    enhanced_backtest_parser.add_argument("--start", help="Start date YYYY-MM-DD")
    enhanced_backtest_parser.add_argument("--end", help="End date YYYY-MM-DD")
    enhanced_backtest_parser.add_argument(
        "--no-patterns", action="store_true", help="Disable pattern detection"
    )
    enhanced_backtest_parser.add_argument(
        "--no-divergence", action="store_true", help="Disable divergence detection"
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma", action="store_true", help="Enable RSI-MA curl gate (momentum confirmation)"
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-period", type=int, default=5, help="RSI-MA lookback period (default: 5)"
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-variant",
        choices=["curl", "fresh", "slope", "distance", "confidence", "conditional", "gate"],
        default="curl",
        help="RSI-MA variant: curl (cross back), fresh (not exhausted), slope (inflection), distance (momentum threshold), confidence (modifier not gate), conditional (low-conf only)",
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-distance-max",
        type=float,
        default=15.0,
        help="Max RSI-to-MA distance for distance variant (default: 15)",
    )
    enhanced_backtest_parser.add_argument(
        "--rsi-ma-confidence-mod",
        type=float,
        default=0.85,
        help="Confidence multiplier when no curl detected for confidence variant (default: 0.85)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence",
        action="store_true",
        help="Enable EMA trend-alignment confidence modifier (boost when price agrees with EMA-ref trend, dampen when counter)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence-ref",
        type=int,
        default=200,
        help="EMA period for trend reference (default: 200)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence-boost",
        type=float,
        default=1.10,
        help="Confidence multiplier when trend-aligned (default: 1.10)",
    )
    enhanced_backtest_parser.add_argument(
        "--ema-confidence-dampen",
        type=float,
        default=0.85,
        help="Confidence multiplier when trend-counter (default: 0.85)",
    )

    subparsers.add_parser("telegram-poll", help="Poll Telegram commands (e.g. /watchlist)")
    subparsers.add_parser("healthcheck", help="Check scanner and Telegram runtime health")

    logs_status_parser = subparsers.add_parser(
        "logs-status",
        help="Report managed log sizes vs rotation threshold",
    )
    logs_status_parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Telegram alerts when warn/critical thresholds are newly crossed",
    )

    dash_parser = subparsers.add_parser("dashboard", help="Signal dashboard and paper P&L")
    dash_parser.add_argument("--days", type=int, default=30, help="Days of history to show")

    etr_parser = subparsers.add_parser(
        "etr",
        help="Fetch ETR Market Terminal report (btc|gold|nasdaq|oil)",
    )
    etr_parser.add_argument(
        "--asset",
        default="btc",
        help="Asset slug: btc, gold, nasdaq, oil (default: btc)",
    )
    etr_parser.add_argument(
        "--notify",
        action="store_true",
        help="Also send the full report to Telegram",
    )
    etr_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of human text",
    )

    etr_scan_parser = subparsers.add_parser(
        "etr-scan",
        help="Poll all configured ETR assets; Telegram on structural changes only",
    )
    etr_scan_parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore min_poll_interval_seconds",
    )
    etr_scan_parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Update state/audit without sending Telegram",
    )
    etr_scan_parser.add_argument(
        "--assets",
        help="Comma-separated asset list (default: settings.etr.assets)",
    )

    subparsers.add_parser(
        "etr-shadow",
        help="Forward paper-shadow summary (TP1 vs invalidation, open events)",
    )

    briefing_parser = subparsers.add_parser(
        "pre-ny-briefing",
        help="Once-per-day pre-NY three-pillar briefing (Gold/BTC/Nasdaq/Oil)",
    )
    briefing_parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore schedule window, weekend skip, and once-per-day state",
    )
    briefing_parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Build the briefing without sending Telegram",
    )

    return parser


async def run_analyze(pair: str, timeframe: str) -> None:
    """Print the same instrument card used by the pre-NY briefing."""
    from src.briefing.formatter import format_instrument_briefing
    from src.briefing.service import build_briefing

    _ = timeframe
    settings = get_settings()
    symbol = pair.strip().upper()
    try:
        briefing = await build_briefing(
            settings,
            instrument_ids=[symbol],
            attach_hermes=False,
            fetch_funding=False,
        )
    except Exception as exc:
        print(f"[ANALYZE] {symbol}: {exc}")
        return
    if not briefing.instruments:
        print(f"[ANALYZE] {symbol}: no briefing card")
        return
    print(format_instrument_briefing(briefing.instruments[0]))


async def run_news(hours: int) -> None:
    checker = NewsChecker()

    try:
        await checker.fetch_events(hours_ahead=hours)
        now = datetime.now(UTC)
        events = checker.get_display_events(hours, now)
        print(
            format_cli_news_report(
                events,
                hours,
                now,
                checker.get_surprise_readiness(),
            )
        )
    except Exception as exc:
        print(f"  Error: {exc}")


async def run_enhanced_backtest(
    pair: str,
    start: str | None,
    end: str | None,
    use_patterns: bool = True,
    use_divergence: bool = True,
    use_rsi_ma: bool = False,
    rsi_ma_period: int = 5,
    rsi_ma_variant: str = "curl",
    rsi_ma_distance_max: float = 15.0,
    rsi_ma_confidence_mod: float = 0.85,
    use_ema_confidence: bool = False,
    ema_confidence_ref_period: int = 200,
    ema_confidence_boost: float = 1.10,
    ema_confidence_dampen: float = 0.85,
) -> None:
    """Run enhanced backtest with realistic TP/SL simulation.

    Replay-only. This command does not send broker orders and is not a
    live-go or promote path. Holdout metrics are printed and unused.
    """
    require_backtest_supported(pair)
    from src.backtest.enhanced_engine import EnhancedBacktestEngine
    from src.backtest.windows import (
        WindowMetrics,
        cutoff_at,
        format_window_line,
        split_trade_metrics,
    )

    fetcher = DataFetcher()

    print(f"\n[ENHANCED BACKTEST] {pair}")
    print("  Mode: offline replay — not a live-go / promote path")
    print(f"  Patterns: {'enabled' if use_patterns else 'disabled'}")
    print(f"  Divergence: {'enabled' if use_divergence else 'disabled'}")
    print(
        f"  RSI-MA: {'enabled (variant=' + rsi_ma_variant + ', period=' + str(rsi_ma_period) + ')' if use_rsi_ma else 'disabled'}"
    )

    try:
        # Fetch 1h data for longer history (15m limited to 60 days on yfinance)
        symbol = pair.replace("/", "").replace("-", "")
        data = fetcher.fetch(symbol, period="2y", interval="1h")

        if data.empty:
            print("  Error: No data available")
            return

        settings = get_settings()
        engine = EnhancedBacktestEngine(
            initial_balance=10000.0,
            risk_per_trade=0.02,
            use_patterns=use_patterns,
            use_divergence=use_divergence,
            sma_period=int(_get_pair_param(pair, "sma_period", settings.strategy.sma_period)),
            reward_ratio=float(
                _get_pair_param(pair, "tp_atr_multiplier", settings.risk.tp_atr_multiplier)
            ),
            sl_atr_multiplier=float(
                _get_pair_param(pair, "sl_atr_multiplier", settings.risk.sl_atr_multiplier)
            ),
            use_rsi_ma=use_rsi_ma,
            rsi_ma_period=rsi_ma_period,
            rsi_ma_variant=rsi_ma_variant,
            rsi_ma_distance_max=rsi_ma_distance_max,
            rsi_ma_confidence_mod=rsi_ma_confidence_mod,
            use_ema_confidence=use_ema_confidence,
            ema_confidence_ref_period=ema_confidence_ref_period,
            ema_confidence_boost=ema_confidence_boost,
            ema_confidence_dampen=ema_confidence_dampen,
        )
        if use_ema_confidence:
            print(
                f"  EMA confidence: enabled (ref=EMA{ema_confidence_ref_period}, "
                f"boost={ema_confidence_boost}, dampen={ema_confidence_dampen})"
            )

        result = engine.run(pair, data, verbose=False)
        costs = engine.cost_book
        print(
            f"  Costs: spread {costs.spread_pips:g} pips, slip {costs.slippage_pips:g} pips, "
            f"${costs.commission_usd_per_lot_side:g}/lot/side, size {costs.lot_size:g} lot"
        )
        cutoff = cutoff_at(data.index)
        develop: WindowMetrics | None = None
        holdout: WindowMetrics | None = None
        if cutoff is not None:
            develop, holdout = split_trade_metrics(
                result.trades, cutoff, initial_balance=engine.initial_balance
            )

        print(f"\n  Period: {result.start_date.date()} to {result.end_date.date()}")
        print(f"  Total trades: {result.total_trades}")
        if develop is not None and holdout is not None:
            print(format_window_line("Develop (first 65%)", develop))
            print(
                format_window_line(
                    "Holdout (last 35%, unused for selection)",
                    holdout,
                )
            )
        print(f"  All-period (not a rank key) win rate: {result.win_rate:.1%}")
        print(f"  All-period PnL: ${result.total_pnl:.2f} ({result.total_pnl_pct:.2f}%)")
        print(f"  Max drawdown: {result.max_drawdown_pct:.2f}%")
        print(f"  Avg win: ${result.avg_win:.2f}")
        print(f"  Avg loss: ${result.avg_loss:.2f}")
        print(f"  All-period profit factor: {result.profit_factor:.2f}")
        print(f"  Sharpe ratio: {result.sharpe_ratio:.2f}")

        if use_patterns:
            print(
                f"\n  Pattern trades: {result.pattern_trades} (win rate: {result.pattern_win_rate:.1%})"
            )
        if use_divergence:
            print(
                f"  Divergence trades: {result.divergence_trades} (win rate: {result.divergence_win_rate:.1%})"
            )
        if use_patterns and use_divergence:
            print(
                f"  Combined trades: {result.combined_trades} (win rate: {result.combined_win_rate:.1%})"
            )

    except Exception as exc:
        print(f"  Error: {exc}")


async def run_telegram_poll() -> None:
    settings = get_settings()
    if not settings.telegram.enabled:
        print("Telegram disabled in settings")
        return
    if not settings.telegram.poll_enabled:
        print("Telegram command polling disabled (TELEGRAM_POLL_ENABLED=false)")
        return
    token = settings.telegram.bot_token
    chat_id = settings.telegram.chat_id
    if not token or not chat_id:
        print("Telegram token/chat_id missing")
        return

    from src.notifications.telegram_commands import (
        HEARTBEAT_PATH,
        POLL_LOCK_PATH,
        TelegramCommandHandler,
    )
    from src.notifications.telegram_security import TelegramPollLock

    lock = TelegramPollLock(POLL_LOCK_PATH)
    if not lock.acquire():
        print("[TELEGRAM] Another telegram-poll process already holds getUpdates lock; exiting")
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(
            json.dumps(
                {
                    "status": "error",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "error": "duplicate local telegram-poll process",
                }
            ),
            encoding="utf-8",
        )
        return

    try:
        handler = TelegramCommandHandler(token, chat_id)
        print("[TELEGRAM] Polling commands...")
        await handler.run_forever()
    finally:
        lock.release()


async def run_healthcheck() -> None:
    await _healthcheck_run()


async def run_etr(asset: str, *, notify: bool = False, as_json: bool = False) -> None:
    """Fetch one ETR Market Terminal report and print it."""
    import json as json_lib

    from src.etr.alerts import chunk_telegram, format_full_report
    from src.etr.service import fetch_one_report
    from src.notifications.telegram import TelegramNotifier

    settings = get_settings()
    report = await fetch_one_report(settings, asset.lower().strip())
    if as_json:
        print(json_lib.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_full_report(report))

    if notify and settings.telegram.enabled and settings.telegram.is_configured:
        notifier = TelegramNotifier(settings.telegram.bot_token, settings.telegram.chat_id)
        for chunk in chunk_telegram(format_full_report(report)):
            await notifier.send(chunk)


async def run_etr_scan(
    *,
    force: bool = False,
    no_notify: bool = False,
    assets: list[str] | None = None,
) -> None:
    """Poll ETR assets and send change-only Telegram alerts."""
    from src.etr.service import poll_and_notify
    from src.notifications.telegram import TelegramNotifier

    settings = get_settings()
    notifier = None
    if settings.telegram.enabled and settings.telegram.is_configured and not no_notify:
        notifier = TelegramNotifier(settings.telegram.bot_token, settings.telegram.chat_id)

    summary = await poll_and_notify(
        settings,
        notifier,
        assets=assets,
        force=force,
        notify=False if no_notify else None,
    )
    print(summary.message)
    for result in summary.results:
        if result.error:
            print(f"  {result.asset}: ERROR {result.error}")
        elif result.seeded:
            print(f"  {result.asset}: baseline seeded")
        elif result.changes:
            fields = ", ".join(c.field for c in result.changes)
            flag = "NOTIFIED" if result.notified else "changed"
            print(f"  {result.asset}: {flag} [{fields}]")
        else:
            print(f"  {result.asset}: no structural change")


async def run_etr_shadow() -> None:
    """Print prospective ETR zone-entry shadow stats."""
    from src.etr.shadow import format_shadow_summary

    print(format_shadow_summary())


async def run_pre_ny_briefing(*, force: bool = False, no_notify: bool = False) -> None:
    """Build the pre-NY briefing; send Telegram when in the once-per-day window."""
    from src.briefing.formatter import format_pre_ny_briefing
    from src.briefing.service import maybe_send_briefing
    from src.notifications.telegram import TelegramNotifier

    settings = get_settings()
    notifier = None
    if settings.telegram.enabled and settings.telegram.is_configured and not no_notify:
        notifier = TelegramNotifier(settings.telegram.bot_token, settings.telegram.chat_id)

    result = await maybe_send_briefing(
        settings,
        notifier,
        force=force,
        notify=not no_notify,
    )
    print(f"[PRE-NY] {result.reason} session={result.session_date} sent={result.sent}")
    if result.briefing is not None:
        print(format_pre_ny_briefing(result.briefing))


async def run_logs_status(notify: bool) -> None:
    await _logs_status_run(notify=notify)


async def run_dashboard(days: int) -> None:
    await _dashboard_run(days)


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
        "backtest-enhanced": lambda: run_enhanced_backtest(
            args.pair,
            args.start,
            args.end,
            use_patterns=not args.no_patterns,
            use_divergence=not args.no_divergence,
            use_rsi_ma=getattr(args, "rsi_ma", False),
            rsi_ma_period=getattr(args, "rsi_ma_period", 5),
            rsi_ma_variant=getattr(args, "rsi_ma_variant", "curl"),
            rsi_ma_distance_max=getattr(args, "rsi_ma_distance_max", 15.0),
            rsi_ma_confidence_mod=getattr(args, "rsi_ma_confidence_mod", 0.85),
            use_ema_confidence=getattr(args, "ema_confidence", False),
            ema_confidence_ref_period=getattr(args, "ema_confidence_ref", 200),
            ema_confidence_boost=getattr(args, "ema_confidence_boost", 1.10),
            ema_confidence_dampen=getattr(args, "ema_confidence_dampen", 0.85),
        ),
        "telegram-poll": run_telegram_poll,
        "healthcheck": run_healthcheck,
        "logs-status": lambda: run_logs_status(args.notify),
        "dashboard": lambda: run_dashboard(args.days),
        "etr": lambda: run_etr(args.asset, notify=args.notify, as_json=args.json),
        "etr-scan": lambda: run_etr_scan(
            force=args.force,
            no_notify=args.no_notify,
            assets=_parse_etr_assets(getattr(args, "assets", None)),
        ),
        "etr-shadow": run_etr_shadow,
        "pre-ny-briefing": lambda: run_pre_ny_briefing(
            force=getattr(args, "force", False),
            no_notify=getattr(args, "no_notify", False),
        ),
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    asyncio.run(handler())
    return 0


if __name__ == "__main__":
    sys.exit(main())
