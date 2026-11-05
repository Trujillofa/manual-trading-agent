"""Stateless renderer for ETR analysis pages.

The page is a client-rendered app. The server returns a shell with no analysis in
it: bias, context score and scenarios are computed in the browser from the
account's candles, and only exist in the DOM after that engine runs. A plain HTTP
GET can never see them, at any user agent.

This service runs the browser and returns the page's visible text. It holds no
credentials and keeps no session: the caller passes its own Supabase cookie, so
authentication stays the agent's responsibility and this container is disposable.

One browser is launched lazily and reused: a cold Chromium start costs several
seconds on this host, and launching one per asset made a four-asset poll take
longer than the agent was willing to wait. Renders are serialised because a
single page peaks at a few hundred MB.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import Browser, Playwright, async_playwright

READY_MARKER = "context score"
# The terminal says one of these while it has no scenarios to show. They must hold
# for a couple of ticks: the page shows "reconectando precios" briefly while it
# hydrates, and returning on the first sighting produced a half-rendered header
# instead of the analysis.
# Permanent: the terminal will not produce scenarios until its data is current.
NOTICE_MARKERS = (
    "escenarios se mostrar",
    "historial de 5m está desactualizado",
    "historial de 5m esta desactualizado",
)
# Transient: shown while the quote stream reconnects, including on pages that do
# go on to render a full analysis. Never bail out on this one alone.
TRANSIENT_MARKERS = ("reconectando precios",)
NOTICE_TICKS = 3
POLL_INTERVAL_MS = 2_000
# "Reconectando precios" shows while the app hydrates and can persist for half a
# minute on a cold page. Treating it as conclusive early returned a header with no
# charts, so a notice only counts after the page has had time to load.
MIN_NOTICE_SECONDS = 30

app = FastAPI(title="etr-renderer")

_lock = asyncio.Lock()
_playwright: Playwright | None = None
_browser: Browser | None = None


class RenderRequest(BaseModel):
    url: str
    cookie: str
    timeout_ms: int = 75_000


async def _browser_instance() -> Browser:
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
    return _browser


_context = None


async def _context_instance(browser: Browser):
    """One context for the whole process: a fresh context per render re-downloaded

    the entire Next.js bundle for every asset, which is what made a four-asset poll
    take minutes and time out before the analysis appeared.
    """
    global _context
    if _context is None:
        _context = await browser.new_context(
            viewport={"width": 1360, "height": 1800}, locale="es-ES"
        )
    return _context


async def _add_cookies(context, cookie_header: str) -> None:
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if not name or not value:
            continue
        await context.add_cookies(
            [
                {
                    "name": name,
                    "value": value,
                    "domain": ".etracademy.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                }
            ]
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/render")
async def render(req: RenderRequest) -> dict[str, object]:
    async with _lock:
        browser = await _browser_instance()
        context = await _context_instance(browser)
        try:
            await _add_cookies(context, req.cookie)
            page = await context.new_page()
            response = await page.goto(
                req.url, wait_until="domcontentloaded", timeout=req.timeout_ms
            )
            started = time.monotonic()
            deadline = started + req.timeout_ms / 1000
            text = ""
            notice_ticks = 0
            while time.monotonic() < deadline:
                text = await page.inner_text("body")
                low = text.lower()
                if READY_MARKER in low:
                    break
                settled = time.monotonic() - started >= MIN_NOTICE_SECONDS
                if any(marker in low for marker in NOTICE_MARKERS):
                    notice_ticks += 1
                    if notice_ticks >= NOTICE_TICKS and settled:
                        break
                elif settled and "cierre" not in low:
                    # No chart close price after the settle window: this asset has
                    # no market data on the account, so no analysis is coming.
                    notice_ticks += 1
                    if notice_ticks >= NOTICE_TICKS:
                        break
                else:
                    notice_ticks = 0
                await page.wait_for_timeout(POLL_INTERVAL_MS)
            low = text.lower()
            print(
                f"render {req.url} chars={len(text)} score={READY_MARKER in low} "
                f"sesgo={'sesgo' in low}",
                flush=True,
            )
            return {
                "text": text,
                "url": page.url,
                "status": response.status if response else None,
            }
        finally:
            await page.close()
