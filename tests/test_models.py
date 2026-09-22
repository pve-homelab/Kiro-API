"""Unit tests for OpenAI response builders + token estimation."""
from __future__ import annotations

from app.models import (
    Usage,
    build_chat_chunk,
    build_chat_response,
    build_completion_response,
)
from app.runner import estimate_tokens


def test_chat_response_shape():
    r = build_chat_response("auto", "hi", Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3))
    assert r["object"] == "chat.completion"
    assert r["choices"][0]["message"]["content"] == "hi"
    assert r["choices"][0]["finish_reason"] == "stop"
    assert r["usage"]["total_tokens"] == 3
    assert r["id"].startswith("chatcmpl-")


def test_chat_chunk_with_and_without_usage():
    c = build_chat_chunk("auto", "rid", {"content": "x"})
    assert c["object"] == "chat.completion.chunk"
    assert "usage" not in c
    c2 = build_chat_chunk("auto", "rid", {}, "stop", Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    assert c2["choices"][0]["finish_reason"] == "stop"
    assert c2["usage"]["total_tokens"] == 2


def test_completion_response_shape():
    r = build_completion_response("auto", "text out", Usage(prompt_tokens=2, completion_tokens=2, total_tokens=4))
    assert r["object"] == "text_completion"
    assert r["choices"][0]["text"] == "text out"


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") >= 1
    assert estimate_tokens("a" * 400) == 100
