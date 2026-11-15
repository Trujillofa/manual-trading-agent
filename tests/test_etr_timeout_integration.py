"""Exercise timeout handling through the poller using in-memory I/O only."""
from types import SimpleNamespace

import httpx
import pytest

from src.etr import service
from src.etr.client import EtrClient


@pytest.mark.asyncio
async def test_timeout_retry_audit_and_global_alert_cooldown(monkeypatch):
    events, calls, messages = [], [], []
    meta, state = {}, {}
    client = EtrClient("user@example.com", "dummy", renderer_url="http://renderer")
    original_client = httpx.AsyncClient

    async def session(*, force_login=False):
        return {"access_token": "dummy"}

    def respond(request):
        calls.append(request.read())
        return httpx.Response(200, json={"text": "partial", "status": 200, "outcome": "timeout"})

    monkeypatch.setattr(client, "ensure_session", session)
    monkeypatch.setattr("src.etr.client.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(service, "is_etr_enabled", lambda config: True)
    monkeypatch.setattr(service, "build_client", lambda config: client)
    monkeypatch.setattr(service, "load_etr_state", lambda: state)
    monkeypatch.setattr(service, "load_global_meta", lambda: meta)
    monkeypatch.setattr(service, "append_etr_audit", events.append)
    monkeypatch.setattr(service, "save_state_with_meta", lambda *_: None)
    monkeypatch.setattr(service, "process_shadow_for_report", lambda *a, **k: None)
    now = [100000.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])
    config = SimpleNamespace(assets=["nasdaq"], telegram_alerts=True, error_alert_cooldown_minutes=360)

    class Notifier:
        enabled = True

        async def send(self, message):
            messages.append(message)
            return True

    settings = SimpleNamespace(etr=config)
    notifier = Notifier()
    first = await service.poll_and_notify(settings, notifier, force=True)
    second = await service.poll_and_notify(settings, notifier, force=True)
    await service._maybe_error_alert(config, meta, notifier, "btc", "different failure", True)
    assert (first.error_count, second.error_count) == (1, 1)
    assert len(calls) == 4 and [e["event"] for e in events] == ["error", "error"]
    assert len(messages) == 1 and "nasdaq" in messages[0]
    now[0] += 360 * 60
    await service._maybe_error_alert(config, meta, notifier, "btc", "different failure", True)
    assert len(messages) == 2 and "btc" in messages[1]
    assert state == {}
