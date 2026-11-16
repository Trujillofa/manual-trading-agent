"""A busy renderer is a scheduling outcome, never an error alert."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.config.settings import EtrConfig
from src.etr import service
from src.etr.client import EtrClient, EtrRendererBusyError
from src.etr.parser import EtrUnavailableError
from src.etr.state import load_etr_state
from tests.support import renderer_stub


def _load_renderer(monkeypatch):
    # monkeypatch kept in the signature: the stub installs and restores its own
    # sys.modules entries, nothing else needs patching here.
    return renderer_stub.load("etr_renderer_busy")


@pytest.mark.asyncio
async def test_overlapping_render_gets_bounded_busy_outcome(monkeypatch) -> None:
    renderer = _load_renderer(monkeypatch)
    monkeypatch.setattr(renderer, "RENDER_QUEUE_TIMEOUT_SECONDS", 0.05)
    entered, release = asyncio.Event(), asyncio.Event()
    pages = []

    class Page:
        url = "https://etracademy.com/analisis/btc"

        async def goto(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(status=200)

        async def inner_text(self, selector):
            return "Context score"

        async def close(self):
            pass

    class Context:
        async def add_cookies(self, cookies):
            pass

        async def new_page(self):
            page = Page()
            pages.append(page)
            return page

    async def browser():
        return object()

    async def context(_):
        return Context()

    monkeypatch.setattr(renderer, "_browser_instance", browser)
    monkeypatch.setattr(renderer, "_context_instance", context)
    request = renderer.RenderRequest(url=Page.url, cookie="", timeout_ms=75)

    first = asyncio.create_task(renderer.render(request))
    await asyncio.wait_for(entered.wait(), 1)

    started = asyncio.get_running_loop().time()
    busy = await renderer.render(request)
    waited = asyncio.get_running_loop().time() - started

    assert busy == {
        "text": "",
        "url": Page.url,
        "status": None,
        "outcome": "busy",
    }
    assert waited < 1
    assert len(pages) == 1
    assert renderer._lock.locked() is True  # the first render still owns it

    release.set()
    assert (await asyncio.wait_for(first, 2))["outcome"] == "ready"
    assert renderer._lock.locked() is False


@pytest.mark.asyncio
async def test_cancelled_busy_wait_does_not_release_someone_elses_lock(monkeypatch) -> None:
    renderer = _load_renderer(monkeypatch)
    monkeypatch.setattr(renderer, "RENDER_QUEUE_TIMEOUT_SECONDS", 5)
    await renderer._lock.acquire()
    try:
        waiter = asyncio.create_task(renderer.render(renderer.RenderRequest(url="u", cookie="")))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        # A waiter cancelled before acquiring must not release the holder's lock.
        assert renderer._lock.locked() is True
    finally:
        renderer._lock.release()


def _render_client(monkeypatch, handler) -> EtrClient:
    client = EtrClient("user@example.com", "secret", renderer_url="http://renderer:8000")

    async def session(*, force_login=False):
        return {"access_token": "offline"}

    monkeypatch.setattr(client, "ensure_session", session)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "src.etr.client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    return client


@pytest.mark.asyncio
async def test_client_maps_busy_payload_to_unavailable_without_retry(monkeypatch) -> None:
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(
            200,
            json={
                "text": "",
                "url": "https://etracademy.com/analisis/btc",
                "status": None,
                "outcome": "busy",
            },
        )

    client = _render_client(monkeypatch, handler)
    with pytest.raises(EtrRendererBusyError, match="busy"):
        await client.fetch_report("btc")
    assert len(calls) == 1  # busy is not the timeout-retry path


@pytest.mark.asyncio
async def test_client_maps_busy_503_body_to_unavailable(monkeypatch) -> None:
    # A proxy/ingress can forward the renderer's own 503; the body still names
    # the scheduling outcome, so it stays non-alerting.
    def handler(request):
        return httpx.Response(
            503,
            json={"text": "", "url": "https://etracademy.com/analisis/btc", "outcome": "busy"},
        )

    client = _render_client(monkeypatch, handler)
    with pytest.raises(EtrRendererBusyError, match="busy"):
        await client.fetch_report("btc")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="busy"),
        httpx.Response(503, json={"detail": "service unavailable"}),
        httpx.Response(503, text="<html>502 Bad Gateway</html>"),
    ],
)
async def test_client_plain_503_is_an_error_not_a_busy_skip(monkeypatch, response) -> None:
    # A dead renderer, a proxy error page or a readiness probe also answer 503.
    # Treating those as scheduling made a renderer outage completely silent.
    def handler(request):
        return response

    client = _render_client(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="HTTP 503") as caught:
        await client.fetch_report("btc")
    assert not isinstance(caught.value, EtrUnavailableError)


@pytest.mark.asyncio
async def test_client_still_fails_on_upstream_etr_http_status(monkeypatch) -> None:
    # etracademy answering 503 inside a successful render is still an upstream
    # failure, not a busy sidecar.
    def handler(request):
        return httpx.Response(
            200,
            json={"text": "partial", "status": 503, "outcome": "timeout"},
        )

    client = _render_client(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="ETR upstream HTTP 503"):
        await client.fetch_report("btc")


class _Notifier:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> bool:
        self.messages.append(message)
        return True


class _BusyClient:
    async def fetch_report(self, asset: str):
        raise EtrRendererBusyError(f"ETR renderer busy for {asset}")


@pytest.mark.asyncio
async def test_busy_renderer_does_not_alert_end_to_end(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("MANUAL_TRADING_AGENT_LOG_DIR", str(logs))
    monkeypatch.setattr(service, "build_client", lambda config: _BusyClient())
    notifier = _Notifier()
    settings = SimpleNamespace(
        etr=EtrConfig(
            assets=["btc"],
            telegram_alerts=True,
            login="busy@example.com",
            password="offline",
        )
    )

    summary = await service.poll_and_notify(settings, notifier, force=True)

    assert summary.error_count == 0
    assert summary.notified_count == 0
    assert notifier.messages == []
    assert summary.results[0].error is None
    assert load_etr_state(logs / "etr_state.json") == {}

    audit = [
        json.loads(line)
        for line in (logs / "etr_audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in audit] == ["unavailable"]
    assert "busy" in audit[0]["reason"]
