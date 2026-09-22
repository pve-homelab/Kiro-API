"""Turn OpenAI chat messages into a single prompt string for kiro-cli.

kiro-cli `chat --no-interactive` takes a single prompt on stdin, so we flatten
the message array into a readable transcript. Content parts (arrays used by the
vision/multimodal schema) are reduced to their text pieces.
"""
from __future__ import annotations

from typing import Any

from .models import ChatMessage


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    parts.append(str(part["text"]))
                elif "text" in part:
                    parts.append(str(part["text"]))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return str(content)


ROLE_LABELS = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool",
    "function": "Function",
}


def flatten_messages(messages: list[ChatMessage]) -> str:
    """Flatten a chat transcript into a single prompt.

    The final user turn is emphasized so kiro-cli treats it as the active
    instruction while still seeing prior context.
    """
    lines: list[str] = []
    for msg in messages:
        label = ROLE_LABELS.get(msg.role, msg.role.capitalize())
        text = _content_to_text(msg.content).strip()
        if not text:
            continue
        lines.append(f"{label}: {text}")
    return "\n\n".join(lines).strip()


def wants_json(response_format: Any) -> bool:
    if response_format is None:
        return False
    rf_type = getattr(response_format, "type", None)
    return rf_type in {"json_object", "json_schema"}


JSON_INSTRUCTION = (
    "\n\nRespond with a single valid JSON object only. "
    "Do not include markdown fences or any text outside the JSON."
)


def strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` fences if the model wrapped its JSON."""
    t = text.strip()
    if t.startswith("```"):
        # drop first fence line
        newline = t.find("\n")
        if newline != -1:
            t = t[newline + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
