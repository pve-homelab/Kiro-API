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


@router.get("/metrics")
async def metrics(request: Request):
    """Prometheus text-exposition metrics."""
    from fastapi.responses import PlainTextResponse

    app = request.app
    pool = app.state.pool.stats()
    auth = app.state.auth.state
    m = app.state.service.metrics.snapshot()
    lines: list[str] = []

    def metric(name, mtype, value, help_text, labels=""):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name}{labels} {value}")

    metric("kiro_api_uptime_seconds", "gauge", int(time.time() - _START), "Service uptime.")
    metric("kiro_api_requests_total", "counter", m["requests_total"], "Total API turns started.")
    metric("kiro_api_errors_total", "counter", m["errors_total"], "Total failed turns.")
    metric("kiro_api_prompt_tokens_total", "counter", m["prompt_tokens_total"], "Estimated prompt tokens.")
    metric("kiro_api_completion_tokens_total", "counter", m["completion_tokens_total"], "Estimated completion tokens.")
    metric("kiro_api_workers", "gauge", pool["workers_total"], "Live workers.")
    metric("kiro_api_workers_busy", "gauge", pool["workers_busy"], "Busy workers.")
    metric("kiro_api_workers_max", "gauge", pool["max_workers"], "Max workers (ceiling).")
    metric("kiro_api_queue_waiters", "gauge", pool["queue_waiters"], "Requests waiting for a worker.")
    metric("kiro_api_logged_in", "gauge", 1 if auth.logged_in else 0, "1 if logged in to Kiro, else 0.")
    # Per-category error breakdown.
    lines.append("# HELP kiro_api_errors_by_category_total Failed turns by category.")
    lines.append("# TYPE kiro_api_errors_by_category_total counter")
    for cat, n in m["errors_by_category"].items():
        safe = cat.replace('"', "")
        lines.append(f'kiro_api_errors_by_category_total{{category="{safe}"}} {n}')

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


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
