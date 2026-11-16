"""A renderer that parks inside a Playwright await must not wedged the feed.

The defect this guards: ``render`` bounded only the *waiter*. The holder's awaits
(browser launch, ``new_page``, ``page.close``) had no deadline, so one hung page
kept the process-local browser lock forever. Every later render then answered
``outcome=busy``, the poller counted them all as unavailable, and ``/health``
kept reporting ``ok`` — a silent starvation of the whole ETR feed.

These run the real renderer module offline: FastAPI and Playwright are stubbed
(see ``tests.support.renderer_stub``), the lock, deadline, cancellation and
health bookkeeping are production code.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from tests.support import renderer_stub

URL = "https://etracademy.com/analisis/btc"


class _Page:
    def __init__(self, hang: list[bool], parked: asyncio.Event) -> None:
        self.hang = hang
        self.parked = parked
        self.closed = False
        self.url = URL

    async def goto(self, *args: Any, **kwargs: Any) -> Any:
        if self.hang[0]:
            self.parked.set()
            await asyncio.Event().wait()  # a dead Chromium target never answers
        return SimpleNamespace(status=200)

    async def inner_text(self, selector: str) -> str:
        return "Context score sesgo alcista"

    async def wait_for_timeout(self, ms: int) -> None:
        await asyncio.sleep(ms / 1000)

    async def close(self) -> None:
        self.closed = True


class _Context:
    def __init__(self, hang: list[bool], parked: asyncio.Event) -> None:
        self.hang = hang
        self.parked = parked

    async def add_cookies(self, cookies: Any) -> None:
        pass

    async def new_page(self) -> _Page:
        return _Page(self.hang, self.parked)


def _renderer(hang: list[bool], parked: asyncio.Event):
    renderer = renderer_stub.load()
    renderer.RENDER_QUEUE_TIMEOUT_SECONDS = 0.05
    renderer.RENDER_SETUP_GRACE_SECONDS = 0.15

    async def browser() -> object:
        return object()

    async def context(_browser: object) -> _Context:
        return _Context(hang, parked)

    renderer._browser_instance = browser
    renderer._context_instance = context
    renderer.test_request = lambda timeout_ms=10: renderer.RenderRequest(
        url=URL, cookie="", timeout_ms=timeout_ms
    )
    return renderer


@pytest.mark.asyncio
async def test_wedged_holder_is_bounded_and_frees_the_lock() -> None:
    parked = asyncio.Event()
    hang = [True]
    renderer = _renderer(hang, parked)

    first = asyncio.create_task(renderer.render(renderer.test_request()))
    await asyncio.wait_for(parked.wait(), 1)

    # While the holder is parked, a second request must get the bounded busy
    # answer instead of queueing for a full render timeout.
    started = asyncio.get_running_loop().time()
    busy = await renderer.render(renderer.test_request())
    assert busy == {"text": "", "url": URL, "status": None, "outcome": "busy"}
    assert asyncio.get_running_loop().time() - started < 1

    # The holder itself is now bounded: it returns, and the lock is free.
    bounded = await asyncio.wait_for(first, 3)
    assert bounded["outcome"] == "timeout"
    assert renderer._lock.locked() is False

    health, _ = renderer._health_payload()
    assert health["consecutive_overruns"] == 1
    assert health["consecutive_busy"] >= 1

    # And the next render is served, not starved: the wedge is not sticky.
    hang[0] = False
    healthy = await asyncio.wait_for(renderer.render(renderer.test_request()), 3)
    assert healthy["outcome"] == "ready"
    assert renderer._lock.locked() is False
    assert renderer._health_payload()[0]["consecutive_overruns"] == 0


@pytest.mark.asyncio
async def test_cancelled_caller_releases_the_lock() -> None:
    parked = asyncio.Event()
    renderer = _renderer([True], parked)

    task = asyncio.create_task(renderer.render(renderer.test_request()))
    await asyncio.wait_for(parked.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert renderer._lock.locked() is False
    assert renderer._health_payload()[0]["render_in_flight"] is False


@pytest.mark.asyncio
async def test_health_exposes_busy_since_and_consecutive_busy() -> None:
    renderer = _renderer([False], asyncio.Event())

    await renderer._lock.acquire()
    try:
        for _ in range(3):
            assert (await renderer.render(renderer.test_request()))["outcome"] == "busy"
        payload, degraded = renderer._health_payload()
    finally:
        renderer._lock.release()

    assert degraded is False
    assert payload["status"] == "ok"
    assert payload["consecutive_busy"] == 3
    assert isinstance(payload["busy_since"], float)
    assert payload["busy_for_seconds"] is not None
    assert payload["busy_for_seconds"] >= 0


@pytest.mark.asyncio
async def test_health_returns_503_after_repeated_overruns() -> None:
    renderer = _renderer([True], asyncio.Event())
    renderer.RENDER_SETUP_GRACE_SECONDS = 0.05

    for _ in range(renderer.OVERRUN_DEGRADED_COUNT):
        assert (await renderer.render(renderer.test_request()))["outcome"] == "timeout"

    response = await renderer.health()
    assert isinstance(response, renderer_stub.JSONResponse)
    assert response.status_code == 503
    assert response.content["status"] == "degraded"
    assert response.content["degraded"] is True
    assert response.content["consecutive_overruns"] == renderer.OVERRUN_DEGRADED_COUNT


@pytest.mark.asyncio
async def test_health_ok_path_stays_a_plain_mapping() -> None:
    renderer = _renderer([False], asyncio.Event())
    response = await renderer.health()
    assert response["status"] == "ok"
    assert response["degraded"] is False
    assert response["consecutive_busy"] == 0
