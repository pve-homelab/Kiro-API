"""Anthropic-compatible routes: /v1/messages, /v1/messages/count_tokens."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..errors import ApiError
from ..shims.prompt import build_prompt_blocks, estimate_tokens
from ..streaming import anthropic_sse
from .common import (
    long_running,
    mcp_servers_from_request,
    require_auth,
    workspace_from_request,
)

router = APIRouter(tags=["anthropic"])


@router.post("/v1/messages")
async def messages(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    svc = request.app.state.service
    cfg = request.app.state.config
    model = body.get("model") or cfg.default_model
    msgs = body.get("messages") or []
    system = body.get("system")
    stream = bool(body.get("stream"))
    lr = long_running(request, model)
    mcp = mcp_servers_from_request(request, body)
    cwd = workspace_from_request(request)
    rid = f"msg-{int(time.time()*1000)}"

    if stream:
        events = svc.run_stream(msgs, model, system=system, mcp_servers=mcp, long_running=lr, cwd=cwd)
        return StreamingResponse(
            anthropic_sse(events, model, rid, cfg, cfg.surface_thinking),
            media_type="text/event-stream",
            headers={"X-Request-ID": rid, "Cache-Control": "no-cache"},
        )
    try:
        result = await svc.run(msgs, model, system=system, mcp_servers=mcp, long_running=lr, cwd=cwd)
    except ApiError as exc:
        return JSONResponse(exc.anthropic_body(), status_code=exc.http_status, headers=exc.headers())

    out = {
        "id": rid,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": result.content}],
        "stop_reason": _stop(result.finish_reason),
        "stop_sequence": None,
        "usage": {"input_tokens": result.prompt_tokens, "output_tokens": result.completion_tokens},
    }
    return JSONResponse(out, headers={"X-Request-ID": rid})


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request, _: None = Depends(require_auth)) -> dict:
    body = await request.json()
    blocks = build_prompt_blocks(body.get("messages") or [], system=body.get("system"))
    text = "\n".join(b.get("text", "") for b in blocks)
    return {"input_tokens": estimate_tokens(text)}


def _stop(finish: str) -> str:
    return {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}.get(finish, "end_turn")
