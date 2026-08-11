"""Authenticated HTTP client for etracademy.com Market Terminal pages."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from src.etr.models import ASSET_PATHS, VALID_ASSETS
from src.scanner.state import _logs_dir

logger = logging.getLogger(__name__)

DEFAULT_SUPABASE_URL = "https://wgwzaykukvcotxoqefir.supabase.co"
# Public browser publishable key shipped in the ETR web client (not a secret key).
DEFAULT_SUPABASE_ANON_KEY = "sb_publishable_5Z_DxQ8niIFn3_nFxfHEww_oyK79hFO"
SITE_ORIGIN = "https://etracademy.com"
COOKIE_NAME = "sb-wgwzaykukvcotxoqefir-auth-token"


class EtrAuthError(RuntimeError):
    """Raised when login or token refresh fails."""


class EtrClient:
    """Supabase password auth + cookie session fetch of /analisis/{asset}."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        supabase_url: str | None = None,
        supabase_anon_key: str | None = None,
        session_path: Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.email = email.strip()
        self.password = password
        self.supabase_url = (
            supabase_url
            or os.environ.get("ETR_SUPABASE_URL")
            or DEFAULT_SUPABASE_URL
        ).rstrip("/")
        self.supabase_anon_key = (
            supabase_anon_key
            or os.environ.get("ETR_SUPABASE_ANON_KEY")
            or DEFAULT_SUPABASE_ANON_KEY
        )
        self.session_path = session_path or (_logs_dir() / "etr_session.json")
        self.timeout = timeout
        self._session: dict[str, Any] | None = None

    def _headers_auth(self) -> dict[str, str]:
        return {
            "apikey": self.supabase_anon_key,
            "Authorization": f"Bearer {self.supabase_anon_key}",
            "Content-Type": "application/json",
        }

    def _load_session_file(self) -> dict[str, Any] | None:
        if not self.session_path.exists():
            return None
        try:
            payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict) or not payload.get("access_token"):
            return None
        return payload

    def _save_session_file(self, session: dict[str, Any]) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        slim = {
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "expires_at": session.get("expires_at"),
            "token_type": session.get("token_type", "bearer"),
            "user": {
                "id": (session.get("user") or {}).get("id"),
                "email": (session.get("user") or {}).get("email"),
            },
            "saved_at": int(time.time()),
        }
        self.session_path.write_text(json.dumps(slim), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(self.session_path, 0o600)

    def _password_login(self) -> dict[str, Any]:
        url = f"{self.supabase_url}/auth/v1/token?grant_type=password"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                url,
                headers=self._headers_auth(),
                json={"email": self.email, "password": self.password},
            )
        if response.status_code != 200:
            raise EtrAuthError(
                f"ETR login failed HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        if not data.get("access_token"):
            raise EtrAuthError("ETR login response missing access_token")
        # expires_at may be absent — derive from expires_in
        if "expires_at" not in data and data.get("expires_in"):
            data["expires_at"] = int(time.time()) + int(data["expires_in"])
        self._save_session_file(data)
        logger.info("ETR session established for %s", self.email)
        return data

    def _refresh(self, refresh_token: str) -> dict[str, Any]:
        url = f"{self.supabase_url}/auth/v1/token?grant_type=refresh_token"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                url,
                headers=self._headers_auth(),
                json={"refresh_token": refresh_token},
            )
        if response.status_code != 200:
            raise EtrAuthError(f"ETR refresh failed HTTP {response.status_code}")
        data = response.json()
        if not data.get("access_token"):
            raise EtrAuthError("ETR refresh response missing access_token")
        if "expires_at" not in data and data.get("expires_in"):
            data["expires_at"] = int(time.time()) + int(data["expires_in"])
        # Keep refresh token if rotation omitted it
        if not data.get("refresh_token"):
            data["refresh_token"] = refresh_token
        self._save_session_file(data)
        logger.info("ETR session refreshed")
        return data

    def ensure_session(self, *, force_login: bool = False) -> dict[str, Any]:
        if force_login:
            self._session = self._password_login()
            return self._session

        session = self._session or self._load_session_file()
        if session:
            expires_at = session.get("expires_at")
            still_valid = True
            if expires_at is not None:
                try:
                    still_valid = int(expires_at) > int(time.time()) + 60
                except (TypeError, ValueError):
                    still_valid = True
            if still_valid and session.get("access_token"):
                self._session = session
                return session
            refresh = session.get("refresh_token")
            if refresh:
                try:
                    self._session = self._refresh(str(refresh))
                    return self._session
                except EtrAuthError as exc:
                    logger.warning("ETR refresh failed, re-login: %s", exc)

        self._session = self._password_login()
        return self._session

    def _cookie_header(self, session: dict[str, Any]) -> str:
        payload = {
            "access_token": session.get("access_token"),
            "token_type": session.get("token_type", "bearer"),
            "expires_in": session.get("expires_in"),
            "expires_at": session.get("expires_at"),
            "refresh_token": session.get("refresh_token"),
            "user": session.get("user"),
        }
        return f"{COOKIE_NAME}={json.dumps(payload, separators=(',', ':'))}"

    def fetch_analysis_html(self, asset: str) -> str:
        asset = asset.lower().strip()
        if asset not in VALID_ASSETS:
            raise ValueError(f"Unknown ETR asset '{asset}'. Valid: {', '.join(VALID_ASSETS)}")
        path = ASSET_PATHS[asset]
        url = f"{SITE_ORIGIN}{path}"

        session = self.ensure_session()
        headers = {
            "Cookie": self._cookie_header(session),
            "User-Agent": (
                "Mozilla/5.0 (compatible; manual-trading-agent/0.1; +https://etracademy.com)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }

        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            # Retry once on login redirect / unauthorized-looking page
            if response.status_code in {401, 403} or "/login" in str(response.url):
                session = self.ensure_session(force_login=True)
                headers["Cookie"] = self._cookie_header(session)
                response = client.get(url, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(f"ETR fetch {asset} HTTP {response.status_code}")
        if "/login" in str(response.url):
            raise EtrAuthError(f"ETR fetch {asset} redirected to login")
        text = response.text
        if "Iniciar sesión" in text and "Market Terminal" not in text:
            # Force re-login and one more attempt
            session = self.ensure_session(force_login=True)
            headers["Cookie"] = self._cookie_header(session)
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(url, headers=headers)
            text = response.text
            if "Market Terminal" not in text and "Context score" not in text:
                raise EtrAuthError(f"ETR fetch {asset} still unauthenticated")
        return text

    def fetch_report(self, asset: str):
        from datetime import UTC, datetime

        from src.etr.parser import parse_analysis_html

        html = self.fetch_analysis_html(asset)
        report = parse_analysis_html(html, asset)
        report.fetched_at = datetime.now(UTC).isoformat()
        return report
