"""Client for calling the Codex/ChatGPT backend API."""

import json
import uuid
from collections.abc import AsyncGenerator

import httpx

from config import CHATGPT_API_BASE, CODEX_MODELS_CACHE, DEFAULT_REASONING
from clients.token_manager import TokenManager
from models import ChatRequest


class CodexClient:
    def __init__(self, token_manager: TokenManager):
        self.tm = token_manager
        self.base_url = CHATGPT_API_BASE
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=120.0)
        return self._http

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    def _build_headers(self) -> dict:
        """Build all required headers for the ChatGPT backend."""
        return {
            "Authorization": f"Bearer {self.tm.access_token}",
            "ChatGPT-Account-ID": self.tm.account_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Origin": "https://chatgpt.com",
        }

    def _build_payload(self, req: ChatRequest) -> dict:
        """Build the Responses API payload matching Codex CLI format.

        NOTE: The ChatGPT backend requires stream=true always.
        """
        input_items = []
        for msg in req.messages:
            role = msg.role
            if role == "system":
                role = "developer"

            input_items.append({
                "type": "message",
                "role": role,
                "content": [
                    {
                        "type": "input_text",
                        "text": msg.content,
                    }
                ],
            })

        payload = {
            "model": req.model,
            "instructions": req.instructions or "You are a helpful coding assistant.",
            "input": input_items,
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": False,
            "stream": True,  # Always true - backend requires it
            "include": ["reasoning.encrypted_content"],
        }

        payload["reasoning"] = req.reasoning or DEFAULT_REASONING

        if req.temperature is not None:
            payload["temperature"] = req.temperature

        if req.max_output_tokens is not None:
            payload["max_output_tokens"] = req.max_output_tokens

        return payload

    async def chat(self, req: ChatRequest) -> dict:
        """Non-streaming: collect all SSE events and assemble a response."""
        client = await self._client()
        await self.tm.ensure_fresh()
        payload = self._build_payload(req)

        # Collect streamed events into a full response
        response_id = ""
        output_text_parts = []
        usage = {}

        async with client.stream(
            "POST",
            f"{self.base_url}/responses",
            json=payload,
            headers=self._build_headers(),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "response.created":
                    response_id = event.get("response", {}).get("id", "")

                elif event_type == "response.output_text.delta":
                    output_text_parts.append(event.get("delta", ""))

                elif event_type == "response.completed":
                    r = event.get("response", {})
                    response_id = r.get("id", response_id)
                    usage = r.get("usage", {})

        return {
            "id": response_id,
            "output_text": "".join(output_text_parts),
            "usage": usage,
        }

    async def chat_stream(self, req: ChatRequest) -> AsyncGenerator[str, None]:
        """Streaming: yield SSE events as-is."""
        client = await self._client()
        await self.tm.ensure_fresh()
        payload = self._build_payload(req)

        async with client.stream(
            "POST",
            f"{self.base_url}/responses",
            json=payload,
            headers=self._build_headers(),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line[6:]
                elif line.strip():
                    yield line

    def get_models(self) -> list[dict]:
        """Load models from the Codex CLI cache."""
        if not CODEX_MODELS_CACHE.exists():
            return []
        data = json.loads(CODEX_MODELS_CACHE.read_text())
        return data.get("models", [])
