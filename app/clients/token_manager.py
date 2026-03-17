"""Manages Codex OAuth tokens: load, refresh, persist."""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from config import (
    CODEX_AUTH_FILE,
    CLIENT_ID,
    TOKEN_URL,
    TOKEN_REFRESH_INTERVAL_DAYS,
)


class TokenManager:
    def __init__(self, auth_file: Path = CODEX_AUTH_FILE):
        self.auth_file = auth_file
        self._tokens: dict | None = None
        self._last_refresh: datetime | None = None
        self._load()

    def _load(self):
        if not self.auth_file.exists():
            raise FileNotFoundError(
                f"Codex auth file not found: {self.auth_file}\n"
                "Run 'codex login' first to authenticate."
            )
        data = json.loads(self.auth_file.read_text())
        self._tokens = data.get("tokens", {})
        lr = data.get("last_refresh")
        if lr:
            # Truncate nanoseconds to microseconds for Python 3.10 compat
            lr = lr.replace("Z", "+00:00")
            import re
            lr = re.sub(r"(\.\d{6})\d+", r"\1", lr)
            self._last_refresh = datetime.fromisoformat(lr)

    @property
    def access_token(self) -> str:
        return self._tokens.get("access_token", "")

    @property
    def refresh_token(self) -> str:
        return self._tokens.get("refresh_token", "")

    @property
    def account_id(self) -> str:
        return self._tokens.get("account_id", "")

    def needs_refresh(self) -> bool:
        if not self._last_refresh:
            return True
        age = datetime.now(timezone.utc) - self._last_refresh
        return age > timedelta(days=TOKEN_REFRESH_INTERVAL_DAYS)

    async def ensure_fresh(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self.needs_refresh():
            await self.refresh()
        return self.access_token

    async def refresh(self):
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            raise RuntimeError("No refresh token available. Run 'codex login' again.")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TOKEN_URL,
                json={
                    "client_id": CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            new_tokens = resp.json()

        # Merge new tokens (only changed values are returned)
        if "access_token" in new_tokens:
            self._tokens["access_token"] = new_tokens["access_token"]
        if "id_token" in new_tokens:
            self._tokens["id_token"] = new_tokens["id_token"]
        if "refresh_token" in new_tokens:
            self._tokens["refresh_token"] = new_tokens["refresh_token"]

        self._last_refresh = datetime.now(timezone.utc)
        self._persist()

    def _persist(self):
        """Save tokens back to auth.json."""
        data = {
            "OPENAI_API_KEY": None,
            "tokens": self._tokens,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
        }
        self.auth_file.write_text(json.dumps(data, indent=2))

    def get_auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
