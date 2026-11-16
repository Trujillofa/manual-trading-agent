"""Run one real ETR poll in its own process, with a fake upstream.

Used by the multi-process coordination tests. No network, renderer or notifier
is touched: the fake client returns a parsed fixture, while state, audit, lock
and atomic writes are the production implementations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True)
    parser.add_argument("--logs", required=True)
    parser.add_argument("--barrier", required=True)
    parser.add_argument("--peers", type=int, default=1)
    parser.add_argument("--updated-at", default="12/08/2026")
    parser.add_argument("--hold-file", default=None)
    parser.add_argument("--lock-timeout", type=float, default=None)
    args = parser.parse_args()

    os.environ["MANUAL_TRADING_AGENT_LOG_DIR"] = args.logs
    barrier = Path(args.barrier)
    barrier.mkdir(parents=True, exist_ok=True)
    fixture = (Path(__file__).resolve().parents[1] / "fixtures" / "etr_rendered_btc.txt").read_text(
        encoding="utf-8"
    )

    from src.config.settings import EtrConfig
    from src.etr import coordination, service
    from src.etr.parser import parse_rendered_text

    if args.lock_timeout is not None:
        coordination.DEFAULT_POLL_TIMEOUT_SECONDS = args.lock_timeout

    class FakeClient:
        async def fetch_report(self, asset: str):
            (barrier / f"fetching-{asset}").write_text("1", encoding="utf-8")
            deadline = time.monotonic() + 20
            while True:
                if args.hold_file:
                    if Path(args.hold_file).exists():
                        break
                elif len(list(barrier.glob("fetching-*"))) >= args.peers:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("subprocess barrier timed out")
                await asyncio.sleep(0.01)
            (barrier / f"fetched-{asset}").write_text("1", encoding="utf-8")
            report = parse_rendered_text(fixture, asset)
            report.updated_at = args.updated_at
            return report

    service.build_client = lambda config: FakeClient()
    config = EtrConfig(
        assets=[args.asset],
        min_poll_interval_seconds=1,
        telegram_alerts=False,
        login="subprocess@example.com",
        password="offline",
    )
    summary = asyncio.run(service.poll_and_notify(SimpleNamespace(etr=config), None, force=True))
    print(
        json.dumps(
            {
                "skipped": summary.skipped,
                "message": summary.message,
                "notified": summary.notified_count,
                "errors": summary.error_count,
                "seeded": [result.seeded for result in summary.results],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
