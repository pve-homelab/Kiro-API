"""Unit tests for prompt flattening and JSON handling."""
from __future__ import annotations

from app.models import ChatMessage
from app.prompt import (
    JSON_INSTRUCTION,
    flatten_messages,
    strip_json_fences,
    wants_json,
)


def test_flatten_simple_transcript():
    msgs = [
        ChatMessage(role="system", content="Be brief."),
        ChatMessage(role="user", content="Hello"),
    ]
    out = flatten_messages(msgs)
    assert "System: Be brief." in out
    assert "User: Hello" in out


def test_flatten_skips_empty_content():
    msgs = [ChatMessage(role="user", content=""), ChatMessage(role="user", content="hi")]
    assert flatten_messages(msgs).strip() == "User: hi"


def test_flatten_content_parts_array():
    msgs = [ChatMessage(role="user", content=[{"type": "text", "text": "part-a"},
                                              {"type": "text", "text": "part-b"}])]
    out = flatten_messages(msgs)
    assert "part-a" in out and "part-b" in out


def test_wants_json_detection():
    class RF:
        type = "json_object"
    assert wants_json(RF()) is True
    assert wants_json(None) is False


def test_json_instruction_appended():
    assert "JSON" in JSON_INSTRUCTION


def test_strip_json_fences():
    fenced = "```json\n{\"a\": 1}\n```"
    assert strip_json_fences(fenced) == '{"a": 1}'


def test_strip_json_fences_noop_when_plain():
    assert strip_json_fences('{"a": 1}') == '{"a": 1}'
