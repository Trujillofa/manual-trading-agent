"""Stateless renderer for ETR analysis pages.

The page is a client-rendered app. The server returns a shell with no analysis in
it: bias, context score and scenarios are computed in the browser from the
account's candles, and only exist in the DOM after that engine runs. A plain HTTP
GET can never see them, at any user agent.

This service runs the browser and returns the page's visible text. It holds no
credentials and keeps no session: the caller passes its own Supabase cookie, so
authentication stays the agent's responsibility and this container is disposable.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright

# Wait for the analysis itself. Do NOT wait for the "reconnecting prices" or
# "escenarios se mostrarán" notices: both appear transiently while the page is
# still hydrating, and matching them returned a half-rendered header instead of
# the analysis. When the terminal genuinely has nothing, the wait times out and
# we return the settled page, which carries the notice the caller needs.
READY_JS = """
() => {
  const t = document.body.innerText.toLowerCase();
  return t.includes('context score') || t.includes('sesgo');
}
"""

app = FastAPI(title="etr-renderer")


class RenderRequest(BaseModel):
    url: str
    cookie: str
    timeout_ms: int = 90_000


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/render")
async def render(req: RenderRequest) -> dict[str, object]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            context = await browser.new_context(
                viewport={"width": 1360, "height": 1800}, locale="es-ES"
            )
            for part in req.cookie.split(";"):
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
            page = await context.new_page()
            response = await page.goto(
                req.url, wait_until="domcontentloaded", timeout=req.timeout_ms
            )
            try:
                await page.wait_for_function(READY_JS, timeout=req.timeout_ms)
            except Exception:  # noqa: BLE001 - no analysis: return the notice
                await page.wait_for_timeout(3_000)
            await page.wait_for_timeout(300)
            text = await page.inner_text("body")
            low = text.lower()
            print(
                f"render {req.url} chars={len(text)} score={'context score' in low} "
                f"sesgo={'sesgo' in low} unavailable={'escenarios se mostrar' in low or 'reconectando' in low}",
                flush=True,
            )
            return {
                "text": text,
                "url": page.url,
                "status": response.status if response else None,
            }
        finally:
            await browser.close()
