"""Shared helpers for route shims: auth dependency, MCP passthrough, long-run hint."""
from __future__ import annotations

import hmac
import json

from fastapi import Header, HTTPException, Request


def _key_matches(presented: str | None, expected: str) -> bool:
    """Constant-time comparison so a wrong key can't be timing-probed."""
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


async def require_auth(request: Request, authorization: str | None = Header(default=None),
                       x_api_key: str | None = Header(default=None)) -> None:
    cfg = request.app.state.config
    if not cfg.auth_required:
        return
    key = cfg.auth_key
    bearer = authorization[len("Bearer "):] if authorization and authorization.startswith("Bearer ") else None
    if _key_matches(bearer, key) or _key_matches(x_api_key, key):
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def long_running(request: Request, model: str) -> bool:
    if request.headers.get("X-Kiro-Long", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return "long" in (model or "").lower()


def mcp_servers_from_request(request: Request, body: dict | None = None) -> list[dict]:
    """Extract MCP servers a harness declared (header or body) for passthrough.

    The gateway forwards these to kiro-cli on session/new; it never runs them.
    """
    servers: list[dict] = []
    hdr = request.headers.get("X-Kiro-MCP-Servers")
    if hdr:
        try:
            parsed = json.loads(hdr)
            if isinstance(parsed, list):
                servers = parsed
        except ValueError:
            pass
    if not servers and body and isinstance(body.get("mcp_servers"), list):
        servers = body["mcp_servers"]
    return [s for s in servers if isinstance(s, dict)]


def workspace_from_request(request: Request) -> str | None:
    return request.headers.get("X-Kiro-Workspace") or None


def effort_from_request(request: Request, body: dict | None = None) -> str | None:
    """Resolve the reasoning-effort level for a turn, in precedence order:
    X-Kiro-Effort header > OpenAI `reasoning_effort` / nested `reasoning.effort` >
    Anthropic `thinking.type`/budget hint. Returned raw; the service normalizes it.
    """
    hdr = request.headers.get("X-Kiro-Effort")
    if hdr:
        return hdr
    if not body:
        return None
    if body.get("reasoning_effort"):
        return body["reasoning_effort"]
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        return reasoning["effort"]
    # Anthropic extended-thinking: map enabled thinking to a sensible level.
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = thinking.get("budget_tokens")
        if isinstance(budget, int):
            if budget >= 32000:
                return "max"
            if budget >= 16000:
                return "xhigh"
            if budget >= 8000:
                return "high"
            return "medium"
        return "high"
    return None
