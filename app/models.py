"""OpenAI-compatible request/response models.

Only the fields Kiro-API actually uses are validated strictly; unknown fields
are accepted and ignored so clients written against the full OpenAI schema work
without modification.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = ""  # string or content-parts array


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    response_format: ResponseFormat | None = None


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    prompt: Any = ""
    stream: bool = False


# ---- Response builders ---------------------------------------------------

def _rid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def build_chat_response(model: str, content: str, usage: Usage) -> dict[str, Any]:
    return {
        "id": _rid("chatcmpl"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage.model_dump(),
    }


def build_chat_chunk(
    model: str,
    request_id: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: Usage | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        chunk["usage"] = usage.model_dump()
    return chunk


def build_completion_response(model: str, text: str, usage: Usage) -> dict[str, Any]:
    return {
        "id": _rid("cmpl"),
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
        "usage": usage.model_dump(),
    }
