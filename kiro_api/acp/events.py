"""Normalized internal event contract produced by an ACP worker and consumed by
the protocol shims. Plain dicts keep the boundary simple and test-friendly.

Event shapes:
    {"type": "text",              "content": str}
    {"type": "thinking",          "content": str}
    {"type": "tool_call",         "id": str, "name": str, "kind": str, "arguments": dict}
    {"type": "tool_call_update",  "id": str, "name": str, "kind": str, "output": str}
    {"type": "plan",              "entries": list[dict], "description": str}
    {"type": "done",              "finish_reason": str, "usage": dict}
    {"type": "error",             "message": str, "category": str}
"""
from __future__ import annotations


def text(content: str) -> dict:
    return {"type": "text", "content": content}


def thinking(content: str) -> dict:
    return {"type": "thinking", "content": content}


def tool_call(tid: str, name: str, kind: str, arguments: dict) -> dict:
    return {"type": "tool_call", "id": tid, "name": name, "kind": kind, "arguments": arguments}


def tool_update(tid: str, name: str, kind: str, output: str) -> dict:
    return {"type": "tool_call_update", "id": tid, "name": name, "kind": kind, "output": output}


def plan(entries: list[dict], description: str = "") -> dict:
    return {"type": "plan", "entries": entries, "description": description}


def done(finish_reason: str = "stop", usage: dict | None = None) -> dict:
    return {"type": "done", "finish_reason": finish_reason, "usage": usage or {}}


def error(message: str, category: str = "upstream") -> dict:
    return {"type": "error", "message": message, "category": category}
