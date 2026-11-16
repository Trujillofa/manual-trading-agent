"""Stateless renderer for ETR analysis pages.

The page is a client-rendered app. The server returns a shell with no analysis in
it: bias, context score and scenarios are computed in the browser from the
account's candles, and only exist in the DOM after that engine runs. A plain HTTP
GET can never see them, at any user agent.

This service runs the browser and returns the page's visible text. The caller
passes its Supabase cookie on each request. The shared in-memory browser context
can retain cookies and other session state until the renderer process exits.

One browser is launched lazily and reused: a cold Chromium start costs several
seconds on this host, and launching one per asset made a four-asset poll take
longer than the agent was willing to wait. Renders are serialised because a
single page peaks at a few hundred MB.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress

from fastapi import FastAPI
from playwright.async_api import (
    Browser,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from pydantic import BaseModel

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
NOTICE_TICKS = 3
POLL_INTERVAL_MS = 2_000
# A second request waits this long for the single browser before being told the
# renderer is busy. It is bounded so a caller is never queued for a full render
# timeout; "busy" is a non-alerting outcome, not a failure.
RENDER_QUEUE_TIMEOUT_SECONDS = 20.0
# "Reconectando precios" shows while the app hydrates and can persist for half a
# minute on a cold page. Treating it as conclusive early returned a header with no
# charts, so a notice only counts after the page has had time to load.
MIN_NOTICE_SECONDS = 30
# Anything short of READY or a definitive notice waits for the full timeout. There
# used to be an early bail for pages without a chart "cierre" price, meant to spot
# assets with no market data. A missing price can also occur during hydration;
# a timeout does not establish that the terminal has no analysis.

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


async def _acquire_lock_bounded(lock: asyncio.Lock, timeout: float) -> bool:
    """Acquire in bounded time without ever leaking the lock on cancellation.

    ``asyncio.wait_for(lock.acquire(), ...)`` can cancel acquire() at the instant
    it succeeded, leaving the lock held forever. Reconcile the waiter task and
    hand the lock back if that race was lost.
    """
    waiter = asyncio.ensure_future(lock.acquire())
    try:
        done, _ = await asyncio.wait({waiter}, timeout=timeout)
        if done:
            return True
        waiter.cancel()
    except asyncio.CancelledError:
        waiter.cancel()
        await _give_back_if_acquired(waiter, lock)
        raise
    await _give_back_if_acquired(waiter, lock)
    return False


async def _give_back_if_acquired(waiter: asyncio.Future, lock: asyncio.Lock) -> None:
    with suppress(asyncio.CancelledError):
        await waiter
    if waiter.done() and not waiter.cancelled():
        try:
            acquired = waiter.result()
        except Exception:
            acquired = False
        if acquired:
            lock.release()


@app.post("/render")
async def render(req: RenderRequest) -> dict[str, object]:
    if not await _acquire_lock_bounded(_lock, RENDER_QUEUE_TIMEOUT_SECONDS):
        # A busy renderer is not a failure: report it so the caller can count the
        # asset as unavailable instead of raising an error alert.
        print(
            f"render {req.url} outcome=busy elapsed_ms=0 queue_timeout_s="
            f"{RENDER_QUEUE_TIMEOUT_SECONDS:g}",
            flush=True,
        )
        return {"text": "", "url": req.url, "status": None, "outcome": "busy"}
    try:
        browser = await _browser_instance()
        context = await _context_instance(browser)
        page = None
        try:
            await _add_cookies(context, req.cookie)
            page = await context.new_page()
            # Bound navigation and hydration, excluding lock queue and setup.
            started = time.monotonic()
            deadline = started + req.timeout_ms / 1000
            try:
                response = await page.goto(
                    req.url, wait_until="domcontentloaded", timeout=req.timeout_ms
                )
            except PlaywrightTimeoutError:
                return {"text": "", "url": page.url, "status": None, "outcome": "timeout"}
            if response is not None and response.status != 200:
                print(
                    f"render {req.url} chars=0 score=False sesgo=False "
                    f"status={response.status} outcome=http_error "
                    f"elapsed_ms={int((time.monotonic() - started) * 1000)} "
                    f"redirected={page.url != req.url}",
                    flush=True,
                )
                return {
                    "text": "",
                    "url": page.url,
                    "status": response.status,
                    "outcome": "http_error",
                }
            text = ""
            notice_ticks = 0
            outcome = "timeout"
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    text = await asyncio.wait_for(page.inner_text("body"), timeout=remaining)
                except (TimeoutError, PlaywrightTimeoutError):
                    break
                low = text.lower()
                if READY_MARKER in low:
                    outcome = "ready"
                    break
                settled = time.monotonic() - started >= MIN_NOTICE_SECONDS
                if settled and any(marker in low for marker in NOTICE_MARKERS):
                    notice_ticks += 1
                    if notice_ticks >= NOTICE_TICKS:
                        outcome = "notice"
                        break
                else:
                    notice_ticks = 0
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    break
                await page.wait_for_timeout(min(POLL_INTERVAL_MS, remaining_ms))
            low = text.lower()
            print(
                f"render {req.url} chars={len(text)} score={READY_MARKER in low} "
                f"sesgo={'sesgo' in low} status={response.status if response else None} "
                f"outcome={outcome} elapsed_ms={int((time.monotonic() - started) * 1000)} "
                f"redirected={page.url != req.url}",
                flush=True,
            )
            return {
                "text": text,
                "url": page.url,
                "status": response.status if response else None,
                "outcome": outcome,
            }
        finally:
            if page is not None:
                await page.close()
    finally:
        _lock.release()
