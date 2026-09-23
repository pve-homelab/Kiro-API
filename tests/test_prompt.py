"""Prompt block building."""
from __future__ import annotations

from kiro_api.shims.prompt import build_prompt_blocks, estimate_tokens


def test_single_user_message_verbatim():
    blocks = build_prompt_blocks([{"role": "user", "content": "hello"}])
    assert blocks == [{"type": "text", "text": "hello"}]


def test_multiturn_labelled():
    blocks = build_prompt_blocks([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ])
    text = blocks[0]["text"]
    assert "System: be brief" in text
    assert "User: hi" in text
    assert "Assistant: hello" in text
    assert text.index("System:") < text.index("User: hi") < text.index("Assistant:")


def test_anthropic_system_field():
    blocks = build_prompt_blocks([{"role": "user", "content": "q"}], system="you are helpful")
    text = blocks[0]["text"]
    assert "System: you are helpful" in text
    assert "User: q" in text


def test_content_parts_flattened():
    blocks = build_prompt_blocks([
        {"role": "user", "content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}]}
    ])
    assert "part1" in blocks[0]["text"]
    assert "part2" in blocks[0]["text"]


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100
