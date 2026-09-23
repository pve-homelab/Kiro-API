"""Build ACP session/prompt content blocks from OpenAI/Anthropic messages.

ACP's prompt is a role-less list of content blocks and the gateway is stateless
(fresh session per request), so a multi-turn conversation is serialized into a
single labelled transcript (System:/Developer:/User:/Assistant:), preserving
order. A lone user message is sent verbatim.
"""
from __future__ import annotations

from typing import Any

_LABELS = {"user": "User", "assistant": "Assistant", "system": "System", "developer": "Developer"}


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in (None, "text") and "text" in part:
                    parts.append(str(part["text"]))
                elif part.get("type") == "text" and "text" in part:
                    parts.append(str(part["text"]))
            else:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    return str(content)


def build_prompt_blocks(messages: list[dict], system: str | list | None = None) -> list[dict]:
    """Return ACP content blocks for a conversation.

    `system` handles Anthropic's separate top-level system field.
    """
    parts: list[tuple[str, str]] = []
    if system:
        sys_text = _content_to_text(system)
        if sys_text.strip():
            parts.append(("system", sys_text.strip()))
    for m in messages:
        role = m.get("role", "user") if isinstance(m, dict) else getattr(m, "role", "user")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        text = _content_to_text(content).strip()
        if text:
            parts.append((role, text))

    if not parts:
        return [{"type": "text", "text": ""}]
    if len(parts) == 1 and parts[0][0] == "user":
        return [{"type": "text", "text": parts[0][1]}]
    transcript = "\n\n".join(f"{_LABELS.get(r, 'User')}: {c}" for r, c in parts)
    return [{"type": "text", "text": transcript}]


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)
