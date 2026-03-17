"""Configuration for Codex OAuth API Server."""

from pathlib import Path

# OAuth Constants (from Codex CLI source)
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
SCOPES = "openid profile email offline_access"
REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"

# API Base URLs
CHATGPT_API_BASE = "https://chatgpt.com/backend-api/codex"
OPENAI_API_BASE = "https://api.openai.com/v1"

# Token storage
CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"
CODEX_MODELS_CACHE = Path.home() / ".codex" / "models_cache.json"

# Token refresh interval (Codex CLI uses 8 days)
TOKEN_REFRESH_INTERVAL_DAYS = 8

# Server defaults (overridable via env vars)
import json
import os

DEFAULT_MODEL = os.environ.get("CODEX_MODEL", "gpt-5.1-codex-mini")
DEFAULT_SUBAGENT_MODEL = os.environ.get("CODEX_SUBAGENT_MODEL", "gpt-5.1-codex-mini")
DEFAULT_REASONING = {"effort": os.environ.get("CODEX_REASONING", "xhigh")}
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8741

# Claude model → Codex model mapping
# opus/sonnet → main model, haiku → sub-agent model

_default_model_map = {
    "claude-opus-4-6": DEFAULT_MODEL,
    "claude-sonnet-4-6": DEFAULT_MODEL,
    "claude-haiku-4-5-20251001": DEFAULT_SUBAGENT_MODEL,
    "default": DEFAULT_MODEL,
}

_env_map = os.environ.get("CODEX_MODEL_MAP")
MODEL_MAP: dict[str, str] = (
    {**_default_model_map, **json.loads(_env_map)} if _env_map else _default_model_map
)
