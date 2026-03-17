"""Pydantic models for API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(..., description="Role: system, user, or assistant")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    model: str = Field(default="gpt-5.1-codex-mini", description="Model slug")
    messages: list[Message] = Field(..., description="Conversation messages")
    instructions: str | None = Field(
        default=None,
        description="System instructions for the model",
    )
    stream: bool = Field(default=False, description="Enable SSE streaming")
    reasoning: dict | None = Field(
        default=None,
        description="Reasoning config, e.g. {'effort': 'medium'}",
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1)


class ChatChoice(BaseModel):
    index: int = 0
    message: Message
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    model: str
    choices: list[ChatChoice]
    usage: Usage = Usage()


class ModelInfo(BaseModel):
    slug: str
    display_name: str
    description: str
    context_window: int | None = None
    reasoning_levels: list[str] = []


class ModelsResponse(BaseModel):
    models: list[ModelInfo]


class HealthResponse(BaseModel):
    status: str
    authenticated: bool
    account_id: str = ""
    token_age_days: float = 0


# ---------------------------------------------------------------------------
# Anthropic Messages API models
# ---------------------------------------------------------------------------


class AnthropicContentBlock(BaseModel):
    """A single content block inside an Anthropic message."""

    type: str = "text"
    text: str = ""
    # tool_use fields
    id: str | None = None
    name: str | None = None
    input: dict | None = None
    # tool_result fields
    tool_use_id: str | None = None
    content: str | list | None = None
    is_error: bool | None = None


class AnthropicMessage(BaseModel):
    """One message in the Anthropic messages array."""

    role: str
    content: str | list[AnthropicContentBlock]


class AnthropicToolDefinition(BaseModel):
    """Tool definition in Anthropic format."""

    name: str
    description: str = ""
    input_schema: dict = Field(default_factory=dict)


class AnthropicRequest(BaseModel):
    """Incoming request in Anthropic Messages API format."""

    model: str
    max_tokens: int = 4096
    messages: list[AnthropicMessage]
    system: str | list[AnthropicContentBlock] | None = None
    stream: bool | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    tools: list[AnthropicToolDefinition] | None = None
    tool_choice: dict | None = None
    metadata: dict | None = None
    reasoning: dict | None = None
    stop_sequences: list[str] | None = None
