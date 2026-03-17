"""FastAPI server exposing Codex API via OAuth tokens."""

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import DEFAULT_MODEL, MODEL_MAP
from clients.token_manager import TokenManager
from clients.codex_client import CodexClient
from adapters.translator import anthropic_to_codex, AnthropicSSEBuilder
from models import (
    AnthropicRequest,
    ChatRequest,
    ChatResponse,
    ChatChoice,
    Message,
    Usage,
    ModelInfo,
    ModelsResponse,
    HealthResponse,
)

logger = logging.getLogger("codex_proxy")

# Global instances
tm: TokenManager | None = None
client: CodexClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tm, client
    tm = TokenManager()
    client = CodexClient(tm)
    print(f"[OK] Token loaded. Account: {tm.account_id[:8]}...")
    print(f"[OK] Token refresh needed: {tm.needs_refresh()}")
    yield
    await client.close()


app = FastAPI(
    title="Codex OAuth API Server",
    description="OpenAI Codex API proxy using OAuth tokens (no API key needed)",
    version="1.0.0",
    lifespan=lifespan,
)


from starlette.middleware.base import BaseHTTPMiddleware

class RequestLogger(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        body_preview = ""
        if request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            body_preview = f" body={body[:200]}" if body else ""
        print(f"[REQ] {request.method} {request.url.path}?{request.url.query}{body_preview}", flush=True)
        response = await call_next(request)
        print(f"[RES] {request.method} {request.url.path} → {response.status_code}", flush=True)
        return response

app.add_middleware(RequestLogger)


@app.get("/health", response_model=HealthResponse)
async def health():
    age = 0.0
    if tm._last_refresh:
        delta = datetime.now(timezone.utc) - tm._last_refresh
        age = delta.total_seconds() / 86400
    return HealthResponse(
        status="ok",
        authenticated=bool(tm.access_token),
        account_id=tm.account_id[:8] + "..." if tm.account_id else "",
        token_age_days=round(age, 2),
    )


@app.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    raw = client.get_models()
    models = []
    for m in raw:
        levels = [r["effort"] for r in m.get("supported_reasoning_levels", [])]
        models.append(
            ModelInfo(
                slug=m["slug"],
                display_name=m.get("display_name", m["slug"]),
                description=m.get("description", ""),
                context_window=m.get("context_window"),
                reasoning_levels=levels,
            )
        )
    return ModelsResponse(models=models)


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    """OpenAI-compatible chat completions endpoint."""
    if req.stream:
        return _stream_response(req)

    try:
        raw = await client.chat(req)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Codex API error: {e}")

    return ChatResponse(
        id=raw.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
        model=req.model,
        choices=[
            ChatChoice(
                message=Message(role="assistant", content=raw.get("output_text", "")),
            )
        ],
        usage=Usage(
            prompt_tokens=raw.get("usage", {}).get("input_tokens", 0),
            completion_tokens=raw.get("usage", {}).get("output_tokens", 0),
            total_tokens=raw.get("usage", {}).get("total_tokens", 0),
        ),
    )


def _stream_response(req: ChatRequest):
    """Return SSE streaming response."""

    async def event_generator():
        try:
            async for chunk in client.chat_stream(req):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            error = json.dumps({"error": str(e)})
            yield f"data: {error}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/v1/responses")
async def responses_passthrough(body: dict):
    """Direct passthrough to the Codex Responses API."""
    messages = []
    for item in body.get("input", body.get("messages", [])):
        role = item.get("role", "user")
        content = item.get("content", "")
        # Handle both string content and array-of-objects content
        if isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            content = " ".join(text_parts)
        messages.append(Message(role=role, content=content))

    try:
        raw = await client.chat(
            ChatRequest(
                model=body.get("model", DEFAULT_MODEL),
                messages=messages,
                instructions=body.get("instructions"),
                stream=False,
                reasoning=body.get("reasoning"),
                temperature=body.get("temperature"),
                max_output_tokens=body.get("max_output_tokens"),
            )
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return raw


@app.post("/v1/token/refresh")
async def force_refresh():
    """Manually trigger token refresh."""
    try:
        await tm.refresh()
        return {"status": "refreshed", "account_id": tm.account_id[:8] + "..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Anthropic Messages API compatible endpoints
# ---------------------------------------------------------------------------


@app.post("/v1/messages")
async def anthropic_messages(req: AnthropicRequest, request: Request):
    """Anthropic Messages API compatible endpoint.

    Translates incoming Anthropic-format requests to Codex Responses API,
    streams the response, and translates Codex SSE events back to
    Anthropic SSE format.
    """
    logger.info(
        f"[anthropic] model={req.model} messages={len(req.messages)} "
        f"max_tokens={req.max_tokens} stream={req.stream}"
    )

    # Dump system prompt for debugging
    from pathlib import Path
    dump_path = Path(__file__).parent / "debug_system_prompt.txt"
    with open(dump_path, "w") as f:
        f.write("=== SYSTEM PROMPT ===\n")
        if req.system is not None:
            if isinstance(req.system, str):
                f.write(req.system)
            elif isinstance(req.system, list):
                for block in req.system:
                    f.write(getattr(block, "text", str(block)))
                    f.write("\n")
        else:
            f.write("(None)")
        f.write("\n\n=== MESSAGES ===\n")
        for i, msg in enumerate(req.messages):
            f.write(f"\n--- [{i}] role={msg.role} ---\n")
            if isinstance(msg.content, str):
                f.write(msg.content[:500])
            elif isinstance(msg.content, list):
                for block in msg.content:
                    f.write(getattr(block, "text", str(block))[:500])
            f.write("\n")

    # Translate request
    codex_payload = anthropic_to_codex(req)
    mapped_model = codex_payload["model"]
    logger.info(f"[anthropic] mapped model: {req.model} → {mapped_model}")

    # Build Anthropic SSE builder
    builder = AnthropicSSEBuilder(model=req.model)

    async def stream_anthropic():
        http = await client._client()
        await client.tm.ensure_fresh()

        try:
            async with http.stream(
                "POST",
                f"{client.base_url}/responses",
                json=codex_payload,
                headers=client._build_headers(),
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    logger.error(
                        f"[anthropic] Codex API error {resp.status_code}: {body[:500]}"
                    )
                    # Send error as an SSE event
                    error_event = builder._sse(
                        "error",
                        {
                            "type": "error",
                            "error": {
                                "type": "api_error",
                                "message": f"Upstream error {resp.status_code}: {body.decode(errors='replace')[:200]}",
                            },
                        },
                    )
                    yield error_event
                    return

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

                    # Translate each Codex event
                    for sse_chunk in builder.translate_codex_event(event):
                        yield sse_chunk

        except Exception as e:
            logger.exception(f"[anthropic] Stream error: {e}")
            error_event = builder._sse(
                "error",
                {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": str(e),
                    },
                },
            )
            yield error_event

    return StreamingResponse(
        stream_anthropic(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/v1/messages/count_tokens")
async def count_tokens(req: AnthropicRequest):
    """Stub token counter — returns a rough estimate."""
    total_text = ""
    if req.system:
        if isinstance(req.system, str):
            total_text += req.system
        elif isinstance(req.system, list):
            for block in req.system:
                total_text += getattr(block, "text", "") or ""
    for msg in req.messages:
        if isinstance(msg.content, str):
            total_text += msg.content
        elif isinstance(msg.content, list):
            for block in msg.content:
                total_text += getattr(block, "text", "") or ""

    # Rough estimate: ~4 chars per token
    estimated = max(1, len(total_text) // 4)
    return {"input_tokens": estimated}
