"""Translate between Anthropic Messages API and Codex Responses API formats."""

import json
import re
import time
import uuid

from config import MODEL_MAP, DEFAULT_REASONING
from models import AnthropicRequest, AnthropicContentBlock

# Claude → Codex identity replacements
_IDENTITY_REPLACEMENTS = [
    (r"You are Claude Code, Anthropic's official CLI for Claude\.", "You are Codex Code, a powerful CLI coding assistant."),
    (r"You are powered by the model named [\w. ]+\. The exact model ID is claude-[\w-]+\.", "You are powered by an OpenAI Codex model."),
    (r"Assistant knowledge cutoff is \w+ \d+\.", ""),
    (r"The most recent Claude model family is Claude [\d./]+\. Model IDs[^.]+\.", ""),
    (r"claude-opus-4-6", "gpt-5.1-codex"),
    (r"claude-sonnet-4-6", "gpt-5.1-codex-mini"),
    (r"claude-haiku-4-5-20251001", "gpt-5-codex-mini"),
    (r"Claude Code", "Codex Code"),
    (r"Anthropic's", "OpenAI's"),
]

# Behavioral overrides injected at the end of the system prompt
_BEHAVIOR_PATCH = """

# CRITICAL BEHAVIORAL OVERRIDES
- NEVER use the Task tool or spawn sub-agents (Explore, Plan, etc.). They are not available. Use Read, Glob, Grep, Bash directly.
- NEVER use EnterPlanMode or EnterWorktree. Just start working directly.
- If a tool call fails, try a DIFFERENT approach immediately. NEVER retry the same failing tool call.
- Prefer simple direct actions: Read files, Write files, Edit files, run Bash commands.
- Do NOT over-plan. Just do the task step by step using basic tools.
"""


def _rewrite_identity(text: str) -> str:
    """Replace Claude-specific identity references in the system prompt."""
    for pattern, replacement in _IDENTITY_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    # Append behavioral overrides
    text += _BEHAVIOR_PATCH
    return text


def _convert_tools(req: AnthropicRequest) -> list[dict]:
    """Convert Anthropic tool definitions to Codex/OpenAI function format.

    Anthropic:  {"name": "X", "description": "...", "input_schema": {...}}
    Codex:      {"type": "function", "name": "X", "description": "...", "parameters": {...}}
    """
    if not req.tools:
        return []
    codex_tools = []
    for tool in req.tools:
        codex_tools.append({
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        })
    return codex_tools


def _to_fc_id(original_id: str) -> str:
    """Ensure function call IDs start with 'fc_' as required by Codex API."""
    if original_id.startswith("fc_"):
        return original_id
    # Strip known prefixes and re-prefix
    for prefix in ("toolu_", "call_", "tool_"):
        if original_id.startswith(prefix):
            return "fc_" + original_id[len(prefix):]
    return "fc_" + original_id


# Map from Codex fc_ IDs back to original Anthropic IDs for response translation
_id_map: dict[str, str] = {}


def anthropic_to_codex(req: AnthropicRequest) -> dict:
    """Convert an Anthropic Messages API request to a Codex Responses API payload."""

    # --- system prompt → instructions ---
    instructions = "You are a helpful coding assistant."
    if req.system is not None:
        if isinstance(req.system, str):
            instructions = req.system
        elif isinstance(req.system, list):
            parts = []
            for block in req.system:
                if isinstance(block, AnthropicContentBlock):
                    parts.append(block.text)
                elif isinstance(block, dict):
                    parts.append(block.get("text", ""))
            instructions = "\n".join(parts)

    # Rewrite Claude identity → Codex identity
    instructions = _rewrite_identity(instructions)

    # --- messages → input ---
    input_items: list[dict] = []
    for msg in req.messages:
        role = msg.role
        if role == "system":
            role = "developer"

        # Normalise content — may contain text, tool_use, tool_result blocks
        if isinstance(msg.content, str):
            content_type = "output_text" if role == "assistant" else "input_text"
            input_items.append({
                "type": "message",
                "role": role,
                "content": [{"type": content_type, "text": msg.content}],
            })
        elif isinstance(msg.content, list):
            # Process each content block separately
            for block in msg.content:
                if isinstance(block, AnthropicContentBlock):
                    btype = block.type
                    bdict = block.model_dump()
                elif isinstance(block, dict):
                    btype = block.get("type", "text")
                    bdict = block
                else:
                    continue

                if btype == "text":
                    text = bdict.get("text", "")
                    content_type = "output_text" if role == "assistant" else "input_text"
                    input_items.append({
                        "type": "message",
                        "role": role,
                        "content": [{"type": content_type, "text": text}],
                    })

                elif btype == "tool_use":
                    # Assistant made a tool call → Codex function_call item
                    orig_id = bdict.get("id", f"call_{uuid.uuid4().hex[:12]}")
                    fc_id = _to_fc_id(orig_id)
                    _id_map[fc_id] = orig_id
                    input_items.append({
                        "type": "function_call",
                        "id": fc_id,
                        "call_id": fc_id,
                        "name": bdict.get("name", ""),
                        "arguments": json.dumps(bdict.get("input", {})),
                    })

                elif btype == "tool_result":
                    # User sent tool result back
                    orig_tool_id = bdict.get("tool_use_id", bdict.get("id", ""))
                    fc_id = _to_fc_id(orig_tool_id)
                    # Extract text from content (tool_result uses "content" not "text")
                    result_content = bdict.get("content", bdict.get("text", ""))
                    if isinstance(result_content, list):
                        text_parts = []
                        for sub in result_content:
                            if isinstance(sub, dict):
                                text_parts.append(sub.get("text", ""))
                            elif isinstance(sub, AnthropicContentBlock):
                                text_parts.append(sub.text)
                            else:
                                text_parts.append(str(sub))
                        result_text = "\n".join(text_parts)
                    elif isinstance(result_content, str):
                        result_text = result_content
                    else:
                        result_text = str(result_content)

                    input_items.append({
                        "type": "function_call_output",
                        "call_id": fc_id,
                        "output": result_text,
                    })
        else:
            content_type = "output_text" if role == "assistant" else "input_text"
            input_items.append({
                "type": "message",
                "role": role,
                "content": [{"type": content_type, "text": str(msg.content)}],
            })

    # --- model mapping ---
    codex_model = MODEL_MAP.get(req.model, MODEL_MAP.get("default", "gpt-5.1-codex-mini"))

    # --- tools ---
    codex_tools = _convert_tools(req)

    payload: dict = {
        "model": codex_model,
        "instructions": instructions,
        "input": input_items,
        "tools": codex_tools,
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
    }

    payload["reasoning"] = req.reasoning or DEFAULT_REASONING

    if codex_tools:
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = False

    return payload


# ---------------------------------------------------------------------------
# Codex SSE → Anthropic SSE event translation
# ---------------------------------------------------------------------------


class AnthropicSSEBuilder:
    """Stateful builder that translates Codex SSE events to Anthropic SSE events.

    Handles both text output and tool_use (function_call) responses.

    Text sequence:
      1. event: message_start
      2. event: content_block_start (type=text)
      3. event: content_block_delta (text_delta) × N
      4. event: content_block_stop
      5. event: message_delta (stop_reason + usage)
      6. event: message_stop

    Tool use sequence (per tool call):
      1. event: content_block_start (type=tool_use, id, name)
      2. event: content_block_delta (input_json_delta) × N
      3. event: content_block_stop
    Then ends with message_delta (stop_reason=tool_use) + message_stop
    """

    def __init__(self, model: str, request_id: str | None = None):
        self.model = model
        self.request_id = request_id or f"msg_{uuid.uuid4().hex[:24]}"
        self._started = False
        self._block_started = False
        self._block_index = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._has_tool_calls = False
        # Track current function call being streamed
        self._current_fc_id: str | None = None
        self._current_fc_name: str | None = None

    def _sse(self, event_type: str, data: dict) -> str:
        """Format a single SSE event."""
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    def _ensure_started(self) -> list[str]:
        """Ensure message_start has been emitted."""
        if not self._started:
            self._started = True
            return [self._sse(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.request_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": self.model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": self._input_tokens, "output_tokens": 0},
                    },
                },
            )]
        return []

    def _close_current_block(self) -> list[str]:
        """Close the current content block if one is open."""
        if self._block_started:
            self._block_started = False
            result = [self._sse(
                "content_block_stop",
                {"type": "content_block_stop", "index": self._block_index},
            )]
            self._block_index += 1
            return result
        return []

    def _start_text_block(self) -> list[str]:
        """Start a new text content block."""
        chunks = self._ensure_started()
        self._block_started = True
        chunks.append(self._sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": self._block_index,
                "content_block": {"type": "text", "text": ""},
            },
        ))
        return chunks

    def _start_tool_use_block(self, tool_id: str, name: str) -> list[str]:
        """Start a new tool_use content block."""
        chunks = self._ensure_started()
        # Close any open block first
        chunks.extend(self._close_current_block())
        self._block_started = True
        self._has_tool_calls = True
        chunks.append(self._sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": self._block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "input": {},
                },
            },
        ))
        return chunks

    def translate_codex_event(self, event: dict) -> list[str]:
        """Translate a single Codex SSE event into Anthropic SSE events."""
        event_type = event.get("type", "")
        chunks: list[str] = []

        if event_type == "response.created":
            chunks.extend(self._ensure_started())
            if not self._block_started:
                chunks.extend(self._start_text_block())

        elif event_type == "response.output_text.delta":
            if not self._started or not self._block_started:
                chunks.extend(self._start_text_block())
            delta = event.get("delta", "")
            if delta:
                chunks.append(self._sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self._block_index,
                        "delta": {"type": "text_delta", "text": delta},
                    },
                ))

        elif event_type == "response.output_item.added":
            # New output item — could be a function_call
            item = event.get("item", {})
            if item.get("type") == "function_call":
                # Close any open text block
                chunks.extend(self._close_current_block())
                raw_id = item.get("call_id", item.get("id", ""))
                # Convert Codex fc_ ID to Anthropic toolu_ format
                anthropic_id = _id_map.get(raw_id, f"toolu_{raw_id.replace('fc_', '')}" if raw_id else f"toolu_{uuid.uuid4().hex[:24]}")
                fc_name = item.get("name", "unknown")
                self._current_fc_id = anthropic_id
                self._current_fc_name = fc_name
                chunks.extend(self._start_tool_use_block(anthropic_id, fc_name))

        elif event_type == "response.function_call_arguments.delta":
            delta = event.get("delta", "")
            if delta:
                if not self._block_started and self._current_fc_id:
                    chunks.extend(self._start_tool_use_block(
                        self._current_fc_id, self._current_fc_name or "unknown"
                    ))
                chunks.append(self._sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self._block_index,
                        "delta": {"type": "input_json_delta", "partial_json": delta},
                    },
                ))

        elif event_type == "response.function_call_arguments.done":
            # Function call arguments complete — close the tool_use block
            chunks.extend(self._close_current_block())
            self._current_fc_id = None
            self._current_fc_name = None

        elif event_type == "response.output_text.done":
            # Text output complete — close the text block
            chunks.extend(self._close_current_block())

        elif event_type == "response.completed":
            response = event.get("response", {})
            usage = response.get("usage", {})
            self._input_tokens = usage.get("input_tokens", 0)
            self._output_tokens = usage.get("output_tokens", 0)

            # Ensure started
            chunks.extend(self._ensure_started())

            # If there's still an open block, close it
            if not self._block_started:
                # If nothing was ever opened, open and close an empty text block
                chunks.extend(self._start_text_block())
            chunks.extend(self._close_current_block())

            # Determine stop reason
            stop_reason = "tool_use" if self._has_tool_calls else "end_turn"

            # message_delta + message_stop
            chunks.append(self._sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": self._output_tokens},
                },
            ))
            chunks.append(self._sse("message_stop", {"type": "message_stop"}))

        return chunks
