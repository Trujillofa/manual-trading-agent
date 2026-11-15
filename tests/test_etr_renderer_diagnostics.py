"""Offline renderer diagnostics; run with the renderer image's Python."""
import asyncio
import importlib.util
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

app_path = os.environ.get("ETR_RENDERER_APP_PATH") or str(
    Path(__file__).resolve().parents[1] / "deploy/etr-renderer/app.py"
)
spec = importlib.util.spec_from_file_location("etr_renderer", app_path)
assert spec is not None and spec.loader is not None
renderer = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(renderer)
except ModuleNotFoundError as exc:
    if exc.name in {"fastapi", "playwright"}:
        raise unittest.SkipTest(f"renderer dependency missing: {exc.name}") from exc
    raise


class Page:
    def __init__(self, text, status=503, url="https://etracademy.com/analisis/btc"):
        self.text, self.status, self.url = text, status, url
        self.closed = False

    async def goto(self, *args, **kwargs):
        return type("Response", (), {"status": self.status})()

    async def inner_text(self, selector):
        return self.text

    async def wait_for_timeout(self, ms):
        clock[0] += ms / 1000

    async def close(self):
        self.closed = True


clock = [0.0]


class Context:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page

    async def add_cookies(self, cookies):
        pass


class RendererDiagnosticsTests(unittest.TestCase):
    def run_render(self, page, timeout=75000):
        clock[0] = 0
        async def browser():
            return object()
        async def context(_):
            return Context(page)
        output = io.StringIO()
        with patch.object(renderer, "_browser_instance", browser), patch.object(renderer, "_context_instance", context), patch.object(renderer.time, "monotonic", lambda: clock[0]), redirect_stdout(output):
            result = asyncio.run(renderer.render(renderer.RenderRequest(url="https://etracademy.com/analisis/btc", cookie="", timeout_ms=timeout)))
        return result, output.getvalue()

    def test_short_error_body_diagnostics_without_content(self):
        page = Page("x" * 21)
        result, log = self.run_render(page)
        self.assertEqual(result["text"], "x" * 21)
        self.assertEqual(result["status"], 503)
        self.assertEqual(result["outcome"], "timeout")
        self.assertIn("chars=21", log)
        self.assertIn("status=503 outcome=timeout elapsed_ms=75000 redirected=False", log)
        self.assertNotIn("x" * 21, log)
        self.assertTrue(page.closed)

    def test_partial_page_waits_instead_of_bailing_out(self):
        # A page holding only the terminal shell used to be abandoned after the
        # settle window, which turned a hydrating analysis into a false
        # "no analysis for this asset". It must now use the whole timeout.
        page = Page("Análisis técnico\n" + "y" * 1000)
        result, log = self.run_render(page)
        self.assertEqual(result["outcome"], "timeout")
        self.assertIn("elapsed_ms=75000", log)
        self.assertGreater(len(result["text"]), 1000)

    def test_ready_notice_and_timeout(self):
        for text, timeout, outcome in [("Context score sesgo", 75000, "ready"), ("historial de 5m está desactualizado", 75000, "notice"), ("cierre", 4000, "timeout")]:
            with self.subTest(outcome=outcome):
                page = Page(text, 200)
                _, log = self.run_render(page, timeout)
                self.assertIn("outcome=" + outcome, log)
                self.assertTrue(page.closed)

    def test_cookie_failure_preserves_original_exception(self):
        async def browser():
            return object()
        async def context(_):
            return object()
        async def fail(*args):
            raise ValueError("cookie failure")
        with patch.object(renderer, "_browser_instance", browser), patch.object(renderer, "_context_instance", context), patch.object(renderer, "_add_cookies", fail), self.assertRaisesRegex(ValueError, "cookie failure"):
            asyncio.run(renderer.render(renderer.RenderRequest(url="https://etracademy.com/analisis/btc", cookie="")))

    def test_navigation_timeout_is_retryable_and_closes_page(self):
        class TimedOutPage(Page):
            async def goto(self, *args, **kwargs):
                raise renderer.PlaywrightTimeoutError("navigation timed out")

        page = TimedOutPage("")
        result, _ = self.run_render(page)
        self.assertEqual(result["outcome"], "timeout")
        self.assertEqual(result["text"], "")
        self.assertTrue(page.closed)


if __name__ == "__main__":
    unittest.main()
