"""Shared helpers for route shims: auth dependency, MCP passthrough, long-run hint."""
from __future__ import annotations

import json

from fastapi import Header, HTTPException, Request


async def require_auth(request: Request, authorization: str | None = Header(default=None),
                       x_api_key: str | None = Header(default=None)) -> None:
    cfg = request.app.state.config
    if not cfg.auth_required:
        return
    key = cfg.auth_key
    if authorization == f"Bearer {key}" or x_api_key == key:
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
