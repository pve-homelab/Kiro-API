"""Control endpoints: /health, /ready, /stats, /ws/stats."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request

from .. import __version__

router = APIRouter()
_START = time.time()


def _snapshot(request: Request) -> dict:
    app = request.app
    auth = app.state.auth
    pool = app.state.pool
    cfg = app.state.config
    return {
        "version": __version__,
        "uptime_secs": int(time.time() - _START),
        "auth": auth.state.snapshot(),
        "pool": pool.stats(),
        "urls": cfg.urls(),
        "default_model": cfg.default_model,
        "auth_required": cfg.auth_required,
    }


@router.get("/health")
async def health(request: Request) -> dict:
    """Liveness — the process is up and responding."""
    return {"status": "ok", **_snapshot(request)}


@router.get("/ready")
async def ready(request: Request):
    """Readiness — logged in AND a worker is available to serve."""
    snap = _snapshot(request)
    logged_in = snap["auth"]["logged_in"]
    pool_ok = request.app.state.pool.any_available()
    ready = logged_in and pool_ok
    body = {"ready": ready, "logged_in": logged_in, "pool_available": pool_ok, **snap}
    from fastapi.responses import JSONResponse
    return JSONResponse(body, status_code=200 if ready else 503)


@router.get("/stats")
async def stats(request: Request) -> dict:
    return _snapshot(request)


@router.websocket("/ws/stats")
async def ws_stats(ws):
    await ws.accept()
    try:
        while True:
            snap = _snapshot_from_app(ws.app)
            await ws.send_text(json.dumps(snap))
            await asyncio.sleep(2)
    except Exception:
        return


def _snapshot_from_app(app) -> dict:
    auth = app.state.auth
    pool = app.state.pool
    cfg = app.state.config
    return {
        "version": __version__,
        "uptime_secs": int(time.time() - _START),
        "auth": auth.state.snapshot(),
        "pool": pool.stats(),
        "urls": cfg.urls(),
        "default_model": cfg.default_model,
        "auth_required": cfg.auth_required,
    }
