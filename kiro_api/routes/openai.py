"""OpenAI-compatible routes: /v1/chat/completions, /v1/responses, /v1/models."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..errors import ApiError
from ..streaming import openai_sse
from .common import (
    effort_from_request,
    long_running,
    mcp_servers_from_request,
    require_auth,
    workspace_from_request,
)

router = APIRouter(prefix="/v1", tags=["openai"])


@router.get("/models")
async def list_models(request: Request, _: None = Depends(require_auth)) -> dict:
    svc = request.app.state.service
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": now, "owned_by": "kiro"}
            for m in svc.models()
        ],
    }


@router.get("/models/{model_id}")
async def get_model(model_id: str, request: Request, _: None = Depends(require_auth)) -> dict:
    return {"id": model_id, "object": "model", "created": int(time.time()), "owned_by": "kiro"}


@router.post("/chat/completions")
async def chat_completions(request: Request, _: None = Depends(require_auth)):
    body = await request.json()
    svc = request.app.state.service
    cfg = request.app.state.config
    model = body.get("model") or cfg.default_model
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    lr = long_running(request, model)
    mcp = mcp_servers_from_request(request, body)
    cwd = workspace_from_request(request)
    effort = effort_from_request(request, body)
    rid = f"chatcmpl-{int(time.time()*1000)}"

    if stream:
        events = svc.run_stream(messages, model, mcp_servers=mcp, long_running=lr, cwd=cwd, effort=effort)
        return StreamingResponse(
            openai_sse(events, model, rid, cfg, cfg.surface_thinking),
            media_type="text/event-stream",
            headers={"X-Request-ID": rid, "Cache-Control": "no-cache"},
        )
    try:
        result = await svc.run(messages, model, mcp_servers=mcp, long_running=lr, cwd=cwd, effort=effort)
    except ApiError as exc:
        return JSONResponse(exc.openai_body(), status_code=exc.http_status, headers=exc.headers())

    body_out = {
        "id": rid,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result.content,
                        **({"reasoning_content": result.reasoning} if result.reasoning else {})},
            "finish_reason": result.finish_reason,
        }],
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }
    return JSONResponse(body_out, headers={"X-Request-ID": rid})


@router.post("/responses")
async def responses(request: Request, _: None = Depends(require_auth)):
    """Minimal OpenAI Responses API (non-streaming aggregate)."""
    body = await request.json()
    svc = request.app.state.service
    cfg = request.app.state.config
    model = body.get("model") or cfg.default_model
    # Responses accepts `input` (str or list) or `messages`.
    messages = _responses_to_messages(body)
    lr = long_running(request, model)
    effort = effort_from_request(request, body)
    rid = f"resp-{int(time.time()*1000)}"
    try:
        result = await svc.run(messages, model, long_running=lr, effort=effort)
    except ApiError as exc:
        return JSONResponse(exc.openai_body(), status_code=exc.http_status, headers=exc.headers())
    out = {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": result.content}],
        }],
        "usage": {
            "input_tokens": result.prompt_tokens,
            "output_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }
    return JSONResponse(out, headers={"X-Request-ID": rid})


def _responses_to_messages(body: dict) -> list[dict]:
    if isinstance(body.get("messages"), list):
        return body["messages"]
    inp = body.get("input")
    msgs = []
    if isinstance(body.get("instructions"), str):
        msgs.append({"role": "system", "content": body["instructions"]})
    if isinstance(inp, str):
        msgs.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if isinstance(item, dict):
                role = item.get("role", "user")
                content = item.get("content")
                msgs.append({"role": role, "content": content})
    return msgs or [{"role": "user", "content": ""}]
