"""Native ACP routes: /acp/chat (non-streaming) and /acp/chat/stream (SSE).

These surface the normalized event contract directly (structured), for ACP-aware
clients that want reasoning/tool/plan events rather than a flattened completion.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..errors import ApiError
from ..streaming import _with_keepalive
from .common import (
    effort_from_request,
    long_running,
    mcp_servers_from_request,
    require_auth,
    workspace_from_request,
)

router = APIRouter(prefix="/acp", tags=["acp"])


@router.post("/chat")
async def acp_chat(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    svc = request.app.state.service
    cfg = request.app.state.config
    model = body.get("model") or cfg.default_model
    msgs = body.get("messages") or []
    mcp = mcp_servers_from_request(request, body)
    cwd = workspace_from_request(request)
    effort = effort_from_request(request, body)
    lr = long_running(request, model)

    collected: list[dict] = []
    content = []
    reasoning = []
    finish = "stop"
    try:
        async for event in svc.run_stream(msgs, model, mcp_servers=mcp, long_running=lr, cwd=cwd, effort=effort):
            collected.append(event)
            if event["type"] == "text":
                content.append(event["content"])
            elif event["type"] == "thinking":
                reasoning.append(event["content"])
            elif event["type"] == "done":
                finish = event.get("finish_reason", "stop")
    except ApiError as exc:
        return JSONResponse(exc.openai_body(), status_code=exc.http_status, headers=exc.headers())
    return {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "finish_reason": finish,
        "events": collected,
    }


@router.post("/chat/stream")
async def acp_chat_stream(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    svc = request.app.state.service
    cfg = request.app.state.config
    model = body.get("model") or cfg.default_model
    msgs = body.get("messages") or []
    mcp = mcp_servers_from_request(request, body)
    cwd = workspace_from_request(request)
    effort = effort_from_request(request, body)
    lr = long_running(request, model)

    async def gen():
        events = svc.run_stream(msgs, model, mcp_servers=mcp, long_running=lr, cwd=cwd, effort=effort)
        try:
            async for event in _with_keepalive(events, cfg.sse_keepalive_interval):
                if event is None:
                    yield ": keepalive\n\n"
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        except ApiError as exc:
            yield f"data: {json.dumps({'type': 'error', **exc.openai_body()})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
