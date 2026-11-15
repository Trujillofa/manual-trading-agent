"""ETR client retry policy for renders that timed out before hydrating (no network)."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.etr.client import (
    RENDER_RETRY_TIMEOUT_MS,
    RENDER_TIMEOUT_MS,
    EtrClient,
    EtrRenderIncompleteError,
)
from src.etr.parser import EtrUnavailableError

FIXTURES = Path(__file__).parent / "fixtures"
FULL = (FIXTURES / "etr_rendered_btc.txt").read_text(encoding="utf-8")
# Rendered, but the terminal itself says it has nothing to show.
NOTICE_ONLY = (FIXTURES / "etr_rendered_gold_unavailable.txt").read_text(encoding="utf-8")
# Shell with the section title and no analysis yet: what a page looks like mid-hydration.
PARTIAL = "ETR Edge\nTrading Terminal\nAnálisis técnico\n"


def make_client() -> EtrClient:
    return EtrClient("user@example.com", "secret", renderer_url="http://renderer:8000")


def fetch(client: EtrClient, sequence: list[tuple[str, str]]) -> tuple[Any, list[int]]:
    calls: list[int] = []

    async def fake_render(_self: EtrClient, _asset: str, *, timeout_ms: int) -> tuple[str, str]:
        calls.append(timeout_ms)
        return sequence[len(calls) - 1]

    async def run() -> Any:
        with patch.object(EtrClient, "_render_once", fake_render):
            return await client.fetch_report("btc")

    try:
        return asyncio.run(run()), calls
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller for assertions
        return exc, calls


class RenderRetryTests(unittest.TestCase):
    def test_timeout_is_retried_once_and_can_recover(self) -> None:
        report, calls = fetch(make_client(), [(PARTIAL, "timeout"), (FULL, "ready")])
        self.assertEqual(calls, [RENDER_TIMEOUT_MS, RENDER_RETRY_TIMEOUT_MS])
        self.assertEqual(report.asset, "btc")
        self.assertIsNotNone(report.context_score)

    def test_ready_render_is_never_retried(self) -> None:
        report, calls = fetch(make_client(), [(FULL, "ready")])
        self.assertEqual(calls, [RENDER_TIMEOUT_MS])
        self.assertIsNotNone(report.context_score)

    def test_renderer_without_outcome_still_parses(self) -> None:
        report, calls = fetch(make_client(), [(FULL, "")])
        self.assertEqual(calls, [RENDER_TIMEOUT_MS])
        self.assertIsNotNone(report.context_score)

    def test_two_timeouts_with_an_empty_terminal_are_unavailable(self) -> None:
        result, calls = fetch(make_client(), [(NOTICE_ONLY, "timeout"), (NOTICE_ONLY, "timeout")])
        self.assertEqual(len(calls), 2)
        self.assertIsInstance(result, EtrUnavailableError)

    def test_two_timeouts_without_a_terminal_are_a_render_failure(self) -> None:
        result, calls = fetch(make_client(), [("x" * 21, "timeout"), ("x" * 21, "timeout")])
        self.assertEqual(len(calls), 2)
        self.assertIsInstance(result, EtrRenderIncompleteError)
        self.assertIn("did not load the analysis page", str(result))


if __name__ == "__main__":
    unittest.main()
