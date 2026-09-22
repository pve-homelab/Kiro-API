"""Kiro-API V2 — FastAPI application.

Serves:
  * OpenAI-compatible /v1 endpoints (chat/completions, completions, models)
  * Control endpoints: /health, /stats, /ws/stats (WebSocket)
  * The slim live dashboard at / (dashboard app, see dashboard.py)
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .admin import router as admin_router
from .config import config
from .dashboard import dashboard_html
from .kiro_status import check_login
from .models import (
    ChatCompletionRequest,
    CompletionRequest,
    Usage,
    build_chat_chunk,
    build_chat_response,
    build_completion_response,
)
from .prompt import (
    JSON_INSTRUCTION,
    flatten_messages,
    strip_json_fences,
    wants_json,
)
from .reaper import reaper
from .runner import BusyError, KiroCliError, runner


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Warn if child processes grow past ~2x the concurrency ceiling + headroom;
    # that indicates jobs aren't being reaped and surfaces as an orange dot.
    reaper.child_warn_threshold = max(16, config.max_concurrency * 2 + 8)
    reaper.start()
    try:
        yield
    finally:
        await reaper.stop()


app = FastAPI(title="Kiro-API V2", version=__version__, lifespan=lifespan)
app.include_router(admin_router)

_START_TS = time.time()


# ---- Auth dependency -----------------------------------------------------
async def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not config.auth_required:
        return
    expected = f"Bearer {config.auth_key}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _long_running(request: Request, model: str) -> bool:
    if request.headers.get("X-Kiro-Long", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return "long" in (model or "").lower()


# ---- OpenAI: models ------------------------------------------------------
@app.get("/v1/models")
async def list_models(_: None = Depends(require_auth)) -> dict:
    now = int(time.time())
    ids = {config.default_model, "auto"}
    return {
        "object": "list",
        "data": [
            {"id": mid, "object": "model", "created": now, "owned_by": "kiro"}
            for mid in sorted(ids)
        ],
    }


# ---- OpenAI: chat completions -------------------------------------------
@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest, request: Request, _: None = Depends(require_auth)
):
    model = req.model or config.default_model
    prompt = flatten_messages(req.messages)
    if wants_json(req.response_format):
        prompt += JSON_INSTRUCTION
    long_running = _long_running(request, model)
    request_id = f"chatcmpl-{int(time.time()*1000)}"

    if req.stream:
        return StreamingResponse(
            _stream_chat(prompt, model, request_id, long_running, json_mode=wants_json(req.response_format)),
            media_type="text/event-stream",
            headers={"X-Request-ID": request_id, "Cache-Control": "no-cache"},
        )

    try:
        text, ptok, ctok = await runner.run(prompt, model, long_running)
    except BusyError as exc:
        raise HTTPException(status_code=429, detail="Server busy, queue full") from exc
    except KiroCliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if wants_json(req.response_format):
        text = strip_json_fences(text)

    usage = Usage(prompt_tokens=ptok, completion_tokens=ctok, total_tokens=ptok + ctok)
    return JSONResponse(build_chat_response(model, text, usage), headers={"X-Request-ID": request_id})


async def _stream_chat(prompt, model, request_id, long_running, json_mode):
    """Emit an OpenAI-style SSE stream. kiro-cli returns a full response, so we
    send the role delta, then the content as one chunk, then a final usage chunk
    and [DONE]. (Token-level streaming isn't available from the CLI.)"""
    try:
        text, ptok, ctok = await runner.run(prompt, model, long_running)
    except BusyError:
        err = {"error": {"message": "Server busy, queue full", "type": "rate_limit"}}
        yield f"data: {json.dumps(err)}\n\n"
        yield "data: [DONE]\n\n"
        return
    except KiroCliError as exc:
        err = {"error": {"message": str(exc), "type": "upstream_error"}}
        yield f"data: {json.dumps(err)}\n\n"
        yield "data: [DONE]\n\n"
        return

    if json_mode:
        text = strip_json_fences(text)

    # role delta
    yield f"data: {json.dumps(build_chat_chunk(model, request_id, {'role': 'assistant'}))}\n\n"
    # content delta
    yield f"data: {json.dumps(build_chat_chunk(model, request_id, {'content': text}))}\n\n"
    # final chunk with finish_reason + usage
    usage = Usage(prompt_tokens=ptok, completion_tokens=ctok, total_tokens=ptok + ctok)
    yield f"data: {json.dumps(build_chat_chunk(model, request_id, {}, 'stop', usage))}\n\n"
    yield "data: [DONE]\n\n"


# ---- OpenAI: legacy completions -----------------------------------------
@app.post("/v1/completions")
async def completions(req: CompletionRequest, request: Request, _: None = Depends(require_auth)):
    model = req.model or config.default_model
    prompt = req.prompt if isinstance(req.prompt, str) else "\n".join(map(str, req.prompt or []))
    long_running = _long_running(request, model)

    if req.stream:
        request_id = f"cmpl-{int(time.time()*1000)}"

        async def gen():
            try:
                text, ptok, ctok = await runner.run(prompt, model, long_running)
            except (BusyError, KiroCliError) as exc:
                yield f"data: {json.dumps({'error': {'message': str(exc) or 'busy'}})}\n\n"
                yield "data: [DONE]\n\n"
                return
            chunk = {
                "id": request_id,
                "object": "text_completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        text, ptok, ctok = await runner.run(prompt, model, long_running)
    except BusyError as exc:
        raise HTTPException(status_code=429, detail="Server busy, queue full") from exc
    except KiroCliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    usage = Usage(prompt_tokens=ptok, completion_tokens=ctok, total_tokens=ptok + ctok)
    return build_completion_response(model, text, usage)


# ---- Control: health -----------------------------------------------------
@app.get("/health")
async def health() -> dict:
    login = await check_login()
    stats = runner.stats.snapshot()
    reap = reaper.stats.snapshot()
    healthy = login["logged_in"] and not reap["leak_warning"]
    return {
        "status": "ok",
        "version": __version__,
        "healthy": healthy,
        "logged_in": login["logged_in"],
        "login_detail": login["detail"],
        "v1_url": config.v1_base_url,
        "dashboard_url": config.dashboard_url,
        "default_model": config.default_model,
        "auth_required": config.auth_required,
        "uptime_secs": int(time.time() - _START_TS),
        "pool": stats,
        "reaper": reap,
    }


# ---- Control: stats snapshot + WebSocket --------------------------------
@app.get("/stats")
async def stats() -> dict:
    login = await check_login()
    return {
        "pool": runner.stats.snapshot(),
        "reaper": reaper.stats.snapshot(),
        "logged_in": login["logged_in"],
        "v1_url": config.v1_base_url,
        "default_model": config.default_model,
        "auth_required": config.auth_required,
        "uptime_secs": int(time.time() - _START_TS),
    }


@app.websocket("/ws/stats")
async def ws_stats(ws):
    await ws.accept()
    try:
        while True:
            login = await check_login()
            payload = {
                "pool": runner.stats.snapshot(),
                "reaper": reaper.stats.snapshot(),
                "logged_in": login["logged_in"],
                "v1_url": config.v1_base_url,
                "default_model": config.default_model,
                "auth_required": config.auth_required,
                "uptime_secs": int(time.time() - _START_TS),
            }
            await ws.send_text(json.dumps(payload))
            await asyncio.sleep(2)
    except Exception:
        # client closed
        return


# ---- Dashboard (served on the dashboard port; also reachable here) -------
@app.get("/")
async def root():
    return dashboard_html()


@app.get("/dashboard")
async def dashboard():
    return dashboard_html()
