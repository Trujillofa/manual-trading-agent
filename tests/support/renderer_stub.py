"""Load ``deploy/etr-renderer/app.py`` with stubbed FastAPI and Playwright.

The renderer image installs both; the agent's dev environment does not, and the
module is never imported by the agent itself. The stubs mirror the few framework
attributes the module touches, so the real renderer code (lock, deadlines,
health bookkeeping, navigation loop) runs unmodified in offline tests.

Each call executes a fresh module object, so module-level state such as the
browser lock and health counters starts clean.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "deploy" / "etr-renderer" / "app.py"
_MODULES = ("fastapi", "fastapi.responses", "playwright", "playwright.async_api")


class JSONResponse:
    """Minimal stand-in for ``fastapi.responses.JSONResponse``."""

    def __init__(self, content: Any, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code


class StubFastAPI:
    def __init__(self, **kwargs: Any) -> None:
        self.title = kwargs.get("title")
        self.routes: dict[tuple[str, str], Any] = {}

    def _register(self, method: str, path: str):
        def decorator(fn: Any) -> Any:
            self.routes[(method, path)] = fn
            return fn

        return decorator

    def get(self, path: str):
        return self._register("GET", path)

    def post(self, path: str):
        return self._register("POST", path)


def _stub_modules() -> dict[str, types.ModuleType]:
    fastapi = types.ModuleType("fastapi")
    fastapi.FastAPI = StubFastAPI
    responses = types.ModuleType("fastapi.responses")
    responses.JSONResponse = JSONResponse
    fastapi.responses = responses

    playwright = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    api.Browser = object
    api.Playwright = object
    api.TimeoutError = TimeoutError
    api.async_playwright = lambda: None
    playwright.async_api = api

    return {
        "fastapi": fastapi,
        "fastapi.responses": responses,
        "playwright": playwright,
        "playwright.async_api": api,
    }


def load(module_name: str = "etr_renderer_under_test"):
    saved = {name: sys.modules.get(name) for name in _MODULES}
    sys.modules.update(_stub_modules())
    try:
        spec = importlib.util.spec_from_file_location(module_name, RENDERER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module
