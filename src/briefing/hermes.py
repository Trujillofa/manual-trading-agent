"""Invoke the existing Hermes Agent for a per-symbol NY-session plan.

Uses the Hetzner/local Hermes CLI (``hermes chat -q``) or an optional
OpenAI-compatible HTTP endpoint. Never starts a new LLM stack.
Failure is non-fatal: the briefing still sends with an explicit unavailable line.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from src.briefing.models import NyPlan, PreNyBriefing

if TYPE_CHECKING:
    from src.config.settings import BriefingHermesConfig

logger = logging.getLogger(__name__)

HermesComplete = Callable[[str], Awaitable[str]]

_RECOMMENDATIONS = {
    "wait": "esperar",
    "esperar": "esperar",
    "buy_pullback": "compra en retroceso",
    "buy-pullback": "compra en retroceso",
    "compra": "compra en retroceso",
    "compra en retroceso": "compra en retroceso",
    "sell_rally": "venta en rally",
    "sell-rally": "venta en rally",
    "venta": "venta en rally",
    "venta en rally": "venta en rally",
    "stand_aside": "no operar",
    "stand-aside": "no operar",
    "standaside": "no operar",
    "no operar": "no operar",
    "aside": "no operar",
}

_CONFIDENCE = {
    "low": "baja",
    "baja": "baja",
    "medium": "media",
    "med": "media",
    "media": "media",
    "high": "alta",
    "alta": "alta",
}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class HermesError(Exception):
    """Hermes invoke or parse failure."""


def unavailable_plan(reason: str) -> NyPlan:
    text = reason.strip() or "error desconocido"
    return NyPlan(available=False, unavailable_reason=text[:180])


def _norm_rec(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "no operar"
    compact = raw.replace(" ", "_").replace("-", "_")
    if compact in _RECOMMENDATIONS:
        return _RECOMMENDATIONS[compact]
    if compact.replace("__", "_") in _RECOMMENDATIONS:
        return _RECOMMENDATIONS[compact.replace("__", "_")]
    if raw in _RECOMMENDATIONS:
        return _RECOMMENDATIONS[raw]
    head = raw.split()[0].strip(" -:,.")
    if head in _RECOMMENDATIONS:
        return _RECOMMENDATIONS[head]
    if "no operar" in raw or "stand_aside" in raw or "stand-aside" in raw:
        return "no operar"
    if head in {"esperar", "wait"} or raw.startswith("esperar"):
        return "esperar"
    if any(token in raw for token in ("corto", "sell", "venta")):
        return "venta en rally"
    if any(token in raw for token in ("largo", "buy", "compra")):
        return "compra en retroceso"
    return "no operar"


def _norm_conf(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        if 0 <= score <= 1:
            score *= 100
        if score < 40:
            return "baja"
        if score < 70:
            return "media"
        return "alta"
    key = str(value or "").strip().lower()
    if key.isdigit():
        return _norm_conf(int(key))
    return _CONFIDENCE.get(key, key or "baja")


def _levels(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,/;|]", value) if part.strip()]
        return tuple(parts[:4])
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return tuple(out[:4])
    text = str(value).strip()
    return (text,) if text else ()


def _plan_from_mapping(raw: dict[str, object]) -> NyPlan:
    honesty = str(raw.get("honesty") or raw.get("caveat") or "").strip()
    return NyPlan(
        available=True,
        htf_trend=str(raw.get("htf_trend") or raw.get("trend") or "indefinido").strip()[:80],
        htf_basis=str(raw.get("htf_basis") or raw.get("htf_note") or "").strip()[:120],
        support=_levels(raw.get("support") or raw.get("soportes")),
        resistance=_levels(raw.get("resistance") or raw.get("resistencias")),
        recommendation=_norm_rec(raw.get("recommendation") or raw.get("plan")),
        why=str(raw.get("why") or raw.get("razon") or "").strip()[:180],
        invalidation=str(raw.get("invalidation") or raw.get("invalida") or "").strip()[:140],
        confidence=_norm_conf(raw.get("confidence") or raw.get("confianza")),
        honesty=honesty[:140],
    )


def extract_json(text: str) -> object:
    """Parse JSON from a model reply; tolerate fences and trailing chatter."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def parse_plans(text: str, instrument_ids: list[str]) -> dict[str, NyPlan]:
    """Map Hermes output to per-symbol plans. Bad JSON → fallback plan."""
    aliases = {item: item for item in instrument_ids}
    aliases.update(
        {
            "xau/usd": "XAU/USD",
            "xauusd": "XAU/USD",
            "gold": "XAU/USD",
            "oro": "XAU/USD",
            "btc/usd": "BTC/USD",
            "btcusd": "BTC/USD",
            "btc": "BTC/USD",
            "bitcoin": "BTC/USD",
            "nasdaq": "NASDAQ",
            "nq": "NASDAQ",
            "us100": "NASDAQ",
            "oil": "OIL",
            "wti": "OIL",
            "petroleo": "OIL",
            "petróleo": "OIL",
        }
    )
    try:
        payload = extract_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        fallback = NyPlan(
            available=True,
            htf_trend="indefinido",
            recommendation="no operar",
            why=text.strip()[:180] or "respuesta vacía",
            confidence="baja",
            honesty=f"Hermes no devolvió JSON ({exc})",
        )
        return dict.fromkeys(instrument_ids, fallback)

    raw_plans: dict[str, object]
    if isinstance(payload, dict) and isinstance(payload.get("plans"), dict):
        raw_plans = payload["plans"]  # type: ignore[assignment]
    elif (
        isinstance(payload, dict)
        and all(isinstance(payload.get(key), dict) for key in instrument_ids if key in payload)
        and any(key in payload for key in instrument_ids)
    ):
        raw_plans = payload
    elif isinstance(payload, dict) and {"recommendation", "htf_trend", "plan"} & payload.keys():
        # Single-plan object: apply only if exactly one instrument requested.
        raw_plans = {instrument_ids[0]: payload} if len(instrument_ids) == 1 else {}
    elif isinstance(payload, list):
        raw_plans = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = str(item.get("instrument_id") or item.get("symbol") or "").strip()
            if key:
                raw_plans[key] = item
    else:
        raw_plans = {}

    plans: dict[str, NyPlan] = {}
    for key, value in raw_plans.items():
        resolved = aliases.get(str(key).strip().lower(), aliases.get(str(key).strip()))
        if resolved is None:
            continue
        if not isinstance(value, dict):
            continue
        plans[resolved] = _plan_from_mapping(value)

    for instrument_id in instrument_ids:
        if instrument_id not in plans:
            plans[instrument_id] = unavailable_plan("Hermes no cubrió este símbolo")
    return plans


def build_decision_prompt(briefing: PreNyBriefing) -> str:
    """Facts-only prompt. Hermes judges; it must not invent extra timeframes."""
    lines = [
        "Eres el cierre de decisión del briefing pre-sesión NY (paper/alert only).",
        "No eres autorización para operar ni para auto-órdenes.",
        f"Fecha sesión: {briefing.session_date}. NY open (este repo): {briefing.ny_open_utc} UTC.",
        "Solo tienes velas/indicadores de 1h, 30m y 15m. NO inventes D1/H4 ni otros TF.",
        "Oro / Nasdaq / Petróleo: futuros continuos — niveles aproximados.",
        "Usa SOLO los hechos de abajo. Si ETR está viejo o faltan datos, bájale la confianza.",
        "NO des BUY/SELL ni compra/venta. Eso lo decide solo el scanner V2.",
        "Recomendación contextual (no es la acción): wait | stand_aside.",
        "Responde SOLO un JSON (sin markdown, sin herramientas) con esta forma:",
        "{",
        '  "XAU/USD": {',
        '    "htf_trend": "alcista|bajista|rango|indefinido",',
        '    "htf_basis": "qué TF/indicador usaste de los suministrados",',
        '    "support": ["nivel", "..."],',
        '    "resistance": ["nivel", "..."],',
        '    "recommendation": "wait|stand_aside",',
        '    "why": "una frase",',
        '    "invalidation": "qué cancela el plan",',
        '    "confidence": "low|medium|high",',
        '    "honesty": "opcional si datos flacos"',
        "  },",
        '  "BTC/USD": { ... }, "NASDAQ": { ... }, "OIL": { ... }',
        "}",
        "Textos de valores en español, cortos.",
    ]
    if briefing.synthesis:
        lines.extend(["", f"Síntesis: {briefing.synthesis}"])
    if briefing.shared_fundamental is not None:
        lines.append("Macro 3★:")
        lines.extend(f"- {line}" for line in briefing.shared_fundamental.render_lines())
    for item in briefing.instruments:
        lines.extend(["", f"## {item.instrument_id} ({item.display_name} · {item.yf_symbol})"])
        if item.data_freshness:
            lines.append(f"OHLC: {item.data_freshness}")
        for pillar in (item.technical, item.fundamental, item.sentiment):
            lines.append(f"{pillar.name}:")
            lines.extend(f"- {line}" for line in pillar.render_lines())
    return "\n".join(lines)


def format_ny_plan(plan: NyPlan) -> list[str]:
    """Phone-readable Spanish Plan NY block (2–3 lines when available)."""
    if not plan.available:
        reason = plan.unavailable_reason or "error desconocido"
        return [f"*Plan NY* no disponible: {reason}"]

    def _short_levels(levels: tuple[str, ...]) -> str:
        clipped: list[str] = []
        for level in levels[:2]:
            token = level.split("(")[0].strip() or level
            clipped.append(token[:18])
        return "/".join(clipped) if clipped else "—"

    support = _short_levels(plan.support)
    resist = _short_levels(plan.resistance)
    trend = plan.htf_trend[:40]
    action = plan.recommendation or plan.action or "no operar"
    first = f"*Plan NY* {action} · HTF {trend} · S {support} / R {resist}"
    bits = [part for part in (plan.why, plan.invalidation and f"Inv: {plan.invalidation}") if part]
    if plan.confidence:
        bits.append(f"Conf: {plan.confidence}")
    second = " · ".join(bits) if bits else ""
    lines = [first]
    if second:
        lines.append(second)
    if plan.honesty:
        lines.append(plan.honesty)
    return lines


def _resolve_hermes_binary(command: str) -> str | None:
    """Find the existing Hermes CLI. Login PATH is often missing in cron/docker."""
    if os.path.sep in command:
        return command if os.path.isfile(command) and os.access(command, os.X_OK) else None
    found = shutil.which(command)
    if found:
        return found
    home = Path.home()
    for candidate in (
        home / ".local/bin" / command,
        home / ".hermes/hermes-agent/venv/bin" / command,
        Path("/home/emilio/.local/bin") / command,
        Path("/home/emilio/.hermes/hermes-agent/venv/bin") / command,
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _chat_url(endpoint: str) -> str:
    url = endpoint.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/v1/chat/completions"


@dataclass
class HermesClient:
    """Thin client: HTTP OpenAI-compat if ``endpoint`` is set, else Hermes CLI."""

    enabled: bool = True
    endpoint: str = ""
    cli_command: str = "hermes"
    timeout_seconds: float = 240.0
    model: str = ""
    api_key: str | None = None

    @classmethod
    def from_config(cls, cfg: BriefingHermesConfig) -> HermesClient:
        return cls(
            enabled=cfg.enabled,
            endpoint=cfg.endpoint.strip(),
            cli_command=cfg.cli_command.strip() or "hermes",
            timeout_seconds=float(cfg.timeout_seconds),
            model=cfg.model.strip(),
            api_key=cfg.api_key,
        )

    async def complete(self, prompt: str) -> str:
        if not self.enabled:
            raise HermesError("deshabilitado")
        if self.endpoint:
            return await self._http(prompt)
        return await self._cli(prompt)

    async def _http(self, prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, object] = {
            "model": self.model or "glm-5.2",
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": "Devuelve solo JSON. Sin herramientas. Paper/alert only.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    _chat_url(self.endpoint), headers=headers, json=payload
                )
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            raise HermesError(f"timeout {self.timeout_seconds:.0f}s") from exc
        except httpx.HTTPError as exc:
            raise HermesError(f"http: {exc}") from exc
        except ValueError as exc:
            raise HermesError(f"http json: {exc}") from exc
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise HermesError("http: respuesta OpenAI-compat inválida") from exc

    async def _cli(self, prompt: str) -> str:
        tokens = shlex.split(self.cli_command)
        if not tokens:
            raise HermesError("cli_command vacío")
        resolved = _resolve_hermes_binary(tokens[0])
        if not resolved:
            raise HermesError(f"hermes no encontrado ({tokens[0]})")
        cmd = [resolved, *tokens[1:], "chat", "-q", prompt, "-Q", "--max-turns", "1"]
        if self.model:
            cmd.extend(["-m", self.model])
        configured = os.environ.get("HERMES_HOME")
        default_home = str(Path.home() / ".hermes")
        if configured and os.path.isdir(configured):
            hermes_home = configured
        elif os.path.isdir(default_home):
            hermes_home = default_home
        else:
            hermes_home = None
        # Hermes walks parents for .git; avoid /root when the scan user is root.
        cwd = hermes_home or "/tmp"
        env = os.environ.copy()
        if hermes_home:
            env.setdefault("HERMES_HOME", hermes_home)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except OSError as exc:
            raise HermesError(f"cli: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.communicate(), timeout=2)
            raise HermesError(f"timeout {self.timeout_seconds:.0f}s") from exc
        text = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode not in (0, None) and not text:
            raise HermesError((err or f"exit {proc.returncode}")[:160])
        if not text:
            raise HermesError(err[:160] if err else "respuesta vacía")
        return text


async def attach_ny_plans(
    briefing: PreNyBriefing,
    *,
    cfg: BriefingHermesConfig,
    complete: HermesComplete | None = None,
    actions: Mapping[str, str] | None = None,
) -> PreNyBriefing:
    """Fill ``ny_plan`` then overwrite the action from V2. Never raises."""
    from src.briefing.actions import DeskAction, apply_desk_action

    allowed: dict[str, DeskAction] = {
        "STAND_ASIDE": "STAND_ASIDE",
        "WATCH": "WATCH",
        "ENTER_ONLY_IF": "ENTER_ONLY_IF",
    }
    ids = [item.instrument_id for item in briefing.instruments]
    resolved: dict[str, DeskAction] = {}
    for instrument_id in ids:
        resolved[instrument_id] = allowed.get(
            (actions or {}).get(instrument_id, "STAND_ASIDE"),
            "STAND_ASIDE",
        )

    hermes_note: str | None = None
    plans: dict[str, NyPlan] = {}
    if cfg.enabled:
        try:
            runner: HermesComplete
            if complete is None:
                client = HermesClient.from_config(cfg)
                runner = client.complete
            else:
                runner = complete
            raw = await runner(build_decision_prompt(briefing))
            plans = parse_plans(raw, ids)
        except Exception as exc:
            logger.warning("Hermes NY plan failed: %s", exc)
            reason = str(exc).strip() or exc.__class__.__name__
            if isinstance(exc, TimeoutError) or "timeout" in reason.lower():
                reason = reason if reason.startswith("timeout") else f"timeout: {reason}"
            hermes_note = reason
            plans = {item: unavailable_plan(reason) for item in ids}
    else:
        hermes_note = "Hermes deshabilitado"

    briefing.instruments = [
        replace(
            item,
            ny_plan=apply_desk_action(
                plans.get(item.instrument_id),
                resolved[item.instrument_id],
                hermes_note=hermes_note,
            ),
        )
        for item in briefing.instruments
    ]
    return briefing
