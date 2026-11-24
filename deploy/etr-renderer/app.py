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

Every render is bounded end to end — lock queue, browser setup, navigation, the
hydration poll and page teardown. Playwright awaits can park past their own
timeouts when a Chromium target dies, and holding the single browser lock through
that hang starved every later render (each one answered ``busy``) while
``/health`` kept saying ``ok``. When the deadline wins, the browser is abandoned,
its replacement is launched by the next request, and the wedge is visible in
``/health``.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
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
    # The terminal gives up on the instrument itself and says why. Nothing will
    # hydrate while the account cannot read Exness for that symbol, so waiting
    # out the deadline only turned a stated pause into a render timeout.
    "análisis en pausa",
    "no se pudo consultar el instrumento",
)
NOTICE_TICKS = 3
POLL_INTERVAL_MS = 2_000
# A second request waits this long for the single browser before being told the
# renderer is busy. It is bounded so a caller is never queued for a full render
# timeout; "busy" is a non-alerting outcome, not a failure.
RENDER_QUEUE_TIMEOUT_SECONDS = 20.0
# A render's own deadline is its navigation budget plus this much for launching
# the browser, opening a page and closing it. Without it the *holder* was the
# unbounded part: one parked Playwright await held the lock forever.
RENDER_SETUP_GRACE_SECONDS = 30.0
# Closing an abandoned browser is best effort and must not become the next
# unbounded await.
BROWSER_CLOSE_TIMEOUT_SECONDS = 10.0
# /health fails once the renderer has been unable to complete anything for this
# long, or after this many consecutive renders had to be abandoned.
BUSY_DEGRADED_SECONDS = 240.0
OVERRUN_DEGRADED_COUNT = 2
# A render that reaches its own deadline and returns no analysis — navigation
# timeout, upstream HTTP error, or a page that never hydrated — is a *failure*,
# never a completion. It used to take the success branch of ``render``:
# ``_note_completed()`` zeroed the counters and /health answered ``ok`` while
# every render failed. That was 103 of 242 renders in production logs, and the
# container stayed green throughout. Three in a row means a whole poll cycle
# produced nothing, which is the state the healthcheck exists to catch.
COMPLETED_OUTCOMES = frozenset({"ready", "notice"})
FAILURE_DEGRADED_COUNT = 3
# A caller-supplied timeout is a caller-supplied render budget. Without a
# ceiling a huge value makes the budget effectively unbounded, and one parked
# await owns the browser lock again. 180 s is the longest the agent asks for
# (its retry budget); anything below a second cannot survive a cold page.
MIN_RENDER_TIMEOUT_MS = 1_000
MAX_RENDER_TIMEOUT_MS = 180_000
RENDER_TIMEOUT_DEFAULT_MS = 75_000
# "Reconectando precios" shows while the app hydrates and can persist for half a
# minute on a cold page. Treating it as conclusive early returned a header with
# no charts, so a notice only counts after the page has had time to load.
MIN_NOTICE_SECONDS = 30
# Anything short of READY or a definitive notice waits for the full timeout. There
# used to be an early bail for pages without a chart "cierre" price, meant to spot
# assets with no market data. A missing price can also occur during hydration;
# a timeout does not establish that the terminal has no analysis.

app = FastAPI(title="etr-renderer")

_lock = asyncio.Lock()
_playwright: Playwright | None = None
_browser: Browser | None = None


class _Health:
    """Renderer health counters.

    Single uvicorn worker, single event loop, so plain module state is enough.
    Three independent failure shapes are tracked, because they have different
    causes and only one of them was reachable in the deployed topology:

    ``consecutive_busy`` / ``busy_since``
        requests answered ``outcome=busy`` since the last completion. Only
        reachable when a *second* client shares the sidecar, or when a wedged
        holder makes another caller wait out the queue timeout.
    ``consecutive_overruns``
        renders abandoned at their deadline, the signature of a wedged
        Playwright target.
    ``consecutive_failures``
        renders that ran to their deadline and returned no analysis
        (navigation timeout, upstream HTTP error, page never hydrated). This is
        the shape that actually happens with one sequential client, and it used
        to be counted as success.

    Only ``ready`` and ``notice`` are completions. A plain class, not a
    dataclass: this module is also loaded by tests through ``module_from_spec``
    without a ``sys.modules`` entry.
    """

    __slots__ = (
        "busy_since",
        "busy_since_wall",
        "consecutive_busy",
        "consecutive_failures",
        "consecutive_overruns",
        "detached",
        "in_flight",
    )

    def __init__(self) -> None:
        self.busy_since: float | None = None  # monotonic
        self.busy_since_wall: float | None = None  # epoch, reported by /health
        self.consecutive_busy = 0
        self.consecutive_overruns = 0
        self.consecutive_failures = 0
        self.in_flight = False
        # Renders we stopped waiting for but which are still parked inside a
        # Playwright await. They are not done, so ``render_in_flight`` must keep
        # telling the truth about them.
        self.detached = 0


_health = _Health()
_background: set[asyncio.Task[Any]] = set()


class RenderRequest(BaseModel):
    url: str
    cookie: str
    timeout_ms: int = RENDER_TIMEOUT_DEFAULT_MS


async def _browser_instance() -> Browser:
    global _playwright, _browser, _context, _context_owner
    if _browser is not None and _browser.is_connected():
        return _browser
    # The last browser died on its own — chromium OOM-killed, its process gone —
    # so nothing raised and nothing cleared the caches. A relaunch that kept the
    # old ``_context`` handed the new browser a context bound to the dead target:
    # every later render raised "Target page, context or browser has been
    # closed", and because that is a fast error rather than an overrun, /health
    # stayed green while the feed produced nothing. The stale driver is also
    # stopped here: relaunching it without stopping leaked one Playwright
    # instance per crash.
    stale_browser, stale_playwright = _browser, _playwright
    _browser = None
    _playwright = None
    _context = None
    _context_owner = None
    if stale_browser is not None or stale_playwright is not None:
        print("renderer reset: browser is no longer connected", flush=True)
        _detach(asyncio.ensure_future(_close_quietly(stale_browser, stale_playwright)))
    driver = await async_playwright().start()
    _playwright = driver
    _browser = await driver.chromium.launch(
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    return _browser


_context = None
# The browser the cached context belongs to. Identity, not liveness, is the
# cheap correctness key: a context is only reusable by the browser that created
# it, so any path that swaps the browser must not be able to hand back the old
# one.
_context_owner: Browser | None = None


async def _context_instance(browser: Browser):
    """One context per browser instance.

    A fresh context per render re-downloaded the entire Next.js bundle for every
    asset, which is what made a four-asset poll take minutes and time out before
    the analysis appeared. It is cached against the browser that created it, so
    a relaunched browser can never be served a context from its dead
    predecessor.
    """
    global _context, _context_owner
    if _context is None or _context_owner is not browser:
        _context = await browser.new_context(
            viewport={"width": 1360, "height": 1800}, locale="es-ES"
        )
        _context_owner = browser
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


def _health_payload() -> tuple[dict[str, object], bool]:
    now = time.monotonic()
    busy_for = None if _health.busy_since is None else now - _health.busy_since
    degraded = (
        (busy_for is not None and busy_for >= BUSY_DEGRADED_SECONDS)
        or _health.consecutive_overruns >= OVERRUN_DEGRADED_COUNT
        or _health.consecutive_failures >= FAILURE_DEGRADED_COUNT
    )
    payload: dict[str, object] = {
        "status": "degraded" if degraded else "ok",
        "degraded": degraded,
        # A render we stopped waiting for is still running: reporting False here
        # while a wedged task sits inside a Playwright await was the lie that
        # made a wedged renderer look idle.
        "render_in_flight": _health.in_flight or _health.detached > 0,
        "detached_renders": _health.detached,
        "busy_since": _health.busy_since_wall,
        "busy_for_seconds": None if busy_for is None else round(busy_for, 1),
        "consecutive_busy": _health.consecutive_busy,
        "consecutive_overruns": _health.consecutive_overruns,
        "consecutive_failures": _health.consecutive_failures,
    }
    return payload, degraded


@app.get("/health")
async def health() -> Any:
    """Liveness *and* progress.

    A renderer that answers every render `busy` is alive but useless; the compose
    healthcheck needs to fail on that, so degradation is a 503, not just a field.
    """
    payload, degraded = _health_payload()
    if degraded:
        return JSONResponse(payload, status_code=503)
    return payload


def _note_busy() -> None:
    if _health.busy_since is None:
        _health.busy_since = time.monotonic()
        _health.busy_since_wall = time.time()
    _health.consecutive_busy += 1


def _note_completed() -> None:
    _health.busy_since = None
    _health.busy_since_wall = None
    _health.consecutive_busy = 0
    _health.consecutive_overruns = 0
    _health.consecutive_failures = 0


def _note_failed(outcome: str) -> None:
    """A render that returned no analysis. Not a completion, whatever it cost.

    The request was served, so the renderer is not *starved*: the busy window
    closes. The overrun counter is left alone — an abandoned render is a
    stronger signal than a failed one, and only a real completion clears it.
    """
    _health.busy_since = None
    _health.busy_since_wall = None
    _health.consecutive_busy = 0
    _health.consecutive_failures += 1
    print(
        f"render outcome={outcome} counted=failure consecutive_failures="
        f"{_health.consecutive_failures}",
        flush=True,
    )


def _note_overrun() -> None:
    _health.consecutive_overruns += 1


def _consume(task: asyncio.Task[Any]) -> None:
    with suppress(BaseException):
        task.exception()


def _release_detached(_task: asyncio.Task[Any]) -> None:
    _health.detached = max(0, _health.detached - 1)


def _detach(task: asyncio.Task[Any], *, render: bool = False) -> None:
    """Keep a reference to a task we stop waiting for and swallow its outcome.

    ``render=True`` records that a *render* is still running without a waiter,
    so ``/health`` cannot report an idle renderer while a wedged render holds a
    Playwright await.
    """
    if render:
        _health.detached += 1
        task.add_done_callback(_release_detached)
    _background.add(task)
    task.add_done_callback(_consume)
    task.add_done_callback(_background.discard)


async def _close_quietly(browser: Browser | None, playwright: Playwright | None) -> None:
    if browser is not None:
        with suppress(Exception):
            await asyncio.wait_for(browser.close(), BROWSER_CLOSE_TIMEOUT_SECONDS)
    if playwright is not None:
        with suppress(Exception):
            await asyncio.wait_for(playwright.stop(), BROWSER_CLOSE_TIMEOUT_SECONDS)


def _discard_browser(reason: str) -> None:
    """Forget the shared browser so the next render starts from a clean process.

    The wedged render may still be parked inside a Playwright await, so closing
    from here could hang exactly the same way. Cleanup runs detached and bounded;
    anything still alive is left to process exit, and the next render launches a
    browser of its own.
    """
    global _browser, _context, _context_owner, _playwright
    browser, playwright = _browser, _playwright
    _browser = None
    _context = None
    _context_owner = None
    _playwright = None
    if browser is None and playwright is None:
        return
    print(f"renderer reset: {reason}", flush=True)
    _detach(asyncio.ensure_future(_close_quietly(browser, playwright)))


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


def _render_budget_seconds(req: RenderRequest) -> float:
    return req.timeout_ms / 1000 + RENDER_SETUP_GRACE_SECONDS


def _bounded_timeout_ms(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return RENDER_TIMEOUT_DEFAULT_MS
    return max(MIN_RENDER_TIMEOUT_MS, min(value, MAX_RENDER_TIMEOUT_MS))


@app.post("/render")
async def render(req: RenderRequest) -> dict[str, object]:
    # The caller's timeout is the render's whole budget. Clamp it before it
    # reaches the deadline maths: an unbounded value makes the budget unbounded,
    # and then a single parked await owns the lock for as long as the caller
    # likes — the exact starvation this module exists to prevent.
    clamped = _bounded_timeout_ms(req.timeout_ms)
    if clamped != req.timeout_ms:
        print(
            f"render {req.url} timeout_ms={req.timeout_ms} clamped={clamped}",
            flush=True,
        )
        req.timeout_ms = clamped

    if not await _acquire_lock_bounded(_lock, RENDER_QUEUE_TIMEOUT_SECONDS):
        # A busy renderer is not a failure: report it so the caller can count the
        # asset as unavailable instead of raising an error alert. Repetition is
        # what makes it a failure, and /health is where that shows.
        _note_busy()
        print(
            f"render {req.url} outcome=busy elapsed_ms=0 queue_timeout_s="
            f"{RENDER_QUEUE_TIMEOUT_SECONDS:g} consecutive_busy="
            f"{_health.consecutive_busy}",
            flush=True,
        )
        return {"text": "", "url": req.url, "status": None, "outcome": "busy"}

    _health.in_flight = True
    work = asyncio.ensure_future(_render_holding_lock(req))
    try:
        done, _ = await asyncio.wait({work}, timeout=_render_budget_seconds(req))
        if work in done:
            # result() re-raises whatever the render raised; the lock is released
            # by the finally block either way.
            payload = work.result()
            # Only a page that gave us analysis is a completion. A navigation
            # timeout, an upstream HTTP error or a page that never hydrated all
            # return a payload too — counting those as success is what kept
            # /health green while every render failed.
            outcome = str(payload.get("outcome") or "")
            if outcome in COMPLETED_OUTCOMES:
                _note_completed()
            else:
                _note_failed(outcome or "unknown")
            return payload
        # The page or the browser stopped answering. Stop waiting, stop the work,
        # and give the lock back: a hung await must never own it past the budget.
        _note_overrun()
        work.cancel()
        _detach(work, render=True)
        _discard_browser(f"render exceeded {_render_budget_seconds(req):g}s budget")
        print(
            f"render {req.url} chars=0 score=False status=None outcome=timeout "
            f"elapsed_ms={int(_render_budget_seconds(req) * 1000)} "
            f"deadline=exceeded consecutive_overruns={_health.consecutive_overruns}",
            flush=True,
        )
        return {"text": "", "url": req.url, "status": None, "outcome": "timeout"}
    except asyncio.CancelledError:
        # The caller went away (client disconnect). Same rule: never leave the
        # render running against a browser the next request will reuse. The
        # render is being torn down rather than wedged, so it is not counted as
        # a detached in-flight render.
        work.cancel()
        _detach(work)
        _discard_browser("render cancelled by caller")
        raise
    finally:
        _health.in_flight = False
        _lock.release()


async def _render_holding_lock(req: RenderRequest) -> dict[str, object]:
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
            # Bounded: a page whose target died can park inside close() too, and
            # the request budget should not be spent by teardown.
            with suppress(Exception):
                await asyncio.wait_for(page.close(), BROWSER_CLOSE_TIMEOUT_SECONDS)
