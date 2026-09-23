"""Render tool-call / plan events into human-readable reasoning text for the
OpenAI/Anthropic shims (which have no structured tool-activity channel by
default). Keeps behavior parity with what agent harnesses expect to see.
"""
from __future__ import annotations


def render_tool_call(event: dict) -> str:
    name = (event.get("name") or "").strip()
    args = event.get("arguments") or {}
    lines = [f"\n**⚙ {name}**"] if name else []
    for key in ("command", "path", "pattern", "query", "url"):
        val = args.get(key)
        if val:
            lines.append(f"  {key}=`{val}`")
    return "\n".join(lines) + "\n" if lines else ""


def render_tool_update(event: dict) -> str:
    out = (event.get("output") or "").strip()
    if not out:
        return ""
    if event.get("kind") == "execute":
        body = out if len(out) <= 2000 else out[:2000] + "\n… (truncated)"
        return "\n```\n" + body + "\n```\n"
    return f"\n  ↳ {out[:500]}\n"


def render_plan(event: dict) -> str:
    entries = event.get("entries") or []
    if not entries:
        return ""
    desc = event.get("description") or ""
    lines = [f"\nPlan — {desc}" if desc else "\nPlan"]
    for e in entries:
        box = {"completed": "[x]", "in_progress": "[~]"}.get(e.get("status"), "[ ]")
        lines.append(f"- {box} {e.get('content', '')}")
    return "\n".join(lines) + "\n"
