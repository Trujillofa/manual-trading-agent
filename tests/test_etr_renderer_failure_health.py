"""A render that returns no analysis is a failure, and /health must say so.

The renderer used to take the success branch of ``render`` for every payload it
produced, including navigation timeouts, upstream HTTP errors and pages that
never hydrated. ``_note_completed()`` zeroed the counters, so /health answered
``ok`` with ``consecutive_overruns: 0`` while every single render failed — 103 of
242 renders in the production logs were exactly that class, and nothing in the
container's healthcheck ever noticed.

The second half is lifecycle: a browser killed from under the renderer (OOM, a
crashed process) was relaunched, but the cached context was not, so every later
render raised "Target page, context or browser has been closed" and, being a
fast error rather than an overrun, stayed invisible to /health too.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from tests.support import renderer_stub

URL = "https://etracademy.com/analisis/btc"


def _load():
    return renderer_stub.load("etr_renderer_failure_health")


READY_TEXT = "Context score 62 · sesgo Alcista · escenarios"*3


def _page(*, text: str = READY_TEXT, status: int = 200, raise_timeout: bool = False):
    class Page:
        url = URL

        async def goto(self, *args, **kwargs):
            if raise_timeout:
                raise TimeoutError("navigation timeout")
            return SimpleNamespace(status=status)

        async def wait_for_selector(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, *args, **kwargs):
            return None

        async def inner_text(self, selector):
            return text

        async def close(self):
            return None

    return Page()


def _wire(renderer, pages):
    """Serve ``pages`` in order, one per ``new_page()``."""
    served = list(pages)

    class Context:
        def __init__(self):
            self.pages = []

        async def add_cookies(self, cookies):
            return None

        async def new_page(self):
            page = served.pop(0) if served else _page()
            self.pages.append(page)
            return page

        async def close(self):
            return None

    contexts = []

    async def browser():
        return object()

    async def context(_browser):
        built = Context()
        contexts.append(built)
        return built

    renderer._browser_instance = browser
    renderer._context_instance = context
    return contexts


async def _render(renderer, *, timeout_ms: int = 75, url: str = URL):
    return await renderer.render(renderer.RenderRequest(url=url, cookie="", timeout_ms=timeout_ms))


@pytest.mark.asyncio
async def test_repeated_navigation_timeouts_degrade_health() -> None:
    renderer = _load()
    _wire(renderer, [_page(raise_timeout=True) for _ in range(6)])

    for _ in range(renderer.FAILURE_DEGRADED_COUNT):
        payload = await _render(renderer)
        assert payload["outcome"] == "timeout"

    payload, degraded = renderer._health_payload()
    assert degraded is True
    assert payload["status"] == "degraded"
    assert payload["consecutive_failures"] == renderer.FAILURE_DEGRADED_COUNT
    # Still a completion-shaped zero: the failure counter, not the overrun one,
    # is what caught this.
    assert payload["consecutive_overruns"] == 0


@pytest.mark.asyncio
async def test_upstream_http_error_counts_as_a_failure() -> None:
    renderer = _load()
    _wire(renderer, [_page(status=503), _page(status=200)])

    assert (await _render(renderer))["outcome"] == "http_error"
    assert renderer._health.consecutive_failures == 1

    payload = await _render(renderer)
    assert payload["outcome"] == "ready"
    assert renderer._health.consecutive_failures == 0


@pytest.mark.asyncio
async def test_short_page_counts_as_a_failure_not_a_completion() -> None:
    renderer = _load()
    _wire(renderer, [_page(text="Cargando…"), _page(text="Cargando…")])

    for _ in range(2):
        payload = await _render(renderer)
        assert payload["outcome"] == "timeout"

    assert renderer._health.consecutive_failures == 2
    assert renderer._health.busy_since is None


@pytest.mark.asyncio
async def test_a_single_completion_clears_the_failure_counter() -> None:
    renderer = _load()
    _wire(renderer, [_page(raise_timeout=True), _page(status=500), _page(), _page(status=500)])

    await _render(renderer)
    await _render(renderer)
    assert renderer._health.consecutive_failures == 2

    assert (await _render(renderer))["outcome"] == "ready"
    assert renderer._health.consecutive_failures == 0

    await _render(renderer)
    assert renderer._health.consecutive_failures == 1, "counter must not carry across a completion"


@pytest.mark.asyncio
async def test_health_stays_green_below_the_failure_threshold() -> None:
    renderer = _load()
    _wire(renderer, [_page(raise_timeout=True) for _ in range(2)])

    for _ in range(renderer.FAILURE_DEGRADED_COUNT - 1):
        await _render(renderer)

    payload, degraded = renderer._health_payload()
    assert degraded is False
    assert payload["status"] == "ok"
    assert payload["consecutive_failures"] == renderer.FAILURE_DEGRADED_COUNT - 1


class _FakeBrowser:
    def __init__(self, contexts: list) -> None:
        self.contexts = contexts
        self.connected = True
        self.closed = False
        self._alive = True

    def is_connected(self) -> bool:
        return self.connected

    async def new_context(self, **kwargs):
        context = SimpleNamespace(browser=self, pages=[], closed=False)

        async def new_page():
            page = _page()
            context.pages.append(page)
            return page

        async def add_cookies(cookies):
            return None

        async def close():
            context.closed = True

        context.new_page = new_page
        context.add_cookies = add_cookies
        context.close = close
        self.contexts.append(context)
        return context

    async def close(self):
        self.connected = False
        self.closed = True


class _FakeDriver:
    def __init__(self, browsers: list) -> None:
        self.browsers = browsers
        self.launches = 0
        self.stops = 0

    async def start(self):
        driver = self

        class Chromium:
            async def launch(self, **kwargs):
                browser = _FakeBrowser([])
                driver.browsers.append(browser)
                driver.launches += 1
                return browser

        async def stop():
            driver.stops += 1

        return SimpleNamespace(chromium=Chromium(), stop=stop)


@pytest.mark.asyncio
async def test_dead_browser_is_replaced_without_reusing_its_context() -> None:
    renderer = _load()
    browsers: list[_FakeBrowser] = []
    driver = _FakeDriver(browsers)
    renderer.async_playwright = lambda: driver

    first = await _render(renderer)
    assert first["outcome"] == "ready"
    assert len(browsers) == 1
    first_browser = browsers[0]
    assert len(first_browser.contexts) == 1

    # chromium is killed from under the renderer: no exception, is_connected()
    # simply starts returning False.
    first_browser.connected = False

    second = await _render(renderer)
    assert second["outcome"] == "ready", "the relaunched browser must serve renders"
    assert len(browsers) == 2, "a disconnected browser must be replaced"
    assert len(browsers[1].contexts) == 1
    # The stale driver is stopped and the renderer is not holding two of them.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert driver.stops == 1
    assert driver.launches - driver.stops == 1

    payload, degraded = renderer._health_payload()
    assert degraded is False
    assert payload["consecutive_failures"] == 0
    assert payload["consecutive_overruns"] == 0


@pytest.mark.asyncio
async def test_context_is_keyed_on_the_browser_that_created_it() -> None:
    renderer = _load()
    browsers: list[_FakeBrowser] = []
    driver = _FakeDriver(browsers)
    renderer.async_playwright = lambda: driver

    await _render(renderer)
    # Swap the browser without going through _browser_instance: the cached
    # context belongs to the old one and must not be handed back.
    renderer._browser = _FakeBrowser([])
    payload = await _render(renderer)
    assert payload["outcome"] == "ready"
    assert renderer._context_owner is renderer._browser
    assert renderer._context.browser is renderer._browser


@pytest.mark.asyncio
async def test_detached_wedged_render_is_reported_as_in_flight() -> None:
    renderer = _load()
    # Keep the deadline short: the point is the accounting, not the wait.
    renderer.RENDER_SETUP_GRACE_SECONDS = 0.05
    _wire(renderer, [_page()])
    release = asyncio.Event()

    class Wedged:
        url = URL

        async def goto(self, *args, **kwargs):
            # A Playwright await parked in a sync driver call does not honour
            # cancellation; this is the shape that makes a render detach.
            while not release.is_set():
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(release.wait(), 0.05)
            return SimpleNamespace(status=200)

        async def wait_for_selector(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, *args, **kwargs):
            return None

        async def inner_text(self, selector):
            return READY_TEXT

        async def close(self):
            return None

    _wire(renderer, [Wedged()])

    payload = await _render(renderer, timeout_ms=renderer.MIN_RENDER_TIMEOUT_MS)
    assert payload["outcome"] == "timeout"

    health, _ = renderer._health_payload()
    assert health["detached_renders"] >= 1
    assert health["render_in_flight"] is True, "a wedged render is still running"

    release.set()
    for _ in range(20):
        await asyncio.sleep(0.01)
        if renderer._health.detached == 0:
            break
    assert renderer._health.detached == 0, "the counter must drain when the work ends"


@pytest.mark.asyncio
async def test_caller_timeout_is_clamped_to_the_renderer_maximum() -> None:
    renderer = _load()
    _wire(renderer, [_page()])
    request = renderer.RenderRequest(url=URL, cookie="", timeout_ms=10_000_000)

    payload = await renderer.render(request)
    assert payload["outcome"] == "ready"
    assert request.timeout_ms == renderer.MAX_RENDER_TIMEOUT_MS
    assert renderer._bounded_timeout_ms(10_000_000) == renderer.MAX_RENDER_TIMEOUT_MS
    assert renderer._bounded_timeout_ms(1) == renderer.MIN_RENDER_TIMEOUT_MS
    assert renderer._bounded_timeout_ms("nonsense") == renderer.RENDER_TIMEOUT_DEFAULT_MS
